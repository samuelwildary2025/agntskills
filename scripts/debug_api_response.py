import requests
import json

API_URL = "http://45.178.95.233:5001/api/Produto/GetProdutos"

try:
    print(f"Fetching products form {API_URL}...")
    response = requests.get(API_URL, timeout=60)
    data = response.json()
    
    if isinstance(data, list) and len(data) > 0:
        print("First item keys:", data[0].keys())
        print("First item sample:", json.dumps(data[0], indent=2))
    elif isinstance(data, dict):
        print("Data is a dict. Keys:", data.keys())
        if "data" in data and isinstance(data["data"], list) and len(data["data"]) > 0:
             print("First item in 'data':", data["data"][0])
    else:
        print("Data type:", type(data))
        print("Data sample:", str(data)[:500])

except Exception as e:
    print(f"Error: {e}")
