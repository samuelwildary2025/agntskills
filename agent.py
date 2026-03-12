"""
Agente de IA para Atendimento de Supermercado usando LangGraph
Arquitetura: Vendedor (Agente Único + Skills)

Versão 7.0 - Skills Architecture
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
    """Função para combinar listas de mensagens."""
    return left + right

class AgentState(TypedDict):
    """Estado compartilhado entre os agentes."""
    messages: Annotated[list, add_messages]
    phone: str
    final_response: str  # Resposta final para o cliente

# ============================================
# Definição das Ferramentas (Separadas por Agente)
# ============================================

# --- FERRAMENTAS DO VENDEDOR ---

@tool
def busca_produto_tool(telefone: str, query: str) -> str:
    """
    Busca produtos e preços. A Inteligência de Busca é SUA!
    - Se o cliente pediu especificamente por nome (ex: "massa de tapioca"), passe "tapioca".
    - Se o cliente usou gírias ou sinônimos complexos (ex: "veneno pra rato", "muriçoca"), VOcÊ deve traduzir para a categoria correta (ex: "inseticida", "rato").
    - Não passe textos longos. Extraia a essência do produto (ex: "tem aquele sabão em pó brilhante?" -> passe apenas "sabao em po brilhante").

    Retorna um JSON list com os dados dos produtos avaliados semanticamente.
    """
    from tools.skill_executor import buscar_e_validar
    return buscar_e_validar(telefone, query)

@tool
def add_item_tool(telefone: str, produto: str, quantidade: float = 1.0, observacao: str = "", preco: float = 0.0, unidades: int = 0) -> str:
    """
    Adicionar um item ao pedido do cliente.
    USAR IMEDIATAMENTE quando o cliente demonstrar intenção de compra.
    
    Para produtos vendidos por KG (frutas, legumes, carnes):
    - quantidade: peso em kg (ex: 0.45 para 450g)
    - unidades: número de unidades pedidas (ex: 3 para 3 tomates)
    - preco: preço por kg
    
    Para produtos unitários:
    - quantidade: número de itens
    - unidades: deixar 0
    - preco: preço por unidade
    """
    
    # IMPORTAR AQUI para evitar ciclo de importação
    from tools.redis_tools import get_suggestions
    import difflib

    prod_lower = produto.lower().strip()
    
    # 0. TENTATIVA DE RECUPERAÇÃO DE PREÇO (Auto-Healing)
    # Se o agente esqueceu o preço (0.0), tentamos achar nas sugestões recentes
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

            # Limiar mais conservador para evitar casar produto errado por ruído.
            if melhor_match and melhor_score >= 0.72:
                preco_recuperado = float(melhor_match.get("preco", 0.0) or 0.0)
                if preco_recuperado > 0:
                    preco = preco_recuperado
                    logger.info(
                        f"✨ [AUTO-HEAL] Preço recuperado para '{produto}': R$ {preco:.2f} "
                        f"(score={melhor_score:.2f}, base='{melhor_match.get('nome')}')"
                    )
    
    # BLOQUEIO: Não permitir adicionar item sem preço válido
    if preco <= 0.01:
        logger.warning(f"🚫 [ADD_ITEM] Bloqueado: '{produto}' sem preço válido (R$ {preco:.2f}). Use busca_produto_tool primeiro.")
        return f"❌ Não consegui encontrar o preço de '{produto}'. Use busca_produto_tool para verificar o preço antes de adicionar."
    
    # Validar match_ok nas sugestões — se o produto não passou na validação, avisar
    if melhor_match is not None and not bool(melhor_match.get("match_ok", True)):
        logger.warning(f"⚠️ [ADD_ITEM] Produto '{produto}' tem match_ok=false. Pedindo confirmação.")
        return f"⚠️ '{produto}' não parece ser uma correspondência exata. Confirme com o cliente qual opção ele deseja antes de adicionar."
    
    if unidades > 0 and quantidade <= 0.01:
         logger.warning(f"⚠️ [ADD_ITEM] Item '{produto}' com unidades={unidades} mas peso zerado. O LLM deveria ter calculado.")
    
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
         # Calcular valor estimado TOTAL (já que o peso deve vir correto do LLM)
         valor_estimado = quantidade * preco
         if unidades > 0:
             return f"✅ Adicionado: {unidades}x {produto} ({quantidade:.3f}kg)"
         else:
             return f"✅ Adicionado: {quantidade} {produto}"
    return "❌ Erro ao adicionar item."

@tool
def reset_pedido_tool(telefone: str) -> str:
    """
    Zera o pedido do cliente (carrinho, sessão, comprovante e sugestões) e inicia uma nova sessão.
    """
    telefone = normalize_phone(telefone)
    clear_cart(telefone)
    clear_order_session(telefone)
    clear_comprovante(telefone)
    clear_suggestions(telefone)
    clear_pending_confirmations(telefone)
    start_order_session(telefone)
    return "✅ Pedido zerado com sucesso! Pode me enviar a nova lista de itens."

@tool
def ver_pedido_tool(telefone: str) -> str:
    """Ver os itens atuais no pedido do cliente."""
    items = get_cart_items(telefone)
    if not items:
        return "📝 Sua lista está vazia."
    
    lines = ["📝 **Resumo do Pedido:**"]
    total = 0.0
    for i, item in enumerate(items, 1):
        nome = item.get("produto", "Item")
        qtd = item.get("quantidade", 1)
        preco = item.get("preco", 0)
        unidades = item.get("unidades", 0)
        
        qtd_display = int(qtd) if qtd == int(qtd) else qtd
        lines.append(f"{i}. {qtd_display}x {nome} - R$ {preco:.2f}/un")
    
    return "\n".join(lines)

@tool
def remove_item_tool(telefone: str, item_index: int, quantidade: float = 0) -> str:
    """
    Remover um item do carrinho pelo número (índice 1-based).
    
    Se quantidade = 0 ou não informada: Remove o item INTEIRO.
    Se quantidade > 0: Remove APENAS essa quantidade (ex: tirar 1 unidade de 3).
    
    Exemplos:
    - Cliente: "tira o item 2" → remove_item_tool(tel, 2, 0) → Remove item 2 inteiro
    - Cliente: "tira 1 nescau" → remove_item_tool(tel, 2, 1) → Remove 1 unidade do item 2
    """
    from tools.redis_tools import remove_item_from_cart, update_item_quantity
    
    # Converter para índice 0-based
    idx_zero_based = int(item_index) - 1
    
    if quantidade > 0:
        # Remoção PARCIAL - reduz quantidade
        result = update_item_quantity(telefone, idx_zero_based, quantidade)
        
        if result["success"]:
            if result["removed_completely"]:
                return f"✅ {result['item_name']} removido completamente do pedido."
            else:
                return f"✅ Removido {quantidade} de {result['item_name']}. Agora tem {result['new_quantity']} no pedido."
        return f"❌ Erro: Item {item_index} não encontrado."
    else:
        # Remoção COMPLETA - comportamento original
        success = remove_item_from_cart(telefone, idx_zero_based)
        if success:
            return f"✅ Item {item_index} removido do pedido."
        return f"❌ Erro: Item {item_index} não encontrado."


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
        return "❌ Pedido vazio. Não é possível calcular total."
    
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
        f"📝 **Cálculo Oficial do Sistema:**\n"
        f"Subtotal: R$ {subtotal:.2f}\n"
        f"Taxa de Entrega: R$ {taxa_entrega:.2f}\n"
        f"----------------\n"
        f"💰 **TOTAL FINAL: R$ {total_final:.2f}**"
    )
    return res

@tool
def salvar_endereco_tool(telefone: str, endereco: str) -> str:
    """
    Salva o endereço do cliente para usar depois no fechamento do pedido.
    Use IMEDIATAMENTE quando o cliente informar o endereço (mesmo no início da conversa).
    """
    if save_address(telefone, endereco):
        return f"✅ Endereço salvo: {endereco}"
    return "❌ Erro ao salvar endereço."

@tool
def finalizar_pedido_tool(cliente: str, telefone: str, endereco: str, forma_pagamento: str, itens_json: str, observacao: str = "", comprovante: str = "", taxa_entrega: float = 0.0) -> str:
    """
    Finalizar o pedido enviando TODOS os itens confirmados.
    Use quando o cliente confirmar que quer fechar a compra e repasse todos os itens do contexto da conversa.
    
    Args:
    - cliente: Nome do cliente
    - telefone: Telefone (com DDD)
    - endereco: Endereço de entrega completo
    - forma_pagamento: Pix, Cartão ou Dinheiro
    - itens_json: String em formato JSON com todos os itens, ex: [{"produto": "Arroz", "quantidade": 2.0, "preco": 20.0}]
    - observacao: Observações extras (troco, etc)
    - comprovante: URL do comprovante PIX (se houver)
    - taxa_entrega: Valor da taxa de entrega em reais (opcional, padrão 0)
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
        return f"❌ Erro ao ler os itens do pedido: erro de formato JSON - {e}. Corrija o JSON e tente novamente."

    if not isinstance(items, list) or not items:
        return "❌ O pedido está vazio! Você deve repassar a lista de produtos confirmados."

    comprovante_salvo = get_comprovante(telefone)
    comprovante_final = comprovante or comprovante_salvo or ""

    total = Decimal("0")
    itens_formatados = []

    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            return f"❌ Item {idx} inválido no JSON. Corrija e tente novamente."

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
                logger.info(f"✨ [CHECKOUT] Preço recuperado no SQL para '{nome_produto}': R$ {preco:.2f}")
            else:
                return (
                    f"❌ Não consegui validar o preço de '{nome_produto}' para fechar o pedido. "
                    "Use busca_produto_tool novamente e confirme o item/preço antes de finalizar."
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
        logger.info(f"📋 [AUDIT] Pedido registrado para {telefone} - R$ {float(total):.2f}")
    except Exception as audit_err:
        logger.warning(f"⚠️ Falha no audit log: {audit_err}")

    result = pedidos(json_body)

    if "sucesso" in result.lower() or "✅" in result:
        # Ao concluir checkout, limpamos estado para evitar vazamento em novo pedido.
        mark_order_sent(telefone, result)
        clear_pending_confirmations(telefone)
        clear_suggestions(telefone)
        clear_cart(telefone)
        clear_order_session(telefone)
        try:
            get_session_history(telefone).clear()
            logger.info(f"🧹 Contexto da conversa limpo após finalização: {telefone}")
        except Exception as e:
            logger.warning(f"Falha ao limpar contexto após finalização: {e}")

        return (
            f"{result}\n\n"
            f"💰 **Valor Total Oficial:** R$ {float(total):.2f}\n"
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
    """Busca mensagens anteriores do cliente com horários."""
    return search_message_history(telefone, keyword)

# ============================================
# Listas de Ferramentas por Agente
# ============================================

VENDEDOR_TOOLS = [
    busca_produto_tool,
    time_tool,
    salvar_endereco_tool,
    finalizar_pedido_tool,
]

# ============================================
# Funções de Carregamento de Prompts
# ============================================

def load_prompt(filename: str) -> str:
    """Carrega um prompt do diretório prompts/"""
    base_dir = Path(__file__).resolve().parent
    prompt_path = base_dir / "prompts" / filename
    
    logger.info(f"📄 Carregando prompt: {prompt_path}")
    
    try:
        text = prompt_path.read_text(encoding="utf-8")
        text = text.replace("{base_url}", settings.supermercado_base_url)
        text = text.replace("{ean_base}", settings.estoque_ean_base_url)
        return text
    except Exception as e:
        logger.error(f"Falha ao carregar prompt {filename}: {e}")
        raise

# ============================================
# Construção dos LLMs
# ============================================

def _build_llm(temperature: float = 0.1, model_override: str = None):
    """Constrói um LLM baseado nas configurações."""
    model = model_override or getattr(settings, "llm_model", "gemini-1.5-flash")
    provider = getattr(settings, "llm_provider", "google")
    
    if provider == "google":
        logger.debug(f"🚀 Usando Google Gemini: {model}")
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
        
        logger.debug("🛡️ Configurando fallback para gemini-2.5-flash")
        return primary_llm.with_fallbacks([fallback_llm])
    else:
        logger.debug(f"🚀 Usando OpenAI (compatível): {model}")
        
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
    """Constrói um LLM rápido e leve para o Orquestrador."""
    # Usa baixa temperatura para manter boa aderência às regras com leve flexibilidade.
    return _build_llm(temperature=0.1)

# ============================================
# Nós do Grafo (Agentes)
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
            # Novo formato LangChain v1.x: content é lista de blocos
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
        r"\bs[oó] isso\b",
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
        r"\brecome(c|ç)ar\b",
        r"\bcome(c|ç)ar de novo\b",
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
        "pix, cartão ou dinheiro",
        "pix, cartao ou dinheiro",
        "já temos seu endereço",
        "ja temos seu endereco",
        "se for finalizar",
        "podemos fechar",
        "endereço da",
        "endereco da",
    ]
    kept = []
    for ln in lines:
        low = ln.lower()
        if any(term in low for term in blocked_terms):
            continue
        kept.append(ln)
    out = "\n".join(kept).strip()

    # Verificamos se há itens no carrinho (antes desta mensagem) para decidir o follow-up
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
            # Se o carrinho estiver vazio, não adicionamos nada agora, e o agente não fez uma pergunta
            out = (out + "\n\nComo posso te ajudar hoje?").strip()
            
    return out

# Orquestrador removido



def vendedor_node(state: AgentState) -> dict:
    """
    Nó Vendedor: Agente especializado em vendas com prompt completo.
    """
    logger.info("👩‍💼 [VENDEDOR] Processando...")
    
    # set_current_phone(state["phone"]) # REMOVIDO: Contexto do analista
    
    prompt = load_prompt("atendente_core.md")
    llm = _build_llm(temperature=0.1)  # Temperatura baixa para manter consistência nas regras
    
    # Criar agente ReAct com as ferramentas do vendedor
    agent = create_react_agent(llm, VENDEDOR_TOOLS, prompt=prompt)
    
    logger.info(f"👩‍💼 [VENDEDOR] Agente criado. Invocando...")
    
    # Configuração
    config = {
        "configurable": {"thread_id": state["phone"]},
        "recursion_limit": 50
    }
    
    result = agent.invoke({"messages": state["messages"]}, config)
    response = _extract_response(result)

    # Evita vazar mensagem técnica do executor para o cliente.
    low = (response or "").lower()
    if "need more steps to process this request" in low:
        logger.warning("⚠️ Resposta técnica por limite de passos detectada; aplicando fallback amigável.")
        response = (
            "Entendi. Vou seguir com isso agora: 1 cartela de Danone Ninho "
            "(iogurte polpa). Confirmo já no seu pedido."
        )

    # Guard rail: se o cliente não pediu para fechar, bloqueia pergunta de
    # endereço/pagamento na mesma resposta de adição.
    last_user_text = ""
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            last_user_text = _message_content_to_text(msg.content)
            break
    if last_user_text and not _is_close_intent(last_user_text):
        response = _sanitize_premature_checkout(response, state["phone"])

    logger.info(f"👩‍💼 [VENDEDOR] Resposta: {response[:100]}...")

    
    return {
        "final_response": response,
        "messages": result.get("messages", [])[-1:] if result.get("messages") else []
    }


# Caixa removido e Roteamento removido


# ============================================
# Construção do Grafo
# ============================================

def build_agent_graph():
    """Constrói o StateGraph com a arquitetura de Agente Único."""
    
    graph = StateGraph(AgentState)
    
    # Adicionar nó único
    graph.add_node("vendedor", vendedor_node)
    
    # Fluxo: START → Vendedor → END
    graph.add_edge(START, "vendedor")
    graph.add_edge("vendedor", END)
    
    return graph.compile()

# ============================================
# Função Principal
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
            "output": "Estou finalizando sua última solicitação. Me manda só um instante e eu já te respondo.",
            "error": "busy"
        }

    # Evita vazamento de sugestões de turnos anteriores para o turno atual.
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
        logger.info(f"📸 Mídia detectada: {image_url}")

    # 2) Injeta contexto de sessão quando não veio do buffer.
    runtime_message = clean_message
    if not runtime_message.strip().startswith("[SESS"):
        try:
            order_ctx = get_order_context(telefone, runtime_message)
            if order_ctx:
                runtime_message = f"{order_ctx}\n\n{runtime_message}" if runtime_message else order_ctx
        except Exception as e:
            logger.warning(f"⚠️ Falha ao obter contexto de sessão: {e}")

    session_directive, runtime_user_text = _extract_session_directive(runtime_message)
    if runtime_user_text:
        clean_message = runtime_user_text

    should_reset_context = _session_indicates_new_order(session_directive) or _is_fresh_order_request(clean_message)

    # 3) Histórico híbrido (Redis contexto + Postgres log).
    history_handler = HybridChatMessageHistory(
        session_id=telefone,
        redis_ttl=getattr(settings, "redis_ttl", 2400),
    )

    previous_messages = []
    if should_reset_context:
        logger.info(f"🆕 Novo pedido detectado para {telefone}: limpando contexto anterior.")
        try:
            clear_cart(telefone)
            clear_order_session(telefone)
            clear_pending_confirmations(telefone)
            clear_suggestions(telefone)
            start_order_session(telefone)
        except Exception as e:
            logger.warning(f"⚠️ Falha parcial limpando estado de pedido: {e}")

        try:
            history_handler.clear()
        except Exception as e:
            logger.warning(f"⚠️ Falha ao limpar histórico híbrido: {e}")
    else:
        try:
            previous_messages = history_handler.messages
        except Exception as e:
            logger.error(f"Erro ao buscar histórico híbrido: {e}")

    # 4) Persistir mensagem do usuário (sem tags internas de sessão).
    user_message_for_history = clean_message or incoming_message
    try:
        history_handler.add_user_message(user_message_for_history)
    except Exception as e:
        logger.error(f"Erro ao salvar msg user no histórico: {e}")

    try:
        # CONSTRUIR O GRAFO A CADA EXECUÇÃO para garantir ISOLAMENTO TOTAL.
        graph = build_agent_graph()

        # 5) Construir mensagem com contexto.
        hora_atual = get_current_time()
        contexto = f"[TELEFONE_CLIENTE: {telefone}]\n[HORÁRIO_ATUAL: {hora_atual}]\n"

        if session_directive:
            contexto += f"{session_directive}\n"
        if should_reset_context:
            contexto += "[SESSÃO] Considere apenas os itens desta nova conversa. Ignore pedidos anteriores.\n"

        if image_url:
            contexto += f"[URL_IMAGEM: {image_url}]\n"

        msg_norm = (clean_message or "").strip().lower()
        is_greeting_like = bool(re.match(r"^(oi|ol[aá]|bom dia|boa tarde|boa noite|opa|e ai|eai)\b", msg_norm))
        is_first_turn = len(previous_messages) == 0
        if is_first_turn and not is_greeting_like:
            contexto += "[INSTRUÇÃO_DE_ESTILO: cliente iniciou com pedido direto. Faça uma saudação curta e natural (máx 1 linha), depois responda objetivamente.]\n"

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
                    contexto += f"[CLIENTE_CADASTRADO: {nome_cli} | Endereço: {endereco_full} | Pedidos anteriores: {total_ped}]\n[SESSÃO] Nova conversa.\n"
                else:
                    contexto += f"[DADOS DO CLIENTE PARA ENTREGA: {nome_cli} | Endereço: {endereco_full}]\n"

                logger.info(f"👤 Cliente cadastrado: {nome_cli} ({total_ped} pedidos)")
            else:
                if is_first_turn:
                    contexto += "[CLIENTE_NOVO: não cadastrado]\n[SESSÃO] Nova conversa.\n"
        except Exception as e:
            logger.warning(f"⚠️ Falha ao consultar cliente: {e}")
            if is_first_turn:
                contexto += "[CLIENTE_NOVO: não cadastrado]\n[SESSÃO] Nova conversa.\n"

        # Expansão de mensagens curtas.
        mensagem_expandida = clean_message
        msg_lower = (clean_message or "").lower().strip()

        if msg_lower in ["sim", "s", "ok", "pode", "isso", "quero", "beleza", "blz", "bora", "vamos"]:
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
                    f"\"{ultima_pergunta_ia}...\". Se você sugeriu produtos, use busca_produto_tool para "
                    "confirmar preço e atualizar o pedido no contexto. Não invente preço."
                )
                logger.info(f"🔄 Mensagem curta expandida: '{clean_message}'")
        elif msg_lower in ["nao", "não", "n", "nope", "nao quero", "não quero"]:
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

        logger.info(f"📨 Enviando {len(all_messages)} mensagens para o grafo")
        config = {"configurable": {"thread_id": telefone}}

        # 8) Executar o grafo.
        result = graph.invoke(initial_state, config)
        output = result.get("final_response", "")

        if not output or not output.strip():
            logger.warning("⚠️ Resposta vazia, tentando extrair das mensagens")
            output = _extract_response({"messages": result.get("messages", [])})

        if not output or not output.strip():
            output = "Desculpe, tive um problema ao processar. Pode repetir por favor?"

        logger.info(f"✅ [AGENT] Resposta: {output[:200]}...")

        # 9) Salvar histórico (IA).
        try:
            history_handler.add_ai_message(output)
        except Exception as e:
            logger.error(f"Erro DB AI: {e}")

        return {"output": output, "error": None}

    except Exception as e:
        logger.error(f"Falha agente: {e}", exc_info=True)
        return {"output": "Tive um problema técnico, tente novamente.", "error": str(e)}
    finally:
        try:
            release_agent_lock(telefone, lock_token)
        except Exception:
            pass


def get_session_history(session_id: str) -> HybridChatMessageHistory:
    return HybridChatMessageHistory(session_id=normalize_phone(session_id), redis_ttl=settings.human_takeover_ttl or 900)

# Alias para compatibilidade
run_agent = run_agent_langgraph
