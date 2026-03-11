"""
Ferramentas para manipulação de tempo e histórico
"""
import datetime
import pytz
import json
import psycopg2
from typing import List, Optional
from config.logger import setup_logger
from config.settings import settings

logger = setup_logger(__name__)


def get_current_time(timezone: str = "America/Fortaleza") -> str:
    """
    Retorna a data e hora atual no fuso horário especificado.
    
    Args:
        timezone: Fuso horário (padrão: America/Fortaleza)
    
    Returns:
        String formatada com data e hora
    """
    try:
        tz = pytz.timezone(timezone)
        now = datetime.datetime.now(tz)
        
        # Formato amigável
        formatted_time = now.strftime("%d/%m/%Y às %H:%M:%S (%Z)")
        
        # Informações adicionais
        day_of_week = now.strftime("%A")
        day_names = {
            "Monday": "Segunda-feira",
            "Tuesday": "Terça-feira",
            "Wednesday": "Quarta-feira",
            "Thursday": "Quinta-feira",
            "Friday": "Sexta-feira",
            "Saturday": "Sábado",
            "Sunday": "Domingo"
        }
        day_pt = day_names.get(day_of_week, day_of_week)
        
        result = f"📅 {day_pt}, {formatted_time}"
        
        logger.info(f"Hora atual consultada: {result}")
        return result
    
    except pytz.exceptions.UnknownTimeZoneError:
        error_msg = f"❌ Erro: Fuso horário '{timezone}' desconhecido."
        logger.error(error_msg)
        return error_msg


def search_message_history(telefone: str, keyword: str = None) -> str:
    """
    Busca mensagens anteriores do cliente.
    
    Use esta ferramenta quando o cliente perguntar sobre:
    - "O que eu pedi?"
    - "Quais produtos mencionamos?"
    
    Args:
        telefone: Número do cliente (formato: 5511999998888)
        keyword: Palavra-chave para buscar (opcional)
    
    Returns:
        String com mensagens encontradas
    """
    try:
        # Sanitizar telefone
        telefone_limpo = ''.join(filter(str.isdigit, telefone))
        
        # Conectar ao PostgreSQL
        conn = psycopg2.connect(settings.postgres_connection_string)
        cursor = conn.cursor()
        
        # Query simplificada (sem created_at)
        if keyword:
            query = """
                SELECT message 
                FROM {} 
                WHERE session_id = %s 
                AND message->>'content' ILIKE %s
                LIMIT 10
            """.format(settings.postgres_table_name)
            cursor.execute(query, (telefone_limpo, f'%{keyword}%'))
        else:
            query = """
                SELECT message 
                FROM {} 
                WHERE session_id = %s 
                LIMIT 15
            """.format(settings.postgres_table_name)
            cursor.execute(query, (telefone_limpo,))
        
        results = cursor.fetchall()
        
        if not results:
            return "❌ Não encontrei mensagens anteriores. Talvez seja o início da nossa conversa."
        
        # Formatar resultado
        mensagens_formatadas = []
        for row in results:
            msg_data = row[0]
            
            # Extrair tipo e conteúdo
            msg_type = msg_data.get('type', 'unknown')
            content = msg_data.get('content', '')
            
            # Identificar quem enviou
            remetente = "Cliente" if msg_type == "human" else "Ana"
            
            # Limitar tamanho da mensagem
            if len(content) > 50:
                content = content[:47] + "..."
            
            mensagens_formatadas.append(f"- {remetente}: {content}")
        
        cursor.close()
        conn.close()
        
        # Criar resposta final
        if keyword:
            resumo = f"📋 Encontrei {len(mensagens_formatadas)} mensagens sobre '{keyword}':\n\n"
        else:
            resumo = f"📋 Últimas {len(mensagens_formatadas)} mensagens:\n\n"
        
        resumo += "\n".join(mensagens_formatadas)
        
        logger.info(f"Histórico consultado para {telefone_limpo}: {len(mensagens_formatadas)} mensagens")
        return resumo
        
    except psycopg2.Error as e:
        error_msg = f"❌ Erro ao acessar banco de dados: {str(e)}"
        logger.error(error_msg)
        return error_msg
    
    except Exception as e:
        error_msg = f"❌ Erro ao buscar histórico: {str(e)}"
        logger.error(error_msg)
        return error_msg

