
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
        
        # 1. Investigate 'documents' table columns
        cur.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'documents'")
        columns = cur.fetchall()
        print(f"Columns in 'documents': {columns}")
        
        # 2. Check content/metadata
        col_names = [c[0] for c in columns]
        
        if 'metadata' in col_names:
            print("Searching in METADATA (JSONB)...")
            # JSONB search
            # Try to find key 'name' or 'produto' in metadata
            query = """
                SELECT id, metadata 
                FROM documents 
                WHERE (metadata->>'name' ILIKE '% vo %') 
                   OR (metadata->>'name' ILIKE '% vô %')
                   OR (metadata->>'name' ILIKE '%arroz%vo%')
                   OR (metadata->>'produto' ILIKE '%arroz%vo%')
                   OR (content ILIKE '%arroz%vo%') 
                LIMIT 20
            """
            print(f"Executing JSONB query...")
            cur.execute(query)
            results = cur.fetchall()
            for r in results:
                print(f"ID: {r[0]}, META: {r[1]}")
        else:
            print("No metadata column? searching content...")
            # If no metadata, search 'content' column
            if 'content' in col_names:
                query = "SELECT id, content FROM documents WHERE content ILIKE '%arroz%vo%' LIMIT 20"
                cur.execute(query)
                results = cur.fetchall()
                for r in results:
                    print(r)
            else:
                 # Check if 'name' exists directly
                 if 'name' in col_names:
                     query = "SELECT * FROM documents WHERE name ILIKE '%arroz%vo%' LIMIT 20"
                     cur.execute(query)
                     results = cur.fetchall()
                     for r in results:
                         print(r)

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    main()
