"""
WhatsApp API - UAZAPI Integration
==================================
Integração com a API UAZAPI para envio/recebimento de mensagens WhatsApp.

Documentação: https://docs.uazapi.com/
"""

import requests
import json
import re
from typing import Optional, Dict, Any
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)


class WhatsAppAPI:
    """
    Integração com UAZAPI para WhatsApp.
    
    Configurações necessárias no .env:
    - UAZAPI_BASE_URL: URL da instância (ex: https://aimerc.uazapi.com)
    - UAZAPI_TOKEN: Token da instância
    """
    
    def __init__(self):
        self.base_url = (getattr(settings, 'uazapi_base_url', None) or "").rstrip("/")
        self.token = getattr(settings, 'uazapi_token', None) or ""
        
        if not self.base_url:
            logger.warning("⚠️ UAZAPI_BASE_URL não configurado!")
        if not self.token:
            logger.warning("⚠️ UAZAPI_TOKEN não configurado!")
            
        logger.info(f"🔌 UAZAPI inicializada: {self.base_url}")
    
    def _get_headers(self) -> Dict[str, str]:
        """Headers padrão para requisições à UAZAPI."""
        return {
            "Content-Type": "application/json",
            "token": self.token  # UAZAPI usa header 'token'
        }
    
    def _clean_number(self, phone: str) -> str:
        """Remove caracteres não numéricos do telefone."""
        return re.sub(r"\D", "", str(phone))
    
    def send_text(self, to: str, text: str) -> bool:
        """
        Envia mensagem de texto.
        
        POST /send/text
        Body: { "number": "5511...", "text": "...", "presence": true }
        """
        if not self.base_url or not self.token:
            logger.error("❌ UAZAPI não configurada! Mensagem NÃO enviada.")
            return False
        
        # Suporte a <BREAK> para múltiplas mensagens
        if "<BREAK>" in text:
            parts = text.split("<BREAK>")
            logger.info(f"🔄 Mensagem multi-parte: {len(parts)} partes")
            
            import time
            success_all = True
            for i, part in enumerate(parts):
                part = part.strip()
                if not part:
                    continue
                if i > 0:
                    time.sleep(2.0)  # Delay entre mensagens
                if not self.send_text(to, part):
                    success_all = False
            return success_all
        
        url = f"{self.base_url}/send/text"
        clean_num = self._clean_number(to)
        
        payload = {
            "number": clean_num,
            "text": text,
            "delay": 0,
            "presence": True,  # Mostra "digitando..." antes de enviar
            "linkpreview": True
        }
        
        logger.info(f"📤 Enviando texto para {clean_num}: {text[:50]}...")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=15)
            
            if resp.status_code == 200:
                logger.info(f"✅ Mensagem enviada para {clean_num}")
                return True
            else:
                logger.error(f"❌ Erro UAZAPI ({resp.status_code}): {resp.text[:300]}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mensagem: {e}")
            return False
    
    def send_media(self, to: str, media_url: str = None, caption: str = "",
                   base64_data: str = None, mimetype: str = "image/jpeg") -> bool:
        """
        Envia mídia (imagem, vídeo, documento, áudio).
        
        POST /send/media
        Body: { "number": "...", "mediatype": "image", "media": "url_or_base64", "caption": "..." }
        """
        if not self.base_url or not self.token:
            logger.error("❌ UAZAPI não configurada!")
            return False
        
        url = f"{self.base_url}/send/media"
        clean_num = self._clean_number(to)
        
        # Determinar tipo de mídia
        mediatype = "image"  # Default
        if mimetype:
            if "video" in mimetype:
                mediatype = "video"
            elif "audio" in mimetype:
                mediatype = "audio"
            elif "pdf" in mimetype or "document" in mimetype:
                mediatype = "document"
        
        # Usar URL ou Base64
        media_content = base64_data if base64_data else media_url
        
        payload = {
            "number": clean_num,
            "mediatype": mediatype,
            "media": media_content,
            "caption": caption,
            "presence": True
        }
        
        logger.info(f"📷 Enviando mídia ({mediatype}) para {clean_num}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=30)
            
            if resp.status_code == 200:
                logger.info(f"✅ Mídia enviada para {clean_num}")
                return True
            else:
                logger.error(f"❌ Erro envio mídia ({resp.status_code}): {resp.text[:300]}")
                return False
                
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mídia: {e}")
            return False
    
    def send_presence(self, to: str, presence: str = "composing") -> bool:
        """
        Envia status de presença (digitando, gravando).
        
        POST /message/presence
        Body: { "number": "...", "presence": "composing", "delay": 5000 }
        
        Valores: composing, recording, paused
        """
        if not self.base_url or not self.token:
            return False
        
        url = f"{self.base_url}/message/presence"
        clean_num = self._clean_number(to)
        
        payload = {
            "number": clean_num,
            "presence": presence,
            "delay": 5000  # 5 segundos
        }
        
        try:
            requests.post(url, headers=self._get_headers(), json=payload, timeout=5)
            logger.debug(f"⌨️ Presença '{presence}' enviada para {clean_num}")
            return True
        except Exception:
            return False
    
    def mark_as_read(self, chat_id: str, message_id: str = None) -> bool:
        """
        Marca mensagens como lidas.
        
        POST /message/markread
        Body: { "id": ["MSG_ID"] }
        """
        if not self.base_url or not self.token or not message_id:
            return False
        
        url = f"{self.base_url}/message/markread"
        
        payload = {
            "id": [message_id] if isinstance(message_id, str) else message_id
        }
        
        logger.info(f"👀 Marcando como lido: {message_id}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=5)
            if resp.status_code == 200:
                logger.info(f"✅ Mensagem marcada como lida")
                return True
            else:
                logger.warning(f"⚠️ Erro mark_as_read ({resp.status_code})")
                return False
        except Exception as e:
            logger.error(f"❌ Erro mark_as_read: {e}")
            return False
    
    def get_media_base64(self, message_id: str) -> Optional[Dict[str, str]]:
        """
        Baixa mídia de uma mensagem recebida.
        
        POST /message/download
        Body: { "id": "MSG_ID", "return_base64": true }
        
        Retorna: { "base64": "...", "mimetype": "..." }
        """
        if not self.base_url or not self.token or not message_id:
            return None
        
        url = f"{self.base_url}/message/download"
        
        payload = {
            "id": message_id,
            "return_link": False,
            "return_base64": True,
            "generate_mp3": True  # Converte áudio para MP3 se necessário
        }
        
        logger.info(f"🖼️ Baixando mídia: {message_id}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=10)
            
            if resp.status_code == 200:
                data = resp.json()
                
                # UAZAPI pode retornar em diferentes formatos
                if isinstance(data, dict):
                    # Formato: { "success": true, "data": { "base64": "...", "mimetype": "..." } }
                    if data.get("success") and "data" in data:
                        return data["data"]
                    # Formato direto: { "base64": "...", "mimetype": "..." }
                    if "base64" in data:
                        return data
                    # Novo formato UAZAPI: { "base64Data": "..." }
                    if "base64Data" in data:
                        return {"base64": data["base64Data"], "mimetype": data.get("mimetype", "")}
                
                logger.warning(f"⚠️ Formato de resposta inesperado: {str(data)[:200]}")
                return None
            else:
                logger.error(f"❌ Erro download mídia ({resp.status_code}): {resp.text[:200]}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Erro ao baixar mídia: {e}")
            return None


# Instância global
whatsapp = WhatsAppAPI()
