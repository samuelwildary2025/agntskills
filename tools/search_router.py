"""Roteador de busca de produtos.

Fluxo:
1) Tenta Typesense (busca tolerante a typo) quando habilitado.
2) Se confiança for baixa, complementa com Postgres existente.
3) Retorna sempre JSON list para manter contrato das tools.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config.logger import setup_logger
from config.settings import settings
from tools.db_search import search_products_db
from tools.redis_tools import save_suggestions
from tools.typesense_search import search_products_typesense

logger = setup_logger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _parse_rows(raw: str) -> List[Dict[str, Any]]:
    try:
        parsed = json.loads(raw or "[]")
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
    except Exception:
        pass
    return []


def _is_typesense_confident(rows: List[Dict[str, Any]]) -> bool:
    if not rows:
        return False
    top = _safe_float(rows[0].get("match_score"), 0.0)
    top_ok = bool(rows[0].get("match_ok"))
    ok_count = sum(1 for r in rows[:3] if bool(r.get("match_ok")))
    return top >= 0.62 or top_ok or ok_count >= 2


def _row_key(row: Dict[str, Any]) -> str:
    return str(row.get("id") or row.get("nome") or "").strip().lower()


def _merge_ranked(primary: List[Dict[str, Any]], secondary: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}

    for row in primary + secondary:
        if not isinstance(row, dict):
            continue
        key = _row_key(row)
        if not key:
            continue
        current = merged.get(key)
        if current is None:
            merged[key] = dict(row)
            continue
        if _safe_float(row.get("match_score"), 0.0) > _safe_float(current.get("match_score"), 0.0):
            merged[key] = dict(row)

    out = list(merged.values())
    out.sort(key=lambda r: _safe_float(r.get("match_score"), 0.0), reverse=True)
    return out[:limit]


def _save_suggestions_for_phone(telefone: str, query: str, rows: List[Dict[str, Any]]) -> None:
    if not telefone:
        return
    try:
        payload = []
        for r in rows:
            payload.append(
                {
                    "nome": r.get("nome") or "",
                    "preco": _safe_float(r.get("preco"), 0.0),
                    "termo_busca": query,
                    "match_ok": bool(r.get("match_ok")),
                    "match_score": _safe_float(r.get("match_score"), 0.0),
                }
            )
        save_suggestions(telefone, payload[:6])
    except Exception as exc:
        logger.warning(f"Falha ao salvar sugestões do search_router: {exc}")


def search_products(query: str, limit: int = 8, telefone: Optional[str] = None) -> str:
    """Busca unificada para produtos com fallback transparente."""
    limit = max(1, min(int(limit or 8), 25))

    # Mantém comportamento legado quando Typesense está desligado.
    if not settings.typesense_enabled:
        return search_products_db(query=query, limit=limit, telefone=telefone)

    # 1) Busca primária no Typesense.
    ts_rows = search_products_typesense(query=query, limit=max(limit, 10))
    if ts_rows and _is_typesense_confident(ts_rows):
        chosen = ts_rows[:limit]
        _save_suggestions_for_phone(telefone or "", query, chosen)
        return json.dumps(chosen, ensure_ascii=False)

    # 2) Complementa com Postgres (camada atual já validada no projeto).
    db_raw = search_products_db(query=query, limit=limit, telefone=telefone)
    db_rows = _parse_rows(db_raw)

    if ts_rows:
        merged = _merge_ranked(ts_rows, db_rows, limit=limit)
        if merged:
            _save_suggestions_for_phone(telefone or "", query, merged)
            return json.dumps(merged, ensure_ascii=False)
        _save_suggestions_for_phone(telefone or "", query, ts_rows[:limit])
        return json.dumps(ts_rows[:limit], ensure_ascii=False)

    # 3) Sem resultado do Typesense: mantém retorno do DB.
    return db_raw
