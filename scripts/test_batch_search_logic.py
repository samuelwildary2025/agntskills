
import time
import logging
from tools.search_agent import analista_produtos_tool

# Configure logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_batch_search():
    print("🚀 Iniciando teste de busca em lote (paralela)...")
    
    # Simula uma query com múltiplos itens enviada pelo Vendedor
    query = "arroz, feijão, macarrão, café, açúcar"
    
    start_time = time.time()
    result = analista_produtos_tool(query, telefone="5511999999999")
    end_time = time.time()
    
    duration = end_time - start_time
    print(f"\n⏱️ Tempo total: {duration:.2f} segundos")
    print(f"📄 Resultado:\n{result[:500]}...") # Print first 500 chars

    # Verificação básica de sucesso
    if "arroz" in result.lower() and "feijão" in result.lower():
        print("\n✅ Teste passou: Itens encontrados.")
    else:
        print("\n❌ Teste falhou: Itens não encontrados no retorno.")

    if duration < 10: # Assuming 5 items sequentially would take > 15s (3s each)
        print("✅ Performance OK: Execução rápida (provavelmente paralela).")
    else:
        print("⚠️ Performance ALERTA: Execução lenta (>10s).")

if __name__ == "__main__":
    test_batch_search()
