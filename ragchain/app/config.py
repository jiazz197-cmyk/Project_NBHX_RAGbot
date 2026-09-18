"""ragchain 容器配置（pydantic-settings）。

字段名/默认值严格对齐 .dsh/ragchain-interfaces.md §1。
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """容器运行配置；同名字段直接读取大写环境变量。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Server
    RAGCHAIN_HOST: str = "0.0.0.0"
    RAGCHAIN_PORT: int = 8000
    RAGCHAIN_LOG_LEVEL: str = "INFO"
    SECRET_KEY: str = ""
    ALGORITHM: str = "HS256"
    MAIN_APP_BASE_URL: str = "http://host.docker.internal:8000/api/v1"
    OUTBOUND_HTTP_TIMEOUT_SEC: float = 30.0

    # Main LLM
    MAIN_LLM_API_URL: str = ""
    MAIN_LLM_MODEL: str = "qwen3.8-27b"
    MAIN_LLM_API_KEY: str = ""
    LANGCHAIN_CHAT_TIMEOUT_SEC: float = 300.0
    LANGCHAIN_MAX_OUTPUT_TOKENS: int = 4096
    MAIN_LLM_TEMPERATURE: float = 0.3

    # Sub LLM
    SUB_LLM_API_URL: str = ""
    SUB_LLM_MODEL: str = ""
    SUB_LLM_API_KEY: str = ""
    SUB_LLM_ENABLE_THINKING: bool = False
    SUB_LLM_TIMEOUT_SEC: float = 60.0
    SUB_LLM_TEMPERATURE: float = 0.1

    # Reranker
    RERANKER_API_URL: str = "http://172.28.16.50:8096/v1/rerank"
    RERANKER_MODEL_NAME: str = "bge-reranker-v2-m3"
    AI_INFERENCE_API_KEY: str = ""
    RERANKER_TIMEOUT_SEC: float = 30.0

    # Web search
    SEARCH_ENGINE_URL: str = "http://10.80.153.12:8080/search"
    SEARCH_RESULT_COUNT: int = 5
    SEARCH_TIMEOUT_SEC: float = 15.0

    # Retrieval
    DOC_COLLECTION: str = "knowledge_chunks"
    EXCEL_COLLECTION: str = "excel_db_chunks"
    RAG_RETRIEVE_TOP_K: int = 10
    RAG_RERANK_TOP_N: int = 5

    # Memory
    MEMORY_RECENT_TURNS: int = 10
    MEMORY_COMPRESS_THRESHOLD: int = 20
    MEMORY_COMPRESS_N_RECENT: int = 5

    # Excel context
    EXCEL_CONTEXT_MAX_CHARS: int = 8000

    # Sandbox tool
    TOOL_MAX_ITERATIONS: int = 3
    TOOL_EXEC_TIMEOUT_SEC: float = 10.0
    TOOL_CODE_MAX_CHARS: int = 8000
    TOOL_OUTPUT_MAX_CHARS: int = 4000

    # SSE / tasks / guard
    SSE_HEARTBEAT_SEC: float = 15.0
    TASK_REGISTRY_TTL_SEC: float = 600.0
    RAGCHAIN_GUARD_STRICT: bool = False


settings = Settings()
