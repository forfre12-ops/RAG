"""중앙 설정. pydantic-settings로 .env 로드."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- 인프라 ---
    database_url: str = "postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "lloydk"
    minio_secret_key: str = "lloydk_dev_minio"
    minio_secure: bool = False
    minio_bucket_docs: str = "lloydk-docs"
    minio_bucket_models: str = "lloydk-models"
    minio_bucket_mlflow: str = "mlflow"

    mlflow_tracking_uri: str = "http://localhost:5000"

    # --- API ---
    api_key: str = "lloydk_dev_apikey"

    # --- LLM ---
    llm_provider: str = "noop"  # noop|anthropic|openai|google|vllm
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""
    vllm_base_url: str = "http://localhost:8001/v1"
    vllm_model: str = "Qwen/Qwen3-14B"
    vllm_enable_thinking: bool = False

    # --- Models ---
    classifier_base_model: str = "kakaobank/kf-deberta-base"
    classifier_lightweight_model: str = "monologg/koelectra-base-v3-discriminator"
    embedding_model: str = "nlpai-lab/KURE-v1"
    embedding_fallback_model: str = "BAAI/bge-m3"

    # --- 청크/처리 ---
    max_seq_len: int = 512
    chunk_size: int = 512
    chunk_overlap: int = 64

    # --- 동작 모드 ---
    # dryrun: 무거운 모델 다운로드 없이 mock으로 검증
    # full: 실제 모델 로드 (GPU/대용량 필요)
    poc_mode: str = "dryrun"


settings = Settings()
