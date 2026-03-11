
import sys
import os

# Adicionar diretório raiz ao path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from tools.search_agent import search_specialist_tool
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

def test_searches():
    print("🚀 INICIANDO TESTE DO SUB-AGENTE 🚀")
    print(f"Provider LLM: {settings.llm_provider}")
    
    scenarios = [
        "arroz, feijao",
        "leite",             # Ambíguo: deve preferir líquido integral/desnatado, não creme nem doce
        "coca cola zero",    # Específico
        "pao",               # Deve preferir pão francês
    ]
    
    for query in scenarios:
        print("\n" + "="*50)
        print(f"🔎 BUSCANDO: '{query}'")
        print("="*50)
        
        try:
            result = search_specialist_tool(query)
            print(f"📝 RESULTADO:\n{result}")
        except Exception as e:
            print(f"❌ ERRO: {e}")

if __name__ == "__main__":
    test_searches()
