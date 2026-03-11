import sys
from tools.db_search import search_products_db

query = sys.argv[1]
print(search_products_db(query, limit=10))
