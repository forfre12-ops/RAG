"""중앙 설정. pydantic-settings로 .env 로드.

보안:
- dev 자격증명(`api_key`, `minio_secret_key` 등) 디폴트값은 모두 빈 문자열.
- 로컬 개발은 `.env.dev` 또는 `.env`로 명시적으로 주입 (CI는 환경변수).
- 빈 자격증명으로 운영 모드(`poc_mode=full`) 진입 시 startup에서 fail-fast.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- 인프라 ---
    # 디폴트는 localhost dev DB. 운영은 DATABASE_URL env 필수.
    database_url: str = "postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk"
    redis_url: str = "redis://localhost:6379/0"

    # 벡터 DB
    vector_backend: str = "es"  # es | inmemory
    es_url: str = "http://localhost:9200"
    es_username: str = ""
    es_password: str = ""
    es_api_key: str = ""
    es_verify_certs: bool = True

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""  # J1: dev 디폴트 제거. .env 필수.
    minio_secure: bool = False
    minio_bucket_docs: str = "lloydk-docs"
    minio_bucket_models: str = "lloydk-models"
    minio_bucket_mlflow: str = "mlflow"

    mlflow_tracking_uri: str = "http://localhost:5000"

    # --- API ---
    # J1: dev 디폴트 제거. dryrun/테스트는 빈 키 허용, full 모드는 startup에서 fail-fast.
    api_key: str = ""

    # CORS allow-origins. 운영에서는 .env로 origin allowlist 설정.
    # 기본값 ["*"]은 PoC·dryrun·테스트 편의를 위함. 운영 배포 시 명시적 origin 필수.
    cors_allow_origins: list[str] = ["*"]

    # --- LLM ---
    # provider 선택: 원격(원격 API) 또는 로컬(OpenAI 호환 endpoint) 자유 선택.
    # 일반화 납품을 위해 어느 한쪽에 잠금하지 않음.
    #   noop          : 결정론적 mock (CI·dryrun)
    #   anthropic     : Claude Sonnet/Opus/Haiku 원격
    #   openai        : GPT-4o 등 원격
    #   google        : Gemini 원격 (어댑터 준비 중)
    #   local_openai  : OpenAI 호환 endpoint (vLLM·Ollama·LM Studio·llama.cpp)
    #   vllm          : (alias) local_openai와 동일 — 하위호환
    #   ollama        : (alias) local_openai와 동일 — Ollama 기본 11434 endpoint
    llm_provider: str = "noop"
    llm_model: str = "claude-sonnet-4-6"
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    google_api_key: str = ""

    # 로컬 OpenAI 호환 endpoint (vLLM·Ollama·LM Studio·llama.cpp)
    local_llm_base_url: str = "http://localhost:8001/v1"  # vLLM 기본
    local_llm_model: str = "Qwen/Qwen3-14B"
    local_llm_api_key: str = "EMPTY"   # vLLM은 EMPTY, Ollama는 ollama, LM Studio는 lm-studio
    local_llm_enable_thinking: bool = False  # Qwen3 /think 지시어

    # 하위호환 alias (vllm_*) — 기존 코드·테스트가 참조 중
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

    # --- 업로드 한도 (DoS 차단) ---
    # R3: guide upload·classify content 본문 크기 한도. 환경변수 LLOYDK_MAX_UPLOAD_MB로 조정.
    # 운영 기본 20MB — 대부분 가이드 PDF·DOCX 커버. 초과 시 413 Payload Too Large 반환.
    max_upload_mb: int = 20

    # --- 동작 모드 ---
    # dryrun: 무거운 모델 다운로드 없이 mock으로 검증
    # full: 실제 모델 로드 (GPU/대용량 필요)
    poc_mode: str = "dryrun"


settings = Settings()


def assert_production_credentials() -> None:
    """운영 모드에서 빈 자격증명 차단. dryrun/테스트는 우회.

    호출 위치: api/app.py startup hook. fail-fast로 운영 미설정 사고 방지.
    """
    if settings.poc_mode != "full":
        return
    missing: list[str] = []
    if not settings.api_key:
        missing.append("LLOYDK_API_KEY")
    if not settings.minio_secret_key:
        missing.append("LLOYDK_MINIO_SECRET_KEY")
    if missing:
        raise RuntimeError(
            f"production 모드인데 필수 자격증명 누락: {', '.join(missing)}. "
            ".env 또는 환경변수로 설정 필요."
        )
