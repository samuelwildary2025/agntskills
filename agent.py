"""
Agente de IA para Atendimento de Supermercado usando LangGraph
Arquitetura: Vendedor (Agente Ãšnico + Skills)

VersÃ£o 7.0 - Skills Architecture
"""

from typing import Dict, Any, TypedDict, Annotated, List, Literal
import re
import operator
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import create_react_agent
from langgraph.checkpoint.memory import MemorySaver
from pathlib import Path
import json

from config.settings import settings
from config.logger import setup_logger
from tools.http_tools import estoque, pedidos, alterar, consultar_encarte

from tools.time_tool import get_current_time, search_message_history
from tools.redis_tools import (
    mark_order_sent, 
    add_item_to_cart, 
    get_cart_items, 
    remove_item_from_cart, 
    clear_cart,
    set_comprovante,
    get_comprovante,
    clear_comprovante,
    get_saved_address,
    save_address,
    get_order_session,
    normalize_phone,
    acquire_agent_lock,
    release_agent_lock,
    clear_order_session,
    start_order_session,
    clear_suggestions,
    resolve_pending_confirmation,
    clear_pending_confirmations,
    get_order_context,
)
from memory.hybrid_memory import HybridChatMessageHistory

logger = setup_logger(__name__)

# ============================================
# Estado Compartilhado do Grafo
# ============================================

def add_messages(left: list, right: list) -> list:
    """FunÃ§Ã£o para combinar listas de mensagens."""
    return left + right

class AgentState(TypedDict):
    """Estado compartilhado entre os agentes."""
    messages: Annotated[list, add_messages]
    phone: str
    final_response: str  # Resposta final para o cliente

# ============================================
# DefiniÃ§Ã£o das Ferramentas (Separadas por Agente)
# ============================================

# --- FERRAMENTAS DO VENDEDOR ---

@tool
def busca_produto_tool(telefone: str, query: str) -> str:
    """
    Busca produtos e preÃ§os. A InteligÃªncia de Busca Ã© SUA!
    - Se o cliente pediu especificamente por nome (ex: "massa de tapioca"), passe "tapioca".
    - Se o cliente usou gÃ­rias ou sinÃ´nimos complexos (ex: "veneno pra rato", "muriÃ§oca"), VOcÃŠ deve traduzir para a categoria correta (ex: "inseticida", "rato").
    - NÃ£o passe textos longos. Extraia a essÃªncia do produto (ex: "tem aquele sabÃ£o em pÃ³ brilhante?" -> passe apenas "sabao em po brilhante").

    Retorna um JSON list com os dados dos produtos avaliados semanticamente.
    """
    from tools.skill_executor import buscar_e_validar
    return buscar_e_validar(telefone, query)

@tool
def add_item_tool(telefone: str, produto: str, quantidade: float = 1.0, observacao: str = "", preco: float = 0.0, unidades: int = 0) -> str:
    """
    Adicionar um item ao pedido do cliente.
    USAR IMEDIATAMENTE quando o cliente demonstrar intenÃ§Ã£o de compra.
    
    Para produtos vendidos por KG (frutas, legumes, carnes):
    - quantidade: peso em kg (ex: 0.45 para 450g)
    - unidades: nÃºmero de unidades pedidas (ex: 3 para 3 tomates)
    - preco: preÃ§o por kg
    
    Para produtos unitÃ¡rios:
    - quantidade: nÃºmero de itens
    - unidades: deixar 0
    - preco: preÃ§o por unidade
    """
    
    # IMPORTAR AQUI para evitar ciclo de importaÃ§Ã£o
    from tools.redis_tools import get_suggestions
    import difflib

    prod_lower = produto.lower().strip()
    
    # 0. TENTATIVA DE RECUPERAÃ‡ÃƒO DE PREÃ‡O (Auto-Healing)
    # Se o agente esqueceu o preÃ§o (0.0), tentamos achar nas sugestÃµes recentes
    melhor_match = None
    if preco <= 0.01:
        sugestoes = get_suggestions(telefone)
        if sugestoes:
            melhor_score = 0.0
            for sug in sugestoes:
                if not isinstance(sug, dict):
                    continue
                nome_sug = str(sug.get("nome", "")).lower().strip()
                if not nome_sug:
                    continue

                ratio = difflib.SequenceMatcher(None, prod_lower, nome_sug).ratio()
                if prod_lower in nome_sug or nome_sug in prod_lower:
                    ratio += 0.18
                if bool(sug.get("match_ok")):
                    ratio += 0.08

                if ratio > melhor_score:
                    melhor_score = ratio
                    melhor_match = sug

            # Limiar mais conservador para evitar casar produto errado por ruÃ­do.
            if melhor_match and melhor_score >= 0.72:
                preco_recuperado = float(melhor_match.get("preco", 0.0) or 0.0)
                if preco_recuperado > 0:
                    preco = preco_recuperado
                    logger.info(
                        f"âœ¨ [AUTO-HEAL] PreÃ§o recuperado para '{produto}': R$ {preco:.2f} "
                        f"(score={melhor_score:.2f}, base='{melhor_match.get('nome')}')"
                    )
    
    # BLOQUEIO: NÃ£o permitir adicionar item sem preÃ§o vÃ¡lido
    if preco <= 0.01:
        logger.warning(f"ðŸš« [ADD_ITEM] Bloqueado: '{produto}' sem preÃ§o vÃ¡lido (R$ {preco:.2f}). Use busca_produto_tool primeiro.")
        return f"âŒ NÃ£o consegui encontrar o preÃ§o de '{produto}'. Use busca_produto_tool para verificar o preÃ§o antes de adicionar."
    
    # Validar match_ok nas sugestÃµes â€” se o produto nÃ£o passou na validaÃ§Ã£o, avisar
    if melhor_match is not None and not bool(melhor_match.get("match_ok", True)):
        logger.warning(f"âš ï¸ [ADD_ITEM] Produto '{produto}' tem match_ok=false. Pedindo confirmaÃ§Ã£o.")
        return f"âš ï¸ '{produto}' nÃ£o parece ser uma correspondÃªncia exata. Confirme com o cliente qual opÃ§Ã£o ele deseja antes de adicionar."
    
    if unidades > 0 and quantidade <= 0.01:
         logger.warning(f"âš ï¸ [ADD_ITEM] Item '{produto}' com unidades={unidades} mas peso zerado. O LLM deveria ter calculado.")
    
    # Construir JSON do item para add_item_to_cart
    import json
    item_data = {
        "produto": produto,
        "quantidade": quantidade,
        "observacao": observacao,
        "preco": preco,
        "unidades": unidades
    }
    item_json = json.dumps(item_data, ensure_ascii=False)
    
    success = add_item_to_cart(telefone, item_json)
    if success:
         try:
             resolve_pending_confirmation(telefone, produto)
         except Exception:
             pass
         # Calcular valor estimado TOTAL (jÃ¡ que o peso deve vir correto do LLM)
         valor_estimado = quantidade * preco
         if unidades > 0:
             return f"âœ… Adicionado: {unidades}x {produto} ({quantidade:.3f}kg)"
         else:
             return f"âœ… Adicionado: {quantidade} {produto}"
    return "âŒ Erro ao adicionar item."

@tool
def reset_pedido_tool(telefone: str) -> str:
    """
    Zera o pedido do cliente (carrinho, sessÃ£o, comprovante e sugestÃµes) e inicia uma nova sessÃ£o.
    """
    telefone = normalize_phone(telefone)
    clear_cart(telefone)
    clear_order_session(telefone)
    clear_comprovante(telefone)
    clear_suggestions(telefone)
    clear_pending_confirmations(telefone)
    start_order_session(telefone)
    return "âœ… Pedido zerado com sucesso! Pode me enviar a nova lista de itens."

@tool
def ver_pedido_tool(telefone: str) -> str:
    """Ver os itens atuais no pedido do cliente."""
    items = get_cart_items(telefone)
    if not items:
        return "ðŸ“ Sua lista estÃ¡ vazia."

    lines = ["ðŸ“ **Resumo do Pedido:**"]
    for i, item in enumerate(items, 1):
        nome = item.get("produto", "Item")
        qtd = item.get("quantidade", 1)
        preco = item.get("preco", 0)

        qtd_display = int(qtd) if qtd == int(qtd) else qtd
        lines.append(f"{i}. {qtd_display}x {nome} - R$ {preco:.2f}/un")

    return "\n".join(lines)

@tool
def remove_item_tool(telefone: str, item_index: int, quantidade: float = 0) -> str:
    """
    Remover um item do carrinho pelo nÃºmero (Ã­ndice 1-based).
    
    Se quantidade = 0 ou nÃ£o informada: Remove o item INTEIRO.
    Se quantidade > 0: Remove APENAS essa quantidade (ex: tirar 1 unidade de 3).
    
    Exemplos:
    - Cliente: "tira o item 2" â†’ remove_item_tool(tel, 2, 0) â†’ Remove item 2 inteiro
    - Cliente: "tira 1 nescau" â†’ remove_item_tool(tel, 2, 1) â†’ Remove 1 unidade do item 2
    """
    from tools.redis_tools import remove_item_from_cart, update_item_quantity
    
    # Converter para Ã­ndice 0-based
    idx_zero_based = int(item_index) - 1
    
    if quantidade > 0:
        # RemoÃ§Ã£o PARCIAL - reduz quantidade
        result = update_item_quantity(telefone, idx_zero_based, quantidade)
        
        if result["success"]:
            if result["removed_completely"]:
                return f"âœ… {result['item_name']} removido completamente do pedido."
            else:
                return f"âœ… Removido {quantidade} de {result['item_name']}. Agora tem {result['new_quantity']} no pedido."
        return f"âŒ Erro: Item {item_index} nÃ£o encontrado."
    else:
        # RemoÃ§Ã£o COMPLETA - comportamento original
        success = remove_item_from_cart(telefone, idx_zero_based)
        if success:
            return f"âœ… Item {item_index} removido do pedido."
        return f"âŒ Erro: Item {item_index} nÃ£o encontrado."


# --- FERRAMENTAS DO CAIXA ---

@tool
def calcular_total_tool(telefone: str, taxa_entrega: float = 0.0) -> str:
    """
    Calcula o valor exato do pedido somando itens do carrinho + taxa de entrega.
    Use SEMPRE antes de informar o total final ao cliente.
    
    Args:
    - telefone: Telefone do cliente
    - taxa_entrega: Valor da taxa de entrega a ser somada (se houver)
    """
    items = get_cart_items(telefone)
    if not items:
        return "âŒ Pedido vazio. NÃ£o Ã© possÃ­vel calcular total."
    
    subtotal = 0.0
    item_details = []
    
    for i, item in enumerate(items):
        preco = float(item.get("preco", 0.0))
        qtd = float(item.get("quantidade", 1.0))
        nome = item.get("produto", "Item")
        
        valor_item = round(preco * qtd, 2)
        subtotal += valor_item
        item_details.append(f"- {nome}: R$ {valor_item:.2f}")
        
    subtotal = round(subtotal, 2)
    taxa_entrega = round(float(taxa_entrega), 2)
    total_final = round(subtotal + taxa_entrega, 2)
    
    res = (
        f"ðŸ“ **CÃ¡lculo Oficial do Sistema:**\n"
        f"Subtotal: R$ {subtotal:.2f}\n"
        f"Taxa de Entrega: R$ {taxa_entrega:.2f}\n"
        f"----------------\n"
        f"ðŸ’° **TOTAL FINAL: R$ {total_final:.2f}**"
    )
    return res

@tool
def salvar_endereco_tool(telefone: str, endereco: str) -> str:
    """
    Salva o endereÃ§o do cliente para usar depois no fechamento do pedido.
    Use IMEDIATAMENTE quando o cliente informar o endereÃ§o (mesmo no inÃ­cio da conversa).
    """
    if save_address(telefone, endereco):
        return f"âœ… EndereÃ§o salvo: {endereco}"
    return "âŒ Erro ao salvar endereÃ§o."

@tool
def finalizar_pedido_tool(cliente: str, telefone: str, endereco: str, forma_pagamento: str, itens_json: str, observacao: str = "", comprovante: str = "", taxa_entrega: float = 0.0) -> str:
    """
    Finalizar o pedido enviando TODOS os itens confirmados.
    Use quando o cliente confirmar que quer fechar a compra e repasse todos os itens do contexto da conversa.
    
    Args:
    - cliente: Nome do cliente
    - telefone: Telefone (com DDD)
    - endereco: EndereÃ§o de entrega completo
    - forma_pagamento: Pix, CartÃ£o ou Dinheiro
    - itens_json: String em formato JSON com todos os itens, ex: [{"produto": "Arroz", "quantidade": 2.0, "preco": 20.0}]
    - observacao: ObservaÃ§Ãµes extras (troco, etc)
    - comprovante: URL do comprovante PIX (se houver)
    - taxa_entrega: Valor da taxa de entrega em reais (opcional, padrÃ£o 0)
    """
    import json as json_lib

    cents = Decimal("0.01")

    def _to_decimal(value: Any, default: str = "0") -> Decimal:
        try:
            raw = str(value if value is not None else default).strip().replace(",", ".")
            return Decimal(raw)
        except (InvalidOperation, ValueError, TypeError):
            return Decimal(default)

    def _recover_price_from_search(nome_produto: str) -> Decimal:
        from tools.search_router import search_products

        raw = search_products(nome_produto, limit=6, telefone=telefone)
        try:
            rows = json_lib.loads(raw or "[]")
        except Exception:
            rows = []

        if not isinstance(rows, list):
            return Decimal("0")

        # Prioriza match_ok=true e preco positivo.
        candidates = [r for r in rows if isinstance(r, dict)]
        candidates.sort(
            key=lambda r: (
                1 if bool(r.get("match_ok")) else 0,
                float(r.get("match_score", 0.0) or 0.0),
            ),
            reverse=True,
        )

        for row in candidates:
            price = _to_decimal(row.get("preco"), "0")
            if price > 0:
                return price
        return Decimal("0")

    try:
        items = json_lib.loads(itens_json)
    except Exception as e:
        return f"âŒ Erro ao ler os itens do pedido: erro de formato JSON - {e}. Corrija o JSON e tente novamente."

    if not isinstance(items, list) or not items:
        return "âŒ O pedido estÃ¡ vazio! VocÃª deve repassar a lista de produtos confirmados."

    comprovante_salvo = get_comprovante(telefone)
    comprovante_final = comprovante or comprovante_salvo or ""

    total = Decimal("0")
    itens_formatados = []

    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return f"âŒ Item {idx} invÃ¡lido no JSON. Corrija e tente novamente."

        nome_produto = str(item.get("produto", item.get("nome_produto", "Produto"))).strip() or "Produto"
        preco = _to_decimal(item.get("preco"), "0")
        quantidade = _to_decimal(item.get("quantidade", 1), "1")
        unidades = int(_to_decimal(item.get("unidades", 0), "0"))
        obs_item = str(item.get("observacao", "") or "").strip()

        if quantidade <= 0:
            quantidade = Decimal("1")

        if preco <= 0:
            preco_recuperado = _recover_price_from_search(nome_produto)
            if preco_recuperado > 0:
                preco = preco_recuperado
                logger.info(f"âœ¨ [CHECKOUT] PreÃ§o recuperado no SQL para '{nome_produto}': R$ {preco:.2f}")
            else:
                return (
                    f"âŒ NÃ£o consegui validar o preÃ§o de '{nome_produto}' para fechar o pedido. "
                    "Use busca_produto_tool novamente e confirme o item/preÃ§o antes de finalizar."
                )

        valor_linha = (preco * quantidade).quantize(cents, rounding=ROUND_HALF_UP)
        total += valor_linha

        if unidades > 0:
            qtd_api = unidades
            preco_unitario_api = (valor_linha / Decimal(qtd_api)).quantize(cents, rounding=ROUND_HALF_UP)
            obs_peso = f"Peso estimado: {float(quantidade):.3f}kg (~R${float(valor_linha):.2f}). PESAR para confirmar valor."
            obs_final = f"{obs_item}. {obs_peso}".strip(". ") if obs_item else obs_peso
        else:
            if quantidade < 1 or quantidade != quantidade.to_integral_value():
                qtd_api = 1
            else:
                qtd_api = int(quantidade)
            preco_unitario_api = preco.quantize(cents, rounding=ROUND_HALF_UP)
            obs_final = obs_item

        itens_formatados.append(
            {
                "nome_produto": nome_produto,
                "quantidade": qtd_api,
                "preco_unitario": float(preco_unitario_api),
                "observacao": obs_final,
            }
        )

    taxa_entrega_dec = _to_decimal(taxa_entrega, "0").quantize(cents, rounding=ROUND_HALF_UP)
    if taxa_entrega_dec > 0:
        total += taxa_entrega_dec

    payload = {
        "nome_cliente": cliente,
        "telefone": telefone,
        "endereco": endereco or "A combinar",
        "forma": forma_pagamento,
        "observacao": observacao or "",
        "comprovante_pix": comprovante_final or None,
        "taxa_entrega": float(taxa_entrega_dec) if taxa_entrega_dec > 0 else None,
        "itens": itens_formatados,
    }

    json_body = json_lib.dumps(payload, ensure_ascii=False)

    # AUDIT LOG: registrar payload antes de enviar.
    try:
        from datetime import datetime
        import os

        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "telefone": telefone,
            "cliente": cliente,
            "total": float(total),
            "itens_count": len(itens_formatados),
            "payload": payload,
        }
        os.makedirs("logs", exist_ok=True)
        with open("logs/pedidos_audit.jsonl", "a", encoding="utf-8") as f:
            f.write(json_lib.dumps(audit_entry, ensure_ascii=False) + "\n")
        logger.info(f"ðŸ“‹ [AUDIT] Pedido registrado para {telefone} - R$ {float(total):.2f}")
    except Exception as audit_err:
        logger.warning(f"âš ï¸ Falha no audit log: {audit_err}")

    result = pedidos(json_body)

    if "sucesso" in result.lower() or "âœ…" in result:
        # Ao concluir checkout, limpamos estado para evitar vazamento em novo pedido.
        mark_order_sent(telefone, result)
        clear_pending_confirmations(telefone)
        clear_suggestions(telefone)
        clear_cart(telefone)
        clear_order_session(telefone)
        try:
            get_session_history(telefone).clear()
            logger.info(f"ðŸ§¹ Contexto da conversa limpo apÃ³s finalizaÃ§Ã£o: {telefone}")
        except Exception as e:
            logger.warning(f"Falha ao limpar contexto apÃ³s finalizaÃ§Ã£o: {e}")

        return (
            f"{result}\n\n"
            f"ðŸ’° **Valor Total Oficial:** R$ {float(total):.2f}\n"
            "(O agente DEVE usar este valor na resposta)"
        )

    return result

# --- FERRAMENTAS COMPARTILHADAS ---

@tool
def time_tool() -> str:
    """Retorna a data e hora atual."""
    return get_current_time()

@tool
def search_history_tool(telefone: str, keyword: str = None) -> str:
    """Busca mensagens anteriores do cliente com horÃ¡rios."""
    return search_message_history(telefone, keyword)

# ============================================
# Listas de Ferramentas por Agente
# ============================================

VENDEDOR_TOOLS = [
    busca_produto_tool,
    add_item_tool,
    ver_pedido_tool,
    remove_item_tool,
    calcular_total_tool,
    reset_pedido_tool,
    time_tool,
    salvar_endereco_tool,
    finalizar_pedido_tool,
]

# ============================================
# FunÃ§Ãµes de Carregamento de Prompts
# ============================================

def load_prompt(filename: str) -> str:
    """Carrega um prompt do diretÃ³rio prompts/"""
    base_dir = Path(__file__).resolve().parent
    prompt_path = base_dir / "prompts" / filename
    
    logger.info(f"ðŸ“„ Carregando prompt: {prompt_path}")
    
    try:
        text = prompt_path.read_text(encoding="utf-8")
        text = text.replace("{base_url}", settings.supermercado_base_url)
        text = text.replace("{ean_base}", settings.estoque_ean_base_url)
        return text
    except Exception as e:
        logger.error(f"Falha ao carregar prompt {filename}: {e}")
        raise

# ============================================
# ConstruÃ§Ã£o dos LLMs
# ============================================

def _build_llm(temperature: float = 0.1, model_override: str = None):
    """ConstrÃ³i um LLM baseado nas configuraÃ§Ãµes."""
    model = model_override or getattr(settings, "llm_model", "gemini-1.5-flash")
    provider = getattr(settings, "llm_provider", "google")
    
    if provider == "google":
        logger.debug(f"ðŸš€ Usando Google Gemini: {model}")
        primary_llm = ChatGoogleGenerativeAI(
            model=model,
            api_key=settings.google_api_key,
            temperature=temperature,
            timeout=120,  # Timeout de 2 minutos para evitar hang
            max_retries=2,
        )
        
        # Fallback para 2.5-flash em caso de 503 ou 429
        fallback_llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-flash",
            api_key=settings.google_api_key,
            temperature=temperature,
            timeout=120,
            max_retries=2,
        )
        
        logger.debug("ðŸ›¡ï¸ Configurando fallback para gemini-2.5-flash")
        return primary_llm.with_fallbacks([fallback_llm])
    else:
        logger.debug(f"ðŸš€ Usando OpenAI (compatÃ­vel): {model}")
        
        client_kwargs = {}
        if settings.openai_api_base:
            client_kwargs["base_url"] = settings.openai_api_base

        return ChatOpenAI(
            model=model,
            api_key=settings.openai_api_key,
            temperature=temperature,
            **client_kwargs
        )

def _build_fast_llm():
    """ConstrÃ³i um LLM rÃ¡pido e leve para o Orquestrador."""
    # Usa baixa temperatura para manter boa aderÃªncia Ã s regras com leve flexibilidade.
    return _build_llm(temperature=0.1)

# ============================================
# NÃ³s do Grafo (Agentes)
# ============================================


# ============================================
# Helpers
# ============================================

def _extract_response(result: Any) -> str:
    """Extrai a resposta de texto de um resultado do LangGraph/LangChain."""
    def _content_to_str(content):
        """Converte content (str ou list) para string."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Novo formato LangChain v1.x: content Ã© lista de blocos
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict) and "text" in block:
                    parts.append(block["text"])
            return "\n".join(parts)
        return str(content) if content else ""

    if isinstance(result, dict) and "messages" in result:
        msgs = result["messages"]
        if msgs:
            last_msg = msgs[-1]
            if isinstance(last_msg, BaseMessage):
                return _content_to_str(last_msg.content)
            return str(last_msg)
    elif isinstance(result, BaseMessage):
        return _content_to_str(result.content)
    return str(result)


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and "text" in block:
                parts.append(str(block.get("text", "")))
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _is_close_intent(text: str) -> bool:
    t = (text or "").lower()
    patterns = [
        r"\bso isso\b",
        r"\bs[oÃ³] isso\b",
        r"\bpode fechar\b",
        r"\bfechar\b",
        r"\bfinalizar\b",
        r"\bencerrar\b",
        r"\bfinaliza\b",
        r"\bconcluir\b",
    ]
    return any(re.search(p, t) for p in patterns)


def _extract_session_directive(message: str) -> tuple[str, str]:
    msg = (message or "").strip()
    if not msg.startswith("[SESS"):
        return "", msg

    first_line, sep, remainder = msg.partition("\n")
    clean_remainder = remainder.strip() if sep else ""
    return first_line.strip(), clean_remainder


def _session_indicates_new_order(session_directive: str) -> bool:
    low = (session_directive or "").lower()
    return ("novo pedido" in low) or ("nova conversa" in low)


def _is_fresh_order_request(text: str) -> bool:
    low = (text or "").strip().lower()
    if not low:
        return False
    patterns = [
        r"\bnovo pedido\b",
        r"\bpedido novo\b",
        r"\bdo zero\b",
        r"\bzerar pedido\b",
        r"\bzera (o )?pedido\b",
        r"\brecome(c|Ã§)ar\b",
        r"\bcome(c|Ã§)ar de novo\b",
        r"\blimpa(r)? (o )?pedido\b",
        r"\besquece(r)? (o )?pedido\b",
        r"\boutro pedido\b",
    ]
    return any(re.search(p, low) for p in patterns)


def _sanitize_premature_checkout(response: str, phone: str = None) -> str:
    if not response:
        return response
    lines = [ln for ln in (response or "").splitlines() if ln.strip()]
    blocked_terms = [
        "forma de pagamento",
        "pix, cartÃ£o ou dinheiro",
        "pix, cartao ou dinheiro",
        "jÃ¡ temos seu endereÃ§o",
        "ja temos seu endereco",
        "se for finalizar",
        "podemos fechar",
        "endereÃ§o da",
        "endereco da",
    ]
    kept = []
    for ln in lines:
        low = ln.lower()
        if any(term in low for term in blocked_terms):
            continue
        kept.append(ln)
    out = "\n".join(kept).strip()

    # Verificamos se hÃ¡ itens no carrinho (antes desta mensagem) para decidir o follow-up
    cart_has_items = False
    if phone:
        try:
            from tools.redis_tools import get_cart_items
            items = get_cart_items(phone)
            cart_has_items = len(items or []) > 0
        except Exception:
            pass

    has_added_items_now = re.search(r"\-\s*(?:\d+)?.*?\-\s*R\$", out) is not None

    if "deseja mais alguma coisa" not in out.lower():
        if cart_has_items or has_added_items_now:
            if "como posso te ajudar hoje" in out.lower():
                # Remove o "como posso te ajudar" inapropriado se acabamos de adicionar itens
                out = re.sub(r"(?i)como posso (te|de) ajudar hoje\??", "", out).strip()
            out = (out + "\n\nDeseja mais alguma coisa ou podemos finalizar?").strip()
        elif "?" not in out:
            # Se o carrinho estiver vazio, nÃ£o adicionamos nada agora, e o agente nÃ£o fez uma pergunta
            out = (out + "\n\nComo posso te ajudar hoje?").strip()
            
    return out


def _parse_brl_amount(value: str) -> Decimal:
    raw = str(value or "").strip().replace(".", "").replace(",", ".")
    try:
        return Decimal(raw)
    except Exception:
        return Decimal("0")


def _reconcile_estimated_total(response: str, phone: str = None) -> str:
    """
    Reconciliar o "Total estimado" para reduzir divergÃªncia de soma manual.
    Prioridade:
    1) Soma do carrinho atual (se disponÃ­vel)
    2) Soma das linhas exibidas na resposta
    """
    if not response:
        return response

    total_line_pattern = re.compile(
        r"(?im)^\s*(Total estimado(?: parcial)?):\s*R\$\s*([0-9\.,]+)\.?\s*$"
    )
    if not total_line_pattern.search(response):
        return response

    reconciled_total = Decimal("0")
    used_cart = False

    if phone:
        try:
            items = get_cart_items(phone) or []
            if items:
                used_cart = True
                for item in items:
                    preco = _parse_brl_amount(item.get("preco", 0))
                    qtd = _parse_brl_amount(item.get("quantidade", 1))
                    reconciled_total += (preco * qtd).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except Exception:
            used_cart = False

    if not used_cart:
        line_vals = re.findall(r"(?im)^\s*-\s+.*?-\s*R\$\s*([0-9\.,]+)\s*$", response)
        for v in line_vals:
            reconciled_total += _parse_brl_amount(v)

    if reconciled_total <= 0:
        return response

    total_fmt = f"{reconciled_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}".replace(".", ",")
    return total_line_pattern.sub(rf"\1: R$ {total_fmt}.", response)


def _sanitize_out_of_context_followups(response: str) -> str:
    """
    Remove follow-up genÃ©rico ("Como posso te ajudar hoje?") em contexto
    de fechamento/finalizaÃ§Ã£o para evitar quebra de fluxo.
    """
    if not response:
        return response

    out = response.strip()
    low = out.lower()

    has_close_context = any(
        marker in low
        for marker in [
            "pedido foi finalizado",
            "pedido de nÃºmero",
            "pedido de numero",
            "finalizado com sucesso",
            "valor total oficial",
            "forma de pagamento",
        ]
    )
    if not has_close_context:
        return out

    out = re.sub(r"(?is)\n*\s*como posso (te|de) ajudar hoje\??\s*$", "", out).strip()
    out = re.sub(r"(?is)\n*\s*deseja mais alguma coisa ou podemos finalizar\??\s*$", "", out).strip()
    return out

# Orquestrador removido



def vendedor_node(state: AgentState) -> dict:
    """
    NÃ³ Vendedor: Agente especializado em vendas com prompt completo.
    """
    logger.info("ðŸ‘©â€ðŸ’¼ [VENDEDOR] Processando...")
    
    # set_current_phone(state["phone"]) # REMOVIDO: Contexto do analista
    
    prompt = load_prompt("atendente_core.md")
    llm = _build_llm(temperature=0.1)  # Temperatura baixa para manter consistÃªncia nas regras
    
    # Criar agente ReAct com as ferramentas do vendedor
    agent = create_react_agent(llm, VENDEDOR_TOOLS, prompt=prompt)
    
    logger.info(f"ðŸ‘©â€ðŸ’¼ [VENDEDOR] Agente criado. Invocando...")
    
    # ConfiguraÃ§Ã£o
    config = {
        "configurable": {"thread_id": state["phone"]},
        # Listas longas podem exigir muitas chamadas de tool (busca + add item).
        "recursion_limit": 140,
    }
    
    result = agent.invoke({"messages": state["messages"]}, config)
    response = _extract_response(result)

    # Evita vazar mensagem técnica do executor para o cliente.
    low = (response or "").lower()
    if (
        "need more steps to process this request" in low
        or "maximum recursion" in low
        or "recursion limit" in low
    ):
        logger.warning("⚠️ Limite de passos detectado; aplicando fallback neutro com base no carrinho.")
        try:
            items = get_cart_items(state["phone"]) or []
            if items:
                subtotal = Decimal("0")
                linhas = []
                for item in items[:30]:
                    nome = str(item.get("produto", "Item"))
                    qtd = Decimal(str(item.get("quantidade", 1) or 1))
                    preco = Decimal(str(item.get("preco", 0) or 0))
                    total_linha = (qtd * preco).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    subtotal += total_linha
                    qtd_txt = int(qtd) if qtd == int(qtd) else float(qtd)
                    linhas.append(f"- {qtd_txt} {nome} - R$ {float(total_linha):.2f}")
                subtotal = subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                response = (
                    "Estou finalizando o processamento da sua lista. Até aqui ficou:\n"
                    + "\n".join(linhas)
                    + f"\nTotal estimado: R$ {float(subtotal):.2f}."
                )
            else:
                response = (
                    "Recebi sua lista, mas houve instabilidade no processamento automático agora. "
                    "Vou continuar daqui sem alterar o que você pediu."
                )
        except Exception:
            response = (
                "Recebi sua lista, mas houve instabilidade no processamento automático agora. "
                "Vou continuar daqui sem alterar o que você pediu."
            )

    # Guard rail: se o cliente nÃ£o pediu para fechar, bloqueia pergunta de
    # endereÃ§o/pagamento na mesma resposta de adiÃ§Ã£o.
    last_user_text = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            last_user_text = _message_content_to_text(msg.content)
            break
    if last_user_text and not _is_close_intent(last_user_text):
        response = _sanitize_premature_checkout(response, state["phone"])

    logger.info(f"ðŸ‘©â€ðŸ’¼ [VENDEDOR] Resposta: {response[:100]}...")

    
    return {
        "final_response": response,
        "messages": result.get("messages", [])[-1:] if result.get("messages") else []
    }


# Caixa removido e Roteamento removido


# ============================================
# ConstruÃ§Ã£o do Grafo
# ============================================

def build_agent_graph():
    """ConstrÃ³i o StateGraph com a arquitetura de Agente Ãšnico."""
    
    graph = StateGraph(AgentState)
    
    # Adicionar nÃ³ Ãºnico
    graph.add_node("vendedor", vendedor_node)
    
    # Fluxo: START â†’ Vendedor â†’ END
    graph.add_edge(START, "vendedor")
    graph.add_edge("vendedor", END)
    
    return graph.compile()

# ============================================
# FunÃ§Ã£o Principal
# ============================================

def run_agent_langgraph(telefone: str, mensagem: str) -> Dict[str, Any]:
    """
    Executa o agente de vendas. Suporta texto e imagem (via tag [MEDIA_URL: ...]).
    """
    telefone = normalize_phone(telefone)
    msg_log = str(mensagem or "")
    logger.info(f"[AGENT] Telefone: {telefone} | Msg: {msg_log[:50]}...")
    lock_token = acquire_agent_lock(telefone)
    if not lock_token:
        return {
            "output": "Estou finalizando sua Ãºltima solicitaÃ§Ã£o. Me manda sÃ³ um instante e eu jÃ¡ te respondo.",
            "error": "busy"
        }

    # Evita vazamento de sugestÃµes de turnos anteriores para o turno atual.
    try:
        clear_suggestions(telefone)
    except Exception:
        pass

    # 1) Extrair URL de imagem se houver.
    image_url = None
    incoming_message = mensagem or ""
    clean_message = incoming_message

    media_match = re.search(r"\[MEDIA_URL:\s*(.*?)\]", incoming_message)
    if media_match:
        image_url = media_match.group(1)
        clean_message = incoming_message.replace(media_match.group(0), "").strip()
        if not clean_message:
            clean_message = "Analise esta imagem/comprovante enviada."
        logger.info(f"ðŸ“¸ MÃ­dia detectada: {image_url}")

    # ConfirmaÃ§Ãµes muito curtas no WhatsApp.
    if (clean_message or "").strip() in {"+", "++", "👍", "👍🏻", "👍🏽", "👍🏿"}:
        clean_message = "sim"

    # 2) Injeta contexto de sessÃ£o quando nÃ£o veio do buffer.
    runtime_message = clean_message
    if not runtime_message.strip().startswith("[SESS"):
        try:
            order_ctx = get_order_context(telefone, runtime_message)
            if order_ctx:
                runtime_message = f"{order_ctx}\n\n{runtime_message}" if runtime_message else order_ctx
        except Exception as e:
            logger.warning(f"âš ï¸ Falha ao obter contexto de sessÃ£o: {e}")

    session_directive, runtime_user_text = _extract_session_directive(runtime_message)
    if runtime_user_text:
        clean_message = runtime_user_text

    should_reset_context = _session_indicates_new_order(session_directive) or _is_fresh_order_request(clean_message)

    # 3) HistÃ³rico hÃ­brido (Redis contexto + Postgres log).
    history_handler = HybridChatMessageHistory(
        session_id=telefone,
        redis_ttl=getattr(settings, "redis_ttl", 2400),
    )

    previous_messages = []
    if should_reset_context:
        logger.info(f"ðŸ†• Novo pedido detectado para {telefone}: limpando contexto anterior.")
        try:
            clear_cart(telefone)
            clear_order_session(telefone)
            clear_pending_confirmations(telefone)
            clear_suggestions(telefone)
            start_order_session(telefone)
        except Exception as e:
            logger.warning(f"âš ï¸ Falha parcial limpando estado de pedido: {e}")

        try:
            history_handler.clear()
        except Exception as e:
            logger.warning(f"âš ï¸ Falha ao limpar histÃ³rico hÃ­brido: {e}")
    else:
        try:
            previous_messages = history_handler.messages
        except Exception as e:
            logger.error(f"Erro ao buscar histÃ³rico hÃ­brido: {e}")

    # 4) Persistir mensagem do usuÃ¡rio (sem tags internas de sessÃ£o).
    user_message_for_history = clean_message or incoming_message
    try:
        history_handler.add_user_message(user_message_for_history)
    except Exception as e:
        logger.error(f"Erro ao salvar msg user no histÃ³rico: {e}")

    try:
        # CONSTRUIR O GRAFO A CADA EXECUÃ‡ÃƒO para garantir ISOLAMENTO TOTAL.
        graph = build_agent_graph()

        # 5) Construir mensagem com contexto.
        hora_atual = get_current_time()
        contexto = f"[TELEFONE_CLIENTE: {telefone}]\n[HORÃRIO_ATUAL: {hora_atual}]\n"

        if session_directive:
            contexto += f"{session_directive}\n"
        if should_reset_context:
            contexto += "[SESSÃƒO] Considere apenas os itens desta nova conversa. Ignore pedidos anteriores.\n"

        if image_url:
            contexto += f"[URL_IMAGEM: {image_url}]\n"

        msg_norm = (clean_message or "").strip().lower()
        is_greeting_like = bool(re.match(r"^(oi|ol[aÃ¡]|bom dia|boa tarde|boa noite|opa|e ai|eai)\b", msg_norm))
        is_first_turn = len(previous_messages) == 0
        if is_first_turn and not is_greeting_like:
            contexto += "[INSTRUÃ‡ÃƒO_DE_ESTILO: cliente iniciou com pedido direto. FaÃ§a uma saudaÃ§Ã£o curta e natural (mÃ¡x 1 linha), depois responda objetivamente.]\n"

        # 5.1) Consultar dados cadastrados do cliente.
        try:
            from tools.http_tools import consultar_cliente
            cliente_data = consultar_cliente(telefone)
            if cliente_data and cliente_data.get("nome"):
                nome_cli = cliente_data["nome"]
                endereco_cli = cliente_data.get("endereco", "")
                bairro_cli = cliente_data.get("bairro", "")
                cidade_cli = cliente_data.get("cidade", "")
                total_ped = cliente_data.get("total_pedidos", 0)
                endereco_full = ", ".join(p for p in [endereco_cli, bairro_cli, cidade_cli] if p.strip())

                if is_first_turn:
                    contexto += f"[CLIENTE_CADASTRADO: {nome_cli} | EndereÃ§o: {endereco_full} | Pedidos anteriores: {total_ped}]\n[SESSÃƒO] Nova conversa.\n"
                else:
                    contexto += f"[DADOS DO CLIENTE PARA ENTREGA: {nome_cli} | EndereÃ§o: {endereco_full}]\n"

                logger.info(f"ðŸ‘¤ Cliente cadastrado: {nome_cli} ({total_ped} pedidos)")
            else:
                if is_first_turn:
                    contexto += "[CLIENTE_NOVO: nÃ£o cadastrado]\n[SESSÃƒO] Nova conversa.\n"
        except Exception as e:
            logger.warning(f"âš ï¸ Falha ao consultar cliente: {e}")
            if is_first_turn:
                contexto += "[CLIENTE_NOVO: nÃ£o cadastrado]\n[SESSÃƒO] Nova conversa.\n"

        # ExpansÃ£o de mensagens curtas.
        mensagem_expandida = clean_message
        msg_lower = (clean_message or "").lower().strip()

        if msg_lower in ["sim", "s", "ok", "pode", "isso", "quero", "beleza", "blz", "bora", "vamos", "+", "++"]:
            ultima_pergunta_ia = ""
            for msg in reversed(previous_messages):
                if isinstance(msg, AIMessage) and msg.content:
                    content = msg.content if isinstance(msg.content, str) else str(msg.content)
                    if content.strip() and not content.startswith("["):
                        ultima_pergunta_ia = content[:200]
                        break

            if ultima_pergunta_ia:
                mensagem_expandida = (
                    f"O cliente respondeu '{clean_message}' CONFIRMANDO. Sua mensagem anterior foi: "
                    f"\"{ultima_pergunta_ia}...\". Se vocÃª sugeriu produtos, use busca_produto_tool para "
                    "confirmar preÃ§o e atualizar o pedido no contexto. NÃ£o invente preÃ§o."
                )
                logger.info(f"ðŸ”„ Mensagem curta expandida: '{clean_message}'")
        elif msg_lower in ["nao", "nÃ£o", "n", "nope", "nao quero", "nÃ£o quero"]:
            mensagem_expandida = f"O cliente respondeu '{clean_message}' (NEGATIVO). Pergunte se precisa de mais alguma coisa."

        contexto += "\n"

        # 6) Construir mensagem (multimodal se tiver imagem).
        if image_url:
            message_content = [
                {"type": "text", "text": contexto + mensagem_expandida},
                {"type": "image_url", "image_url": {"url": image_url}}
            ]
            current_message = HumanMessage(content=message_content)
        else:
            current_message = HumanMessage(content=contexto + mensagem_expandida)

        # 7) Montar estado inicial.
        all_messages = list(previous_messages) + [current_message]
        initial_state = {
            "messages": all_messages,
            "phone": telefone,
            "final_response": ""
        }

        logger.info(f"ðŸ“¨ Enviando {len(all_messages)} mensagens para o grafo")
        config = {"configurable": {"thread_id": telefone}}

        # 8) Executar o grafo.
        result = graph.invoke(initial_state, config)
        output = result.get("final_response", "")

        if not output or not output.strip():
            logger.warning("âš ï¸ Resposta vazia, tentando extrair das mensagens")
            output = _extract_response({"messages": result.get("messages", [])})

        if not output or not output.strip():
            output = "Desculpe, tive um problema ao processar. Pode repetir por favor?"

        output = _reconcile_estimated_total(output, telefone)
        output = _sanitize_out_of_context_followups(output)

        logger.info(f"âœ… [AGENT] Resposta: {output[:200]}...")

        # 9) Salvar histÃ³rico (IA).
        try:
            history_handler.add_ai_message(output)
        except Exception as e:
            logger.error(f"Erro DB AI: {e}")

        return {"output": output, "error": None}

    except Exception as e:
        logger.error(f"Falha agente: {e}", exc_info=True)
        return {"output": "Tive um problema tÃ©cnico, tente novamente.", "error": str(e)}
    finally:
        try:
            release_agent_lock(telefone, lock_token)
        except Exception:
            pass


def get_session_history(session_id: str) -> HybridChatMessageHistory:
    return HybridChatMessageHistory(session_id=normalize_phone(session_id), redis_ttl=settings.human_takeover_ttl or 900)

# Alias para compatibilidade
run_agent = run_agent_langgraph


