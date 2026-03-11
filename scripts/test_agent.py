import sys
import os
from pathlib import Path

# Adicionar raiz ao path
sys.path.append(str(Path(__file__).resolve().parent.parent))

from agent import run_agent_langgraph
from config.logger import setup_logger

logger = setup_logger(__name__)

def test_conversation():
    print("\n🚀 Iniciando Teste de Arquitetura Multi-Agente\n")
    
    telefone = "5511999999999"
    
    # 1. Teste Vendas
    print("--- 1. Teste de Vendas (Orquestrador -> Vendedor) ---")
    msg1 = "Olá, tem cerveja heineken?"
    print(f"👤 Cliente: {msg1}")
    res1 = run_agent_langgraph(telefone, msg1)
    print(f"🤖 Agente: {res1.get('output')}\n")
    
    # 2. Teste Checkout (Orquestrador -> Caixa)
    print("--- 2. Teste de Checkout (Orquestrador -> Caixa) ---")
    msg2 = "Pode fechar a conta"
    print(f"👤 Cliente: {msg2}")
    res2 = run_agent_langgraph(telefone, msg2)
    print(f"🤖 Agente: {res2.get('output')}\n")
    
    # 3. Teste Endereço e Frete (Caixa Puro)
    print("--- 3. Teste de Endereço (Caixa Puro) ---")
    msg3 = "Moro na Rua São João, 112, Centro"
    print(f"👤 Cliente: {msg3}")
    res3 = run_agent_langgraph(telefone, msg3)
    print(f"🤖 Agente: {res3.get('output')}\n")

if __name__ == "__main__":
    try:
        test_conversation()
    except Exception as e:
        print(f"❌ Erro no teste: {e}")
