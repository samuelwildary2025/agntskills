# 📦 Configuração do Banco de Dados - Agente IA Mercadinho

Este documento explica **o que o agente precisa ter** para se comunicar com os bancos de dados.

## 🎯 Visão Geral

O agente utiliza **3 bancos de dados** diferentes:

| Banco | Tipo | Função |
|-------|------|--------|
| **PostgreSQL (Memória)** | Relacional | Armazena histórico de conversas |
| **PostgreSQL (Produtos Vetorial)** | Vetorial (pgvector) | Busca inteligente de produtos |
| **Redis** | Cache/Memória | Carrinho de compras e estado temporário |

---

## 1️⃣ PostgreSQL - Memória de Conversas

### Para que serve?
Armazena o histórico completo de mensagens entre cliente e agente (últimas 8 mensagens por padrão).

### O que precisa ter?

#### ✅ Variáveis de ambiente (.env)
```bash
POSTGRES_CONNECTION_STRING=postgresql://usuario:senha@host:porta/nome_banco
POSTGRES_TABLE_NAME=memoria
POSTGRES_MESSAGE_LIMIT=8
```

#### ✅ Estrutura da tabela
```sql
CREATE TABLE memoria (
    id SERIAL PRIMARY KEY,
    session_id VARCHAR(255) NOT NULL,  -- Telefone do cliente
    message JSONB NOT NULL,             -- Mensagem em formato JSON
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_session_id ON memoria(session_id);
```

#### 📝 Exemplo de conexão
```
postgresql://postgres:minhasenha@localhost:5432/agente_db
```

---

## 2️⃣ PostgreSQL - Banco Vetorial de Produtos (pgvector)

### Para que serve?
Busca inteligente de produtos usando embeddings e similaridade semântica.

### O que precisa ter?

#### ✅ Variáveis de ambiente (.env)
```bash
VECTOR_DB_CONNECTION_STRING=postgres://usuario:senha@host:porta/banco?sslmode=disable
OPENAI_API_KEY=sk-xxxxxxxxxxxxxx  # Para gerar embeddings
```

#### ✅ Extensões PostgreSQL necessárias
```sql
-- Instalar extensões (executar como superusuário)
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- Para busca por texto
```

#### ✅ Estrutura da tabela
```sql
CREATE TABLE langchain_pg_embedding (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id UUID NOT NULL,
    embedding VECTOR(1536),              -- Embedding OpenAI text-embedding-3-small
    document TEXT,                       -- Texto indexado
    cmetadata JSONB,                     -- Metadata (EAN, nome, categoria, etc)
    custom_id VARCHAR(255),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Índices para performance
CREATE INDEX ON langchain_pg_embedding USING ivfflat (embedding vector_cosine_ops);
CREATE INDEX ON langchain_pg_embedding USING gin (cmetadata);
CREATE INDEX ON langchain_pg_embedding USING gin (document gin_trgm_ops);
```

#### ✅ Função de busca híbrida (FTS + Vetorial)
```sql
-- Função híbrida com boost para HORTI-FRUTI e FRIGORIFICO
CREATE OR REPLACE FUNCTION hybrid_search_v2(
    query_text TEXT,
    query_embedding VECTOR(1536),
    match_count INT DEFAULT 20,
    full_text_weight FLOAT DEFAULT 1.0,
    semantic_weight FLOAT DEFAULT 1.0,
    setor_boost FLOAT DEFAULT 0.5,
    rrf_k INT DEFAULT 50
)
RETURNS TABLE (
    text TEXT,
    metadata JSONB,
    score FLOAT,
    rank INT
)
LANGUAGE plpgsql
AS $$
BEGIN
    -- Busca híbrida usando RRF (Reciprocal Rank Fusion)
    -- Combina busca por texto (pg_trgm) + busca vetorial + boost de categoria
    RETURN QUERY
    -- Implementação completa em db_vector_search.py
END;
$$;
```

#### 📝 Exemplo de metadata do produto
```json
{
  "codigo_ean": "7894900027013",
  "produto": "REFRIG COCA COLA PET 2L",
  "categoria1": "MERCEARIA",
  "classificacao01": "MERCEARIA",
  "ativo": true
}
```

### 🔧 Como popular o banco vetorial?

#### Opção 1: CSV → Banco Vetorial
```bash
# Usar o script de vetorização
python scripts/vectorize_products.py --csv data/produtos.csv
```

#### Opção 2: API → Banco Vetorial
```bash
# Buscar produtos da API e vetorizar
python scripts/sync_products_from_api.py
```

---

## 3️⃣ Redis - Cache e Carrinho

### Para que serve?
- **Carrinho de compras** (itens temporários antes de finalizar)
- **Estado do cliente** (última mensagem, pedido em andamento)
- **Rate limiting** (evitar spam)

### O que precisa ter?

#### ✅ Variáveis de ambiente (.env)
```bash
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=          # Deixar vazio se não tiver senha
REDIS_DB=0
```

#### ✅ Estrutura de chaves Redis

| Chave | Tipo | Expiração | Função |
|-------|------|-----------|---------|
| `cart:{telefone}` | List | 24h | Itens do carrinho |
| `order_sent:{telefone}` | String | 15min | Última vez que finalizou pedido |
| `comprovante:{telefone}` | String | 1h | URL do comprovante PIX |

#### 📝 Exemplo de item no carrinho
```json
{
  "produto": "COCA COLA PET 2L",
  "quantidade": 2,
  "preco": 10.99,
  "observacao": "",
  "unidades": 0
}
```

---

## 🔗 APIs Externas Necessárias

### 1. API OpenAI (Embeddings)
```bash
OPENAI_API_KEY=sk-proj-xxxxxxxxxxxxxxxxx
```
- **Uso:** Gerar embeddings para busca vetorial
- **Modelo:** `text-embedding-3-small` (1536 dimensões)
- **Custo:** ~$0.02 por 1M tokens

### 2. API Google Gemini (LLM)
```bash
GOOGLE_API_KEY=AIzaxxxxxxxxxxxxxxxxxxxxxxx
LLM_PROVIDER=google
LLM_MODEL=gemini-2.0-flash-lite
```
- **Uso:** Processamento de linguagem natural do agente
- **Custo:** ~$0.10/1M tokens (input) + $0.40/1M tokens (output)

### 3. API do Dashboard (Estoque e Pedidos)
```bash
SUPERMERCADO_BASE_URL=https://app.aimerc.com.br
SUPERMERCADO_AUTH_TOKEN=Bearer xxxxxxxxxx
```
- **Endpoints usados:**
  - `GET /api/Produto/GetProdutosEAN/{ean}` - Consultar estoque/preço
  - `POST /pedidos/` - Criar novo pedido
  - `PUT /pedidos/telefone/{telefone}` - Atualizar pedido

---

## 📌 Checklist de Configuração

Para o agente funcionar 100%, você precisa ter:

- [ ] PostgreSQL instalado com extensão `pgvector`
- [ ] Banco de memória configurado (tabela `memoria`)
- [ ] Banco vetorial configurado (tabela `langchain_pg_embedding`)
- [ ] Função `hybrid_search_v2` criada no banco vetorial
- [ ] Redis instalado e rodando
- [ ] Arquivo `.env` com todas as variáveis preenchidas:
  - [ ] `POSTGRES_CONNECTION_STRING`
  - [ ] `VECTOR_DB_CONNECTION_STRING`
  - [ ] `REDIS_HOST` e `REDIS_PORT`
  - [ ] `OPENAI_API_KEY`
  - [ ] `GOOGLE_API_KEY`
  - [ ] `SUPERMERCADO_BASE_URL` e `SUPERMERCADO_AUTH_TOKEN`
- [ ] Produtos vetorizados no banco (CSV ou API)

---

## 🚀 Testando a Conexão

### Teste PostgreSQL (Memória)
```python
from memory.limited_postgres_memory import LimitedPostgresChatMessageHistory

history = LimitedPostgresChatMessageHistory("55859999999", limit=8)
history.add_user_message("teste")
print(history.messages)  # Deve retornar a mensagem
```

### Teste PostgreSQL (Vetorial)
```python
from tools.db_vector_search import search_products_vector

result = search_products_vector("coca cola 2l")
print(result)  # Deve retornar lista de produtos
```

### Teste Redis
```python
from tools.redis_tools import add_item_to_cart, get_cart_items
import json

add_item_to_cart("55859999999", json.dumps({"produto": "Teste", "quantidade": 1}))
items = get_cart_items("55859999999")
print(items)  # Deve retornar o item adicionado
```

---

## ❓ Problemas Comuns

### Erro: "relation 'memoria' does not exist"
**Solução:** Criar a tabela de memória no PostgreSQL (ver seção 1.1)

### Erro: "extension 'vector' does not exist"
**Solução:** Instalar pgvector:
```bash
# Ubuntu/Debian
sudo apt install postgresql-16-pgvector

# Mac
brew install pgvector
```

### Erro: "Connection refused" no Redis
**Solução:** Iniciar o Redis:
```bash
# Linux
sudo systemctl start redis

# Mac
brew services start redis

# Docker
docker run -d -p 6379:6379 redis:latest
```

### Erro: "No embedding results for query"
**Solução:** Verificar se `OPENAI_API_KEY` está configurada e válida

---

## 📚 Referências

- [PostgreSQL pgvector](https://github.com/pgvector/pgvector)
- [LangChain PGVector](https://python.langchain.com/docs/integrations/vectorstores/pgvector)
- [OpenAI Embeddings API](https://platform.openai.com/docs/guides/embeddings)
- [Redis Python Client](https://redis-py.readthedocs.io/)

---

**Última atualização:** 19/01/2026
