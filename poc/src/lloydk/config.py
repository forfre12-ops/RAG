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

    # 원본 at-rest 봉투 암호화 (adapters/storage/encrypted_store.py).
    # 원본 영업비밀 바이너리(documents-raw)는 증빙용으로 보관하지만 평문 저장은 유출
    # 리스크다. enabled=True면 build_storage()가 EncryptingStorage로 래핑해 대상 버킷을
    # AES-256-GCM으로 암·복호화한다(인프라 SSE 불요, 폐쇄망 LocalStorage에서도 동작).
    # 기본 False(비파괴) — 운영 진입 시 enabled=True면 키가 필수(startup fail-fast).
    storage_encryption_enabled: bool = False
    storage_encryption_key: str = ""          # 고엔트로피 시크릿(SHA-256으로 32B 키 유도)
    storage_encrypted_buckets: list[str] = ["documents-raw"]  # 암호화 대상 버킷

    mlflow_tracking_uri: str = "http://localhost:5000"

    # --- API ---
    # J1: dev 디폴트 제거. dryrun/테스트는 빈 키 허용, full 모드는 startup에서 fail-fast.
    api_key: str = ""

    # P1-C3: JWT 인증 모드. api_key(기본) | jwt | both
    auth_mode: str = "api_key"
    jwt_jwks_path: str = ""           # JWKS JSON 파일 경로 (kid → key)
    jwt_public_key: str = ""          # 단일 RS256 공개키 PEM (JWKS 미사용시)
    jwt_issuer: str = ""              # iss claim 검증값. 빈 문자열이면 검증 skip (개발용)
    jwt_audience: str = ""            # aud claim 검증값. 빈 문자열이면 검증 skip (개발용)

    # RBAC actor_role 소스 (보안: X-Actor-Role 헤더 위조 차단).
    # api_key 모드는 단일 공유키라 키 보유자=신뢰된 시스템 호출자. role은 서버가 고정.
    #   api_key_role: api_key 인증 성공 시 부여되는 역할 (require_role 검사 대상).
    #   api_key_trust_actor_role_header: True면 X-Actor-Role 헤더로 역할 자칭 허용
    #     (개발·테스트 편의). 운영(poc_mode=full)에서 True면 startup fail-fast.
    #     헤더값은 항상 Actor enum(admin|reviewer|system|kl_backend)으로 검증.
    api_key_role: str = "system"
    api_key_trust_actor_role_header: bool = False

    # --- DB 커넥션 풀 (운영 동시성) ---
    # 기본 5+10=15는 dev용. 운영은 동시 요청 수에 맞춰 DB_POOL_SIZE/DB_MAX_OVERFLOW 조정.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # DB 연결 타임아웃(초). PG 일시 불가 시 연결이 무한 대기하면 GradeRegistry·세션 등이
    # 워커 부팅/추론기 생성을 무한 블록한다 → 타임아웃 후 빠르게 예외 → 호출부 폴백.
    # PostgreSQL(libpq) connect_timeout으로 주입. 0 이하면 미적용. 운영은 3~10s 권장.
    db_connect_timeout: int = 5

    # --- Celery worker safety limits ---
    celery_result_expires: int = 24 * 60 * 60
    celery_task_soft_time_limit: int = 300
    celery_task_time_limit: int = 600
    celery_worker_max_tasks_per_child: int = 100

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

    # 서빙 softmax temperature scaling 계수 (pipeline.py 추론 경로).
    # 분류기 softmax는 OOD(학습에 없던 새 문체)에서 과신(overconfident)하는 경향이 있어
    # 0.7 confidence 게이트가 보정 안 된 값 위에 설 수 있다. T>1이면 분포를 부드럽게 해
    # 과신을 완화한다(calibration). 기본 1.0 = 무보정(기존 동작 보존). 평가에서 측정한
    # temperature를 .env로 주입: CLASSIFIER_TEMPERATURE=1.3 형식. 신뢰도 분포 모니터링은
    # drift_monitor와 연계한다(§9.3).
    classifier_temperature: float = 1.0

    # 서빙 escalation τ (pipeline._run_model, C-esc). None(기본)=순수 argmax(동작 보존).
    # 값(0<τ<1)을 주면 severity-ordered escalation: 가장 심각한 등급 g 중 prob(g) ≥ τ 인 것을
    # 채택, 없으면 argmax 폴백 — FNR을 더 낮추는 방향(고등급 적극 승격). τ를 낮출수록 미탐↓·과분류↑.
    # 운영점 τ는 scripts/eval_fnr_threshold_sweep.py로 골든셋 PR곡선을 측정해 확정한다(동일 규칙).
    classifier_escalation_tau: float | None = None

    # FNR-safe 오버라이드 threshold (pipeline.py).
    # 룰 엔진의 TS 점수가 이 값 이상이고 룰 등급이 모델 등급보다 높으면 TS로 올림.
    # 높일수록 오버라이드 빈도 감소 (S3 과분류 완화), 낮출수록 FNR 안전성 강화.
    # 권장 범위: 2.5~5.0. 기본 3.0. 운영 데이터 누적 후 조정.
    fnr_rule_ts_threshold: float = 3.0
    fnr_rule_s1_threshold: float = 2.2
    fnr_rule_s2_threshold: float = 1.6

    # 청크 집계에서 most-severe-wins(max-pooling)를 적용할 고등급 코드.
    # 이 등급들은 청크 평균이 아니라 가장 강한 청크로 잡아 한 단락의 비밀 신호가 희석되어
    # 미탐되는 것을 막는다. 기본 TS·S1 — S2·S3는 표준(길이가중) 집계(S2→S3 희석은 고등급
    # 미탐보다 저위험, S2까지 max하면 과분류↑). 도메인에 따라 .env로 조정.
    severe_agg_codes: list[str] = ["TS", "S1"]

    # 저신뢰 검수 라우팅 임계값. 모델 confidence가 이 값 미만이면 응답을
    # status="needs_review"로 표시하고 warning에 검수 권고를 남긴다. **거부(reject)는
    # 하지 않음** — 고위험 도메인에서 저신뢰라고 응답을 막으면 FNR이 악화되므로
    # '플래그+검수권고'까지만. 데모 콘솔(api/static/incident.js)이 광고하는 0.7과 일치.
    # 임계 수치 자체의 정밀 튜닝은 운영 human_review 라벨 누적 후 PR곡선으로 조정.
    review_confidence_threshold: float = 0.7

    # Source-type prior = 비공지성 게이트 (Gate 1). doc/22 §4.0 · doc/32 §2.
    # 이미 공개된 출처(판례·공시·보도자료 등)의 문서는 내용과 무관하게 S3 — 부정경쟁방지법
    # §2.2 비공지성 미충족 → 영업비밀 불성립. 가중합(내용) 결과를 게이트가 덮어쓴다.
    # 실측 근거: 게이트 미적용 시 진짜 공개판례의 85%가 비밀로 과분류(reports/p1_real_public_fpr).
    # 이 게이트는 metadata.source가 공개 출처일 때만 발동하므로 비공개(내부) 문서의 고위험
    # 탐지는 전혀 건드리지 않는다(데이터 재라벨과 달리 FNR 트레이드오프 없음).
    # 유일한 실제 위험은 출처 메타데이터 무결성(비밀을 공개 출처로 오태깅) — ML이 아닌 ingest 책임.
    source_prior_enabled: bool = True
    # cap 레벨: "S3"(공개=S3 강제, 법리 정합·권장) | "S2"(부분완화, TS/S1만 하향 — 레거시).
    source_prior_cap_grade: str = "S3"

    # 등급 산정법(정본 가이드 B안): multiplicative(S×V×M 곱셈) | additive(레거시 4요소 가중합).
    labeling_method: str = "multiplicative"

    # 고등급(TS/S1) 변경 2인검토 (C-cons, doc/36). 기본 False=단일검수자 즉시확정(동작 보존).
    # True면 고등급으로의 confirm/relabel은 **서로 다른 2인**이 같은 등급에 동의해야 확정되고,
    # 1인만 동의한 동안은 classification.status='needs_second_review'로 보류된다(편향·오염 방지).
    # 정식 정책(검토자 자격·시니어 사인오프 범위)은 발주처 협의로 확정 — 본 플래그는 그 메커니즘.
    high_grade_dual_review: bool = False
    high_grade_review_codes: list[str] = ["TS", "S1"]

    # 룰 엔진 고위험 패턴(_HIGH_RISK_PATTERNS, 범용 영문약어 EUV·API·M&A 등) 가중치 배수 (B1).
    # 1.0=기본(동작 보존). 범용 약어의 단독 과분류가 골든셋 PR곡선에서 확인되면 이 값을 낮춰
    # (예 0.6) 코드 변경 없이 영향력을 하향한다. 과분류는 FNR-safe 방향이라 0 결함은 아니나
    # 검수부하·정밀도 관점의 운영 레버. 0이면 고위험 패턴 부스트 비활성.
    rule_high_risk_weight_multiplier: float = 1.0

    # --- 재학습 배포 합격선 게이트 (A2-②/C-ver, doc/36 본개발 #1) ---
    # 재학습 모델은 train_classifier_task에서 항상 ModelVersion으로 등록(C-ver, 이력 보존)하되,
    # 운영 활성화(activate)는 **합격선 게이트 통과 + 아래 opt-in** 모두 충족할 때만.
    # retrain_auto_activate 기본 False = "등록만, 자동 활성 안 함"(미검증 모델 자동배포 차단).
    # 운영에서 자동배포를 켜려면 명시적으로 True 설정 + 게이트 허용오차를 데이터로 확정.
    retrain_auto_activate: bool = False
    # 게이트: 후보 fnr_high(고등급 미탐율) ≤ baseline + 이 허용오차여야 활성 허용(미탐 악화 차단).
    retrain_fnr_high_tolerance: float = 0.02
    # 게이트: 후보 f1_macro ≥ baseline - 이 허용오차여야 활성 허용(전반 성능 붕괴 차단).
    retrain_f1_drop_tolerance: float = 0.05
    # 교정→라벨 재빌드/병합 학습셋 출력 디렉토리(A2-①).
    retrain_dataset_dir: str = "datasets/demo_retrain"

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
    rag_index_chunk_size: int = 1200
    rag_index_chunk_overlap: int = 100
    rag_operational_embedding_model: str = "nlpai-lab/KURE-v1"
    rag_operational_search_mode: str = "hybrid"

    # --- 업로드 한도 (DoS 차단) ---
    # R3: guide upload·classify content 본문 크기 한도. 환경변수 LLOYDK_MAX_UPLOAD_MB로 조정.
    # 운영 기본 20MB — 대부분 가이드 PDF·DOCX 커버. 초과 시 413 Payload Too Large 반환.
    max_upload_mb: int = 20

    # OCR DoS 가드 — 스캔 PDF 한 건을 OCR할 때 변환·인식할 최대 페이지 수.
    # 수백쪽 스캔본 한 건이 pdf2image/Tesseract를 수십분~OOM으로 모는 것을 차단.
    # 초과 페이지는 변환하지 않고 '잘림' 경고를 남긴다. 0 이하면 무제한(명시적 opt-out).
    # 보수적 기본 50쪽 — 대부분의 정상 문서를 커버하면서 악성·사고성 대용량은 차단.
    ocr_max_pages: int = 50

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
    # [M-config-env] 명시 판정을 os.environ(대문자 필드명)으로만 하면, pydantic-settings가
    # .env 파일에서 읽은 값(os.environ에는 없음)을 '미설정'으로 오인해 프로파일이 .env
    # 지정값을 덮어쓴다. model_fields_set(env·.env·생성자 kwargs 모두 포함)을 우선 근거로
    # 삼고, 환경변수 존재는 보조 근거로 OR 결합 — '미설정 키에만 default 채움' 보장.
    fields_set = getattr(s, "model_fields_set", set())
    defaults = _PROFILE_DEFAULTS[profile]
    for field, default in defaults.items():
        env_name = field.upper()
        explicit = field in fields_set or os.environ.get(env_name) is not None
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
    # pytest/TestClient 환경에서는 운영 자격증명 검사 skip.
    # conftest.py가 TESTING=1 을 설정하거나 pytest가 PYTEST_CURRENT_TEST를 주입.
    if os.environ.get("TESTING", "").strip().lower() in {"1", "true"} or os.environ.get("PYTEST_CURRENT_TEST"):
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
    # CORS=["*"] 운영에서 오류
    if settings.cors_allow_origins == ["*"]:
        raise RuntimeError(
            "SECURITY: CORS allow-origins=[\"*\"]는 운영 모드에서 허용되지 않습니다. "
            "LLOYDK_CORS_ALLOW_ORIGINS=https://your.domain.com 으로 명시하세요."
        )

    # 원본 at-rest 암호화가 켜졌는데 키가 없으면 평문 저장 위험 — fail-fast.
    if settings.storage_encryption_enabled and not settings.storage_encryption_key:
        raise RuntimeError(
            "SECURITY: STORAGE_ENCRYPTION_ENABLED=1 인데 STORAGE_ENCRYPTION_KEY 미설정. "
            "원본 at-rest 암호화 키가 없으면 평문으로 저장됩니다. "
            "키를 설정하거나(권장) 암호화를 끄세요."
        )

    # RATE_LIMIT_DISABLED 운영에서 오류
    if os.environ.get("RATE_LIMIT_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "RATE_LIMIT_DISABLED=1 은 운영 모드에서 허용되지 않습니다. "
            "부하 테스트 후 반드시 제거하세요."
        )

    # AUDIT_DISABLED 운영에서 오류 — audit_log 부재 시 보안 사고·법적 추적 불가.
    # (middleware.py가 이 변수로 감사 기록을 noop 처리하므로 운영에선 fail-fast 차단.)
    if os.environ.get("AUDIT_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            "AUDIT_DISABLED=1 은 운영 모드(poc_mode=full)에서 허용되지 않습니다. "
            "audit_log가 없으면 보안 사고·법적 추적이 불가합니다."
        )

    # X-Actor-Role 헤더 신뢰는 운영에서 권한 위조 경로 — 차단.
    # RBAC가 필요하면 auth_mode=jwt(서명된 roles claim) 사용.
    if settings.api_key_trust_actor_role_header:
        raise RuntimeError(
            "SECURITY: API_KEY_TRUST_ACTOR_ROLE_HEADER=True 는 운영 모드에서 허용되지 않습니다. "
            "X-Actor-Role 헤더로 누구나 admin 자칭이 가능합니다. "
            "역할 분리가 필요하면 AUTH_MODE=jwt 를 사용하세요."
        )

    # rule-fallback-v0 운영 차단 — 모델 디렉토리가 명시됐는데 없으면 즉시 오류
    if settings.classifier_model_dir:
        from pathlib import Path  # noqa: PLC0415
        model_path = Path(settings.classifier_model_dir)
        if not model_path.exists():
            raise RuntimeError(
                f"CLASSIFIER_MODEL_DIR={settings.classifier_model_dir!r} 경로가 존재하지 않습니다. "
                "운영 모드에서 모델 미로드 시 rule-fallback-v0으로 조용히 넘어가지 않습니다. "
                "경로를 수정하거나 CLASSIFIER_MODEL_DIR을 비워 rule-fallback 의도를 명시하세요."
            )
    else:
        # 모델 디렉토리 미설정 = rule-fallback 의도. 운영에서 경고만.
        logger.warning(
            "CLASSIFIER_MODEL_DIR 미설정 — rule-fallback-v0으로 분류됩니다. "
            "운영에서 모델 추론이 필요하면 CLASSIFIER_MODEL_DIR을 설정하세요."
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
