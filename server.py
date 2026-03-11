"""
Servidor FastAPI para receber mensagens do WhatsApp e processar com o agente
Suporta: Texto, Áudio (Transcrição), Imagem (Visão) e PDF (Extração de Texto + Link)
Versão: 1.6.0 (Correção de LID e Buffer Personalizado)
"""
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import requests
import json
from datetime import datetime
import time
import random
import threading
import re
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from arq import create_pool
from arq.connections import RedisSettings
from urllib.parse import urlparse
from apscheduler.schedulers.background import BackgroundScheduler
from scripts.populate_products_db import sync_products_db
try:
    from scripts.sync_typesense import sync_typesense_from_postgres
except Exception:
    sync_typesense_from_postgres = None

# Tenta importar pypdf para leitura de comprovantes
try:
    from pypdf import PdfReader
except ImportError:
    PdfReader = None

from config.settings import settings
from config.logger import setup_logger
from agent import run_agent_langgraph as run_agent, get_session_history
from tools.whatsapp_api import whatsapp
from tools.redis_tools import (
    push_message_to_buffer,
    get_buffer_length,
    pop_all_messages,
    set_agent_cooldown,
    is_agent_in_cooldown,
    get_order_session,
    start_order_session,
    refresh_session_ttl,
    get_order_context,
    clear_cart,
    check_and_mark_message_processed,
    acquire_buffer_session_lock,
    refresh_buffer_session_lock,
    release_buffer_session_lock,
)

logger = setup_logger(__name__)

app = FastAPI(title="Agente de Supermercado", version="1.7.0")  # Queue-based version

# ARQ Queue Pool (inicializado no startup)
arq_pool = None

# Scheduler
scheduler = BackgroundScheduler()

# --- Models ---
class WhatsAppMessage(BaseModel):
    telefone: str
    mensagem: str
    message_id: Optional[str] = None
    timestamp: Optional[str] = None
    message_type: Optional[str] = "text"

class AgentResponse(BaseModel):
    success: bool
    response: str
    telefone: str
    timestamp: str
    error: Optional[str] = None

# --- Helpers ---

_REDACT_KEYS = {
    "authorization",
    "token",
    "apikey",
    "api_key",
    "password",
    "secret",
    "mediaBase64",
    "base64",
}


def _sanitize_for_log(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "[...]"
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            key_low = str(k).lower()
            if key_low in _REDACT_KEYS or "base64" in key_low:
                out[k] = "[REDACTED]"
            else:
                out[k] = _sanitize_for_log(v, depth + 1)
        return out
    if isinstance(value, list):
        if len(value) > 10:
            return [_sanitize_for_log(x, depth + 1) for x in value[:10]] + ["..."]
        return [_sanitize_for_log(x, depth + 1) for x in value]
    if isinstance(value, str):
        if len(value) > 400:
            return value[:400] + "...[truncated]"
        return value
    return value


def _sync_search_indexes() -> None:
    """Sincroniza catálogo em Postgres e Typesense."""
    sync_products_db()
    if settings.typesense_enabled and sync_typesense_from_postgres:
        try:
            sync_typesense_from_postgres()
        except Exception as exc:
            logger.warning(f"Falha no sync Typesense: {exc}")

def process_pdf(message_id: str) -> tuple[Optional[str], Optional[str]]:
    """
    Baixa o PDF via nova API e extrai o texto.
    Retorna (texto_extraido, base64_content).
    """
    if not PdfReader:
        logger.error("❌ Biblioteca pypdf não instalada. Adicione ao requirements.txt")
        return "[Erro: sistema não suporta leitura de PDF]", None

    if not message_id:
        return None, None
    
    logger.info(f"📄 Processando PDF: {message_id}")
    
    try:
        import base64
        
        # Obter PDF via nova API (Base64)
        media_data = whatsapp.get_media_base64(message_id)
        
        if not media_data or not media_data.get("base64"):
            logger.error(f"❌ Falha ao obter PDF: {message_id}")
            return None, None
        
        b64_content = media_data["base64"]
        
        # Decodificar Base64
        pdf_bytes = base64.b64decode(b64_content)
        
        # Ler PDF em memória
        f = io.BytesIO(pdf_bytes)
        reader = PdfReader(f)
        
        text_content = []
        for page in reader.pages:
            text_content.append(page.extract_text())
            
        full_text = "\\n".join(text_content)
        full_text = re.sub(r'\s+', ' ', full_text).strip()
        
        logger.info(f"✅ PDF lido com sucesso ({len(full_text)} chars)")
        return full_text, b64_content
        
    except Exception as e:
        logger.error(f"Erro ao ler PDF: {e}")
        return None, None

def transcribe_audio(message_id: str = None, base64_data: str = None, mimetype: str = None) -> Optional[str]:
    """
    Transcreve áudio usando Google Gemini.
    
    Prioridade:
    1. Se base64_data for fornecido, usa diretamente (do webhook)
    2. Senão, tenta baixar via API usando message_id
    """
    import base64
    import tempfile
    import os as os_module
    
    audio_bytes = None
    mime_type_clean = (mimetype or "audio/ogg").split(";")[0].strip()
    
    # 1. Tentar usar Base64 direto (do webhook)
    if base64_data:
        try:
            audio_bytes = base64.b64decode(base64_data)
            logger.info(f"🎤 Usando áudio Base64 direto do webhook ({len(audio_bytes)} bytes)")
        except Exception as e:
            logger.error(f"Erro ao decodificar Base64 do webhook: {e}")
    
    # 2. Fallback: Tentar baixar via API
    if audio_bytes is None and message_id:
        # Dedup guard via Redis: se já tentamos baixar esse audio e falhou, não tentamos de novo
        dedup_key = f"audio_tried:{message_id}"
        try:
            from tools.redis_tools import get_redis_client
            _rc = get_redis_client()
            if _rc and _rc.get(dedup_key):
                logger.warning(f"⚠️ Áudio {message_id} já foi tentado antes e falhou. Ignorando retry.")
                return None
        except Exception:
            pass

        logger.info(f"🎤 Tentando baixar áudio via API: {message_id}")
        media_data = whatsapp.get_media_base64(message_id)
        
        if media_data and media_data.get("base64"):
            try:
                audio_bytes = base64.b64decode(media_data["base64"])
                mime_type_clean = (media_data.get("mimetype") or mime_type_clean).split(";")[0].strip()
                logger.info(f"🎤 Áudio baixado via API ({len(audio_bytes)} bytes)")
            except Exception as e:
                logger.error(f"Erro ao decodificar Base64 da API: {e}")
        else:
            # Marcar no Redis que essa mensagem já foi tentada (TTL 5 minutos)
            try:
                from tools.redis_tools import get_redis_client
                _rc = get_redis_client()
                if _rc:
                    _rc.setex(dedup_key, 300, "1")
            except Exception:
                pass
            logger.warning(f"⚠️ API não retornou Base64 para: {message_id}")
    
    # Se não conseguiu obter o áudio de nenhuma forma
    if audio_bytes is None:
        logger.error("❌ Não foi possível obter o áudio nem do webhook nem da API")
        return None
    
    try:
        if not settings.google_api_key:
            logger.error("❌ GOOGLE_API_KEY não configurada no .env! Necessária para transcrição de áudio.")
            return None

        logger.info(f"🎧 Transcrevendo áudio com Gemini ({mime_type_clean})")
        
        from google import genai
        client = genai.Client(api_key=settings.google_api_key)
        
        # Determinar extensão baseada no content-type
        ext_map = {
            'audio/ogg': '.ogg',
            'audio/mpeg': '.mp3',
            'audio/mp4': '.m4a',
            'audio/wav': '.wav',
            'audio/webm': '.webm',
        }
        ext = ext_map.get(mime_type_clean, '.ogg')
        
        # Salvar temporariamente
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        
        try:
            # Upload do arquivo para Gemini com MIME TYPE explícito
            audio_file = client.files.upload(
                file=tmp_path,
                config={'mime_type': mime_type_clean}
            )
            
            # Transcrever usando Gemini
            response = client.models.generate_content(
                model=settings.gemini_audio_model,
                contents=[
                    "Você é um especialista em transcrição de áudio para Supermercados. O áudio pode conter ruído, nomes de produtos, quantidades e gírias. Transcreva com EXATIDÃO o que o cliente disse. Se estiver inaudível, retorne apenas [inaudível]. Ignore silêncios.",
                    audio_file
                ]
            )
            
            transcription = response.text.strip() if response.text else None
            
            if transcription:
                logger.info(f"✅ Áudio transcrito com Gemini: {transcription[:50]}...")
                return transcription
            else:
                logger.warning("⚠️ Gemini retornou transcrição vazia")
                return None
                
        finally:
            # Limpar arquivo temporário
            try:
                os_module.unlink(tmp_path)
            except:
                pass
            
    except Exception as e:
        logger.error(f"Erro transcrição Gemini: {e}")
        return None

def analyze_image(message_id: Optional[str], url: Optional[str] = None) -> Optional[str]:
    if not settings.google_api_key:
        return None

    file_path = None
    try:
        from google import genai
        import tempfile
        import os as os_module
        import base64

        mime_type_clean = None
        image_bytes = None

        if message_id:
            media_data = whatsapp.get_media_base64(message_id)
            if media_data and media_data.get("base64"):
                image_bytes = base64.b64decode(media_data["base64"])
                mime_type_clean = (media_data.get("mimetype") or "image/jpeg").split(";")[0].strip()

        if image_bytes is None and url:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            image_bytes = resp.content
            mime_type_clean = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()

        if not image_bytes:
            return None

        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        ext = ext_map.get((mime_type_clean or "").lower(), ".jpg")

        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(image_bytes)
            file_path = tmp.name

        client = genai.Client(api_key=settings.google_api_key)
        image_file = client.files.upload(file=file_path, config={"mime_type": mime_type_clean or "image/jpeg"})

        prompt = (
            "Analise cuidadosamente esta imagem. Identifique o que ela contém:\\n\\n"
            "1. Se for um COMPROVANTE DE PAGAMENTO (PIX, transferência, recibo bancário): "
            "Diga 'COMPROVANTE DE PAGAMENTO' e extraia: valor, data/hora, nome do pagador e do recebedor se visíveis.\\n\\n"
            "2. Se for um PRODUTO: Retorne nome do produto, marca, versão/sabor/variante, tamanho/peso/volume.\\n\\n"
            "3. Se não for identificável (foto borrada, pessoa, conversa): Diga 'Imagem não identificada'.\\n\\n"
            "Retorne um texto curto em português. Não invente detalhes."
        )

        model_candidates = [settings.llm_model or "gemini-2.0-flash-lite", "gemini-2.0-flash"]
        last_err = None
        for model in model_candidates:
            try:
                response = client.models.generate_content(model=model, contents=[prompt, image_file])
                txt = (response.text or "").strip()
                if txt:
                    return txt[:800]
            except Exception as e:
                last_err = e

        if last_err:
            logger.error(f"Erro visão Gemini: {last_err}")
        return None

    except Exception as e:
        logger.error(f"Erro ao analisar imagem: {e}")
        return None
    finally:
        if file_path:
            try:
                import os as os_module
                os_module.unlink(file_path)
            except Exception:
                pass

def _analyze_image_from_base64(base64_data: str, mimetype: str = None) -> Optional[str]:
    """Analisa imagem diretamente do Base64 (sem precisar baixar via API)."""
    if not settings.google_api_key or not base64_data:
        return None
    
    file_path = None
    try:
        from google import genai
        import tempfile
        import os as os_module
        import base64
        
        # Decodificar Base64
        image_bytes = base64.b64decode(base64_data)
        mime_type_clean = (mimetype or "image/jpeg").split(";")[0].strip()
        
        ext_map = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }
        ext = ext_map.get(mime_type_clean.lower(), ".jpg")
        
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(image_bytes)
            file_path = tmp.name
        
        client = genai.Client(api_key=settings.google_api_key)
        image_file = client.files.upload(file=file_path, config={"mime_type": mime_type_clean})
        
        prompt = (
            "Analise cuidadosamente esta imagem. Identifique o que ela contém:\\n\\n"
            "1. Se for um COMPROVANTE DE PAGAMENTO (PIX, transferência, recibo bancário): "
            "Diga 'COMPROVANTE DE PAGAMENTO' e extraia: valor, data/hora, nome do pagador e do recebedor se visíveis.\\n\\n"
            "2. Se for um PRODUTO: Retorne nome do produto, marca, versão/sabor/variante, tamanho/peso/volume.\\n\\n"
            "3. Se não for identificável (foto borrada, pessoa, conversa): Diga 'Imagem não identificada'.\\n\\n"
            "Retorne um texto curto em português. Não invente detalhes."
        )
        
        model = settings.llm_model or "gemini-2.0-flash-lite"
        response = client.models.generate_content(model=model, contents=[prompt, image_file])
        txt = (response.text or "").strip()
        
        if txt:
            logger.info(f"✅ Imagem analisada via Base64: {txt[:50]}...")
            return txt[:800]
        return None
        
    except Exception as e:
        logger.error(f"Erro ao analisar imagem Base64: {e}")
        return None
    finally:
        if file_path:
            try:
                import os as os_module
                os_module.unlink(file_path)
            except:
                pass

def _extract_incoming(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normaliza e processa (Texto, Áudio, Imagem, Documento/PDF).
    Suporta payload da nova API: { "event": "message", "data": { ... } }
    """
    
    # DEBUG CRÍTICO
    try:
        keys = list(payload.keys())
        logger.info(f"🔍 DEBUG EXTRACT START: Keys={keys}")
    except: pass
    
    # Se o payload vier envelopado no formato novo
    if "data" in payload and isinstance(payload["data"], dict):
        payload = payload["data"]
        try:
            logger.info(f"🔍 DEBUG EXTRACT UNWRAPPED: Keys={list(payload.keys())} | From={payload.get('from')} | Body={payload.get('body')}")
        except: pass

    # ADAPTAÇÃO: Se o payload tiver uma chave 'message' (payload aninhado extra)
    # Ex: { "event": "message", "data": { "instanceId": "...", "message": { ... } } }
    # Mantemos o payload original para buscar metadados (como resolvedPhone)
    original_data = payload.copy()
    if "message" in payload and isinstance(payload["message"], dict):
        payload = payload["message"]
        try:
             logger.info(f"🔍 DEBUG EXTRACT PROMOTED MESSAGE: Keys={list(payload.keys())}")
        except: pass
    else:
        original_data = {} # Não precisa se não houve promoção

    def _clean_number(jid: Any) -> Optional[str]:
        """Extrai apenas o número de telefone de um JID válido."""
        if not jid or not isinstance(jid, str): return None
        
        # Se tiver @lid, é ID de dispositivo (IGNORAR)
        if "@lid" in jid: return None
        
        # Se tiver @g.us, é grupo (IGNORAR)
        if "@g.us" in jid: return None
        
        # Pega a parte antes do @
        # Funciona para: @s.whatsapp.net, @c.us, @lid
        if "@" in jid:
            jid = jid.split("@")[0]
        
        # Remove o :XX (device ID) se existir
        # Ex: "558591517149:23" -> "558591517149"
        if ":" in jid:
            jid = jid.split(":")[0]
            
        # Remove tudo que não for dígito
        num = re.sub(r"\\D", "", jid)
        
        # Validação básica (evita IDs estranhos)
        # Aumentado limite superior para números internacionais
        if len(num) > 20 or len(num) < 8:
            return None
            
        return num

    chat = payload.get("chat") or {}
    # BUGFIX: Após promoção, payload JÁ É a mensagem, não precisa buscar "message" de novo
    message_any = payload  # Era: payload.get("message") or {} - que retornava {} vazio!
    
    if isinstance(payload.get("messages"), list):
        try:
            m0 = payload["messages"][0]
            message_any = m0
            chat = {"wa_id": m0.get("sender") or m0.get("chatid")}
        except: pass

    # --- LÓGICA DE TELEFONE BLINDADA ---
    telefone = None
    
    # Ordem de prioridade para encontrar o número real
    candidates = []
    
    # 0. Resolved Phone (PRIORIDADE MÁXIMA - para casos de LID)
    candidates.append(original_data.get("resolvedPhone"))
    candidates.append(payload.get("resolvedPhone"))
    
    # 1. Sender/ChatID (Geralmente o mais preciso: 5585...@s.whatsapp.net)
    if isinstance(message_any, dict):
        candidates.append(message_any.get("sender"))
        candidates.append(message_any.get("sender_pn")) # FIX: Prioridade para o número real se vier
        candidates.append(message_any.get("chatid"))
    
    # 2. Objeto Chat
    candidates.append(chat.get("id"))
    candidates.append(chat.get("wa_id"))
    candidates.append(chat.get("phone"))
    
    # 3. Payload Raiz (Menos confiável)
    candidates.append(payload.get("from"))
    candidates.append(payload.get("sender"))

    # 4. Estrutura Baileys/Key (CRUCIAL PARA MÍDIA/ÁUDIO)
    # Procura dentro de 'key' se existir no payload
    if isinstance(payload.get("key"), dict):
        candidates.append(payload["key"].get("remoteJid"))
        candidates.append(payload["key"].get("participant")) # Para grupos (embora a gente ignore grupos)

    # Varre a lista e pega o primeiro válido (sem LID)
    for cand in candidates:
        cleaned = _clean_number(cand)
        if cleaned:
            telefone = cleaned
            break

    # 5. Fallback: ID da mensagem (Muitas vezes contém o número: 5585...@s.whatsapp.net:HASH)
    if not telefone:
        id_candidates = []
        id_candidates.append(payload.get("id"))
        id_candidates.append(payload.get("messageid"))
        if isinstance(message_any, dict):
            id_candidates.append(message_any.get("id"))
            id_candidates.append(message_any.get("messageid"))
            
        for cid in id_candidates:
            cleaned = _clean_number(cid)
            if cleaned:
                # Validação extra: ID geralmente tem : ou prefixo longo
                logger.info(f"ℹ️ Telefone extraído do ID da mensagem: {cleaned}")
                telefone = cleaned
                break
            
    # Fallback de emergência (avisa no log)
    if not telefone and payload.get("from"):
        raw = str(payload.get("from"))
        if "@lid" not in raw:
            telefone = re.sub(r"\\D", "", raw)
            logger.warning(f"⚠️ Usando fallback de telefone: {telefone}")

    # --- Extração de Conteúdo (Adaptado para nova API) ---
    # Na nova API, 'body' é o texto e 'mediaUrl' indica mídia
    mensagem_texto = payload.get("body") or payload.get("text")
    message_id = payload.get("id") or payload.get("messageid")
    def _parse_bool(v) -> bool:
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        if isinstance(v, (int, float)):
            return v != 0
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "y", "sim"):
            return True
        if s in ("false", "0", "no", "n", "nao", "não", ""):
            return False
        return False

    from_me = _parse_bool(payload.get("fromMe"))
    
    # Determinar tipo e buscar mídias aninhadas (Formato Baileys/Common)
    msg_keys = list(payload.keys())
    media_url = payload.get("mediaUrl") or payload.get("url")
    
    # NOVO: Capturar mediaBase64 diretamente do webhook (mais eficiente que download)
    media_base64 = payload.get("mediaBase64")
    media_mimetype = payload.get("mimetype")
    media_caption = payload.get("caption")
    
    # ============================================
    # QUOTED MESSAGE (Mensagem Citada/Respondida)
    # ============================================
    # Quando o cliente responde uma mensagem específica (arrasta e responde),
    # o webhook envia informações sobre a mensagem original citada.
    quoted_text = None
    quoted_sender = None
    
    # DEBUG: Log do campo quoted para investigar estrutura (verifica em ambos os payloads)
    raw_quoted = payload.get("quoted")
    raw_quoted_original = original_data.get("quoted") if original_data else None
    
    # Log de debug para investigar
    if raw_quoted:
        logger.info(f"🔍 DEBUG QUOTED (payload): type={type(raw_quoted).__name__}, keys={list(raw_quoted.keys()) if isinstance(raw_quoted, dict) else 'N/A'}, value={str(raw_quoted)[:200]}")
    if raw_quoted_original:
        logger.info(f"🔍 DEBUG QUOTED (original): type={type(raw_quoted_original).__name__}, keys={list(raw_quoted_original.keys()) if isinstance(raw_quoted_original, dict) else 'N/A'}")
    
    # Tentar extrair de diferentes estruturas de payload
    # 1. Formato UAZAPI: "quoted" (campo principal - pode estar em payload ou original_data)
    quoted_msg = payload.get("quoted") or original_data.get("quoted") or payload.get("quotedMsg") or payload.get("quotedMessage") or {}
    if isinstance(quoted_msg, dict) and quoted_msg:
        # UAZAPI envia: quoted.body, quoted.text, quoted.caption, ou pode ser nested
        quoted_text = quoted_msg.get("body") or quoted_msg.get("text") or quoted_msg.get("caption") or quoted_msg.get("conversation") or quoted_msg.get("message")
        quoted_sender = quoted_msg.get("participant") or quoted_msg.get("sender") or quoted_msg.get("from")
        if quoted_text:
            logger.info(f"💬 [UAZAPI] Quoted extraído de 'quoted': {quoted_text[:50]}...")
    
    # 2. Formato contextInfo (Baileys/WPPConnect)
    if not quoted_text:
        context_info = payload.get("contextInfo") or payload.get("context") or {}
        if isinstance(context_info, dict) and context_info:
            quoted_inner = context_info.get("quotedMessage", {})
            if isinstance(quoted_inner, dict):
                quoted_text = quoted_inner.get("conversation") or \
                              quoted_inner.get("extendedTextMessage", {}).get("text") or \
                              quoted_inner.get("body")
            quoted_sender = context_info.get("participant") or context_info.get("remoteJid")
            if quoted_text:
                logger.info(f"💬 [contextInfo] Quoted extraído: {quoted_text[:50]}...")
    
    # 3. Formato simples (algumas APIs enviam direto como string)
    if not quoted_text:
        quoted_text = payload.get("quotedText") or payload.get("quoted_text") or payload.get("quotedBody")
    
    # Se encontrou uma mensagem citada, adicionar como contexto
    if quoted_text:
        quoted_text = str(quoted_text).strip()
        if quoted_text:
            # Formatar para o agente entender o contexto
            context_prefix = f"[Cliente respondeu à mensagem: \"{quoted_text[:200]}\"]\n"
            logger.info(f"💬 Quoted message detectada: {quoted_text[:80]}...")
    else:
        context_prefix = ""
    
    
    # Se tem mediaBase64, já sabemos que é mídia
    if media_base64:
        if media_mimetype and "audio" in media_mimetype:
            msg_type = "audio"
        elif media_mimetype and "pdf" in media_mimetype:
            msg_type = "document"
        else:
            msg_type = "image"
        # Usar caption como texto se existir
        if media_caption:
            mensagem_texto = media_caption
    # Se não achou tipo explícito, tenta deduzir de chaves aninhadas common
    elif any(k in msg_keys for k in ["imageMessage", "videoMessage", "viewOnceMessage", "image"]):
        msg_type = "image"
        sub = payload.get("imageMessage") or payload.get("image") or payload.get("viewOnceMessage")
        if isinstance(sub, dict):
            mensagem_texto = mensagem_texto or sub.get("caption") or sub.get("text")
            media_url = media_url or sub.get("url")
    elif any(k in msg_keys for k in ["audioMessage", "ptt", "audio"]):
        msg_type = "audio"
    elif any(k in msg_keys for k in ["documentMessage", "document"]):
        msg_type = "document"
    else:
        msg_type = str(payload.get("type") or payload.get("messageType") or "chat").lower()

    message_type = "text"
    if msg_type in ["ptt", "audio"] or "audio" in msg_type:
        message_type = "audio"
    elif msg_type in ["image", "video"] or "image" in msg_type or (media_url and any(ext in str(media_url).lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".mp4"])):
        message_type = "image"
    elif msg_type == "document" or "document" in msg_type or (media_url and ".pdf" in str(media_url).lower()):
        message_type = "document"

    # Se for mídia, tenta pegar a URL direto do payload se vier
    if message_type in ["image", "audio", "document"] and media_url:
        # Na nova API, a URL já vem no payload
        pass

    # Lógica legada para garantir compatibilidade com estruturas antigas
    if not mensagem_texto:
        message_any = payload  # No novo formato, payload já é a mensagem
        
        raw_type = str(message_any.get("messageType") or "").lower()
        media_type = str(message_any.get("mediaType") or "").lower()
        base_type = str(message_any.get("type") or "").lower()
        mimetype = str(message_any.get("mimetype") or "").lower()
        
        if "audio" in raw_type or "ptt" in media_type or "audio" in base_type:
            message_type = "audio"
        elif "image" in raw_type or "image" in media_type or "image" in base_type:
            message_type = "image"
        elif "document" in raw_type or "document" in base_type or "application/pdf" in mimetype:
            message_type = "document"

        content = message_any.get("content")
        if isinstance(content, str) and not mensagem_texto:
            mensagem_texto = content
        elif isinstance(content, dict):
            mensagem_texto = content.get("text") or content.get("caption") or mensagem_texto
        
        if not mensagem_texto:
            txt = message_any.get("text")
            if isinstance(txt, dict):
                mensagem_texto = txt.get("body")
            else:
                mensagem_texto = txt or message_any.get("body")

    if from_me:
        # Se for mensagem enviada por MIM (atendente), precisamos travar o CLIENTE da conversa.
        # Prioriza campos de destinatário/chat e evita usar "sender" (que costuma ser o próprio agente).
        agent_clean = re.sub(r"\\D", "", (settings.whatsapp_agent_number or ""))
        candidates_me = [
            payload.get("to"),
            payload.get("recipient"),
            payload.get("chatId"),
            message_any.get("chatid") if isinstance(message_any, dict) else None,
            chat.get("wa_id"),
            chat.get("id"),
            chat.get("phone"),
            payload.get("from"),
        ]
        for cand in candidates_me:
            cleaned = _clean_number(str(cand)) if cand is not None else None
            if not cleaned:
                continue
            if agent_clean and cleaned == agent_clean:
                continue
            telefone = cleaned
            break
        # NÃO BAIXAR nem analisar nada. Apenas retornar o placeholder.
        # O Human Takeover será tratado mais abaixo, após o logging.
        mensagem_texto = mensagem_texto or f"[Mídia do atendente]"
    elif message_type == "audio" and not mensagem_texto:
        # Prioriza Base64 do webhook (mais eficiente que API)
        if media_base64:
            logger.info(f"🎤 Transcrevendo áudio via Base64 direto do webhook...")
            trans = transcribe_audio(message_id=message_id, base64_data=media_base64, mimetype=media_mimetype)
        elif message_id:
            # Fallback: tentar baixar via API
            trans = transcribe_audio(message_id=message_id)
        else:
            trans = None
            
        mensagem_texto = f"[Áudio]: {trans}" if trans else "[Áudio inaudível]"
            
    elif message_type == "image":
        caption = mensagem_texto or ""
        analysis = None
        
        # NOVO: Tentar usar mediaBase64 direto (mais eficiente)
        if media_base64:
            try:
                logger.info(f"📷 Analisando imagem via Base64 direto...")
                analysis = _analyze_image_from_base64(media_base64, media_mimetype)
            except Exception as e:
                logger.error(f"Erro ao analisar imagem Base64: {e}")
        
        # Fallback: usar API de download (via Base64)
        if not analysis:
            analysis = analyze_image(message_id, media_url)
        
        if analysis:
            base = caption.strip()
            mensagem_texto = f"{base}\\n[Análise da imagem]: {analysis}".strip() if base else f"[Análise da imagem]: {analysis}"
            
            # AUTO-SAVE: Se for comprovante de pagamento, salvar Base64 no Redis automaticamente
            if "COMPROVANTE" in analysis.upper() and media_base64:
                from tools.redis_tools import set_comprovante
                # Salvar o Base64 com prefixo data:image para o painel converter
                mime = media_mimetype or "image/jpeg"
                data_uri = f"data:{mime};base64,{media_base64}"
                set_comprovante(telefone, data_uri)
                logger.info(f"🧾 Comprovante Base64 salvo automaticamente para {telefone}")
        else:
            mensagem_texto = caption.strip() if caption else "[Imagem recebida]"

    elif message_type == "document":
        pdf_text = ""
        pdf_b64 = media_base64 # Prioriza o que veio no webhook
        
        if message_id and not pdf_b64:
            # Se não veio b64 no webhook, tenta baixar/processar
            extracted_text, extracted_b64 = process_pdf(message_id)
            if extracted_text:
                pdf_text = f"\\n[Conteúdo PDF]: {extracted_text[:1200]}..."
            if extracted_b64:
                pdf_b64 = extracted_b64
        elif pdf_b64 and message_id:
            # Se veio b64, ainda tentamos extrair texto se possível (mas sem baixar de novo se passarmos o stream?
            # Por simplicidade, se já temos b64, process_pdf baixaria de novo via API?
            # A função process_pdf usa get_media_base64.
            # Vamos tentar extrair texto só se tivermos pypdf e o bytes
            if PdfReader:
                try:
                    import base64
                    pdf_bytes = base64.b64decode(pdf_b64)
                    f = io.BytesIO(pdf_bytes)
                    reader = PdfReader(f)
                    text_content = [page.extract_text() for page in reader.pages]
                    full_text = "\\n".join(text_content)
                    full_text = re.sub(r'\s+', ' ', full_text).strip()
                    if full_text:
                        pdf_text = f"\\n[Conteúdo PDF]: {full_text[:1200]}..."
                except Exception as e:
                    logger.error(f"Erro extração texto PDF local: {e}")

        # AUTO-SAVE PDF (Comprovante)
        # Se tem texto extraído ou caption contendo palavras-chave
        keywords = ["comprovante", "pix", "pagamento", "recibo", "transferencia", "transferência", "comprovante"]
        content_check = (mensagem_texto or "") + (pdf_text or "") + (media_caption or "")
        is_receipt = any(k in content_check.lower() for k in keywords)
        
        # Salvar se for identificado como recibo OU se estivermos num fluxo muito óbvio (ex: PDF enviado sozinho)
        # Por segurança, salvamos se tivermos o binário. O agente decide se usa ou não, 
        # mas como o finalizar_pedido_tool pega o ÚLTIMO comprovante salvo, é bom garantir.
        if pdf_b64:
            from tools.redis_tools import set_comprovante
            mime = media_mimetype or "application/pdf"
            # O painel/backend precisa saber lidar com data URI de PDF
            data_uri = f"data:{mime};base64,{pdf_b64}"
            set_comprovante(telefone, data_uri)
            logger.info(f"🧾 PDF Comprovante salvo automaticamente para {telefone} (Size: {len(pdf_b64)})")
            
            # Avisar no texto que foi salvo
            mensagem_texto = f"📄 Documento PDF Recebido e Salvo como Comprovante. {media_caption or ''} {pdf_text}"
        else:
            if pdf_text:
                mensagem_texto = f"📄 Comprovante/PDF Recebido (Texto extraído). {pdf_text}"
            else:
                mensagem_texto = "[PDF recebido, não foi possível extrair texto ou salvar arquivo]"

    # Adicionar contexto da mensagem citada (quoted message) se existir
    if context_prefix and mensagem_texto:
        mensagem_texto = context_prefix + mensagem_texto
    elif context_prefix:
        mensagem_texto = context_prefix.strip()

    return {
        "telefone": telefone,
        "mensagem_texto": mensagem_texto,
        "message_type": message_type,
        "message_id": message_id,
        "from_me": from_me,
        "media_url": media_url,
        "media_base64": media_base64,
        "media_mimetype": media_mimetype,
        "quoted_text": quoted_text,  # Mensagem citada original (se houver)
    }

def send_whatsapp_message(telefone: str, mensagem: str) -> bool:
    """Envia mensagem usando a nova classe WhatsAppAPI."""
    
    # Configuração de split de mensagens
    # Preferir UMA única mensagem para evitar confusão no WhatsApp.
    # Só dividir se estiver absurdamente grande.
    max_len = 2000
    msgs = []
    
    if len(mensagem) > max_len:
        # Divide por parágrafos duplos primeiro
        paragrafos = mensagem.split('\\n\\n')
        curr = ""
        
        for p in paragrafos:
            # Se o parágrafo sozinho é muito grande, divide por quebras simples
            if len(p) > max_len:
                if curr:
                    msgs.append(curr.strip())
                    curr = ""
                # Divide parágrafo grande por linhas
                linhas = p.split('\\n')
                for linha in linhas:
                    if len(curr) + len(linha) + 1 <= max_len:
                        curr += linha + "\\n"
                    else:
                        if curr: msgs.append(curr.strip())
                        curr = linha + "\\n"
            elif len(curr) + len(p) + 2 <= max_len:
                curr += p + "\\n\\n"
            else:
                if curr: msgs.append(curr.strip())
                curr = p + "\\n\\n"
        
        if curr: msgs.append(curr.strip())
    else:
        msgs = [mensagem]
    
    try:
        for i, msg in enumerate(msgs):
            # Usa a nova API
            whatsapp.send_text(telefone, msg)
            
            # Delay entre mensagens para parecer mais natural (exceto última)
            if i < len(msgs) - 1:
                time.sleep(random.uniform(0.8, 1.5))
                
        return True
    except Exception as e:
        logger.error(f"Erro envio: {e}")
        return False

# --- Presença & Buffer ---
presence_sessions = {}
buffer_sessions = {}

def send_presence(num, type_):
    """Envia status: 'composing' (digitando) ou 'paused' (para de digitar)."""
    # A API aceita diretamente: composing, recording, paused, available, unavailable
    whatsapp.send_presence(num, type_)

def process_async(tel, msg, mid=None):
    """
    Processa mensagem do Buffer.
    Fluxo Humano:
    1. Espera (simula leitura).
    2. Marca como LIDO (Azul).
    3. Digita (composing).
    4. Processa (IA).
    5. Para de digitar (paused).
    6. Envia.
    """
    try:
        num = re.sub(r"\\D", "", tel)
        
        # 1. Simular "Lendo" (Delay Humano)
        tempo_leitura = random.uniform(2.0, 4.0) 
        time.sleep(tempo_leitura)

        # 2. Marcar como LIDO (Azul) AGORA
        # Usa o telefone (chat_id) E o message_id para marcar como lido
        logger.info(f"👀 Marcando chat {tel} como lido... (mid={mid})")
        whatsapp.mark_as_read(tel, message_id=mid)
        time.sleep(0.8) # Delay tático: Garante que o usuário veja o AZUL antes de ver o "Digitando..."

        # 3. Começar a "Digitar"
        send_presence(num, "composing")
        
        # 4. Processamento IA com timeout explícito (fallback síncrono)
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(run_agent, tel, msg)
                res = fut.result(timeout=75)
            txt = res.get("output", "Erro ao processar.")
        except FutureTimeoutError:
            logger.error(f"⏱️ Timeout de inferência (fallback sync) para {tel} (>75s)")
            txt = (
                "Desculpe, demorei mais que o normal para processar seu pedido. "
                "Pode repetir a última parte do pedido em uma mensagem curta para eu continuar?"
            )
        
        # 5. Parar "Digitar"
        send_presence(num, "paused")
        time.sleep(0.5) # Pausa dramática antes de chegar

        # 6. Enviar Mensagem (Inteligente: Texto ou Imagem)
        # Regex para encontrar todas as URLs de imagem (jpg, png, jpeg, webp)
        # OTIMIZADO: Evita pontuação final (.,;!) e captura múltiplos
        regex = r'(https?://[^\s]+\.(?:jpg|jpeg|png|webp))'
        urls_encontradas = re.findall(regex, txt, re.IGNORECASE)
        
        if urls_encontradas:
            # Texto limpo: remove todos os links para não ficar redundante no WhatsApp
            texto_limpo = txt
            for url in urls_encontradas:
                # Substitui links seguidos opcionalmente por quebras de linha/espaços
                texto_limpo = re.sub(re.escape(url) + r'[\\s\\n]*', '', texto_limpo).strip()
            
            logger.info(f"📸 Detectadas {len(urls_encontradas)} URLs de imagem. Texto limpo: {texto_limpo[:50]}...")
            
            # 1. Enviar primeiro o TEXTO como mensagem separada (se houver texto)
            if texto_limpo:
                whatsapp.send_text(tel, texto_limpo)
                # Pequeno delay térmico antes das fotos
                time.sleep(1.0)
            
            # 2. Enviar cada imagem
            for i, image_url in enumerate(urls_encontradas):
                logger.info(f"📸 Processando imagem [{i+1}/{len(urls_encontradas)}]: {image_url}")
                logger.info(f"⬇️ Baixando imagem para enviar como arquivo...")
                
                try:
                    # Baixar imagem para memória
                    import base64
                    img_resp = requests.get(image_url, timeout=15)
                    img_resp.raise_for_status()
                    
                    # Converter para Base64
                    img_b64 = base64.b64encode(img_resp.content).decode('utf-8')
                    mime = img_resp.headers.get("Content-Type", "image/jpeg")
                    
                    # Enviar como mídia (sem caption agora)
                    whatsapp.send_media(tel, caption="", base64_data=img_b64, mimetype=mime)
                    
                    # Pequeno delay entre imagens
                    if i < len(urls_encontradas) - 1:
                        time.sleep(1.2)
                        
                except Exception as e:
                    logger.error(f"❌ Erro ao baixar/enviar imagem {image_url}: {e}")
                    # Fallback: Tentar enviar via URL
                    whatsapp.send_media(tel, media_url=image_url, caption="")
        else:
            send_whatsapp_message(tel, txt)

    except Exception as e:
        logger.error(f"Erro async: {e}")
    finally:
        # Garante limpeza
        send_presence(tel, "paused")
        presence_sessions.pop(re.sub(r"\\D", "", tel), None)

def buffer_loop(tel):
    """
    Loop do Buffer (3 ciclos de 5s = 15 segundos)
    Total espera máxima: ~15 segundos
    
    IMPORTANTE: Após processar, verifica se chegaram novas mensagens durante
    a execução do agente e as processa também (evita mensagens "perdidas").
    """
    try:
        n = re.sub(r"\\D","",tel)
        first_window = True
        
        while True:  # Loop principal para pegar mensagens que chegam durante processamento
            prev = get_buffer_length(n)
            
            # Se não tem mensagens, sair
            if prev == 0:
                break
                
            # Janela deslizante:
            # - Primeira espera: 15s
            # - Se chegarem novas mensagens, próximas esperas: 10s de silêncio
            quiet_window = 15 if first_window else 10
            idle_elapsed = 0
            while idle_elapsed < quiet_window:
                time.sleep(1)
                curr = get_buffer_length(n)
                if curr > prev:
                    prev = curr
                    idle_elapsed = 0
                    quiet_window = 10
                else:
                    idle_elapsed += 1
            
            # Consumir e processar mensagens
            # AGORA RETORNA TEXTOS E LAST_MID
            msgs, last_mid = pop_all_messages(n)
            
            # Usa ' | ' como separador para o agente entender que são itens/pedidos separados
            final = " | ".join([m for m in msgs if m.strip()])
            
            if not final:
                break
                
            # Obter contexto de sessão
            order_ctx = get_order_context(n, final)
            if order_ctx:
                final = f"{order_ctx}\\n\\n{final}"

            # Janela curta para juntar mensagens que chegaram logo após o pop
            # e evitar duas respostas de pedido em sequência.
            time.sleep(2)
            late_msgs, late_mids = pop_all_messages(n)
            late_clean = [m for m in (late_msgs or []) if isinstance(m, str) and m.strip()]
            if late_clean:
                final = f"{final} | {' | '.join(late_clean)}"
                if isinstance(last_mid, list):
                    last_mid = list(last_mid) + list(late_mids or [])
                else:
                    last_mid = list(late_mids or [])
                logger.info(f"📦 Buffer merge tardio (sync): {len(late_clean)} msg(s) anexada(s) para {n}")
            
            # Processar (enquanto isso, novas mensagens podem chegar)
            # Passa o last_mid para marcar como lido
            process_async(n, final, mid=last_mid)
            first_window = False
            
            # Após processar, o loop vai verificar se tem novas mensagens
            # Se tiver, processa novamente. Se não, sai.
            
    except Exception as e:
        logger.error(f"Erro no buffer_loop: {e}")
    finally: 
        buffer_sessions.pop(re.sub(r"\\D","",tel), None)

# --- ARQ Pool Lifecycle ---
@app.on_event("startup")
async def startup_event():
    """Inicializa pool ARQ no startup"""
    global arq_pool
    logger.info("🚀 Inicializando ARQ Pool...")
    if getattr(settings, "redis_url_override", None):
        u = urlparse(settings.redis_url)
        arq_pool = await create_pool(
            RedisSettings(
                host=u.hostname or settings.redis_host,
                port=u.port or settings.redis_port,
                password=u.password or settings.redis_password,
                database=int((u.path or "/0").lstrip("/") or 0),
            )
        )
        logger.info("✅ ARQ Pool inicializado com sucesso")
        
        # Iniciar Scheduler de Sincronização de Catálogo (Postgres + Typesense)
        if not scheduler.running:
            scheduler.add_job(_sync_search_indexes, 'interval', hours=1, id='sync_products_job')
            scheduler.start()
            # Rodar uma vez logo no início (em thread separada para não bloquear startup)
            threading.Thread(target=_sync_search_indexes, daemon=True).start()
            logger.info("⏰ Scheduler iniciado: sincronização de catálogo agendada para cada 1 hora.")
        
        return
    arq_pool = await create_pool(
        RedisSettings(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            database=settings.redis_db,
        )
    )
    logger.info("✅ ARQ Pool inicializado com sucesso")

    # Iniciar Scheduler de Sincronização de Catálogo (Postgres + Typesense)
    if not scheduler.running:
        scheduler.add_job(_sync_search_indexes, 'interval', hours=1, id='sync_products_job')
        scheduler.start()
        # Rodar uma vez logo no início (em thread separada para não bloquear startup)
        threading.Thread(target=_sync_search_indexes, daemon=True).start()
        logger.info("⏰ Scheduler iniciado: sincronização de catálogo agendada para cada 1 hora.")

@app.on_event("shutdown")
async def shutdown_event():
    """Fecha pool ARQ no shutdown"""
    global arq_pool
    if arq_pool:
        logger.info("🔄 Fechando ARQ Pool...")
        await arq_pool.close()
        logger.info("✅ ARQ Pool fechado")
    
    if scheduler.running:
        logger.info("🔄 Fechando Scheduler...")
        scheduler.shutdown()
        logger.info("✅ Scheduler fechado")

# --- ARQ Enqueue Helpers ---
async def _enqueue_process_job(telefone: str, mensagem: str, message_id: str = None):
    """
    Enfileira job de processamento de mensagem no ARQ.
    
    Args:
        telefone: Número do cliente
        mensagem: Texto da mensagem
        message_id: ID da mensagem (opcional)
    """
    global arq_pool
    if not arq_pool:
        logger.error("❌ ARQ Pool não inicializado! Usando fallback síncrono.")
        # Fallback em thread para não bloquear o event loop do FastAPI
        await asyncio.to_thread(process_async, telefone, mensagem, message_id)
        return
    
    try:
        job = await arq_pool.enqueue_job(
            "process_message",  # Nome da função no worker.py
            telefone,
            mensagem,
            message_id,
        )
        logger.info(f"🎉 Job enfileirado: {job.job_id} | Cliente: {telefone}")
    except Exception as e:
        logger.error(f"❌ Erro ao enfileirar job: {e}")
        # Fallback em thread para não perder mensagem sem bloquear o loop
        await asyncio.to_thread(process_async, telefone, mensagem, message_id)

async def _enqueue_buffer_job(telefone: str):
    """
    Aguarda buffer acumular mensagens e depois enfileira job ARQ.
    Equivalente ao antigo buffer_loop, mas enfileira job em vez de processar diretamente.
    
    Args:
        telefone: Número do cliente (apenas números)
    """
    try:
        n = re.sub(r"\\D","",telefone)
        refresh_buffer_session_lock(n)
        first_window = True
        
        while True:
            refresh_buffer_session_lock(n)
            prev = get_buffer_length(n)
            if prev == 0:
                break
            
            # Janela deslizante:
            # - Primeira espera: 15s
            # - Se chegarem novas mensagens, próximas esperas: 10s de silêncio
            quiet_window = 15 if first_window else 10
            idle_elapsed = 0
            while idle_elapsed < quiet_window:
                await asyncio.sleep(1)
                refresh_buffer_session_lock(n)
                curr = get_buffer_length(n)
                if curr > prev:
                    prev = curr
                    idle_elapsed = 0
                    quiet_window = 10
                else:
                    idle_elapsed += 1
            
            # Consumir mensagens do buffer
            msgs, mids = pop_all_messages(n)
            final = " | ".join([m for m in msgs if m.strip()])
            
            if not final:
                break
            
            # Obter contexto de sessão
            order_ctx = get_order_context(n, final)
            if order_ctx:
                final = f"{order_ctx}\n\n{final}"

            # Janela curta para capturar mensagens que chegaram exatamente
            # após o pop do buffer e evitar respostas em blocos duplicados.
            await asyncio.sleep(2)
            late_msgs, late_mids = pop_all_messages(n)
            late_clean = [m for m in (late_msgs or []) if isinstance(m, str) and m.strip()]
            if late_clean:
                final = f"{final} | {' | '.join(late_clean)}"
                mids = list(mids or []) + list(late_mids or [])
                logger.info(
                    f"📦 Buffer merge tardio: {len(late_clean)} msg(s) anexada(s) para {n} antes do enfileiramento"
                )
            
            # MUDANÇA: Enfileirar job com LISTA de IDs
            await _enqueue_process_job(n, final, mids)
            first_window = False
            
    except Exception as e:
        logger.error(f"Erro no buffer_loop async: {e}")
    finally:
        # MUDANÇA: Limpar trava no Redis em vez do dicionário local
        release_buffer_session_lock(telefone)

# --- Endpoints ---
@app.get("/")
async def root(): return {"status":"online", "ver":"1.7.0", "queue":"enabled"}

@app.get("/health")
async def health(): return {"status":"healthy", "ts":datetime.now().isoformat()}

@app.get("/graph")
async def graph():
    """
    Retorna uma página HTML interativa com o diagrama do fluxo do agente.
    Acesse: https://seu-app.easypanel.io/graph
    """
    from fastapi.responses import HTMLResponse
    
    html_content = """
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>🤖 Fluxo do Agente de Vendas</title>
        <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
        <style>
            * { margin: 0; padding: 0; box-sizing: border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
                min-height: 100vh;
                color: #e4e4e4;
            }
            .container {
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
            }
            header {
                text-align: center;
                padding: 30px 0;
                border-bottom: 1px solid rgba(255,255,255,0.1);
                margin-bottom: 30px;
            }
            h1 {
                font-size: 2.5rem;
                background: linear-gradient(90deg, #00d9ff, #00ff88);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 10px;
            }
            .subtitle { color: #8892b0; font-size: 1.1rem; }
            .diagram-container {
                background: rgba(255,255,255,0.05);
                border-radius: 16px;
                padding: 30px;
                backdrop-filter: blur(10px);
                border: 1px solid rgba(255,255,255,0.1);
                margin-bottom: 30px;
            }
            .mermaid {
                display: flex;
                justify-content: center;
            }
            .legend {
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-top: 30px;
            }
            .legend-item {
                background: rgba(255,255,255,0.05);
                border-radius: 12px;
                padding: 20px;
                border-left: 4px solid;
            }
            .legend-item.orchestrator { border-color: #f39c12; }
            .legend-item.vendedor { border-color: #3498db; }
            .legend-item.caixa { border-color: #27ae60; }
            .legend-item.analista { border-color: #9b59b6; }
            .legend-item h3 { margin-bottom: 10px; display: flex; align-items: center; gap: 10px; }
            .legend-item ul { padding-left: 20px; color: #8892b0; }
            .legend-item li { margin: 5px 0; }
            footer {
                text-align: center;
                padding: 20px;
                color: #8892b0;
                font-size: 0.9rem;
            }
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <h1>🤖 Agente Multi-Agente</h1>
                <p class="subtitle">Arquitetura de Fluxo do Sistema de Atendimento</p>
            </header>

            <div class="diagram-container">
                <div class="mermaid">
graph TD
    START([🚀 START]) --> ORCH[🧠 Orquestrador]
    
    ORCH -->|"intent = vendas"| VEND[👩‍💼 Vendedor]
    ORCH -->|"intent = checkout"| CAIXA[💰 Caixa]
    
    VEND -->|"busca_analista"| ANAL[🔍 Analista]
    ANAL -->|"retorna produtos + preços"| VEND
    
    VEND --> END1([🏁 END])
    
    CAIXA -->|"Finaliza pedido"| END2([🏁 END])
    CAIXA -->|"Cliente quer alterar"| ORCH
    
    style START fill:#2ecc71,stroke:#27ae60,color:#fff
    style END1 fill:#e74c3c,stroke:#c0392b,color:#fff
    style END2 fill:#e74c3c,stroke:#c0392b,color:#fff
    style ORCH fill:#f39c12,stroke:#e67e22,color:#fff
    style VEND fill:#3498db,stroke:#2980b9,color:#fff
    style CAIXA fill:#27ae60,stroke:#1e8449,color:#fff
    style ANAL fill:#9b59b6,stroke:#8e44ad,color:#fff
                </div>
            </div>

            <div class="legend">
                <div class="legend-item orchestrator">
                    <h3>🧠 Orquestrador</h3>
                    <p>Classifica a intenção do cliente:</p>
                    <ul>
                        <li><strong>vendas</strong> → Pedir produtos, preços, estoque</li>
                        <li><strong>checkout</strong> → Finalizar, pagar, endereço</li>
                    </ul>
                </div>
                
                <div class="legend-item vendedor">
                    <h3>👩‍💼 Vendedor (Agente Único)</h3>
                    <p>Ferramentas disponíveis:</p>
                    <ul>
                        <li>busca_produto_tool (DB)</li>
                        <li>add_item_tool</li>
                        <li>ver_pedido_tool</li>
                        <li>remove_item_tool</li>
                        <li>calcular_total_tool</li>
                        <li>salvar_endereco_tool</li>
                        <li>finalizar_pedido_tool</li>
                    </ul>
                </div>
            </div>

            <footer>
                <p>Sistema de Atendimento Multi-Agente v5.0 | LangGraph + Gemini</p>
            </footer>
        </div>

        <script>
            mermaid.initialize({
                theme: 'dark',
                themeVariables: {
                    primaryColor: '#3498db',
                    primaryTextColor: '#fff',
                    primaryBorderColor: '#2980b9',
                    lineColor: '#8892b0',
                    secondaryColor: '#27ae60',
                    tertiaryColor: '#f39c12'
                }
            });
        </script>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/")
@app.post("/webhook/whatsapp")
async def webhook(req: Request, tasks: BackgroundTasks):
    try:
        pl = await req.json()
        
        # Log sanitizado para evitar vazar base64/tokens/PII sensível.
        safe_payload = _sanitize_for_log(pl)
        logger.info(
            f"📥 Webhook recebido: event={pl.get('event', '?')} | payload={json.dumps(safe_payload, ensure_ascii=False)[:800]}"
        )
        
        data = _extract_incoming(pl)
        tel, txt, from_me = data["telefone"], data["mensagem_texto"], data["from_me"]
        msg_type = data.get("message_type") or data.get("message_any", {}).get("type", "text")
        msg_id = data.get("message_id")  # ID da mensagem para mark_as_read
        media_url = data.get("media_url")

        # =============================================
        # HUMAN TAKEOVER: VERIFICAÇÃO PRIORITÁRIA
        # Se from_me=True, não faz NADA além de pausar.
        # Sem download de mídia, sem análise, sem buffer.
        # =============================================
        if from_me:
            tel_clean = re.sub(r"\\D", "", tel or "")
            agent_number = (settings.whatsapp_agent_number or "").strip()
            agent_clean = re.sub(r"\\D", "", agent_number) if agent_number else ""
            
            if tel_clean and (not agent_clean or tel_clean != agent_clean):
                ttl = settings.human_takeover_ttl  # Default: 2400s (40min)
                set_agent_cooldown(tel_clean, ttl)
                clear_cart(tel_clean)
                logger.info(f"🙋 Human Takeover ativado para {tel_clean} - IA pausa por {ttl//60}min - Carrinho limpo")
            else:
                logger.warning(
                    f"⚠️ Human Takeover ignorado: telefone inválido/igual ao agente | tel={tel_clean} agent={agent_clean}"
                )
            
            # Registrar mensagem no histórico como mensagem da IA
            log_txt = txt or f"[{msg_type} do atendente]"
            logger.info(f"In: {tel} | {msg_type} | from_me | {log_txt[:50]}")
            try: get_session_history(tel).add_ai_message(log_txt)
            except: pass
            return JSONResponse(content={"status":"ignored_self"})

        # === Daqui para baixo, é SEMPRE mensagem do CLIENTE (from_me=False) ===
        
        # Fallback: Se o tipo vier como 'text' mas o corpo estiver vazio, pode ser uma mídia sem legenda
        if msg_type == "text" and not txt and msg_id:
            logger.info(f"🕵️ Detectada possível mídia sem tipo em {msg_id}. Tentando conversão...")
            data["message_type"] = "image"
            msg_type = "image"

        # Só bloqueamos se não houver telefone, OU se for texto puro sem conteúdo e sem mídia/ID
        if not tel or (not txt and msg_type == "text" and not media_url): 
            logger.warning(f"⚠️ IGNORED | Tel: {tel} | Txt: {txt} | Type: {msg_type} | ID: {msg_id}")
            return JSONResponse(content={"status":"ignored"})
            
        # =============================================
        # DEDUPLICAÇÃO DE WEBHOOK (REDIS SET NX)
        # Previne processar a mesma mensagem duas vezes
        # =============================================
        if not check_and_mark_message_processed(msg_id):
             return JSONResponse(content={"status":"duplicate"})
        
        # Se for mídia sem texto, cria um placeholder para não perder no buffer
        if msg_type in ["image", "audio", "document"] and not txt and msg_id:
            txt = f"[MEDIA:{msg_type.upper()}:{msg_id}]"
            logger.info(f"📎 Placeholder de mídia criado: {txt}")
        
        logger.info(f"In: {tel} | {msg_type} | {txt[:50] if txt else '[Mídia]'}")

        num = re.sub(r"\\D","",tel)
        
        # NOTA: 'send_presence' imediato removido para evitar comportamento robótico.
        # O cliente verá 'digitando' apenas após o buffer, no process_async.

        active, _ = is_agent_in_cooldown(num)
        if active:
            # push_message_to_buffer(num, txt, message_id=msg_id) -> REMOVED to ignore messages during pause
            # SALVAR MENSAGEM DO CLIENTE NO HISTÓRICO mesmo durante cooldown
            try:
                from langchain_core.messages import HumanMessage
                get_session_history(tel).add_message(HumanMessage(content=txt))
                logger.info(f"📝 Mensagem do cliente salva no histórico (cooldown ativo)")
            except Exception as e:
                logger.warning(f"Erro ao salvar mensagem durante cooldown: {e}")
            return JSONResponse(content={"status":"cooldown"})

        try:
            if not presence_sessions.get(num):
                presence_sessions[num] = True
        except: pass

        if push_message_to_buffer(num, txt, message_id=msg_id):
            # MUDANÇA: Usar trava no Redis (distributed) em vez de dict local
            if acquire_buffer_session_lock(num):
                # Se conseguiu o lock, inicia o task de buffer
                asyncio.create_task(_enqueue_buffer_job(num))
        else:
            # Mensagem única (sem buffer) - enfileira diretamente
            await _enqueue_process_job(tel, txt, msg_id)

        return JSONResponse(content={"status":"buffering"})
    except Exception as e:
        logger.error(f"Erro webhook: {e}")
        return JSONResponse(status_code=500, content={"detail": str(e)})

@app.post("/message")
async def direct_msg(msg: WhatsAppMessage):
    try:
        res = await asyncio.to_thread(run_agent, msg.telefone, msg.mensagem)
        return AgentResponse(success=True, response=res["output"], telefone=msg.telefone, timestamp="")
    except Exception as e:
        return AgentResponse(success=False, response="", telefone="", error=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host=settings.server_host, port=settings.server_port, log_level=settings.log_level.lower())
