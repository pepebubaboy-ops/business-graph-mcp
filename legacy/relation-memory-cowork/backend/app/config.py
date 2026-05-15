from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    BASE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent.parent

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    POSTGRES_USER: str = "appuser"
    POSTGRES_PASSWORD: str = "apppassword"
    POSTGRES_DB: str = "appdb"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    MINIO_ROOT_USER: str = "minioadmin"
    MINIO_ROOT_PASSWORD: str = "minioadmin"
    MINIO_HOST: str = "localhost"
    MINIO_PORT: int = 9100
    MINIO_BUCKET: str = "app-bucket"

    DUCKDB_PATH: str = "./backend/data/analytics.duckdb"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_COLLECTION: str = "document_memory"
    QDRANT_API_KEY: str = ""
    NEO4J_URI: str = "bolt://localhost:7687"
    NEO4J_USERNAME: str = "neo4j"
    NEO4J_PASSWORD: str = "neo4jtestpass"
    NEO4J_DATABASE: str = "neo4j"

    LLM_PROVIDER: str = "local_vllm"
    LLM_BASE_URL: str = "http://localhost:8001/v1"
    LLM_MODEL: str = "Qwen/Qwen2.5-7B-Instruct"
    LLM_API_KEY: str = "local-token"
    LLM_ENABLE_DEV_FALLBACK: bool = False
    RELATION_MEMORY_LLM_ANSWER_ENABLED: bool = False
    RELATION_MEMORY_LLM_NARRATOR_ENABLED: bool = False
    RELATION_MEMORY_EXTERNAL_CONTEXT_LLM_ENABLED: bool = False
    RELATION_MEMORY_LLM_ANSWER_TIMEOUT_SECONDS: float = 45.0
    RELATION_MEMORY_LLM_ANSWER_MAX_ROWS: int = 6
    RELATION_MEMORY_OLLAMA_ENABLED: bool = False
    RELATION_MEMORY_OLLAMA_URL: str = "http://localhost:11434"
    RELATION_MEMORY_OLLAMA_MODEL: str = "qwen3:14b"
    RELATION_MEMORY_OLLAMA_TIMEOUT_SECONDS: float = 90.0
    RELATION_MEMORY_OLLAMA_BATCH_SIZE: int = 24
    RELATION_MEMORY_OLLAMA_RELATION_GATE_ENABLED: bool = False
    RELATION_MEMORY_MCP_WORKSPACE_ROOT: str = ""
    RELATION_MEMORY_MCP_MAX_FILE_SIZE_MB: int = 25
    BUSINESS_LITERATURE_DIR: str = "./backend/data/business_literature"
    BUSINESS_LITERATURE_TOP_K: int = 4
    BUSINESS_LITERATURE_MAX_CHARS_PER_SNIPPET: int = 900

    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = "openai/gpt-5.2"
    OPA_URL: str = "http://localhost:8181/v1/data/copilot/allow"
    POLICY_PROVIDER: str = "local"
    OTEL_EXPORTER_OTLP_ENDPOINT: str = ""

    SECRET_KEY: str = "change-me-to-a-random-secret-key"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def duckdb_file(self) -> Path:
        path = Path(self.DUCKDB_PATH)
        if not path.is_absolute():
            path = self.BASE_DIR / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def llm_base_url(self) -> str:
        return self.LLM_BASE_URL.rstrip("/")


settings = Settings()
