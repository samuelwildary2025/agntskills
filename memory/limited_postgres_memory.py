from typing import List, Optional, Dict, Any
import json
import logging
import threading
from langchain_community.chat_message_histories import PostgresChatMessageHistory
from langchain_core.messages import BaseMessage, message_to_dict, messages_from_dict
from langchain_core.chat_history import BaseChatMessageHistory
try:
    import psycopg2
    import psycopg2.extras
    from psycopg2 import sql
except ImportError:
    # Fallback para psycopg 3.x
    import psycopg as psycopg2
    from psycopg import sql

# Configurar logger
logger = logging.getLogger(__name__)

class LimitedPostgresChatMessageHistory(BaseChatMessageHistory):
    """
    Histórico de chat PostgreSQL que armazena todas as mensagens mas
    limita o contexto do agente às mensagens recentes.
    Faz a inserção manual para garantir persistência (COMMIT explícito).
    """
    
    _schema_cache_lock = threading.Lock()
    _schema_checked: set[tuple[str, str]] = set()

    def __init__(
        self,
        session_id: str,
        connection_string: str,
        table_name: str = "memoria",
        max_messages: int = 8,  # Equilíbrio entre contexto e economia de tokens
        **kwargs
    ):
        self.session_id = session_id
        self.connection_string = connection_string
        self.table_name = table_name
        self.max_messages = max_messages
        
        # Garantir que a tabela tenha as colunas necessárias (Auto-fix)
        self._ensure_schema()
        
        # Mantemos a instância base apenas para leitura (se necessário)
        # mas faremos a escrita manualmente para garantir o commit
        try:
            self._postgres_history = PostgresChatMessageHistory(
                session_id=session_id,
                connection_string=connection_string,
                table_name=table_name,
                **kwargs
            )
        except Exception as e:
            logger.warning(f"Erro ao iniciar PostgresChatMessageHistory padrão: {e}")
            self._postgres_history = None
    
    def _ensure_schema(self) -> None:
        """
        Verifica se a tabela tem a coluna created_at e a cria se necessário.
        Isso corrige o erro de 'column created_at does not exist'.
        """
        cache_key = (self.connection_string, self.table_name)
        with self._schema_cache_lock:
            if cache_key in self._schema_checked:
                return

        try:
            table_ident = sql.Identifier(self.table_name)
            index_ident = sql.Identifier(f"idx_{self.table_name}_created_at".replace("-", "_"))
            # Usar conexão passageira para verificar schema
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cursor:
                    # Adicionar coluna created_at se não existir
                    cursor.execute(
                        sql.SQL(
                            "ALTER TABLE {} ADD COLUMN IF NOT EXISTS created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                        ).format(table_ident)
                    )
                    
                    # Opcional: Index para performance
                    cursor.execute(
                        sql.SQL("CREATE INDEX IF NOT EXISTS {} ON {}(created_at)").format(
                            index_ident,
                            table_ident,
                        )
                    )
                    
                    conn.commit()
                    logger.info(f"✅ Schema verificado: coluna 'created_at' garantida na tabela '{self.table_name}'.")
                    with self._schema_cache_lock:
                        self._schema_checked.add(cache_key)
                    
        except Exception as e:
            logger.error(f"⚠️ Erro ao verificar schema (pode ser ignorado se o banco estiver inacessível no init): {e}")
    
    @property
    def messages(self) -> List[BaseMessage]:
        """Obtém mensagens (contexto otimizado)."""
        return self.get_optimized_context()
    
    def add_message(self, message: BaseMessage) -> None:
        """
        Adiciona uma mensagem ao banco de dados com SQL manual e COMMIT explícito.
        """
        conn = None
        try:
            # Converter mensagem para dicionário/JSON compatível
            msg_dict = message_to_dict(message)
            msg_json = json.dumps(msg_dict)
            
            # Conexão manual
            conn = psycopg2.connect(self.connection_string)
            cursor = conn.cursor()
            
            # Query de inserção direta
            query = sql.SQL("INSERT INTO {} (session_id, message) VALUES (%s, %s)").format(
                sql.Identifier(self.table_name)
            )
            
            cursor.execute(query, (self.session_id, msg_json))
            conn.commit() # <--- O PULO DO GATO: Commit explícito
            
            logger.info(f"📝 Mensagem persistida manualmente no DB para {self.session_id}")
            
            cursor.close()
            
        except Exception as e:
            logger.error(f"❌ Erro CRÍTICO ao salvar mensagem no Postgres: {e}")
            if conn:
                conn.rollback()
            # Tentar fallback para o método da biblioteca se o manual falhar
            if self._postgres_history:
                logger.info("Tentando fallback para PostgresChatMessageHistory...")
                self._postgres_history.add_message(message)
        finally:
            if conn:
                conn.close()
    
    def clear(self) -> None:
        """Limpa todas as mensagens da sessão."""
        if self._postgres_history:
            self._postgres_history.clear()
        else:
            # Implementação manual se necessário
            try:
                with psycopg2.connect(self.connection_string) as conn:
                    with conn.cursor() as cursor:
                        cursor.execute(
                            sql.SQL("DELETE FROM {} WHERE session_id = %s").format(
                                sql.Identifier(self.table_name)
                            ),
                            (self.session_id,),
                        )
                        conn.commit()
            except Exception as e:
                logger.error(f"Erro ao limpar histórico: {e}")
    
    def get_optimized_context(self) -> List[BaseMessage]:
        """
        Obtém contexto otimizado lendo diretamente do banco.
        """
        # Se a biblioteca padrão estiver funcionando para leitura, usamos ela
        if self._postgres_history:
            try:
                all_messages = self._postgres_history.messages
                if all_messages:
                    return self._filter_messages(all_messages)
            except Exception as e:
                logger.warning(f"Erro ao ler via langchain lib: {e}. Tentando leitura manual.")
        
        # Leitura manual (fallback robusto)
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "SELECT message FROM {} WHERE session_id = %s ORDER BY created_at ASC"
                        ).format(sql.Identifier(self.table_name)),
                        (self.session_id,),
                    )
                    
                    rows = cursor.fetchall()
                    messages = []
                    for row in rows:
                        # row[0] é o jsonb
                        msg_data = row[0]
                        # Se vier como string (dependendo do driver), faz parse
                        if isinstance(msg_data, str):
                            msg_data = json.loads(msg_data)
                        
                        # Reconstrói o objeto Message
                        msgs = messages_from_dict([msg_data])
                        messages.extend(msgs)
                    
                    return self._filter_messages(messages)
                    
        except Exception as e:
            logger.error(f"Erro ao ler mensagens manualmente: {e}")
            return []

    def _filter_messages(self, all_messages: List[BaseMessage]) -> List[BaseMessage]:
        """Lógica de filtragem de mensagens antigas/confusão."""
        if len(all_messages) <= self.max_messages:
            return all_messages
        
        recent_messages = all_messages[-self.max_messages:]
        
        if self.should_clear_context(recent_messages):
            logger.info(f"🔄 Detectada confusão. Limpando contexto para {self.session_id}")
            return recent_messages[-3:]
            
        return recent_messages

    def should_clear_context(self, recent_messages: List[BaseMessage]) -> bool:
        """Verifica se o agente está confuso."""
        if len(recent_messages) < 3:
            return False
        
        confusion_patterns = [
            "não identifiquei", "não consegui identificar", 
            "informar o nome principal", "desculpe, não", "pode informar"
        ]

        def _to_text(msg: BaseMessage) -> str:
            content = getattr(msg, "content", "")
            if isinstance(content, str):
                return content.lower()
            if isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, str):
                        parts.append(block)
                    elif isinstance(block, dict):
                        txt = block.get("text")
                        if isinstance(txt, str):
                            parts.append(txt)
                return " ".join(parts).lower()
            if isinstance(content, dict):
                txt = content.get("text")
                return txt.lower() if isinstance(txt, str) else str(content).lower()
            return str(content).lower()

        recent_text = " ".join(_to_text(msg) for msg in recent_messages[-3:])
        confusion_count = sum(1 for pattern in confusion_patterns if pattern in recent_text)
        
        return confusion_count >= 2

    # Métodos auxiliares
    def get_message_count(self) -> int:
        try:
            with psycopg2.connect(self.connection_string) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        sql.SQL("SELECT COUNT(*) FROM {} WHERE session_id = %s").format(
                            sql.Identifier(self.table_name)
                        ),
                        (self.session_id,),
                    )
                    return cursor.fetchone()[0]
        except Exception:
            return 0
