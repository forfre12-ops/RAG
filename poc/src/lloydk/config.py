from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "lloydk"
    minio_secret_key: str = "lloydk_dev_minio"
    minio_secure: bool = False

    mlflow_tracking_uri: str = "http://localhost:5000"

    api_key: str = "lloydk_dev_apikey"

    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    vllm_base_url: str = "http://localhost:8001/v1"
    vllm_model: str = "Qwen/Qwen3-14B"
    vllm_enable_thinking: bool = False

    classifier_base_model: str = "kakaobank/kf-deberta-base"
    embedding_model: str = "nlpai-lab/KURE-v1"

    max_seq_len: int = 512
    chunk_overlap: int = 64


settings = Settings()
