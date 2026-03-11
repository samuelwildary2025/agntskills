"""Busca de produtos via Typesense.

Este módulo é opcional e só é usado quando TYPESENSE_ENABLED=true.
Se o cliente Typesense não estiver instalado ou o servidor estiver indisponível,
retorna lista vazia para o chamador aplicar fallback sem quebrar o atendimento.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from config.logger import setup_logger
from config.settings import settings

logger = setup_logger(__name__)

_client = None
_client_lock = threading.Lock()
_typesense_import_failed = False


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _normalize_query(query: str) -> str:
    text = (query or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_nodes() -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []
    for raw in settings.typesense_nodes_list:
        parsed = urlparse(raw)
        if not parsed.hostname:
            continue
        protocol = (parsed.scheme or "http").lower()
        port = parsed.port
        if port is None:
            port = 443 if protocol == "https" else 80
        nodes.append(
            {
                "host": parsed.hostname,
                "port": str(port),
                "protocol": protocol,
            }
        )
    return nodes


def _get_client():
    global _client, _typesense_import_failed

    if _client is not None:
        return _client
    if not settings.typesense_enabled:
        return None

    with _client_lock:
        if _client is not None:
            return _client
        try:
            import typesense  # type: ignore
        except Exception as exc:
            if not _typesense_import_failed:
                logger.warning(f"Typesense client indisponível (pip install typesense): {exc}")
                _typesense_import_failed = True
            return None

        nodes = _parse_nodes()
        if not nodes:
            logger.warning("Typesense habilitado, mas TYPESENSE_NODES está vazio/inválido.")
            return None

        api_key = (settings.typesense_api_key or "").strip()
        if not api_key:
            logger.warning("Typesense habilitado, mas TYPESENSE_API_KEY não configurado.")
            return None

        try:
            _client = typesense.Client(
                {
                    "nodes": nodes,
                    "api_key": api_key,
                    "connection_timeout_seconds": max(1, int(settings.typesense_timeout_seconds)),
                    "num_retries": 2,
                    "retry_interval_seconds": 0.2,
                }
            )
            return _client
        except Exception as exc:
            logger.warning(f"Falha ao criar cliente Typesense: {exc}")
            return None


def _document_to_product(hit: Dict[str, Any], max_text_match: float) -> Optional[Dict[str, Any]]:
    if not isinstance(hit, dict):
        return None
    doc = hit.get("document") or {}
    if not isinstance(doc, dict):
        return None

    text_match = _safe_float(hit.get("text_match"), 0.0)
    if max_text_match > 0:
        norm_score = max(0.0, min(1.0, text_match / max_text_match))
    else:
        norm_score = 0.0

    nome = str(doc.get("nome") or doc.get("produto") or "").strip()
    if not nome:
        return None

    estoque = _safe_float(doc.get("estoque"), 0.0)
    categoria = str(doc.get("categoria") or "").strip()

    return {
        "id": str(doc.get("id") or nome),
        "nome": nome,
        "categoria": categoria,
        "preco": _safe_float(doc.get("preco"), 0.0),
        "estoque": estoque,
        "unidade": str(doc.get("unidade") or "UN"),
        "match_score": round(norm_score, 4),
        "match_ok": bool(norm_score >= 0.50 and (estoque > 0 or "frigor" in categoria.lower() or "horti" in categoria.lower())),
        "engine": "typesense",
    }


def search_products_typesense(query: str, limit: int = 8) -> List[Dict[str, Any]]:
    """Busca produtos no Typesense, retornando lista normalizada.

    Em caso de falha, retorna [] para permitir fallback transparente.
    """
    q = _normalize_query(query)
    if len(q) < 2:
        return []

    client = _get_client()
    if client is None:
        return []

    per_page = max(1, min(int(limit or 8), 25))
    params = {
        "q": q,
        "query_by": settings.typesense_query_by,
        "num_typos": max(0, int(settings.typesense_num_typos)),
        "drop_tokens_threshold": max(0, int(settings.typesense_drop_tokens_threshold)),
        "per_page": per_page,
        "sort_by": "_text_match:desc",
        "filter_by": "ativo:=true",
    }

    try:
        response = client.collections[settings.typesense_collection].documents.search(params)
    except Exception as exc:
        logger.warning(f"Busca Typesense falhou (fallback Postgres): {exc}")
        return []

    hits = response.get("hits") if isinstance(response, dict) else None
    if not isinstance(hits, list) or not hits:
        return []

    max_text_match = 0.0
    for h in hits:
        if isinstance(h, dict):
            max_text_match = max(max_text_match, _safe_float(h.get("text_match"), 0.0))

    results: List[Dict[str, Any]] = []
    for hit in hits:
        item = _document_to_product(hit, max_text_match=max_text_match)
        if item:
            results.append(item)

    return results


def ensure_typesense_collection() -> bool:
    """Cria a collection de produtos no Typesense caso não exista."""
    client = _get_client()
    if client is None:
        return False

    schema = {
        "name": settings.typesense_collection,
        "fields": [
            {"name": "id", "type": "string"},
            {"name": "nome", "type": "string"},
            {"name": "descricao", "type": "string", "optional": True},
            {"name": "categoria", "type": "string", "optional": True},
            {"name": "codigo_barras", "type": "string", "optional": True},
            {"name": "unidade", "type": "string", "optional": True},
            {"name": "preco", "type": "float", "optional": True},
            {"name": "estoque", "type": "float", "optional": True},
            {"name": "ativo", "type": "bool", "optional": True},
            {"name": "updated_at", "type": "int64"},
        ],
        "default_sorting_field": "updated_at",
    }

    try:
        client.collections[settings.typesense_collection].retrieve()
        return True
    except Exception:
        pass

    try:
        client.collections.create(schema)
        logger.info(f"✅ Collection Typesense criada: {settings.typesense_collection}")
        return True
    except Exception as exc:
        logger.error(f"❌ Falha ao criar collection Typesense: {exc}")
        return False


def import_documents_typesense(documents: List[Dict[str, Any]]) -> bool:
    """Importa documentos na collection (upsert em lote)."""
    if not documents:
        return True
    client = _get_client()
    if client is None:
        return False
    if not ensure_typesense_collection():
        return False

    payload = "\n".join(json.dumps(d, ensure_ascii=False) for d in documents)
    try:
        client.collections[settings.typesense_collection].documents.import_(
            payload,
            {"action": "upsert"},
        )
        return True
    except Exception as exc:
        logger.error(f"❌ Falha ao importar lote no Typesense: {exc}")
        return False
