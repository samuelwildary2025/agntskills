from typing import List
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage
from memory.redis_memory import RedisChatMessageHistory
from memory.limited_postgres_memory import LimitedPostgresChatMessageHistory
from config.settings import settings

class HybridChatMessageHistory(BaseChatMessageHistory):
    """
    Memória Híbrida:
    - LEITURA: Apenas do Redis (Sessão Curta/Contexto Quente)
    - ESCRITA: Redis (Sessão) + Postgres (Log Permanente)
    """
    
    def __init__(self, session_id: str, redis_ttl: int = 2400):
        self.session_id = session_id
        # Fonte da verdade para o contexto (Redis)
        self.redis_history = RedisChatMessageHistory(session_id=session_id, ttl=redis_ttl)
        # Log permanente (Postgres)
        self.postgres_history = LimitedPostgresChatMessageHistory(
            connection_string=settings.postgres_connection_string,
            session_id=session_id,
            table_name=settings.postgres_table_name,
            max_messages=settings.postgres_message_limit
        )

    @property
    def messages(self) -> List[BaseMessage]:
        """Lê mensagens APENAS da sessão ativa no Redis."""
        return self.redis_history.messages

    def add_message(self, message: BaseMessage) -> None:
        """Salva em AMBOS: Redis (Sessão) e Postgres (Log)."""
        # 1. Salva na sessão quente (renova TTL)
        self.redis_history.add_message(message)
        
        # 2. Salva no log permanente
        self.postgres_history.add_message(message)

    def clear(self) -> None:
        """Limpa apenas a sessão quente (Redis). O histórico Postgres permanece."""
        self.redis_history.clear()
