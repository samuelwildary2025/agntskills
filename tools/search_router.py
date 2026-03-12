"""Roteador de busca de produtos (DB-only).

Fluxo:
1) Consulta apenas o Postgres local (fonte unica de verdade).
2) Salva sugestoes no Redis para continuidade de contexto.
3) Retorna sempre JSON list para manter contrato das tools.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config.logger import setup_logger
from tools.db_search import search_products_db
from tools.redis_tools import save_suggestions

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
    """Busca unificada DB-only (Postgres local)."""
    limit = max(1, min(int(limit or 8), 25))

    # SQL e' a unica fonte de verdade.
    db_raw = search_products_db(query=query, limit=limit, telefone=telefone)
    db_rows = _parse_rows(db_raw)

    if db_rows:
        chosen = db_rows[:limit]
        _save_suggestions_for_phone(telefone or "", query, chosen)
        return json.dumps(chosen, ensure_ascii=False)

    return db_raw or "[]"
