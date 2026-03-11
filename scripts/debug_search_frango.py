
import sys
import os
from pathlib import Path

# Adicionar raiz ao path
# Adicionar raiz ao path
sys.path.append(str(Path(__file__).resolve().parent.parent))

# MOCK DEPENDENCIES
from unittest.mock import MagicMock
sys.modules["flashrank"] = MagicMock()
sys.modules["flashrank.Ranker"] = MagicMock()
sys.modules["flashrank.RerankRequest"] = MagicMock()

# Unset proxies
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)

from tools.db_vector_search import search_products_vector
from config.settings import settings

def debug_search():
    print("\n🚀 Debugging Search for 'frango'...\n")
    
    query = "frango"
    results = search_products_vector(query, limit=10)
    
    print(f"\nResultados para '{query}':")
    print(results)

if __name__ == "__main__":
    debug_search()
