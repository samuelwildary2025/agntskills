
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

# Unset proxies to avoid httpx/openai conflicts
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)

from agent import orchestrator_node, AgentState
from langchain_core.messages import HumanMessage, SystemMessage, AIMessage

def test_orchestrator():
    print("\n🚀 Iniciando Teste do Orquestrador\n")
    
    # Casos de Teste
    test_cases = [
        ("Quero comprar arroz", "vendas"),
        ("Só isso", "checkout"),
        ("Por enquanto só", "checkout"),
        ("Pode fechar", "checkout"),
        ("Tem desconto?", "vendas"),
        ("Acabou", "checkout"),
        ("So isso", "checkout"),
    ]
    
    for user_input, expected in test_cases:
        print(f"Testing input: '{user_input}' (Expect: {expected})")
        
        # Simula estado
        state = {
            "messages": [HumanMessage(content=user_input)],
            "phone": "123456789"
        }
        
        try:
            result = orchestrator_node(state)
            intent = result.get("intent")
            current_agent = result.get("current_agent")
            
            if intent == expected:
                print(f"✅ PASS: Intent '{intent}' matches expected.")
                if intent == "checkout" and current_agent == "caixa":
                    print("   - Agent routed to Caixa correctly.")
                elif intent == "vendas" and current_agent == "vendedor":
                    print("   - Agent routed to Vendedor correctly.")
                else:
                    print(f"❌ FAIL: Agent mismatch. Intent {intent} -> Agent {current_agent}")
            else:
                print(f"❌ FAIL: Expected '{expected}', got '{intent}'")
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
        
        print("-" * 30)

if __name__ == "__main__":
    test_orchestrator()
