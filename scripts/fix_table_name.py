"""
Corrigir função hybrid_search_v2 para usar tabela 'documents'
"""
import psycopg2

conn_str = "postgres://postgres:Theo2023...@31.97.252.6:2022/projeto_queiroz?sslmode=disable"

print("🔧 Atualizando função hybrid_search_v2 para usar tabela 'documents'...\n")

try:
    with psycopg2.connect(conn_str) as conn:
        with conn.cursor() as cur:
            # Remover função antiga
            print("🗑️ Removendo função antiga...")
            cur.execute("DROP FUNCTION IF EXISTS hybrid_search_v2 CASCADE")
            
            # Criar função correta com nome da tabela correto
            print("🏗️ Criando função com tabela 'documents'...")
            sql = """
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
        SELECT 
            id,
            content as text,
            metadata,
            ts_rank_cd(to_tsvector('portuguese', content), plainto_tsquery('portuguese', query_text)) as fts_score,
            ROW_NUMBER() OVER (ORDER BY ts_rank_cd(to_tsvector('portuguese', content), plainto_tsquery('portuguese', query_text)) DESC) as fts_rank
        FROM documents
        WHERE to_tsvector('portuguese', content) @@ plainto_tsquery('portuguese', query_text)
        ORDER BY fts_score DESC
        LIMIT match_count * 3
    ),
    semantic_results AS (
        SELECT 
            id,
            content as text,
            metadata,
            1 - (embedding <=> query_embedding) as semantic_score,
            ROW_NUMBER() OVER (ORDER BY embedding <=> query_embedding) as semantic_rank
        FROM documents
        ORDER BY embedding <=> query_embedding
        LIMIT match_count * 3
    ),
    combined AS (
        SELECT 
            COALESCE(f.id, s.id) as id,
            COALESCE(f.text, s.text) as text,
            COALESCE(f.metadata, s.metadata) as metadata,
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
            """
            cur.execute(sql)
            
            conn.commit()
            print("✅ Função atualizada com sucesso!\n")
            
            # Testar
            print("🧪 Testando busca...")
            cur.execute("SELECT * FROM hybrid_search_v2('tomate', array_fill(0.0, ARRAY[1536])::vector, 5)")
            results = cur.fetchall()
            print(f"✅ Busca funcionou! Encontrados {len(results)} resultados\n")
            
            if results:
                print("📦 Exemplo de resultado:")
                print(f"  Texto: {results[0][0][:100]}...")
                print(f"  Metadata: {results[0][1]}")
                
except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback
    traceback.print_exc()
