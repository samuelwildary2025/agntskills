import os
import sys
import psycopg2
from dotenv import load_dotenv

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config.settings import settings
    DB_CONNECTION = settings.postgres_connection_string
    TABLE_NAME = settings.postgres_products_table_name
except ImportError:
    load_dotenv()
    DB_CONNECTION = os.getenv("POSTGRES_CONNECTION_STRING")
    TABLE_NAME = os.getenv("POSTGRES_PRODUCTS_TABLE_NAME", "produtos-sp-queiroz")

try:
    conn = psycopg2.connect(DB_CONNECTION)
    cur = conn.cursor()
    cur.execute(f'SELECT COUNT(*) FROM "{TABLE_NAME}"')
    count = cur.fetchone()[0]
    print(f"Total rows in '{TABLE_NAME}': {count}")
    conn.close()
except Exception as e:
    print(f"Error: {e}")
