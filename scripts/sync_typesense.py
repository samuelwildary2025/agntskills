"""Sincroniza produtos do Postgres para o Typesense."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

# Permite execução direta: python scripts/sync_typesense.py
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from tools.typesense_search import import_documents_typesense, ensure_typesense_collection

logger = logging.getLogger(__name__)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _to_updated_at(value: Any) -> int:
    if value is None:
        return int(datetime.now(tz=timezone.utc).timestamp())
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    try:
        parsed = datetime.fromisoformat(str(value))
        dt = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())
    except Exception:
        return int(datetime.now(tz=timezone.utc).timestamp())


def _row_to_doc(row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row.get("id") or ""),
        "nome": str(row.get("nome") or row.get("produto") or "").strip(),
        "descricao": str(row.get("descricao") or "").strip(),
        "categoria": str(row.get("categoria") or "").strip(),
        "codigo_barras": str(row.get("codigo_barras") or "").strip(),
        "unidade": str(row.get("unidade") or "UN").strip() or "UN",
        "preco": _safe_float(row.get("preco"), 0.0),
        "estoque": _safe_float(row.get("estoque"), 0.0),
        "ativo": bool(row.get("ativo", True)),
        "updated_at": _to_updated_at(row.get("ultima_atualizacao")),
    }


def _fetch_rows_from_postgres() -> List[Dict[str, Any]]:
    table_name = settings.postgres_products_table_name or "produtos-sp-queiroz"
    query = sql.SQL(
        """
        SELECT id, nome, descricao, categoria, codigo_barras, unidade, preco, estoque, ativo, ultima_atualizacao
        FROM {table}
        """
    ).format(table=sql.Identifier(table_name))

    with psycopg2.connect(settings.postgres_connection_string) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute(query)
            return cursor.fetchall() or []


def sync_typesense_from_postgres() -> bool:
    """Sincroniza toda a tabela de produtos no Typesense (upsert)."""
    if not settings.typesense_enabled:
        logger.info("Typesense desabilitado; sync_typesense ignorado.")
        return False

    if not ensure_typesense_collection():
        logger.warning("Collection Typesense indisponível; sync_typesense abortado.")
        return False

    try:
        rows = _fetch_rows_from_postgres()
    except Exception as exc:
        logger.error(f"Falha ao ler produtos do Postgres para sync_typesense: {exc}")
        return False

    docs = [_row_to_doc(r) for r in rows if isinstance(r, dict) and r.get("id")]
    if not docs:
        logger.warning("Nenhum produto válido para sync_typesense.")
        return False

    batch_size = max(50, int(settings.typesense_batch_size or 500))
    ok_batches = 0
    total_batches = 0
    for i in range(0, len(docs), batch_size):
        total_batches += 1
        batch = docs[i : i + batch_size]
        if import_documents_typesense(batch):
            ok_batches += 1

    logger.info(
        f"Sync Typesense concluído: {len(docs)} docs, lotes OK {ok_batches}/{total_batches}"
    )
    return ok_batches == total_batches


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    success = sync_typesense_from_postgres()
    print(json.dumps({"success": success}))
