"""중앙 설정. pydantic-settings로 .env 로드.

보안:
- dev 자격증명(`api_key`, `minio_secret_key` 등) 디폴트값은 모두 빈 문자열.
- 로컬 개발은 `.env.dev` 또는 `.env`로 명시적으로 주입 (CI는 환경변수).
- 빈 자격증명으로 운영 모드(`poc_mode=full`) 진입 시 startup에서 fail-fast.

배포 프로파일 (2026-05-29 추가):
- LLOYDK_DEPLOY_PROFILE 한 줄로 4-tier 납품 모드 전환:
    lite-noapi    : GPU·외부 API 모두 없음 (noop LLM + hash embedding + inmemory + 학습 OFF)
    lite-cloud    : GPU 없음, 외부 LLM 사용 (anthropic/openai + KURE/hash + ES + 학습 OFF)
    onprem-local  : GPU 보유, 폐쇄망 (local_openai/ollama + KURE/BGE + ES + 학습 OFF)
    full-train    : 풀스펙 (자유 + KURE/BGE + ES + 학습 ON, KOIPA 풀스펙 대상)
프로파일은 미설정 키에만 default를 채움 — 사용자가 .env로 명시한 값은 항상 우선.
"""

from __future__ import annotations

import logging
import os

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

DEPLOY_PROFILES = ("lite-noapi", "lite-cloud", "onprem-local", "full-train")

# 프로파일별 강제 default — .env/env에 명시 없으면 이 값으로 채움.
# 명시값은 항상 우선 (override 가능).
_PROFILE_DEFAULTS: dict[str, dict[str, object]] = {
    "lite-noapi": {
        "llm_provider": "noop",
        "embedding_provider": "hash",
        "reranker_provider": "noop",
        "vector_backend": "inmemory",
        "storage_backend": "local",
        "enable_training": False,
        "poc_mode": "dryrun",
    },
    "lite-cloud": {
        "llm_provider": "anthropic",
        "embedding_provider": "hf",
        "reranker_provider": "noop",
        "vector_backend": "es",
        "storage_backend": "minio",
        "enable_training": False,
        "poc_mode": "full",
    },
    "onprem-local": {
        "llm_provider": "ollama",
        "embedding_provider": "hf",
        "reranker_provider": "bge",
        "vector_backend": "es",
        "storage_backend": "minio",
        "enable_training": False,
        "poc_mode": "full",
    },
    "full-train": {
        "llm_provider": "anthropic",
        "embedding_provider": "hf",
        "reranker_provider": "bge",
        "vector_backend": "es",
        "storage_backend": "minio",
        "enable_training": True,
        "poc_mode": "full",
    },
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- 배포 프로파일 ---
    # 4-tier 납품 모드. .env에 LLOYDK_DEPLOY_PROFILE=... 한 줄로 전환.
    # 빈 문자열이면 프로파일 미적용 (개별 키만 사용).
    deploy_profile: str = "lite-noapi"

    # 학습 기능 활성 — full-train 프로파일에서만 True. lite-*·onprem 에서는 False.
    # False면 /api/v1/training/* 라우터 등록 자체 skip (OpenAPI에서 사라짐).
    enable_training: bool = False

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

    # P2-C2: 일반화 storage backend — minio | seaweedfs | local
    storage_backend: str = "minio"
    storage_endpoint: str = ""        # seaweedfs s3 게이트웨이 URL (예: http://seaweed:8333)
    storage_verify_tls: bool = True

    mlflow_tracking_uri: str = "http://localhost:5000"

    # --- API ---
    # J1: dev 디폴트 제거. dryrun/테스트는 빈 키 허용, full 모드는 startup에서 fail-fast.
    api_key: str = ""

    # P1-C3: JWT 인증 모드. api_key(기본) | jwt | both
    auth_mode: str = "api_key"
    jwt_jwks_path: str = ""           # JWKS JSON 파일 경로 (kid → key)
    jwt_public_key: str = ""          # 단일 RS256 공개키 PEM (JWKS 미사용시)

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
    # Phase 3 (5070 Ti 풀가동): 학습 가중치 디렉토리. 비어있으면 rule-fallback 유지.
    # .env: CLASSIFIER_MODEL_DIR=artifacts/classifier-1ep/v-ae3f5371 형식.
    classifier_model_dir: str = ""
    embedding_model: str = "nlpai-lab/KURE-v1"

    # 임베딩 어댑터 선택:
    #   hash : hash_embedding (결정론, GPU·다운로드 불필요, lite-noapi 기본)
    #   hf   : hf_embedding (sentence-transformers, KURE/BGE 로컬 캐시, full/onprem 기본)
    embedding_provider: str = "hash"

    # --- Reranker (A1) ---
    # noop  : 입력 순서 유지 (기본, 운영 외)
    # bge   : BAAI/bge-reranker-v2-m3 (FlagEmbedding 또는 sentence_transformers)
    # qwen3 : Qwen/Qwen3-Reranker-0.6B
    reranker_provider: str = "noop"
    reranker_top_k: int = 50  # retriever 1차 후 reranker로 줄일 입력 크기
    embedding_fallback_model: str = "BAAI/bge-m3"

    # --- 청크/처리 ---
    max_seq_len: int = 512
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 표적 6 (2026-05-29): PreprocessPipeline의 PII 마스킹 자동 적용 여부.
    # 보안 민감 도메인이라 기본 True. dryrun/테스트에서 비활성하려면 LLOYDK_PII_MASKING_ENABLED=0.
    pii_masking_enabled: bool = True

    # 표적 1 (2026-05-29): InferencePipeline use_rag 활성 시 retrieval facade 호출 기본값.
    rag_default_collection: str = "docs"
    rag_default_top_k: int = 5
    rag_query_expansion_method: str = "rule"  # rule | llm | hybrid

    # --- 업로드 한도 (DoS 차단) ---
    # R3: guide upload·classify content 본문 크기 한도. 환경변수 LLOYDK_MAX_UPLOAD_MB로 조정.
    # 운영 기본 20MB — 대부분 가이드 PDF·DOCX 커버. 초과 시 413 Payload Too Large 반환.
    max_upload_mb: int = 20

    # --- 동작 모드 ---
    # dryrun: 무거운 모델 다운로드 없이 mock으로 검증
    # full: 실제 모델 로드 (GPU/대용량 필요)
    poc_mode: str = "dryrun"


def apply_profile_defaults(s: Settings) -> dict[str, str]:
    """LLOYDK_DEPLOY_PROFILE에 따라 미설정 키만 default로 채움.

    명시값(.env 또는 env)은 항상 우선 — 빈 문자열이거나 환경변수 미설정인 키만
    프로파일 default로 덮어씀. 같은 인스턴스를 in-place 수정.

    Returns: {field: "profile" | "explicit"} — 어느 키가 어디서 왔는지.
    """
    sources: dict[str, str] = {}
    profile = (s.deploy_profile or "").lower()
    if not profile:
        return {"_status": "no_profile"}
    if profile not in DEPLOY_PROFILES:
        logger.warning(
            "unknown deploy_profile=%r, expected one of %s — skipping",
            profile, DEPLOY_PROFILES,
        )
        return {"_status": f"unknown_profile:{profile}"}

    # Settings는 env_prefix 없이 필드명 그대로 env에 매핑됨 (예: LLM_PROVIDER).
    # 그래서 명시 여부는 환경변수의 대문자 필드명으로 판정.
    defaults = _PROFILE_DEFAULTS[profile]
    for field, default in defaults.items():
        env_name = field.upper()
        explicit = os.environ.get(env_name) is not None
        if explicit:
            sources[field] = "explicit"
            continue
        setattr(s, field, default)
        sources[field] = "profile"
    sources["_profile"] = profile
    sources["_status"] = "ok"
    return sources


settings = Settings()
apply_profile_defaults(settings)


def assert_production_credentials() -> None:
    """운영 모드에서 빈 자격증명 차단. dryrun/테스트는 우회.

    호출 위치: api/app.py startup hook. fail-fast로 운영 미설정 사고 방지.

    D1 (2026-05-29): env가 비었어도 secrets_manager(Vault/AWS SM)에서 채워올
    수 있도록 한 번 fill_from_secrets_manager()를 시도한 뒤 missing 판정.
    LLOYDK_SECRETS_BACKEND=env(기본)는 사실상 no-op이라 dev 동작에 영향 없음.
    """
    if settings.poc_mode != "full":
        return
    fill_from_secrets_manager()
    missing: list[str] = []
    if not settings.api_key:
        missing.append("LLOYDK_API_KEY")
    if not settings.minio_secret_key:
        missing.append("LLOYDK_MINIO_SECRET_KEY")
    if missing:
        raise RuntimeError(
            f"production 모드인데 필수 자격증명 누락: {', '.join(missing)}. "
            ".env / 환경변수 / secrets_manager(Vault·AWS SM)로 설정 필요."
        )


# D1 (2026-05-29): secrets_manager 호출처 일원화.
# settings는 pydantic-settings로 부팅 시점에 env를 빨아들이지만, 운영 환경에서는
# Vault/AWS SM이 진실 소스. fill_from_secrets_manager()가 빈 자리만 SM 값으로 채움.
_SECRETS_FILLED = False


def fill_from_secrets_manager() -> dict:
    """secrets_manager 백엔드에서 빈 자격증명만 보충.

    멱등 — 모듈 lifetime 1회만 실제 fill (idempotent guard).
    backend=env(기본)는 os.getenv 그대로라 settings와 같은 값. SM 백엔드일 때만 의미.

    Returns:
        {key: source} — 어느 값이 어디서 왔는지 (env / sm-vault / sm-aws / skipped).
    """
    global _SECRETS_FILLED
    if _SECRETS_FILLED:
        return {"_status": "already_filled"}

    try:
        from lloydk.services.secrets_manager import get_secrets_manager  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        # SM 모듈 부재 — 기본 동작 유지
        return {"_status": "secrets_manager_unavailable"}

    sm = get_secrets_manager()
    sources: dict[str, str] = {"_status": "ok", "_backend": sm.name}

    # 보충 후보: 운영에 필요한 자격증명만 (전체 settings 덮어쓰기 금지)
    candidates = [
        ("api_key", "LLOYDK_API_KEY"),
        ("minio_secret_key", "LLOYDK_MINIO_SECRET_KEY"),
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
        ("es_password", "ES_PASSWORD"),
        ("es_api_key", "ES_API_KEY"),
    ]
    for attr, key in candidates:
        if getattr(settings, attr, ""):
            sources[attr] = "env"
            continue
        v = sm.get(key)
        if v:
            setattr(settings, attr, v)
            sources[attr] = f"sm-{sm.name}"
        else:
            sources[attr] = "missing"

    _SECRETS_FILLED = True
    return sources
