-- Script para corrigir função duplicada hybrid_search_v2
-- Execute este script no banco de dados vetorial

-- 1. Remover todas as versões da função
DROP FUNCTION IF EXISTS hybrid_search_v2(text, vector, int, float, float, float, int) CASCADE;
DROP FUNCTION IF EXISTS hybrid_search_v2(text, vector(1536), int, float, float, float, int) CASCADE;

-- 2. Recriar a função correta (com tipos explícitos)
CREATE OR REPLACE FUNCTION hybrid_search_v2(
    query_text TEXT,
    query_embedding VECTOR(1536),
    match_count INT DEFAULT 20,
    full_text_weight FLOAT DEFAULT 1.0,
    semantic_weight FLOAT DEFAULT 1.0,
    setor_boost FLOAT DEFAULT 0.5,
    rrf_k INT DEFAULT 50
)
RETURNS TABLE (
    text TEXT,
    metadata JSONB,
    score FLOAT,
    rank INT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    WITH fts_results AS (
        -- Busca por texto usando pg_trgm
        SELECT 
            id,
            document as text,
            cmetadata as metadata,
            ts_rank_cd(to_tsvector('portuguese', document), plainto_tsquery('portuguese', query_text)) as fts_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('portuguese', document), plainto_tsquery('portuguese', query_text)) DESC) as fts_rank
        FROM langchain_pg_embedding
        WHERE to_tsvector('portuguese', document) @@ plainto_tsquery('portuguese', query_text)
        ORDER BY fts_score DESC
        LIMIT match_count * 3
    ),
    semantic_results AS (
        -- Busca vetorial (similaridade cosseno)
        SELECT 
            id,
            document as text,
            cmetadata as metadata,
            1 - (embedding <=> query_embedding) as semantic_score,
            ROW_NUMBER() OVER (ORDER BY embedding <=> query_embedding) as semantic_rank
        FROM langchain_pg_embedding
        ORDER BY embedding <=> query_embedding
        LIMIT match_count * 3
    ),
    combined AS (
        -- Combinar resultados usando RRF (Reciprocal Rank Fusion)
        SELECT 
            COALESCE(f.id, s.id) as id,
            COALESCE(f.text, s.text) as text,
            COALESCE(f.metadata, s.metadata) as metadata,
            -- RRF Score
            (
                COALESCE(full_text_weight / (rrf_k + f.fts_rank), 0.0) +
                COALESCE(semantic_weight / (rrf_k + s.semantic_rank), 0.0)
            ) as rrf_score,
            f.fts_rank,
            s.semantic_rank
        FROM fts_results f
        FULL OUTER JOIN semantic_results s ON f.id = s.id
    ),
    boosted AS (
        -- Aplicar boost para categorias HORTI-FRUTI e FRIGORIFICO
        SELECT 
            text,
            metadata,
            rrf_score + 
            CASE 
                WHEN metadata->>'classificacao01' IN ('HORTI-FRUTI', 'FRIGORIFICO', 'AÇOUGUE', 'PADARIA')
                THEN setor_boost
                ELSE 0.0
            END as final_score,
            ROW_NUMBER() OVER (ORDER BY rrf_score + 
                CASE 
                    WHEN metadata->>'classificacao01' IN ('HORTI-FRUTI', 'FRIGORIFICO', 'AÇOUGUE', 'PADARIA')
                    THEN setor_boost
                    ELSE 0.0
                END DESC
            ) as final_rank
        FROM combined
    )
    SELECT 
        b.text,
        b.metadata,
        b.final_score::FLOAT as score,
        b.final_rank::INT as rank
    FROM boosted b
    ORDER BY b.final_score DESC
    LIMIT match_count;
END;
$$;

-- 3. Verificar que foi criada corretamente
SELECT 
    p.proname as function_name,
    pg_get_function_arguments(p.oid) as arguments,
    pg_get_functiondef(p.oid) as definition
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE p.proname = 'hybrid_search_v2';
