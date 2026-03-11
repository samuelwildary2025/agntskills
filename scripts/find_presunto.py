
import psycopg2
from config.settings import settings
import sys
import json

def main():
    conn_str = settings.vector_db_connection_string or settings.products_db_connection_string
    if not conn_str:
        print("No connection string found.")
        return

    try:
        conn = psycopg2.connect(conn_str)
        cur = conn.cursor()
        
        # Search content for "Presunto"
        query = "SELECT id, content FROM documents WHERE content ILIKE '%presunto%' LIMIT 50"
        print(f"Executing: {query}")
        cur.execute(query)
        results = cur.fetchall()
        
        print("\n--- RESULTS FOR PRESUNTO ---")
        for r in results:
            print(f"[ID {r[0]}]: {r[1]}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
