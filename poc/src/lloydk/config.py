"""중앙 설정. pydantic-settings로 .env 로드.

보안:
- dev 자격증명(`api_key`, `minio_secret_key` 등) 디폴트값은 모두 빈 문자열.
- 로컬 개발은 `.env.dev` 또는 `.env`로 명시적으로 주입 (CI는 환경변수).
- 빈 자격증명으로 운영 모드(`poc_mode=full`) 진입 시 startup에서 fail-fast.

배포 프로파일 (2026-05-29 추가):
- LLOYDK_DEPLOY_PROFILE 한 줄로 4-tier 납품 모드 전환:
    lite-noapi    : GPU·외부 API 모두 없음 (noop LLM + hash embedding + inmemory + 학습 OFF)
    lite-cloud    : GPU 없음, 외부 LLM 사용 (anthropic/openai + KURE/hash + PG + 학습 OFF)
    onprem-local  : GPU 보유, 폐쇄망 (local_openai/ollama + KURE/BGE + PG + 로컬FS저장 + 학습 OFF)
    full-train    : 풀스펙 (자유 + KURE/BGE + PG + 로컬FS저장 + 학습 ON, KOIPA 풀스펙 대상)
프로파일은 미설정 키에만 default를 채움 — 사용자가 .env로 명시한 값은 항상 우선.
"""

from __future__ import annotations

import logging
import os

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# ── #31 설정 검증용 허용 열거값 ──────────────────────────────────────────────
# 의도: 위험한 값(오타·범위밖·관계위반)이 조용히 통과해 런타임에서 폭발하는 것을
# 부팅 시점에 fail-fast로 막는다. 단 하위호환 alias(vllm/ollama 등 기존 .env/코드가
# 쓰는 값)는 반드시 통과시킨다 — 현존 사용값을 깨지 않는 것이 최우선(비파괴).
# 각 집합은 어댑터 팩토리(adapters/*/__init__.py)의 분기·.env*·테스트 실측값을 합집합으로 둠.
_VALID_AUTH_MODE = {"api_key", "jwt", "both"}
# llm: noop/anthropic/openai/google + 로컬 OpenAI 호환(local_openai)와 그 alias들.
_VALID_LLM_PROVIDER = {
    "noop", "anthropic", "openai", "google", "gemini",
    "local_openai", "vllm", "ollama", "lm_studio",
}
# embedding: hash(드라이런)·hf(KURE/BGE). 하위호환 alias 일부 포함(huggingface/kure/bge).
_VALID_EMBEDDING_PROVIDER = {"hash", "hf", "huggingface", "kure", "kure-v1", "bge", "bgem3"}
_VALID_RERANKER_PROVIDER = {"noop", "bge"}
_VALID_VECTOR_BACKEND = {"es", "pg", "pgvector", "postgres", "inmemory"}
_VALID_STORAGE_BACKEND = {"minio", "seaweedfs", "s3", "local"}
_VALID_POC_MODE = {"dryrun", "full"}
_VALID_SOURCE_PRIOR_CAP_GRADE = {"S2", "S3"}

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
        "vector_backend": "pg",
        # [CFG-5] 로컬FS로 통일(2026-07-22 사용자 확정) — 'MinIO 미사용(전체)' 결정·onprem 정합.
        # 종전 'minio' 기본은 번들서 minio 제거와 모순(poc_mode=full 시 미가용→startup 크래시)
        # 드리프트였다. lite-cloud 는 경량 tier(require_safety_gates=False)라 암호화를 강제하지
        # 않되, 오픈망 파일럿서 원본 at-rest 암호화가 필요하면 .env STORAGE_ENCRYPTION_ENABLED=1(+키).
        "storage_backend": "local",
        "enable_training": False,
        "poc_mode": "full",
    },
    "onprem-local": {
        "llm_provider": "ollama",
        "embedding_provider": "hf",
        # reranker: 실측 recall 무개선·CPU 지연 5배 → 폐쇄망 기본 noop (필요시 프로파일서 명시적 bge)
        "reranker_provider": "noop",
        "vector_backend": "pg",
        # 폐쇄망=로컬FS(file://) — 2026-06-24 결정(MinIO 미사용, 원본만 EncryptingStorage 래핑).
        # 이전 'minio' 기본은 결정과 모순되는 드리프트였다.
        "storage_backend": "local",
        "enable_training": False,
        "poc_mode": "full",
        # FNR-safe 서빙 운영점 ON (2026-06-27 감사 + clean 홀드아웃 sweep 측정).
        # τ=0.30: clean 77행에서 미탐 FNR 0.174→0.087(절반)·검토 +5pp·TS recall 0.91→1.0 (저regret 운영점).
        #   더 낮추면(0.10~0.12) FNR≤0.05이나 검토부담 0.80+. 운영 검수역량에 맞춰 .env로 조정 가능.
        # agreement_gate: 룰·모델 불일치만 needs_review 라우팅(등급 무변경·FNR-monotone·실패 시 silent 폴백).
        "classifier_escalation_tau": 0.30,
        # [배포전 P0#④] 서빙 temperature 보정 — 미보정(T=1.0) 서빙은 OOD 과신으로 고등급(TS) 무음
        # 미탐 위험(model serving needs calibration, T≈3). lite-cloud(데모)는 이미 3.0인데 정작
        # 안전이 중요한 하드닝(GA) 프로파일이 누락돼 있던 드리프트를 정합. 모델 동봉 temperature.json
        # 이 있거나 재보정하면 .env(CLASSIFIER_TEMPERATURE)로 override.
        "classifier_temperature": 3.0,
        "agreement_gate_enabled": True,
        # metadata_floor: KL ICD 보안표시·접근범위 상향 게이트 ON. 실데이터 0 환경에선 모델보다
        # 메타데이터가 더 믿을 만하다 — security_marking이 예측보다 높으면 상향(FNR-safe·하향 안 함),
        # access_scope 제한인데 예측 낮으면 검수 라우팅. 입력 메타 부재 시 silent no-op(안전).
        # 발동·메타존재율은 lloydk_metadata_floor_applied_total / _classify_metadata_present_total로 가시화.
        "metadata_floor_enabled": True,
        # [번들 F] 원본 at-rest 암호화 ON — 영업비밀 폐쇄망에서 원본 평문 저장 금지(결정 정합:
        # 원본 EncryptingStorage AES-256-GCM). 운영(full)에선 STORAGE_ENCRYPTION_KEY 필수 —
        # 미설정 시 startup fail-fast(평문 저장 차단 = fail-secure). 다른 운영 시크릿과 동일 취급.
        "storage_encryption_enabled": True,
        # 하드닝 운영 — 위 3개 안전 게이트가 꺼지면 startup 차단(게이트ON=가시성ON).
        "require_safety_gates": True,
        # [obs] 실 임베더 필수 — HF 로드 실패 시 HashEmbedding 무음 폴백은 검색품질 급락(정확도
        # 저하)이라 폐쇄망 실서빙에선 거부. 명시적 hash 요청(dryrun)은 영향 없음(폴백만 차단).
        "require_real_embedder": True,
        # [obs] 실 분류기 필수 — 모델 dir 이 있는데 로드 실패로 rule-fallback-v0 무음 열화하면
        # startup fail-clear(require_real_embedder 형제). 미보정·미탐 위험 서빙을 조용히 띄우지 않음.
        "require_real_classifier": True,
        # [배포전 하드닝 P0#①] 수동 GA 활성도 사람서명 locked 평가 readiness 요구(미검증 모델
        # 무심코 GA 활성 차단; force=True로만 우회·감사됨). 자동활성 게이트와 별개 축.
        "deploy_gate_manual_require_locked_eval": True,
        # [P0#①-b] force 우회 시 사유 필수(미검증 GA 강제활성에 '왜'를 남김 — force 금지 아님).
        "manual_activate_force_requires_reason": True,
        # [P0#①-c] 최초 배포(baseline 없음) 절대 미탐 floor — baseline 비교가 불가능한 첫 모델이
        # 고등급 미탐율 >0.10 이면 deploy gate 거부(degenerate·fnr_high 존재만으로 통과하던 fail-open
        # 차단). 하드닝 GA는 첫 모델도 미탐 하한을 강제. 실데이터 human_review PR곡선으로 값 재확정 권장.
        "deploy_gate_first_deploy_fnr_high_max": 0.10,
        # 데모 콘솔·파괴적 purge 비활성(고객사 운영 — 시연 표면/물리삭제 엔드포인트 미노출).
        "demo_console_enabled": False,
        # [고객사 학습 결정 2026-07] 야간 증분 재학습 ON — 고객사 현장서 사람검수 교정을 기존
        # 학습셋에 섞어 CPU 풀 파인튜닝을 야간 배치로 무인 실행(cpu-incremental-retrain-measured).
        # enable_training(지재원 URGENT 자동재학습 경로)과 별개 축: 이 플래그는 nightly 스케줄만
        # 발화시키고 URGENT 드리프트 자동재학습(30분틱)은 켜지 않는다(죽음의 나선 보수). 재학습본은
        # 후보로만 등록되고 자동 서빙 안 됨 — 활성화는 여전히 사람서명 locked-eval/감사 force 필요.
        "enable_incremental_retrain": True,
        # [고객사 관리자 콘솔] 거버넌스 콘솔(검수→재학습→활성화) 서빙 ON. demo_console_enabled와
        # 분리 — 파괴적 purge/데모 SPA는 계속 OFF(위), 관리 UI만 노출. 모든 쓰기는 RBAC 보호.
        "serve_admin_console": True,
    },
    "full-train": {
        "llm_provider": "anthropic",
        "embedding_provider": "hf",
        # reranker: 실측 recall 무개선·CPU 지연 5배 → 폐쇄망 기본 noop (필요시 프로파일서 명시적 bge)
        "reranker_provider": "noop",
        "vector_backend": "pg",
        # MinIO 미사용 결정 정합(번들도 minio 제거). 학습 산출물·문서는 로컬FS.
        "storage_backend": "local",
        "enable_training": True,
        "poc_mode": "full",
        # FNR-safe 서빙 운영점 ON (onprem-local과 동일 — 위 주석 참조).
        "classifier_escalation_tau": 0.30,
        # [배포전 P0#④] 서빙 temperature 보정 T≈3 (onprem-local과 동일 — 미보정 T=1.0 OOD 과신 방지).
        "classifier_temperature": 3.0,
        "agreement_gate_enabled": True,
        # metadata_floor ON (onprem-local과 동일 — 위 주석 참조).
        "metadata_floor_enabled": True,
        # [번들 F] 원본 at-rest 암호화 ON (onprem-local과 동일 — 위 주석 참조).
        "storage_encryption_enabled": True,
        # 하드닝 운영 — 위 3개 안전 게이트가 꺼지면 startup 차단(onprem-local과 동일).
        "require_safety_gates": True,
        # [obs] 실 임베더 필수(onprem-local과 동일) — hash 무음 폴백 거부.
        "require_real_embedder": True,
        # [obs] 실 분류기 필수(onprem-local과 동일) — 모델 dir 로드 실패 시 rule-fallback 무음 열화 거부.
        "require_real_classifier": True,
        # [배포전 하드닝 P0#①] 수동 GA 활성 locked-eval 요구(onprem-local과 동일).
        "deploy_gate_manual_require_locked_eval": True,
        # [P0#①-b] force 우회 시 사유 필수(onprem-local과 동일).
        "manual_activate_force_requires_reason": True,
        # [P0#①-c] 최초 배포 절대 미탐 floor 0.10 (onprem-local과 동일 — 첫 모델 fail-open 차단).
        "deploy_gate_first_deploy_fnr_high_max": 0.10,
        # 데모 콘솔·파괴적 purge 비활성(모델공장 내부 — 시연/물리삭제 표면 미노출).
        "demo_console_enabled": False,
        # [지재원 관리자 콘솔] 거버넌스 콘솔 서빙 ON(모델공장 운영자용). demo purge/데모 SPA는
        # 계속 OFF(위) — 관리 UI만. 지재원은 enable_training 경로로 학습하므로 야간 증분 플래그는 불필요.
        "serve_admin_console": True,
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

    # [고객사 야간 증분 재학습] enable_training과 별개 축. onprem-local(고객사)에서 True 로,
    # 야간 beat 스케줄(nightly_incremental_retrain_tick)이 교정-병합 학습셋으로 재학습을 발화
    # 시킨다. enable_training 이 여는 URGENT 드리프트 자동재학습(active_learning 30분틱)은 여전히
    # enable_training 노드(지재원)에서만 — 이 플래그는 통제된 야간 배치만 켜 죽음의 나선을 보수한다.
    # 학습 라우터(/train) 등록·train_classifier_task 실행은 (enable_training OR 이 플래그)로 허용.
    # 재학습본은 후보 등록만 — 자동 서빙 안 됨(활성화는 사람서명 locked-eval/감사 force 불변).
    enable_incremental_retrain: bool = False

    # 데모 콘솔(/demo SPA + POST /admin/demo/purge 물리삭제) 노출 여부. 데모/파일럿(lite-*)만 True,
    # 하드닝 프로파일(onprem-local·full-train)은 프로파일 default False 로 꺼 파괴적 초기화 엔드포인트를
    # 고객사 운영에서 비활성화한다. 이 필드가 미선언이던 탓에 admin.py 의 getattr(...,True) 게이트가
    # 항상 True(죽은 no-op)였던 것을 실배선 — 이제 하드닝 프로파일에서 /demo 미마운트·purge 404.
    demo_console_enabled: bool = True

    # [관리자 콘솔 서빙] 거버넌스 콘솔(admin.html — 검수→재학습→활성화 폐곡선 UI) 정적 서빙 여부.
    # demo_console_enabled(파괴적 purge + 데모 SPA)와 분리된 축 — 프로덕션(onprem-local·full-train)은
    # purge 는 계속 OFF 로 두고 관리 UI 만 True 로 노출한다(파괴적 엔드포인트는 여전히 demo_console_
    # enabled 게이트로 404). 모든 상태변경(활성화·재학습·확정)은 RBAC 로 보호되므로 UI 노출은 안전.
    # /demo 마운트 조건 = demo_console_enabled OR serve_admin_console.
    serve_admin_console: bool = False

    # --- 인프라 ---
    # 디폴트는 localhost dev DB. 운영은 DATABASE_URL env 필수.
    database_url: str = "postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk"
    redis_url: str = "redis://localhost:6379/0"

    # 벡터 DB
    vector_backend: str = "pg"  # pg(기본, pgvector dense+ts_rank 하이브리드) | es(레거시) | inmemory
    es_url: str = "http://localhost:9200"
    es_username: str = ""
    es_password: str = ""

    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = ""
    minio_secret_key: str = ""  # J1: dev 디폴트 제거. .env 필수.
    minio_secure: bool = False
    minio_bucket_docs: str = "lloydk-docs"
    minio_bucket_models: str = "lloydk-models"
    minio_bucket_mlflow: str = "mlflow"

    # P2-C2: 일반화 storage backend — minio | seaweedfs | local
    # 폐쇄망=로컬FS(file://) 결정(2026-06-24)에 맞춰 base 기본값도 local.
    # 객체스토어(minio)가 필요한 dev compose는 STORAGE_BACKEND=minio를 명시 주입한다
    # (docker-compose.yml api/worker · .env). 4개 프로파일은 모두 storage_backend를 명시.
    storage_backend: str = "local"
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

    # [QW SSRF] webhook outbox 대상 URL 가드. 스킴(http/https 외)·loopback은 항상 거부(SSRF
    # 차단: file://·gopher://·127.0.0.1로 내부 서비스 타격). 사설IP(RFC1918/4193)는 폐쇄망에선
    # 정상 콜백 대상이라 기본 허용 — 외부망 배포에서만 True로 막는다(과차단 방지).
    outbox_block_private_ips: bool = False

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

    # [#14a] 골든 검수/서명 HTML(브라우저 네비게이션용 무인증 라우터)의 서명 URL 토큰 비밀키.
    # 설정(opt-in) 시 review.html/signoff.html GET 에 HMAC(job_id) 토큰(?t=)을 강제해 uuid4-only
    # capability-URL 의 full-text 노출을 닫는다(변경성 POST signoff 는 별도 require_role 유지).
    # 인증된 GET /golden/jobs/{id} 가 서명 URL 을 발급하고 콘솔이 이를 사용한다. 미설정이면 미강제
    # (기존 배포·브라우저 네비게이션·dev/test 무변경 — 업그레이드 시 북마크를 깨지 않는다).
    golden_html_url_secret: str = ""

    # NFR-SEC-01: 감사체인 HMAC 비밀키. 설정 시 audit_log hash chain을 HMAC-SHA256으로 링크해
    # 키 없는 과거 row 재작성(rewrite)을 차단(audit_chain._link_hex). 빈 값이면 레거시 sha256
    # 폴백(dev/test 비파괴). 운영(poc_mode=full)에서는 assert_production_credentials가 fail-fast.
    # env: AUDIT_CHAIN_SECRET(필드명) 또는 LLOYDK_AUDIT_CHAIN_SECRET(audit_chain._chain_secret
    # os.getenv 폴백 + secrets_manager 후보) — 둘 중 하나면 충분.
    audit_chain_secret: str = ""
    # [L-audit-noalarm] 고위험 변경성 액션(confirm/relabel/train/ingest/guide/upload/delete)의
    # audit 기록 실패 시 fail-closed(503으로 차단) 여부. 기본 False=비파괴(요청 진행+메트릭·경고만).
    # middleware._fail_closed_enabled가 이 필드 우선, 없으면 env LLOYDK_AUDIT_FAIL_CLOSED_HIGH_RISK
    # 폴백. (과거 이 필드가 Settings에 없어 문서화된 설정 토글이 no-op였고 env만 동작했다 — 실 필드화.)
    audit_fail_closed_high_risk: bool = False

    # --- DB 커넥션 풀 (운영 동시성) ---
    # 기본 5+10=15는 dev용. 운영은 동시 요청 수에 맞춰 DB_POOL_SIZE/DB_MAX_OVERFLOW 조정.
    db_pool_size: int = 5
    db_max_overflow: int = 10
    # DB 연결 타임아웃(초). PG 일시 불가 시 연결이 무한 대기하면 GradeRegistry·세션 등이
    # 워커 부팅/추론기 생성을 무한 블록한다 → 타임아웃 후 빠르게 예외 → 호출부 폴백.
    # PostgreSQL(libpq) connect_timeout으로 주입. 0 이하면 미적용. 운영은 3~10s 권장.
    db_connect_timeout: int = 5

    # --- Celery worker safety limits ---
    # [실측 2026-08-02] 종전 soft=300 은 대용량 문서 분류를 완주시키지 못했다.
    # 100페이지(180,000자 · 351청크)가 6 CPU 에서 312초 걸리는데 12초 차이로 잘려
    # `SoftTimeLimitExceeded` → status=partial 로 끝났다. 동기 경로는 이미 청크 상한으로
    # 막아 두었으므로(analyze_sync_max_chunks) 대용량은 이 비동기 경로가 유일한 처리 수단인데,
    # 그 경로마저 완주 못 하면 100페이지는 처리 방법이 없다.
    # 900/1200 은 100페이지(312초) 기준 약 3배 여유 — CPU 할당이 낮은 회원사(2코어면
    # 3배 느려 ~900초)에서도 완주하도록 잡은 값이다. 처리량은
    # scripts/probe_ingest_capacity.py 로 재고 환경에 맞춰 조정할 것.
    celery_result_expires: int = 24 * 60 * 60
    celery_task_soft_time_limit: int = 900
    celery_task_time_limit: int = 1200
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
    #   google        : Gemini 원격 (OpenAI 호환 endpoint 어댑터 구현됨 — adapters/llm/__init__.py)
    #   local_openai  : OpenAI 호환 endpoint (vLLM·Ollama·LM Studio·llama.cpp)
    #   vllm          : (alias) local_openai와 동일 — 하위호환
    #   ollama        : (alias) local_openai와 동일 — Ollama 기본 11434 endpoint
    llm_provider: str = "noop"
    llm_model: str = "claude-sonnet-4-6"
    # [QW] LLM 사용/비용 기록의 billing_phase 라벨. 기본 'development'(개발·검증 비용).
    # 운영 청구 분리가 필요하면 'production'으로 설정 — LLMUsageService.record가 모든 경로에서 사용.
    llm_billing_phase: str = "development"
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

    # --- ConsensusJudge (골든 게이트 self-consistency + Qwen 섀도) ---
    # 주 판정자=상용 택일(anthropic|gemini|openai), 미연동/민감문서면 airgap(Qwen 주판정).
    judge_primary_provider: str = "anthropic"
    judge_shadow_provider: str = "vllm"        # 섀도=배포급 Qwen3-14B (production_suspect 탐지)
    judge_shadow_enabled: bool = True
    judge_k_min: int = 3                        # self-consistency 최소 샘플(만장일치면 여기서 멈춤)
    judge_k_max: int = 5                        # 의견 갈릴 때 최대(adaptive-k)
    judge_temperature: float = 0.7             # 표 분산용(>0 필요)

    # --- Models ---
    classifier_base_model: str = "kakaobank/kf-deberta-base"
    # Phase 3 (5070 Ti 풀가동): 학습 가중치 디렉토리. 비어있으면 rule-fallback 유지.
    # .env: CLASSIFIER_MODEL_DIR=artifacts/classifier-1ep/v-ae3f5371 형식.
    classifier_model_dir: str = ""

    # 서빙 모델 선택 (C-ver 폐곡선 마감) — True면 ClassifyService가 **활성 ModelVersion**
    # (register_and_gate/rollback이 활성화한 버전)의 model_uri를 우선 로드하고, 없거나 로컬
    # 디렉토리가 아니면 classifier_model_dir(env)로 폴백한다. 이로써 activate/rollback이 실제
    # 서빙 모델을 바꾼다(기존엔 env만 읽어 DB 장부와 서빙이 분리됐음). 활성 버전이 없으면
    # 기존 동작(env) 그대로라 동작 보존. 모델 전환은 서비스 초기화(재기동) 시점에 반영.
    serving_prefer_active_model: bool = True

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

    # 룰-폴백 자동확정 최소 증거량 (Gate: sparse-evidence → 검수 라우팅).
    # rule 엔진 confidence는 top_score/total이라, 단일·저가중 키워드 1개가 한 등급에 전 질량을
    # 몰면 confidence=1.0이 된다 — '단 하나의 약한 매치'가 최고신뢰로 자동확정되는 미보정 경로.
    # 골든셋 100건 룰-폴백 실증: TS 문서 5건이 TS신호 0(절대점수 ev≤0.85)인데 S1/S2로
    # conf=1.0 자동확정돼 검수 게이트(conf<0.7)를 그대로 통과하는 silent FNR 확인(모델·LLM폴백이
    # 없는 폐쇄망 초기상태). 자동확정에 필요한 절대 룰 점수 하한 — 미만이면 'sparse-evidence'
    # 경고를 남겨 classify_service가 confidence와 무관하게 needs_review로 라우팅한다.
    # 0이면 비활성(동작 보존). 기본 0.9 = 골든셋에서 silent FNR 5→0, 검수부하 +18/100.
    # 정밀 튜닝은 운영 human_review 라벨 누적 후 PR곡선으로 조정.
    rule_fallback_min_evidence: float = 0.9

    # LLM 2차의견 게이트 (모델 경로 사각지대 방어). 기본 False = 동작 보존.
    # 학습모델은 보정(temperature)해도 일부 TS를 '확신에 찬 오답'으로 무인 미탐한다(룰·모델
    # 공통 사각지대). 골든셋 실증: 보정모델이 G50-TS-10/24를 conf로 S1 자동확정(검수 누락)인데,
    # 로컬 LLM(qwen3:14b/ollama)은 둘 다 TS(conf 0.95)로 정확히 잡았다. True면 **모델이 비-TS
    # 등급을 자동확정하려 할 때만** LLM 2차의견을 호출해, LLM이 더 높은(FNR-safe) 등급을 제시하면
    # 'llm-secondopinion' 경고로 needs_review 라우팅한다(자동 승급이 아니라 사람 확인). 검수만
    # 라우팅하므로 등급을 무인으로 바꾸지 않는다. 비용: 자동확정 비-TS 건당 LLM 1콜(폐쇄망
    # 온프렘 GPU 전제). LLM 미가용·오류는 silent 폴백(기존 자동확정 유지 — 게이트가 죽어도 안전).
    model_secondopinion_llm_enabled: bool = False

    # 등급차등 + 룰·모델 합의 자동확정 게이트 (Gate). 기본 False = 동작 보존(conf 단독).
    # conf 단독 자동확정은 신뢰성 부정됨(golden500: AUROC 0.58, 자동확정 정밀도 63%, 고등급
    # 미탐 46). True면 자동확정 조건을 좁힌다: 공개등급(S3) 예측은 conf 단독 허용(S3 conf
    # 정밀도 94%), 그 외(TS/S1/S2)는 룰엔진과 등급이 합의할 때만 자동확정·불일치면 needs_review.
    # 등급은 무인 변경 없이 검수 라우팅만. 측정(golden500): 자동확정 정밀도 63→81%, 미탐 46→8,
    # 단 자동확정 커버리지↓(검수↑) — '확신 있는 것만 자동확정'의 비용. 룰 산출 실패는 silent 폴백.
    agreement_gate_enabled: bool = False

    # 메타데이터 상향 floor 게이트 (KL ICD R6 — 보안표시·접근범위). 기본 False = 동작 보존.
    # 내용 분류기는 비밀관리성(M)·출처(S)를 못 봐서 인사·재무 비밀이 일상 내부문서(S2)로 보이는
    # 사각지대가 생긴다(golden500 S1→S2 미탐 7건). KL ICD가 주는 보안표시(security_marking)·
    # 접근범위(access_scope)로 그 축을 보완한다. True면: ① security_marking이 예측보다 높은 등급을
    # 가리키면 그 등급으로 *상향* floor(명시표기 우선, ICD §4.2 — 하향은 안 함, 안전하게 높은 쪽);
    # ② 표기 없고 access_scope가 제한적(approved_only/designated/department)인데 예측이 낮으면
    # 'metadata-access-conflict'로 needs_review 라우팅(ICD §4.4 — 관리수준 높은 문서를 낮게 자동확정
    # 안 함). 메타데이터 신뢰도에 의존하며 오류/부재는 silent 폴백(기존 동작 보존).
    metadata_floor_enabled: bool = False

    # [번들-안전게이트] 하드닝 운영(폐쇄망)에서 안전 게이트(agreement_gate·metadata_floor·
    # storage_encryption) 강제 여부. 기본 False(동작 보존·lite-cloud/dev tier). onprem-local·
    # full-train 프로파일이 True로 켠다. True인데 게이트가 하나라도 꺼져 있으면
    # assert_production_credentials가 startup을 차단(fail-clear) — 프로파일이 켜둔 안전장치를
    # .env override(=0)나 개별키 운영 누락으로 '게이트 OFF=무음' 상태로 띄우는 것을 막는다.
    require_safety_gates: bool = False
    # [obs] 실 임베더 필수 — HF 임베딩 로드 실패 시 HashEmbedding 무음 폴백(검색품질 급락=정확도
    # 저하)을 거부하고 명시적 실패(RuntimeError)한다. 기본 False(dryrun/오프라인·lite tier 동작 보존).
    # onprem-local·full-train 프로파일이 True로 켠다. 명시적 hash 요청(force_hash/embedding_model=
    # "hash")은 폴백이 아니므로 영향 없음 — build_embedder의 로드-실패 폴백 경로에서만 작동.
    require_real_embedder: bool = False
    # [obs] 실 분류기 필수 — CLASSIFIER_MODEL_DIR 이 실재 디렉토리로 해석됐는데 가중치 로드에
    # 실패(config.id2label 불일치·torch 부재·손상 등)하면 InferencePipeline 이 rule-fallback-v0 으로
    # *조용히* 넘어간다(pipeline.py `_run_rule_fallback`). config.assert_production_credentials 는 경로
    # '존재'만 보고 로드 성공(_model) 여부는 안 봐서 이 무음 열화가 startup 을 통과했다. True 면
    # warmup 에서 '모델 dir 해석됐는데 미로드' 를 fail-clear 로 차단(require_real_embedder 의 형제).
    # 모델 dir 미설정(rule-fallback 의도)은 대상 아님 — 로드-실패 폴백 경로에서만 작동.
    require_real_classifier: bool = False

    # 검증 라벨 내용해시(file_hash) 재사용 (기본 True). doc_id는 업로드마다 gen_random_uuid라
    # 유니크 → 같은 문서를 *재업로드*하면 새 doc_id가 되어, 기존 doc_id 기반 검증라벨 재사용
    # (_get_verified_label)이 안 먹혔다. 동일 내용(동일 sha256)은 등급이 같은 게 자명하므로,
    # doc_id 매칭 실패 시 같은 file_hash의 다른 문서에 사람이 검증한 라벨이 있으면
    # 그것을 적용한다(추론 스킵). '유사'가 아니라 '정확히 동일 바이트'만 매칭하므로 위험 없음
    # (다른 등급 전파 불가). False면 기존 doc_id-only 동작.
    # tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진) — 전역 file_hash 스코프.
    verified_label_content_reuse: bool = True

    # [Phase 2] 유사도 escalation 게이트 (등급 무변경·검수 라우팅만). 기본 False = 동작 보존(opt-in).
    # 들어온 문서가 *사람이 더 높은(더 비밀) 등급으로 검증한 문서와 매우 유사*(dense 코사인 ≥ τ)하면
    # needs_review로 라우팅한다 — exact-match override(_verified_label_by_content)의 '유사' 아날로그를
    # 등급 *override가 아니라 신호*로만 쓴다. 전파 0·poisoning 0·FNR-safe(이웃이 모델 예측보다 엄격히
    # 더 비밀일 때만 발동). corpus 의존: 사람검증 고등급 문서가 벡터스토어에 없으면 no-op(무실데이터
    # 단계엔 발동 0 → SIMILARITY_ESCALATION_TOTAL 카운터로 가시화). 임베딩/검색/DB 오류는 전부 silent
    # fail-open(분류 진행). _SAFETY_GATES·_PROFILE_DEFAULTS에 넣지 않음 — 강제 ON은 corpus 없으면
    # 무음 no-op이거나 startup 차단(require_safety_gates)이 되어 비파괴 원칙에 어긋남(검증 corpus 생긴 뒤 승격).
    similarity_escalation_enabled: bool = False
    # 유사도 escalation 임계 (dense 코사인 유사도 = 1-cosine_distance). 기본 0.92 = 고정밀(검수 폭증 방지).
    # 0<τ<1. 기존 classifier_escalation_tau(등급별 softmax argmax 보정)와 무관한 별개 노브(이름 구분).
    similarity_escalation_tau: float = 0.92

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

    # 고등급(TS/S1) 변경 2인검토 (C-cons, doc/36). 기본 False=단일검수자 즉시확정(동작 보존).
    # True면 고등급으로의 confirm/relabel은 **서로 다른 2인**이 같은 등급에 동의해야 확정되고,
    # 1인만 동의한 동안은 classification.status='needs_second_review'로 보류된다(편향·오염 방지).
    # 정식 정책(검토자 자격·시니어 사인오프 범위)은 발주처 협의로 확정 — 본 플래그는 그 메커니즘.
    high_grade_dual_review: bool = False
    high_grade_review_codes: list[str] = ["TS", "S1"]
    # 2인검토 정족수 계산 시 검수자 신뢰도(reviewer_trust) 하한. 0.0(기본)=신뢰도 무시(동작 보존).
    # >0이면 신뢰도가 이 값 미만인 검수자의 동의는 정족수에서 제외(이력 없는 신규 검수자는 신뢰).
    # 자주 번복되는 저신뢰 검수자만으론 고등급 변경이 확정되지 않게 — 시니어 사인오프 정책의 근거.
    high_grade_review_min_reliability: float = 0.0

    # [P1a] semantic 매칭 코사인 임계값 — 중앙 설정. 생성자 주입 > EMB_SEMANTIC_THRESHOLD env >
    # 이 값 > 0.75(하드 폴백) 순. 0.75가 env에만 흩어져 있던 것을 다른 룰 노브(아래
    # rule_high_risk_weight_multiplier)와 동일하게 settings로 끌어와 가시화·일관화한다(등급별 임계
    # 차등은 후속 — 실데이터 보정 필요). 0~1 범위.
    rule_semantic_threshold: float = 0.75
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
    # [NEW-M2] 최초 배포(baseline 없음) 절대 FNR floor — baseline 비교가 불가능한 첫 모델이
    # fnr_high 가 이 값을 초과하면 거부(미탐 하한). None(기본)=floor 미적용(동작 보존, 첫
    # 모델은 degenerate 아니면 통과). 실데이터 human_review PR곡선으로 값 확정 후 설정 권장.
    deploy_gate_first_deploy_fnr_high_max: float | None = None
    # [죽음의 나선 #4] deploy gate는 합성 test.jsonl에서 fnr_high를 재 실분포 오염에 맹목이다.
    # True(기본)면 **자동 활성(retrain_auto_activate)** 시 '실(locked_gold_eval) 평가' readiness
    # (등급별 min_locked_per_grade 충족)까지 추가로 요구한다 — 합성/legal_floor 평가만으로는
    # 오염 모델을 자동 승격하지 못한다. 무실데이터 단계엔 locked가 비어 자동활성 fail-closed.
    # False로 끄면 옛 동작(합성 평가만으로 자동 활성). 수동 활성은 이 게이트와 무관.
    deploy_gate_require_locked_eval: bool = True
    deploy_gate_min_locked_per_grade: int = 5
    # [배포전 하드닝 P0#①] 수동 활성(activate_model_manually / POST /admin/model/activate)에도
    # locked_gold_eval readiness를 요구할지. 기본 False = 현행 보존 — 수동 활성은 원래 '지재원
    # 릴리스의 정상 배포 경로'라 위 deploy_gate_require_locked_eval(자동활성 전용)와 무관했다.
    # 하드닝 프로파일(onprem-local·full-train)은 True: 사람서명 locked 평가 없이 미검증 모델을
    # GA로 무심코 활성하는 경로를 막는다(회귀 gate와 별개의 축). force=True로만 우회(감사됨) —
    # 파일럿 부트스트랩(locked 비어있음)은 명시적 force로 활성하며 그 사실이 forced=True로 감사에
    # 남는다. locked_gold_eval이 사람서명으로 채워지면 자동으로 통과(force 불요). readiness는 활성
    # 성패와 무관하게 항상 응답/로그에 노출(가시화). lite-*/dev/pilot=False(현행) 유지.
    deploy_gate_manual_require_locked_eval: bool = False
    # [배포전 하드닝 P0#①-b] 수동 활성에서 force=True 로 게이트(회귀·locked-eval)를 우회할 때
    # 사유(reason) 필수 여부. 기본 False = 현행 보존(force 만으로 우회). 하드닝 프로파일은 True:
    # 미검증 모델을 GA로 강제 활성하려면 '왜'를 남겨야 한다(force 자체는 여전히 허용 — 금지 아님,
    # 감사 강화). 게이트가 통과하면 force 는 no-op 이라 reason 도 불요(force+게이트실패 시에만 요구).
    manual_activate_force_requires_reason: bool = False
    # [번들 D] locked_gold_eval(사람서명 평가정답) 레코드 jsonl 경로 — 운영 검수 readiness 가시화용
    # (등급별 locked 보유/부족·배포가능 여부). 빈 값(기본)이면 readiness=no_locked_records(무실데이터
    # 단계의 진실). 파일이 쌓이면 GET /admin/locked-readiness·게이지가 자동으로 켜진다. 읽기 전용.
    locked_eval_jsonl: str = ""
    # [앵커 연결] 합성 test.jsonl만 보던 게이트에 **외부 사실 앵커 코퍼스**(NKT 공식분류·큐레이트
    # 홀드아웃) 실측 미탐을 주입한다. True면 후보 모델을 앵커로 통과시켜 고등급(TS/S1) under-class
    # 카드가 FAIL이면 게이트 hard block. 기본 False=동작 보존(앵커 측정은 CPU 추론 비용 발생).
    # INCONCLUSIVE는 깨지 않음(측정 불가 — 자동활성 veto는 위 locked-eval 게이트 소관).
    deploy_gate_anchor_eval_enabled: bool = False
    deploy_gate_anchor_cap_per_cell: int = 200   # 앵커 (출처×등급) 칸당 표본 상한(0=전수)
    # [P0#6] 메타모픽 순방향 회귀를 deploy gate hard-signal로 연결(앵커와 상보 — 문체만 바꿔도
    # 등급↓=미탐). 경로에 build_metamorphic_report.to_dict JSON을 두면 게이트가 소비(후보 대상으로
    # 재생성 권장 — gen_metamorphic_pairs 파이프라인). 미설정(빈값)=검사 생략(fail-open, 동작 보존).
    deploy_gate_metamorphic_report_path: str = ""
    deploy_gate_metamorphic_forward_ceiling: float = 0.0  # 순방향 위반율 CI하한 초과 시 hard block
    deploy_gate_metamorphic_min_n: int = 5                # 순방향 쌍 표본 하한(미만=측정 불가)
    # [P0#6 강제 옵트인] 외부-사실 검사(앵커/메타모픽)가 '미제공=생략(fail-open)'이면 리포트 생성이
    # 조용히 깨져도 게이트가 통과한다. True면 *설정된* 검사의 리포트 부재를 hard block으로 승격
    # (fail-closed) — 앵커/메타모픽 코퍼스가 준비된 운영단계에서 켜면 외부-사실 신호의 무음 소실이
    # 배포를 막는다. 기본 False=동작 보존(합성-only 무앵커 단계엔 리포트가 없으므로 강제하면 정당한
    # 배포를 깨뜨림 — 순수 운영 opt-in). 검사를 켜지 않은(anchor_enabled=False·report_path 미설정)
    # 항목은 이 플래그로도 강제되지 않는다(한쪽만 쓰는 운영을 과차단하지 않음).
    deploy_gate_require_external_fact_eval: bool = False
    # Kill-gate (죽음의 나선/품질붕괴 모니터, 비파괴=경보만). 발동 시 metric+log만, 자동 중단 없음.
    # floor/capacity/max=0이면 해당 조건 비활성. capacity는 운영 검수 처리능력으로 설정 권장.
    kill_gate_high_grade_miss_floor: int = 1     # 고등급(TS/S1) 미탐 교정 ≥ 이 값이면 발동(1=any)
    kill_gate_daily_review_capacity: int = 0     # 일 검수 처리능력(0=fatigue 비활성)
    kill_gate_fatigue_multiplier: float = 1.5    # 일 교정 ≥ capacity×배수면 과부하 발동
    kill_gate_overturn_rate_max: float = 0.30    # 라벨변경 교정 비율 ≥ 이 값이면 발동(0=비활성)
    # [번들 E] kill-gate를 '경보'에서 'admin-actuated 안전브레이크'로. 기본 False=동작 보존(경보만).
    # True면 kill-gate tripped 동안 **고등급(TS/S1) 자동확정을 needs_review로 억제**한다(등급 무변경,
    # FNR-safe). 합성 평가신호로 운영을 자동정지(쿼리 차단)하는 게 아니라, '확신 있는 고등급도 사람이
    # 한 번 더 본다'로 보수화 — 조건이 풀리면(gauge→0) 자동 해제. 자동 롤백/재학습 정지와 별개.
    # 억제 발동은 lloydk_kill_gate_autoconfirm_suppressed_total로 가시화. 캐시된 tripped 상태만 읽어
    # 핫패스에 DB 부하 없음(주기 refresh가 run_kill_gate_check로 갱신).
    kill_gate_suppress_autoconfirm: bool = False
    # 교정→라벨 재빌드/병합 학습셋 출력 디렉토리(A2-①).
    retrain_dataset_dir: str = "datasets/demo_retrain"

    # 자동 롤백 (C-ver 고도화) — 활성 모델이 운영에서 미탐 회귀를 보이면 직전 활성으로 복귀.
    # 기본 False=수동 롤백만(동작 보존). True면 auto_rollback_tick이 라이브 FNR을 등록 baseline과
    # 비교해 tolerance 초과 회귀 시 rollback_active_model 호출. 표본이 min 미만이면 판단 보류(무동작).
    auto_rollback_enabled: bool = False
    rollback_fnr_high_tolerance: float = 0.10   # 라이브 fnr_high ≤ baseline + 이 값이어야 유지(배포 게이트보다 느슨)
    rollback_min_samples: int = 20              # 라이브 표본이 이 미만이면 롤백 판단 보류

    embedding_model: str = "nlpai-lab/KURE-v1"

    # 임베딩 어댑터 선택:
    #   hash : hash_embedding (결정론, GPU·다운로드 불필요, lite-noapi 기본)
    #   hf   : hf_embedding (sentence-transformers, KURE/BGE 로컬 캐시, full/onprem 기본)
    embedding_provider: str = "hash"

    # --- Reranker (A1) ---
    # noop  : 입력 순서 유지 (기본, 운영 외)
    # bge   : BAAI/bge-reranker-v2-m3 (FlagEmbedding 또는 sentence_transformers)
    reranker_provider: str = "noop"

    # --- 청크/처리 ---
    max_seq_len: int = 512
    chunk_size: int = 512
    chunk_overlap: int = 64

    # 표적 6 (2026-05-29): PreprocessPipeline의 PII 마스킹 자동 적용 여부.
    # 보안 민감 도메인이라 기본 True. dryrun/테스트에서 비활성하려면 LLOYDK_PII_MASKING_ENABLED=0.
    pii_masking_enabled: bool = True
    # [#pii-skew] 분류기 *입력*에도 PII 마스킹을 적용할지. 기본 False(무마스킹).
    # 근거: 내부 IP·사번·계좌/부품번호는 문서를 S1/S2로 만드는 등급 신호인데 [IP]/[EMP]로 가리면
    # 분류기가 근거를 잃는다. 게다가 학습·평가(m4/m6)는 무마스킹이라 서빙만 마스킹하면 모델이 못 본
    # OOD 브라켓 토큰으로 등급이 낮아지는(silent FNR) train/serve 스큐가 생긴다(보고 F1/FNR은 전부
    # 무마스킹 기준). → 분류기 입력은 무마스킹이 정답(학습·평가 분포와 정합, 재학습 불요). 저장·색인·
    # 표시용 마스킹은 pii_masking_enabled(_finalize/run_file 경로)에서 그대로 유지 — PII 보호는 그
    # 노출 경계에서. True로 켜면 (고신뢰 마스킹으로 학습·평가도 마스킹한 모델 배포 시) 정합 가능.
    pii_mask_classifier_input: bool = False

    # [P0#3] 저품질/OCR 추출 검수 라우팅 — 추출 품질이 낮거나 OCR/추출오류인 문서를 자동분류
    # 그대로 통과시키지 않고 processing_status='needs_review'로 격리(무음 오분류 방지). 깨끗한
    # 텍스트와 OCR/스캔본이 동일 입력으로 분류기에 진입해 표 누락·깨진 문자가 조용히 오분류되던 것을 차단.
    extraction_review_min_quality: float = 0.6  # 정규화 품질(0~1) 이 미만이면 검수 라우팅
    extraction_ocr_requires_review: bool = True  # OCR 사용 추출은 검수 라우팅(스캔본 신뢰 낮음)
    # HWP/HWPX 표 셀 미추출(rhwp의 조용한 표 흘림) 의심 문서를 검수 라우팅. 본문만 분류기에
    # 들어가 표 속 등급·원가·임원보상 같은 영업비밀이 미탐되는 것을 차단(등급 불변·FNR-safe).
    # 기본 ON. 표 다수 HWP 고객사에서 과라우팅 시 0으로 끄거나 [hwp-tables]로 표를 회수.
    extraction_table_coverage_review: bool = True
    # docx/pptx/xlsx의 차트·도형·임베디드 OLE·미디어(OCR 불가/미설치)처럼 추출본에서 조용히
    # 빠진 콘텐츠를 추출기가 warning(*_not_extracted·*_not_ocrd·*_may_be_missing·media_ocr_*)
    # 으로만 남기던 것을, 검수 게이트가 소비해 needs_review로 라우팅(등급 불변·FNR-safe). 표(HWP)
    # 커버리지와 동일 취지를 OOXML 손실 신호로 확장. 기본 ON. 미디어 다수 문서에서 과라우팅 시 0.
    extraction_content_loss_review: bool = True

    # 표적 1 (2026-05-29): InferencePipeline use_rag 활성 시 retrieval facade 호출 기본값.
    rag_default_collection: str = "docs"
    rag_default_top_k: int = 5
    rag_query_expansion_method: str = "rule"  # rule | llm | hybrid
    rag_index_chunk_size: int = 1200
    rag_index_chunk_overlap: int = 100
    rag_operational_embedding_model: str = "nlpai-lab/KURE-v1"
    rag_operational_search_mode: str = "hybrid"

    # RAG /answer 인용 강제(citation enforcement). 기본 False = 동작 보존(경고만).
    # synthesize_answer는 LLM 답변의 [n] 인용을 hits 범위와 대조해 환각 인용
    # (존재하지 않는 근거 번호 인용 = out-of-range)을 warnings의 'hallucination_risk:<n>'
    # 마커로 관측만 한다. True면 그 경고를 집행(enforce)으로 승격한다: out-of-range 인용이
    # 1건이라도 있으면 해당 LLM 답변을 폐기하고 결정론적(deterministic) 답변으로 강등해
    # 환각 인용이 사용자에게 노출되는 것을 차단한다(citations·grade는 보존). 기본 False라
    # 현 동작 불변 — 인용 품질을 강하게 보장해야 하는 배포에서만 .env로 opt-in한다.
    # 이전엔 settings에 미정의라 미문서화 env(getattr 폴백)로만 동작했다(정식화).
    answer_enforce_citations: bool = False

    # --- 업로드 한도 (DoS 차단) ---
    # R3: guide upload·classify content 본문 크기 한도. 환경변수 LLOYDK_MAX_UPLOAD_MB로 조정.
    # 운영 기본 20MB — 대부분 가이드 PDF·DOCX 커버. 초과 시 413 Payload Too Large 반환.
    max_upload_mb: int = 20

    # #13: JSON 본문 크기 DoS 가드 — 일반 JSON body(/classify/batch 등)의 상한.
    # 멀티파트 업로드는 위 max_upload_mb로 막히지만, JSON body는 Pydantic 파싱 후에야
    # 413이 떠서 인증된 호출자가 대용량 body(예: 1000×1MB)로 OOM을 유발할 수 있다.
    # app.py의 미들웨어가 **파싱 전에** Content-Length(또는 스트리밍 누적 크기)를 검사해
    # 이 한도를 넘으면 413을 반환한다. 업로드 멀티파트보다 충분히 크게 잡아(기본 25MB)
    # 정상 JSON 요청·dryrun/테스트는 통과하게 한다. 환경변수 LLOYDK_MAX_REQUEST_BODY_MB로 조정.
    max_request_body_mb: int = 25

    # --- 동기 분석 경로 청크 상한 (워커 타임아웃 차단) ---
    # [실측 2026-08-02] /documents/analyze 는 업로드→추출→분류를 한 요청 안에서 끝낸다.
    # 바이트 상한(max_upload_mb)만으로는 못 막는다 — 문제가 된 .hwp 는 304KB 였는데
    # 표 47개를 회수하면 본문이 46,473자·청크 155개가 되어 분류에서 60초를 넘겼다.
    # gunicorn --timeout 60 이 워커를 SIGABRT 로 죽이고, 클라이언트는 아무 설명 없이
    # 빈 응답(curl: (52) Empty reply from server)을 받는다. --workers 1 이라 그동안
    # 다른 요청도 함께 끊긴다. 비용 동인은 업로드 크기가 아니라 **청크 수**다.
    #
    # 기본값 산정 — 6 CPU(현 기본) 테스트서버 실측 2026-08-02:
    #     4청크 4.3s · 18청크 16.1s · 36청크 32.4s · 88청크 79.8s · 351청크 320.3s
    #     → 고정비 ≈2.9s + 0.82s/청크 (CPU 에 거의 정확히 비례. 2 CPU 대비 2.8~3.0배)
    # 60초 예산의 절반(30초)을 안전선으로 두면 (30-2.9)/0.82 ≈ 33청크. 여유를 둬 34.
    # 절반을 쓰는 이유: 워커가 1개라(WEB_CONCURRENCY=1) 동시 요청이 서로를 밀어낸다 —
    # 단독 실행 기준으로 상한을 잡으면 두 건만 겹쳐도 타임아웃이 난다.
    # ≈9페이지. CPU 한도를 바꾸면 scripts/probe_ingest_capacity.py 로 재고 이 값도 조정할 것.
    # 0 이하면 검사 비활성(측정·디버깅용).
    analyze_sync_max_chunks: int = 34

    # OCR DoS 가드 — 스캔 PDF 한 건을 OCR할 때 변환·인식할 최대 페이지 수.
    # 수백쪽 스캔본 한 건이 pdf2image/Tesseract를 수십분~OOM으로 모는 것을 차단.
    # 초과 페이지는 변환하지 않고 '잘림' 경고를 남긴다. 0 이하면 무제한(명시적 opt-out).
    # 보수적 기본 50쪽 — 대부분의 정상 문서를 커버하면서 악성·사고성 대용량은 차단.
    ocr_max_pages: int = 50

    # --- 동작 모드 ---
    # dryrun: 무거운 모델 다운로드 없이 mock으로 검증
    # full: 실제 모델 로드 (GPU/대용량 필요)
    poc_mode: str = "dryrun"

    # ── #31 검증 로직 ─────────────────────────────────────────────────────────
    # 배경: 이전엔 field_validator/Field 제약이 전무해 위험한 값(임계 범위밖, 음수 풀크기,
    # soft>hard 타임리밋, chunk_overlap>=chunk_size, auth_mode/provider 오타 등)이 조용히
    # 통과해 런타임에서야 터졌다. pydantic v2 validator로 부팅 시점 fail-fast 한다.
    # 비파괴 원칙: 모든 기존 기본값·.env·conftest 세팅값·프로파일 적용 결과는 통과해야 한다.

    # 1) 비율/임계 — [0,1] 범위여야 하는 필드들.
    @field_validator(
        "review_confidence_threshold",
        "retrain_fnr_high_tolerance",
        "retrain_f1_drop_tolerance",
        "rollback_fnr_high_tolerance",
        "high_grade_review_min_reliability",
        "classifier_temperature",  # T>0 이지만 통상 (0,~]; 아래서 양수만 확인 → 여기선 제외 불가, 별도 처리
    )
    @classmethod
    def _check_unit_interval(cls, v: float, info) -> float:  # noqa: ANN001
        # classifier_temperature는 0~1이 아니라 '양수'만 요구(1.0 기본, 1.3 등 >1 정상).
        if info.field_name == "classifier_temperature":
            if v <= 0:
                raise ValueError(f"{info.field_name}는 0보다 커야 합니다 (got {v}).")
            return v
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"{info.field_name}는 0~1 범위여야 합니다 (got {v}).")
        return v

    # escalation τ: None(순수 argmax) 허용, 값이면 0<τ<1 (양 끝 배제 — 0/1은 의미 없음).
    @field_validator("classifier_escalation_tau")
    @classmethod
    def _check_escalation_tau(cls, v: float | None) -> float | None:
        if v is None:
            return v
        if not (0.0 < v < 1.0):
            raise ValueError(f"classifier_escalation_tau는 None이거나 0<τ<1 이어야 합니다 (got {v}).")
        return v

    # 유사도 escalation τ: 0<τ<1 (양 끝 배제 — 1.0=exact·0.0=무의미은 degenerate).
    @field_validator("similarity_escalation_tau")
    @classmethod
    def _check_similarity_escalation_tau(cls, v: float) -> float:
        if not (0.0 < v < 1.0):
            raise ValueError(f"similarity_escalation_tau는 0<τ<1 이어야 합니다 (got {v}).")
        return v

    # 2) 양수/음수 제약 — 풀·업로드·청크·시퀀스·타임리밋 등.
    @field_validator("db_pool_size", "max_upload_mb", "max_request_body_mb", "chunk_size", "max_seq_len")
    @classmethod
    def _check_ge_one(cls, v: int, info) -> int:  # noqa: ANN001
        if v < 1:
            raise ValueError(f"{info.field_name}는 1 이상이어야 합니다 (got {v}).")
        return v

    @field_validator("db_max_overflow", "ocr_max_pages")
    @classmethod
    def _check_ge_zero(cls, v: int, info) -> int:  # noqa: ANN001
        # ocr_max_pages는 0 이하=무제한(명시적 opt-out)이라 음수도 의미상 허용되지만,
        # 혼란을 줄이기 위해 음수는 금지(0=무제한으로 통일).
        if v < 0:
            raise ValueError(f"{info.field_name}는 0 이상이어야 합니다 (got {v}).")
        return v

    @field_validator(
        "celery_task_soft_time_limit",
        "celery_task_time_limit",
        "celery_result_expires",
        "celery_worker_max_tasks_per_child",
    )
    @classmethod
    def _check_positive(cls, v: int, info) -> int:  # noqa: ANN001
        if v <= 0:
            raise ValueError(f"{info.field_name}는 양수여야 합니다 (got {v}).")
        return v

    @field_validator("rag_default_top_k")
    @classmethod
    def _check_top_k(cls, v: int, info) -> int:  # noqa: ANN001
        if v < 1:
            raise ValueError(f"{info.field_name}는 1 이상이어야 합니다 (got {v}).")
        return v

    @field_validator("rag_index_chunk_size")
    @classmethod
    def _check_rag_chunk_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"rag_index_chunk_size는 1 이상이어야 합니다 (got {v}).")
        return v

    @field_validator("rag_index_chunk_overlap", "chunk_overlap", "rollback_min_samples")
    @classmethod
    def _check_nonneg_int(cls, v: int, info) -> int:  # noqa: ANN001
        if v < 0:
            raise ValueError(f"{info.field_name}는 0 이상이어야 합니다 (got {v}).")
        return v

    # 4) 열거값 — 알려진 집합 검증(오타 fail-fast). 하위호환 alias는 모두 포함했다.
    #    case-insensitive 비교(.lower()) — Settings.model_config의 case_sensitive=False 와 정합.
    #    deploy_profile은 의도적으로 검증하지 않는다: apply_profile_defaults가 unknown을
    #    '경고 후 skip'으로 처리(test_unknown_profile_is_skipped가 weird-mode를 통과시킴).
    @field_validator("auth_mode")
    @classmethod
    def _check_auth_mode(cls, v: str) -> str:
        if v.lower() not in _VALID_AUTH_MODE:
            raise ValueError(f"auth_mode는 {sorted(_VALID_AUTH_MODE)} 중 하나여야 합니다 (got {v!r}).")
        return v

    @field_validator("llm_provider")
    @classmethod
    def _check_llm_provider(cls, v: str) -> str:
        if v.lower() not in _VALID_LLM_PROVIDER:
            raise ValueError(f"llm_provider는 {sorted(_VALID_LLM_PROVIDER)} 중 하나여야 합니다 (got {v!r}).")
        return v

    @field_validator("embedding_provider")
    @classmethod
    def _check_embedding_provider(cls, v: str) -> str:
        if v.lower() not in _VALID_EMBEDDING_PROVIDER:
            raise ValueError(
                f"embedding_provider는 {sorted(_VALID_EMBEDDING_PROVIDER)} 중 하나여야 합니다 (got {v!r})."
            )
        return v

    @field_validator("reranker_provider")
    @classmethod
    def _check_reranker_provider(cls, v: str) -> str:
        if v.lower() not in _VALID_RERANKER_PROVIDER:
            raise ValueError(
                f"reranker_provider는 {sorted(_VALID_RERANKER_PROVIDER)} 중 하나여야 합니다 (got {v!r})."
            )
        return v

    @field_validator("vector_backend")
    @classmethod
    def _check_vector_backend(cls, v: str) -> str:
        if v.lower() not in _VALID_VECTOR_BACKEND:
            raise ValueError(f"vector_backend는 {sorted(_VALID_VECTOR_BACKEND)} 중 하나여야 합니다 (got {v!r}).")
        return v

    @field_validator("storage_backend")
    @classmethod
    def _check_storage_backend(cls, v: str) -> str:
        if v.lower() not in _VALID_STORAGE_BACKEND:
            raise ValueError(f"storage_backend는 {sorted(_VALID_STORAGE_BACKEND)} 중 하나여야 합니다 (got {v!r}).")
        return v

    @field_validator("poc_mode")
    @classmethod
    def _check_poc_mode(cls, v: str) -> str:
        if v.lower() not in _VALID_POC_MODE:
            raise ValueError(f"poc_mode는 {sorted(_VALID_POC_MODE)} 중 하나여야 합니다 (got {v!r}).")
        return v

    @field_validator("source_prior_cap_grade")
    @classmethod
    def _check_source_prior_cap_grade(cls, v: str) -> str:
        if v.upper() not in _VALID_SOURCE_PRIOR_CAP_GRADE:
            raise ValueError(
                f"source_prior_cap_grade는 {sorted(_VALID_SOURCE_PRIOR_CAP_GRADE)} 중 하나여야 합니다 (got {v!r})."
            )
        return v

    # 3) 상호관계 — 한 필드만으로 판단 불가한 제약(model_validator, mode='after').
    #    너무 엄격하면 프로파일 적용·부분 .env를 깨므로 '명백한 모순'만 잡는다.
    @model_validator(mode="after")
    def _check_cross_field(self) -> Settings:
        # celery: soft(우아한 종료 시도) < hard(강제 kill). soft>=hard면 soft가 무의미.
        if self.celery_task_soft_time_limit >= self.celery_task_time_limit:
            raise ValueError(
                "celery_task_soft_time_limit < celery_task_time_limit 이어야 합니다 "
                f"(soft={self.celery_task_soft_time_limit}, hard={self.celery_task_time_limit})."
            )
        # 청크: overlap >= size면 슬라이딩 윈도가 전진하지 못함(무한·중복).
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError(
                f"chunk_overlap < chunk_size 이어야 합니다 (overlap={self.chunk_overlap}, size={self.chunk_size})."
            )
        if self.rag_index_chunk_overlap >= self.rag_index_chunk_size:
            raise ValueError(
                "rag_index_chunk_overlap < rag_index_chunk_size 이어야 합니다 "
                f"(overlap={self.rag_index_chunk_overlap}, size={self.rag_index_chunk_size})."
            )
        return self


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


# 안전 게이트(룰·모델 합의·메타데이터 floor·원본 at-rest 암호화·비공지성 source-prior) —
# 하드닝 운영(폐쇄망)에서 필수. .env override(=0)로 조용히 꺼진 채 startup 성공하는 것을 막는다.
# source_prior: 공개출처(판례·공시 등) 문서를 S3로 cap하는 비공지성 게이트. corpus 비의존이라
# 강제해도 무음 no-op 위험이 없고(기본 True), 하드닝 배포가 이 게이트를 조용히 끄면 공개출처가
# 비밀로 과분류(실측 85% FPR)되므로 강제집합에 포함. (similarity_escalation은 corpus 의존이라 제외.)
# require_real_embedder: HF 임베더 로드 실패 시 HashEmbedding 무음 폴백(검색품질 급락) 거부.
# require_real_classifier: 분류기 모델 dir 로드 실패 시 rule-fallback-v0 무음 열화 거부(형제 게이트).
# deploy_gate_require_locked_eval: 자동활성 시 실(locked_gold_eval) 평가 readiness 요구(죽음의 나선 #4).
# 모두 하드닝 프로파일 기본 True — .env override(=0)로 조용히 되살리면 CI/startup 이 잡는다.
_SAFETY_GATES: tuple[tuple[str, str], ...] = (
    ("agreement_gate_enabled", "AGREEMENT_GATE_ENABLED"),
    ("metadata_floor_enabled", "METADATA_FLOOR_ENABLED"),
    ("storage_encryption_enabled", "STORAGE_ENCRYPTION_ENABLED"),
    ("source_prior_enabled", "SOURCE_PRIOR_ENABLED"),
    ("require_real_embedder", "REQUIRE_REAL_EMBEDDER"),
    ("require_real_classifier", "REQUIRE_REAL_CLASSIFIER"),
    ("deploy_gate_require_locked_eval", "DEPLOY_GATE_REQUIRE_LOCKED_EVAL"),
)


def evaluate_safety_gates(s: "Settings") -> dict:
    """안전 게이트 상태 평가 — 순수 함수(assert_production_credentials에서 호출, 테스트 용이).

    require_safety_gates=True(onprem-local·full-train)인데 게이트가 하나라도 꺼져 있으면
    must_raise=True. False(lite-cloud·dev·개별키)면 off만 보고 — 호출부가 가시화(warn)만 한다.
    게이트ON=가시성ON 원칙: 프로파일이 켜둔 안전장치가 .env override(=0)나 개별키 운영 누락으로
    조용히 꺼진 채 startup 성공하는 것을 막는다.

    Returns: {"off": [env명...], "required": bool, "must_raise": bool}
    """
    off = [env for attr, env in _SAFETY_GATES if not getattr(s, attr, False)]
    required = bool(getattr(s, "require_safety_gates", False))
    return {"off": off, "required": required, "must_raise": required and bool(off)}


def hardened_poc_mode_bypass(s: "Settings") -> bool:
    """하드닝(require_safety_gates=True)인데 poc_mode!=full 인 '무음 우회' 구성인지 — 순수 함수.

    하드닝 프로파일(onprem-local·full-train)은 기본 poc_mode=full 이지만, .env 에 POC_MODE=dryrun
    한 줄을 주면 apply_profile_defaults 가 이를 explicit override 로 보고 프로파일 기본값을 덮지 않아
    poc_mode 가 dryrun 으로 남는다. 그러면 assert_production_credentials 의 `poc_mode!=full` 조기
    return 으로 startup 시행부 전체(자격증명·안전게이트·CORS·감사·actor-role·암호화키)가 스킵된다
    = 하드닝을 켠 줄 알지만 실제로는 전부 꺼진 상태. True 면 assert 가 fail-clear 로 차단한다.
    """
    return bool(getattr(s, "require_safety_gates", False)) and getattr(s, "poc_mode", None) != "full"


def _assert_classifier_artifacts(model_dir: str) -> None:
    """CLASSIFIER_MODEL_DIR 이 지정됐을 때 필수 모델 산출물 유효성을 startup 에서 검증.

    존재 여부만이 아니라 내용물까지 본다: config.json(필수) + 가중치 1종(model.safetensors 또는
    pytorch_model.bin, sharded index 포함) 이 없으면 RuntimeError — 어떤 파일이 없는지 명시한다.
    손상/불완전 모델 dir 를 그대로 서빙 진입시키면 transformers from_pretrained 가 난해한 스택
    트레이스로 실패하거나(→require_real_classifier 가 warmup 에서 잡지만) rule-fallback 무음 열화로
    빠질 수 있어, 그 전에 명확히 차단한다(형제: require_real_classifier 는 '로드 성공'의 최종 백스톱).
    temperature.json 부재는 무보정(T=1.0) 서빙 위험이지만 치명적이진 않아 경고만(pipeline 도 loud-warn).
    """
    from pathlib import Path  # noqa: PLC0415

    p = Path(model_dir)
    if not p.exists():
        raise RuntimeError(
            f"CLASSIFIER_MODEL_DIR={model_dir!r} 경로가 존재하지 않습니다. "
            "운영 모드에서 모델 미로드 시 rule-fallback-v0으로 조용히 넘어가지 않습니다. "
            "경로를 수정하거나 CLASSIFIER_MODEL_DIR을 비워 rule-fallback 의도를 명시하세요."
        )
    missing: list[str] = []
    if not (p / "config.json").exists():
        missing.append("config.json")
    _weights = (
        "model.safetensors", "pytorch_model.bin",
        "model.safetensors.index.json", "pytorch_model.bin.index.json",
    )
    if not any((p / w).exists() for w in _weights):
        missing.append("모델 가중치(model.safetensors 또는 pytorch_model.bin)")
    if missing:
        raise RuntimeError(
            f"CLASSIFIER_MODEL_DIR={model_dir!r} 에 필수 모델 산출물 누락: {', '.join(missing)}. "
            "손상·불완전 모델 dir 의 rule-fallback 무음 열화를 막기 위해 startup 을 차단합니다. "
            "아티팩트를 확인하거나 CLASSIFIER_MODEL_DIR 을 비워 rule-fallback 의도를 명시하세요."
        )
    if not (p / "temperature.json").exists():
        logger.warning(
            "CLASSIFIER_MODEL_DIR=%s 에 temperature.json 없음 — 무보정(T=1.0) 서빙 위험(OOD 과신 → "
            "고등급 무음 미탐). CLASSIFIER_TEMPERATURE env 로 주입하거나 보정을 재적용하세요.",
            model_dir,
        )


def assert_production_credentials() -> None:
    """운영 모드에서 빈 자격증명 차단. dryrun/테스트는 우회.

    호출 위치: api/app.py startup hook. fail-fast로 운영 미설정 사고 방지.

    D1 (2026-05-29): env가 비었어도 secrets_manager(Vault/AWS SM)에서 채워올
    수 있도록 한 번 fill_from_secrets_manager()를 시도한 뒤 missing 판정.
    LLOYDK_SECRETS_BACKEND=env(기본)는 사실상 no-op이라 dev 동작에 영향 없음.
    """
    # pytest/TestClient 환경에서는 운영 자격증명 검사 skip (하드닝 프로파일을 구성한 단위테스트도
    # 통과하도록 poc_mode/우회 판정보다 먼저). conftest.py가 TESTING=1, pytest가 PYTEST_CURRENT_TEST 주입.
    _under_pytest = bool(os.environ.get("PYTEST_CURRENT_TEST"))
    _testing = os.environ.get("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}
    # [#13 defense-in-depth] 운영/하드닝 신호(poc_mode=full 또는 require_safety_gates)인데 pytest 밖에서
    # TESTING 이 켜져 있으면 = 운영에 테스트 env 가 오상속된 것(dev .env 재사용·베이스이미지 잔존 등).
    # 그대로 두면 아래 skip 이 전 startup 안전검사(자격증명·CORS·rate-limit·감사·암호화·JWT iss/aud)를
    # 통째로 무음 우회한다 → fail-clear 로 차단(hardened_poc_mode_bypass 와 대칭). pytest 실행 중
    # (PYTEST_CURRENT_TEST 존재)은 정상 단위테스트이므로 제외해 테스트를 깨지 않는다.
    if _testing and not _under_pytest and (
        settings.poc_mode == "full" or getattr(settings, "require_safety_gates", False)
    ):
        raise RuntimeError(
            "운영/하드닝 프로파일에 TESTING env 유입 감지 — TESTING=1 은 모든 startup 안전검사를 무음 "
            "우회합니다. .env·베이스이미지·오케스트레이터에서 TESTING(및 PYTEST_CURRENT_TEST)을 제거하세요. "
            "테스트가 목적이면 비하드닝 프로파일(lite-cloud/dev)·POC_MODE=dryrun 을 쓰세요."
        )
    if _testing or _under_pytest:
        return
    # [게이트ON=가시성ON / 무음 우회 차단] 하드닝 프로파일(require_safety_gates=True)인데 poc_mode!=full
    # 이면 아래 startup 시행부 전체가 조기 return 으로 스킵된다 — .env POC_MODE=dryrun 한 줄로 하드닝을
    # 무음 우회하는 구멍. 이건 poc_mode!=full 조기 return 보다 먼저 fail-clear 로 차단해야 한다.
    if hardened_poc_mode_bypass(settings):
        raise RuntimeError(
            f"하드닝 프로파일(require_safety_gates=True)인데 poc_mode={settings.poc_mode!r} 입니다. "
            "poc_mode≠full 이면 startup 안전검사(자격증명·안전게이트·CORS·감사·암호화키)가 전부 스킵되어 "
            "하드닝이 무음 우회됩니다. POC_MODE=full 로 두거나(권장), 하드닝이 불필요하면 비하드닝 "
            "프로파일(lite-cloud/dev) 또는 REQUIRE_SAFETY_GATES=0 을 명시하세요."
        )
    if settings.poc_mode != "full":
        return
    fill_from_secrets_manager()
    missing: list[str] = []
    if not settings.api_key:
        missing.append("LLOYDK_API_KEY")
    # 객체스토어 백엔드(minio/seaweedfs/s3)일 때만 시크릿 키 필수.
    # 폐쇄망 local(file://) 배포는 minio를 쓰지 않으므로 키를 요구하지 않는다
    # (이전엔 무조건 요구 → onprem-local[storage=local·poc_mode=full] 운영 startup이
    #  쓰지도 않는 minio 키 부재로 차단되던 버그).
    if settings.storage_backend in ("minio", "seaweedfs", "s3") and not settings.minio_secret_key:
        missing.append("LLOYDK_MINIO_SECRET_KEY")
    # NFR-SEC-01: 감사체인 HMAC 비밀키. 미설정 시 키 없는 sha256 폴백 → 과거 row 재작성 가능.
    # 실효 시크릿(필드 또는 LLOYDK_ env) 기준으로 판정 — 둘 다 비면 운영 startup 차단.
    if not (settings.audit_chain_secret or os.environ.get("LLOYDK_AUDIT_CHAIN_SECRET", "").strip()):
        missing.append("LLOYDK_AUDIT_CHAIN_SECRET")
    if missing:
        raise RuntimeError(
            f"production 모드인데 필수 자격증명 누락: {', '.join(missing)}. "
            ".env / 환경변수 / secrets_manager(Vault·AWS SM)로 설정 필요."
        )

    # [번들-안전게이트] 하드닝 운영(require_safety_gates=True)은 룰·모델 합의 게이트·메타데이터
    # 상향 floor·원본 at-rest 암호화가 모두 켜져 있어야 한다. 프로파일이 켜둔 값을 .env
    # override(=0)로 끄거나 개별키 운영에서 빠뜨리면 '게이트 OFF=무음'으로 startup이 성공한다
    # → 차단(fail-clear). require_safety_gates=False(lite-cloud·dev)면 무음 방지 가시화(warn)만.
    _sg = evaluate_safety_gates(settings)
    if _sg["must_raise"]:
        raise RuntimeError(
            "production 하드닝 모드(require_safety_gates=True)인데 안전 게이트가 꺼져 있습니다: "
            f"{', '.join(_sg['off'])}. 폐쇄망 운영(onprem-local/full-train)은 룰·모델 합의 게이트·"
            "메타데이터 상향 floor·원본 at-rest 암호화가 필수입니다. 해당 env를 1로 켜거나, "
            "의도된 비보안 배포면 REQUIRE_SAFETY_GATES=0 으로 명시하세요(권장하지 않음)."
        )
    if _sg["off"]:
        logger.warning(
            "production(full) 모드인데 안전 게이트 OFF: %s. 영업비밀 보안 운영이면 하드닝 "
            "프로파일(LLOYDK_DEPLOY_PROFILE=onprem-local|full-train) 또는 REQUIRE_SAFETY_GATES=1 "
            "로 강제하세요(클라우드 개발/데모 tier면 의도된 상태).",
            ", ".join(_sg["off"]),
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

    # [정합성] 멀티워커 운영에서 idempotency가 in-memory 폴백이면 워커 간 멱등성 붕괴 — fail-fast.
    from lloydk.services.idempotency import (  # noqa: PLC0415
        assert_multiworker_idempotency_safe,
    )
    assert_multiworker_idempotency_safe()

    # [정합성] 동일 사유 — 멀티워커에서 async job 저장소가 in-memory 폴백이면 워커 간 job 유실.
    from lloydk.services.job_store import (  # noqa: PLC0415
        assert_multiworker_jobstore_safe,
    )
    assert_multiworker_jobstore_safe()

    # [관측성] 멀티워커인데 PROMETHEUS_MULTIPROC_DIR 미설정 → 프로세스-로컬 카운터가 워커별로
    # 쪼개져 increase()/rate() 알람(특히 serving_gate_fail_open·verified_label_audit_skip 등 안전
    # 실패 카운터)이 스크랩 타이밍에 따라 신뢰 불가. job/idempotency 는 fail-fast 가드가 있으나
    # prometheus 는 대칭 가드가 없었다. 데이터 무결성이 아닌 관측성 열화라 fail-fast 대신 startup
    # 경고로 승격(근본해결은 PROMETHEUS_MULTIPROC_DIR + MultiProcessCollector). 기본 배포=1워커라 무해.
    try:
        _web_concurrency = int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    except ValueError:
        _web_concurrency = 1
    if _web_concurrency > 1 and not os.environ.get("PROMETHEUS_MULTIPROC_DIR", "").strip():
        import logging as _logging  # noqa: PLC0415
        _logging.getLogger(__name__).warning(
            "WEB_CONCURRENCY=%d(멀티워커)인데 PROMETHEUS_MULTIPROC_DIR 미설정 — Prometheus 카운터가 "
            "워커별로 쪼개져 increase()/rate() 알람(안전 실패 카운터 포함)이 신뢰 불가하다. "
            "멀티프로세스 aggregation 을 배선하거나 단일 워커(WEB_CONCURRENCY=1)로 운영하라.",
            _web_concurrency,
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

    # rule-fallback-v0 운영 차단 — 모델 디렉토리가 명시됐으면 존재+내용물(config.json·가중치) 검증.
    if settings.classifier_model_dir:
        _assert_classifier_artifacts(settings.classifier_model_dir)
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
        ("audit_chain_secret", "LLOYDK_AUDIT_CHAIN_SECRET"),
        ("anthropic_api_key", "ANTHROPIC_API_KEY"),
        ("openai_api_key", "OPENAI_API_KEY"),
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
