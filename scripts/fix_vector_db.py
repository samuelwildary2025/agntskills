import os
import sys
import logging
import psycopg2
from pathlib import Path

# Add parent directory to path to import settings
sys.path.append(str(Path(__file__).resolve().parent.parent))

from config.settings import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fix_vector_db():
    conn_str = settings.vector_db_connection_string
    if not conn_str:
        logger.error("❌ vector_db_connection_string not set in settings!")
        return

    logger.info(f"🔌 Connecting to vector database...")
    
    # SQL to create the function
    # Note: RRF (Reciprocal Rank Fusion) implementation
    sql = """
    DROP FUNCTION IF EXISTS hybrid_search_v2(text,vector,integer,double precision,double precision,double precision,integer);

    CREATE OR REPLACE FUNCTION hybrid_search_v2(
        query_text text,
        query_embedding vector(1536),
        match_count int,
        full_text_weight float DEFAULT 1.0,
        semantic_weight float DEFAULT 1.0,
        setor_boost float DEFAULT 0.5,
        rrf_k int DEFAULT 50
    )
    RETURNS TABLE (
        text text,
        metadata jsonb,
        score float,
        rank bigint
    )
    AS $$
    WITH full_text AS (
        SELECT
            id,
            ROW_NUMBER() OVER(ORDER BY ts_rank_cd(fts, plainto_tsquery('portuguese', query_text)) DESC) as rank
        FROM documents
        WHERE fts @@ plainto_tsquery('portuguese', query_text)
        LIMIT match_count
    ),
    semantic AS (
        SELECT
            id,
            ROW_NUMBER() OVER(ORDER BY embedding <=> query_embedding) as rank
        FROM documents
        ORDER BY embedding <=> query_embedding
        LIMIT match_count
    )
    SELECT
        d.content as text,
        d.metadata,
        (
            COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
            COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight
        ) as score,
        ROW_NUMBER() OVER (ORDER BY (
            COALESCE(1.0 / (rrf_k + ft.rank), 0.0) * full_text_weight +
            COALESCE(1.0 / (rrf_k + sem.rank), 0.0) * semantic_weight
        ) DESC) as rank
    FROM documents d
    LEFT JOIN full_text ft ON d.id = ft.id
    LEFT JOIN semantic sem ON d.id = sem.id
    WHERE ft.id IS NOT NULL OR sem.id IS NOT NULL
    ORDER BY score DESC
    LIMIT match_count;
    $$ LANGUAGE sql;
    """

    try:
        with psycopg2.connect(conn_str) as conn:
            with conn.cursor() as cur:
                logger.info("🛠️ Creating/Replacing function hybrid_search_v2...")
                cur.execute(sql)
                conn.commit()
                logger.info("✅ Function hybrid_search_v2 created successfully!")
                
    except Exception as e:
        logger.error(f"❌ Failed to fix database: {e}")

if __name__ == "__main__":
    fix_vector_db()
