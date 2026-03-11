import psycopg2
from config.settings import settings

def check_dim():
    conn_str = settings.vector_db_connection_string
    if not conn_str:
        print("No vector db connection string")
        return

    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        # Assume table is 'produtos' or check what db_vector_search uses. It sends logic to pgvector.
        # usually table name is dynamic? No, code uses SQL. 
        # Checking db_vector_searchpy:
        # It executes: "SELECT ... ORDER BY embedding <-> %s::vector LIMIT ..."
        # Table name?
        # Let's inspect ONE query from db_vector_search.
        
        # Actually I'll just try to inspect information_schema or common tables.
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = cur.fetchall()
        print("Tables:", tables)
        
        for t in tables:
            tname = t[0]
            try:
                cur.execute(f"SELECT vector_dims(embedding) FROM {tname} LIMIT 1")
                dim = cur.fetchone()
                print(f"Table {tname} embedding dim: {dim}")
            except Exception as e:
                pass # Not a vector table
                conn.rollback()

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_dim()
