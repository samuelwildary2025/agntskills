"""
Configurações do Agente de Supermercado
Carrega variáveis de ambiente usando Pydantic Settings
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Optional, Set
from urllib.parse import urlparse


class Settings(BaseSettings):
    """Configurações da aplicação carregadas do .env"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )
    
    # LLM Provider (openai ou google)
    openai_api_key: Optional[str] = None
    openai_embedding_api_key: Optional[str] = None # Chave específica para embeddings (OpenAI)
    google_api_key: Optional[str] = None
    llm_model: Optional[str] = None
    llm_temperature: float = 0.1  # Baixa temperatura para manter consistência
    llm_provider: str = "google"   # Mantido padrão mas pode ser sobrescrito pelo env
    analista_llm_model: Optional[str] = None
    analista_llm_temperature: Optional[float] = None
    analista_llm_provider: Optional[str] = None
    gemini_audio_model: str = "gemini-1.5-flash" # Modelo padrão para áudio, configurável no env
    openai_api_base: Optional[str] = None # Para usar Grok (xAI) ou outros compatíveis
    moonshot_api_key: Optional[str] = None
    moonshot_api_url: str = "https://api.moonshot.ai/anthropic"
    
    # Postgres
    postgres_connection_string: str
    postgres_table_name: str = "memoria"
    postgres_products_table_name: str = "produtos-sp-queiroz"  # Nova variável para tabela de produtos
    postgres_message_limit: int = 5
    
    # Banco Vetorial de Produtos (Postgres - pgvector)
    vector_db_connection_string: Optional[str] = None
    vector_search_mode: str = "exact"
    vector_search_fallback: bool = True
    vector_search_term_mappings: bool = False

    # Typesense (busca de produtos)
    typesense_enabled: bool = False
    typesense_nodes: str = "http://typesense:8108"
    typesense_api_key: Optional[str] = None
    typesense_collection: str = "produtos"
    typesense_timeout_seconds: float = 2.0
    typesense_query_by: str = "nome,descricao,categoria,codigo_barras"
    typesense_num_typos: int = 2
    typesense_drop_tokens_threshold: int = 1
    typesense_batch_size: int = 500
    typesense_ssl_verify: bool = False
    
    # Redis
    redis_url_override: Optional[str] = Field(default=None, alias="REDIS_URL")
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: Optional[str] = None
    redis_db: int = 0
    
    # API do Supermercado
    supermercado_base_url: str
    supermercado_auth_token: str

    # Consulta de EAN (estoque/preço)
    estoque_ean_base_url: str = "http://45.178.95.233:5001/api/Produto/GetProdutosEAN"

    # EAN Smart Responder (Supabase Functions)
    smart_responder_url: Optional[str] = None
    smart_responder_token: Optional[str] = None
    smart_responder_auth: str = ""
    smart_responder_apikey: str = ""
    pre_resolver_enabled: bool = False
    
    # ============================================
    # WhatsApp API - UAZAPI
    # ============================================
    # Documentação: https://docs.uazapi.com/
    uazapi_base_url: Optional[str] = None  # Ex: https://aimerc.uazapi.com
    uazapi_token: Optional[str] = None     # Token da instância
    whatsapp_agent_number: Optional[str] = None # Número do agente para Human Takeover
    
    # Human Takeover - Tempo de pausa quando atendente humano assume (em segundos)
    human_takeover_ttl: int = 2400  # 40 minutos padrão
    
    # Queue Workers (ARQ)
    workers_max_jobs: int = 15  # Aumentado de 5 para 15 (suportado pela nova chave com billing)
    worker_retry_attempts: int = 3  # Tentativas de retry em caso de falha
    
    # Servidor
    server_host: str = "0.0.0.0"
    server_port: int = 8000
    debug_mode: bool = False

    # Logging
    log_level: str = "INFO"
    log_file: str = "logs/agente.log"
    
    agent_prompt_path: Optional[str] = "prompts/vendedor.md"

    product_context_path: Optional[str] = "prompts/product_context.json"
    term_translations_path: str = "prompts/term_translations.json"
    http_allowed_hosts: str = ""

    @field_validator(
        "openai_api_base",
        "supermercado_base_url",
        "estoque_ean_base_url",
        "uazapi_base_url",
        "smart_responder_url",
        "smart_responder_token",
        "supermercado_auth_token",
        "redis_url_override",
        mode="before",
    )
    @classmethod
    def _strip_wrapping_chars(cls, v):
        if v is None:
            return None
        s = str(v).strip()
        if len(s) >= 2 and ((s[0] == s[-1] == "`") or (s[0] == s[-1] == '"') or (s[0] == s[-1] == "'")):
            s = s[1:-1].strip()
        return s


    @property
    def redis_url(self) -> str:
        """Monta a URL de conexão do Redis baseada nas variáveis"""
        if self.redis_url_override:
            return self.redis_url_override
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def typesense_nodes_list(self) -> list[str]:
        raw = (self.typesense_nodes or "").strip()
        if not raw:
            return []
        return [x.strip() for x in raw.split(",") if x.strip()]

    @property
    def allowed_outbound_hosts(self) -> Set[str]:
        hosts: Set[str] = set()

        def _add_host(url: Optional[str]) -> None:
            if not url:
                return
            try:
                parsed = urlparse(url.strip())
                if parsed.hostname:
                    hosts.add(parsed.hostname.lower())
            except Exception:
                return

        _add_host(self.supermercado_base_url)
        _add_host(self.estoque_ean_base_url)
        _add_host(self.uazapi_base_url)

        raw = (self.http_allowed_hosts or "").strip()
        if raw:
            for item in raw.split(","):
                host = item.strip().lower()
                if host:
                    hosts.add(host)

        return hosts

# Instância global de configurações
settings = Settings()
