"""
Script Python para corrigir função duplicada hybrid_search_v2
Usa a mesma conexão que o agente usa, funcionando mesmo com SSH tunnel/VPN
"""
import psycopg2
import os
import sys

# Tentar carregar settings
try:
    from config.settings import settings
    conn_str = settings.vector_db_connection_string
    print(f"📡 Usando conexão do settings: {conn_str[:50]}...")
except Exception as e:
    print(f"⚠️ Erro ao carregar settings: {e}")
    # Fallback para variável de ambiente
    conn_str = os.getenv("VECTOR_DB_CONNECTION_STRING")
    if not conn_str:
        print("❌ VECTOR_DB_CONNECTION_STRING não configurada!")
        sys.exit(1)

print(f"\n🔧 Corrigindo função hybrid_search_v2 duplicada...\n")

try:
    with psycopg2.connect(conn_str) as conn:
        with conn.cursor() as cur:
            # Passo 1: Listar funções existentes
            print("📋 Funções hybrid_search_v2 encontradas:")
            cur.execute("""
                SELECT 
                    p.proname as function_name,
                    pg_get_function_arguments(p.oid) as arguments
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE p.proname = 'hybrid_search_v2'
            """)
            functions = cur.fetchall()
            for i, func in enumerate(functions, 1):
                print(f"  {i}. {func[0]}({func[1]})")
            
            if not functions:
                print("  ✅ Nenhuma função encontrada - pode estar OK")
                sys.exit(0)
            
            if len(functions) == 1:
                print("  ✅ Apenas 1 função - pode estar OK")
                print("\n⚠️ Mas vamos recriar para garantir que está correta...")
            else:
                print(f"  ❌ {len(functions)} funções duplicadas - PRECISA CORRIGIR!")
            
            # Passo 2: Remover todas as versões
            print("\n🗑️ Removendo funções duplicadas...")
            cur.execute("DROP FUNCTION IF EXISTS hybrid_search_v2(text, vector, int, float, float, float, int) CASCADE")
            cur.execute("DROP FUNCTION IF EXISTS hybrid_search_v2(text, vector(1536), int, float, float, float, int) CASCADE")
            print("  ✅ Funções antigas removidas")
            
            # Passo 3: Criar função correta
            print("\n🏗️ Criando função correta...")
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
            print("  ✅ Função criada com sucesso!")
            
            # Passo 4: Verificar
            print("\n🔍 Verificando...")
            cur.execute("""
                SELECT 
                    p.proname as function_name,
                    pg_get_function_arguments(p.oid) as arguments
                FROM pg_proc p
                JOIN pg_namespace n ON p.pronamespace = n.oid
                WHERE p.proname = 'hybrid_search_v2'
            """)
            functions = cur.fetchall()
            print(f"  ✅ {len(functions)} função encontrada:")
            for func in functions:
                print(f"     {func[0]}({func[1][:80]}...)")
            
            # Commit
            conn.commit()
            print("\n✅ CORREÇÃO CONCLUÍDA COM SUCESSO!")
            print("\n🔄 Próximo passo: Reiniciar o servidor do agente")
            
except psycopg2.Error as e:
    print(f"\n❌ Erro PostgreSQL: {e}")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ Erro: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
