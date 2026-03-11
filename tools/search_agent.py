"""Compatibility layer for legacy imports that used vector-search agent tools.

Current implementation delegates searches to the local Postgres search module.
"""

from __future__ import annotations

import json
import re
from typing import List, Optional

from tools.search_router import search_products


def _split_terms(query: str) -> List[str]:
    text = (query or "").strip()
    if not text:
        return []
    parts = re.split(r"[,\n;]+", text)
    return [p.strip() for p in parts if p.strip()]


def search_specialist_tool(query: str, telefone: Optional[str] = None, limit: int = 8) -> str:
    """Legacy tool name kept for compatibility with old tests/scripts."""
    return search_products(query=query, limit=limit, telefone=telefone)


def analista_produtos_tool(query: str, telefone: Optional[str] = None, limit: int = 8) -> str:
    """Batch-capable wrapper that now uses direct local DB searches."""
    terms = _split_terms(query)
    if not terms:
        return "[]"

    # Single term keeps the original response shape.
    if len(terms) == 1:
        return search_specialist_tool(terms[0], telefone=telefone, limit=limit)

    aggregated = []
    for term in terms:
        raw = search_products(query=term, limit=limit, telefone=telefone)
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                aggregated.append({"termo": term, "resultados": items})
            else:
                aggregated.append({"termo": term, "resultados": []})
        except Exception:
            aggregated.append({"termo": term, "resultados": []})

    return json.dumps(aggregated, ensure_ascii=False)
