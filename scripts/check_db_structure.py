"""
Verificar estrutura do banco de dados projeto_queiroz
"""
import psycopg2

conn_str = "postgres://postgres:Theo2023...@31.97.252.6:2022/projeto_queiroz?sslmode=disable"

try:
    with psycopg2.connect(conn_str) as conn:
        with conn.cursor() as cur:
            # Listar todas as tabelas
            print("📋 TABELAS NO BANCO 'projeto_queiroz':\n")
            cur.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """)
            tables = cur.fetchall()
            
            if not tables:
                print("  ❌ Nenhuma tabela encontrada!")
            else:
                for i, (table,) in enumerate(tables, 1):
                    print(f"  {i}. {table}")
                    
                    # Ver quantos registros tem
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM {table}")
                        count = cur.fetchone()[0]
                        print(f"     → {count:,} registros")
                    except Exception as e:
                        print(f"     → Erro ao contar: {e}")
            
            # Verificar se tem extensão vector instalada
            print("\n🔌 EXTENSÕES INSTALADAS:\n")
            cur.execute("SELECT extname FROM pg_extension ORDER BY extname")
            extensions = cur.fetchall()
            for ext, in extensions:
                print(f"  ✓ {ext}")
                
except Exception as e:
    print(f"❌ Erro: {e}")
