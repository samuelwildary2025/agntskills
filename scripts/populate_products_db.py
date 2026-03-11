import os
import sys
import json
import logging
import requests
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime
from dotenv import load_dotenv

# Add parent directory to path to import settings if needed, 
# but for now we will load env directly to be standalone or use settings if available.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from config.settings import settings
    DB_CONNECTION = settings.postgres_connection_string
    TABLE_NAME = settings.postgres_products_table_name
except ImportError:
    load_dotenv()
    DB_CONNECTION = os.getenv("POSTGRES_CONNECTION_STRING")
    TABLE_NAME = os.getenv("POSTGRES_PRODUCTS_TABLE_NAME", "produtos-sp-queiroz")

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("populate_products_db")

API_URL = "http://45.178.95.233:5001/api/Produto/GetProdutos"

def get_db_connection():
    """Establishes a connection to the PostgreSQL database."""
    try:
        conn = psycopg2.connect(DB_CONNECTION)
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None

def create_table_if_not_exists(conn):
    """Creates the products table if it does not exist."""
    create_query = f"""
    CREATE TABLE IF NOT EXISTS "{TABLE_NAME}" (
        id VARCHAR(255) PRIMARY KEY,
        nome TEXT,
        descricao TEXT,
        preco DECIMAL(10, 2),
        estoque DECIMAL(10, 3),
        codigo_barras TEXT,
        categoria TEXT,
        unidade TEXT,
        ativo BOOLEAN DEFAULT TRUE,
        ultima_atualizacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        raw_data JSONB
    );
    
    CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME.replace("-", "_")}_nome ON "{TABLE_NAME}" (nome);
    CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME.replace("-", "_")}_ean ON "{TABLE_NAME}" (codigo_barras);
    """
    try:
        with conn.cursor() as cur:
            cur.execute(create_query)
        conn.commit()
        logger.info(f"Table '{TABLE_NAME}' checked/created successfully.")
    except Exception as e:
        logger.error(f"Failed to create table: {e}")
        conn.rollback()
        raise

def fetch_products():
    """Fetches products from the API."""
    try:
        logger.info(f"Fetching products from {API_URL}...")
        response = requests.get(API_URL, timeout=60)
        response.raise_for_status()
        data = response.json()
        
        # Ensure it's a list
        if isinstance(data, dict):
             # Some APIs wrap in a 'data' key or similar, but user said "GetProdutos"
             # Let's inspect the first item if possible or assume list
             if "data" in data and isinstance(data["data"], list):
                 return data["data"]
             return [data]
        elif isinstance(data, list):
            return data
        else:
            logger.error(f"Unexpected data format: {type(data)}")
            return []
            
    except Exception as e:
        logger.error(f"Failed to fetch products: {e}")
        return []

def sync_products_db():
    """Main function to sync products from API to DB."""
    logger.info("Starting product sync...")
    
    products = fetch_products()
    if not products:
        logger.warning("No products fetched. Aborting sync.")
        return
    
    logger.info(f"Fetched {len(products)} products. Connecting to DB...")
    
    conn = get_db_connection()
    if not conn:
        return

    try:
        create_table_if_not_exists(conn)
        
        # Prepare data for insertion
        # Schema: id, nome, descricao, preco, estoque, codigo_barras, categoria, unidade, ativo, ultima_atualizacao, raw_data
        
        unique_products = {}
        for p in products:
            # Extract fields safely
            # ID: use 'id_produto'
            p_id = str(p.get("id_produto") or p.get("id") or "")
            if not p_id:
                continue
            
            # Keep the last occurrence or handle as needed
            unique_products[p_id] = p
            
        values = []
        for p_id, p in unique_products.items():
                
            nome = p.get("produto") or p.get("nome") or p.get("descricao") or ""
            descricao = p.get("descricaoEcommerceHTML") or ""
            
            # Price logic
            preco = p.get("vl_produto") or p.get("preco_venda") or 0.0
            
            # Stock logic
            estoque_val = p.get("qtd_produto") or p.get("estoque") or 0.0
            
            cod_barras = str(p.get("codigo_ean") or p.get("cod_barra") or "").strip()
            
            categoria = f"{p.get('classificacao01', '')} {p.get('classificacao02', '')}".strip()
            unidade = p.get("emb") or p.get("unid_medida") or ""
            
            # Ensure boolean
            ativo = bool(p.get("ativo")) if "ativo" in p else True
            
            values.append((
                p_id,
                nome,
                descricao,
                preco,
                estoque_val,
                cod_barras,
                categoria,
                unidade,
                ativo,
                datetime.now(),
                json.dumps(p)
            ))
            
        if not values:
            logger.warning("No valid products to insert.")
            return

        # Upsert query
        insert_query = f"""
        INSERT INTO "{TABLE_NAME}" 
        (id, nome, descricao, preco, estoque, codigo_barras, categoria, unidade, ativo, ultima_atualizacao, raw_data)
        VALUES %s
        ON CONFLICT (id) DO UPDATE SET
            nome = EXCLUDED.nome,
            descricao = EXCLUDED.descricao,
            preco = EXCLUDED.preco,
            estoque = EXCLUDED.estoque,
            codigo_barras = EXCLUDED.codigo_barras,
            categoria = EXCLUDED.categoria,
            unidade = EXCLUDED.unidade,
            ativo = EXCLUDED.ativo,
            ultima_atualizacao = EXCLUDED.ultima_atualizacao,
            raw_data = EXCLUDED.raw_data;
        """
        
        with conn.cursor() as cur:
            execute_values(cur, insert_query, values)
        
        conn.commit()
        logger.info(f"Successfully synced {len(values)} products to database.")

        # Opcional: após sincronizar Postgres, atualizar índice no Typesense.
        try:
            from config.settings import settings as app_settings
            if getattr(app_settings, "typesense_enabled", False):
                from scripts.sync_typesense import sync_typesense_from_postgres
                sync_typesense_from_postgres()
        except Exception as e:
            logger.warning(f"Typesense sync pós-DB ignorado: {e}")
        
    except Exception as e:
        logger.error(f"Sync failed during database operation: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    sync_products_db()
