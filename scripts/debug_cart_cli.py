#!/usr/bin/env python3
"""
Script de Debug para Cart e Sessão do Redis
Uso: python scripts/debug_cart_cli.py <telefone>
Ex: python scripts/debug_cart_cli.py 558599999999
"""
import sys
import os
import json
from pathlib import Path

# Adiciona o diretório raiz ao path para imports
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from tools.redis_tools import (
    get_cart_items,
    get_order_session,
    get_address,
    get_comprovante,
    get_redis_client,
    normalize_phone,
)
from config.settings import settings

def _inspect_raw_keys(raw_phone: str):
    client = get_redis_client()
    if client is None:
        print("\n⚠️ Redis indisponível. Não foi possível inspecionar chaves cruas.")
        return

    norm = normalize_phone(raw_phone)
    candidates = {
        "cart_raw": f"cart:{raw_phone}",
        "cart_norm": f"cart:{norm}",
        "order_session_raw": f"order_session:{raw_phone}",
        "order_session_norm": f"order_session:{norm}",
        "suggestions_raw": f"suggestions:{raw_phone}",
        "suggestions_norm": f"suggestions:{norm}",
        "comprovante_raw": f"comprovante:{raw_phone}",
        "comprovante_norm": f"comprovante:{norm}",
        "address_raw": f"address:{raw_phone}",
        "address_norm": f"address:{norm}",
    }

    print("\n🧪 [CHAVES REDIS] (raw vs normalizado)")
    for label, key in candidates.items():
        try:
            t = client.type(key)
            if t == "none":
                continue
            ttl = client.ttl(key)
            extra = ""
            if t == "list":
                extra = f" | len={client.llen(key)}"
            elif t == "string":
                val = client.get(key)
                extra = f" | value_preview={str(val)[:80]}"
            print(f"- {label}: {key} | type={t} | ttl={ttl}{extra}")
        except Exception as e:
            print(f"- {label}: erro lendo {key}: {e}")

def inspect_client(phone):
    norm = normalize_phone(phone)
    print(f"\n🔍 Inspecionando dados para: {phone} (normalizado: {norm})")
    print(f"🌍 Redis Host: {settings.redis_host}:{settings.redis_port}")
    _inspect_raw_keys(phone)
    
    # 1. Sessão
    session = get_order_session(phone)
    print("\n📦 [SESSÃO]")
    if session:
        print(json.dumps(session, indent=2, ensure_ascii=False))
    else:
        print("❌ Nenhuma sessão ativa.")

    # 2. Endereço
    addr = get_address(phone)
    print(f"\n🏠 [ENDEREÇO]: {addr if addr else '❌ Não salvo'}")

    # 3. Comprovante
    comp = get_comprovante(phone)
    print(f"\n🧾 [COMPROVANTE]: {comp if comp else '❌ Não salvo'}")

    # 4. Carrinho
    items = get_cart_items(phone)
    print(f"\n🛒 [CARRINHO] ({len(items)} itens)")
    if items:
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item.get('produto')} | Qtd: {item.get('quantidade')} | Tot: R${item.get('quantidade',0) * item.get('preco',0):.2f}")
    else:
        print("❌ Carrinho vazio.")
    
    print("\n------------------------------------------------")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/debug_cart_cli.py <telefone>")
        sys.exit(1)
    
    phone = sys.argv[1]
    inspect_client(phone)
