#!/usr/bin/env python3
import json
import re
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from tools.vector_search_subagent import run_vector_search_subagent
from tools.http_tools import estoque_preco

def _extract_eans(vector_output: str) -> list[str]:
    if not vector_output:
        return []
    eans = re.findall(r"\)\s*(\d{5,})\s*-", vector_output)
    if eans:
        return eans
    eans = re.findall(r"\b(\d{8,14})\b", vector_output)
    return eans

def main():
    if len(sys.argv) < 2:
        print("Uso: python scripts/smoke_product_flow.py \"termo do cliente\" [limit]")
        sys.exit(1)

    term = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) >= 3 else 15

    print(f"🔎 Termo: {term}")
    print(f"🔢 Limit: {limit}")
    print("")

    out = run_vector_search_subagent(term, limit=limit)
    print("=== banco_vetorial (raw) ===")
    print(out)
    print("")

    eans = _extract_eans(out)
    if not eans:
        print("❌ Nenhum EAN extraído do banco_vetorial.")
        sys.exit(2)

    ean = eans[0]
    print(f"🧾 Testando estoque_preco com primeiro EAN: {ean}")
    stock_raw = estoque_preco(ean)
    print("=== estoque_preco (raw) ===")
    print(stock_raw)
    print("")

    try:
        data = json.loads(stock_raw)
        if isinstance(data, list) and data:
            item = data[0]
            nome = item.get("produto") or item.get("nome") or ""
            preco = item.get("preco")
            print(f"✅ OK: {nome} | preco={preco}")
        else:
            print("⚠️ estoque_preco retornou lista vazia (sem disponibilidade).")
    except Exception:
        print("⚠️ estoque_preco não retornou JSON parseável (provável erro/instabilidade).")

if __name__ == "__main__":
    main()
