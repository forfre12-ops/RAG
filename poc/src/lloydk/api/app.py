import logging
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from lloydk.config import assert_production_credentials, settings
from lloydk.api._rbac import require_role
from lloydk.api._jwt_auth import require_auth
from fastapi import Depends
from lloydk.schemas.common import Error
from lloydk.api import classify as classify_api
from lloydk.api import classify_stream as classify_stream_api
from lloydk.api import explain as explain_api
from lloydk.api import health as health_api
from lloydk.api import confirm as confirm_api
from lloydk.api import promotion as promotion_api
from lloydk.api import training as training_api
from lloydk.api import synthesis as synthesis_api
from lloydk.api import golden as golden_api
from lloydk.api import guide as guide_api
from lloydk.api import documents as documents_api
from lloydk.api import schema_admin as schema_admin_api
from lloydk.api import metrics as metrics_api
from lloydk.api import async_classify as async_classify_api
from lloydk.api import answer as answer_api
from lloydk.api import prom_metrics as prom_metrics_api
from lloydk.api import admin as admin_api
from lloydk.api.middleware import AuditMiddleware
from lloydk.api._idempotency_mw import IdempotencyMiddleware
from lloydk.api.prom_metrics import PrometheusMiddleware
from lloydk.api.rate_limit import limiter, rate_limit_exceeded_handler

logger = logging.getLogger(__name__)


# M-schema-perm (Track B): /schema/grades 의 GET/PUT 권한 분리.
#
# GET(읽기, 등급체계 조회)은 broad 역할(admin/reviewer/system/kl_backend)에 허용한다.
# PUT(전역 등급체계 추가/비활성 + 재학습 트리거 = 시스템 전역 파괴적 쓰기)는 admin 전용으로
# 좁힌다. 'system'은 공유 api_key의 기본역할이라, 이를 허용하면 사실상 무권한으로
# 전역 등급체계를 바꿀 수 있어 위험하다.
#
# 라우터 모듈(schema_admin.py)을 건드리지 않고 app.py 한 곳에서 메서드별로 분기하기 위해
# 메서드-aware 의존성 하나를 라우터-include 수준에 부착한다. 변경 actor는 AuditMiddleware가
# request.state.auth_*(위조 불가)로 이미 audit_log에 남긴다.
_SCHEMA_GRADES_READ_ROLES = frozenset({"admin", "reviewer", "system", "kl_backend"})
_SCHEMA_GRADES_WRITE_ROLES = frozenset({"admin"})


async def _schema_grades_rbac(request: Request, auth_context: dict = Depends(require_auth)):
    """GET → read 역할, 쓰기(PUT/POST/DELETE/PATCH) → admin 전용.

    fail-closed: 읽기(GET/HEAD/OPTIONS) 외의 메서드는 모두 admin 권한을 요구한다 —
    미래에 추가될 쓰기 엔드포인트가 실수로 broad 역할에 노출되지 않도록.
    require_role과 동일하게 actor_roles(복수, JWT claim) 우선, 없으면 actor_role 단일 폴백.
    """
    roles = set(auth_context.get("actor_roles") or ())
    if not roles:
        single = auth_context.get("actor_role")
        if single:
            roles = {single}
    allowed = (
        _SCHEMA_GRADES_READ_ROLES
        if request.method in ("GET", "HEAD", "OPTIONS")
        else _SCHEMA_GRADES_WRITE_ROLES
    )
    if roles.isdisjoint(allowed):
        from fastapi import HTTPException  # noqa: PLC0415
        raise HTTPException(
            status_code=403,
            detail=f"forbidden: roles {sorted(roles)} not in {sorted(allowed)}",
        )
    return auth_context


# #13: JSON 본문 크기 DoS 가드 (ASGI 미들웨어).
#
# /classify/batch 등 JSON 본문은 Pydantic 파싱 후에야 413으로 막혀, 인증된 호출자가
# 대용량 body(예: 1000×1MB)로 OOM을 유발할 수 있다(멀티파트 업로드는 max_upload_mb로
# 막히지만 일반 JSON body는 무방비). 이 미들웨어는 **본문 파싱 전에** 크기를 검사한다.
#
# 구현 노트:
# - Starlette BaseHTTPMiddleware에서 body를 미리 소비하면 다운스트림(AuditMiddleware 등)이
#   못 읽으므로, 순수 ASGI 미들웨어로 작성해 body를 소비하지 않는다.
# - 1차: Content-Length 헤더가 한도 초과면 본문을 읽기도 전에 즉시 413.
# - 2차: Content-Length 없는 chunked 요청은 receive를 래핑해 누적 바이트가 한도를 넘는 순간
#   413(정상 본문은 그대로 다운스트림에 전달 — body를 가로채 캐시하지 않음).
# - 등록 위치는 가장 바깥(파싱·다른 미들웨어보다 먼저 차단) — app.add_middleware 마지막 호출.
class _BodyTooLarge(Exception):
    """chunked 본문 누적이 한도를 넘었음을 알리는 내부 sentinel (다운스트림 메모리 누적 차단용)."""


class BodySizeLimitMiddleware:
    """Content-Length(우선)·스트리밍 누적 크기로 JSON 본문 한도를 강제하는 ASGI 미들웨어."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        # HTTP 요청만 검사 — websocket/lifespan은 그대로 통과.
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_413():
            # body 파싱 전에 413을 직접 반환(다른 미들웨어/핸들러에 도달시키지 않음).
            await send({
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json")],
            })
            await send({
                "type": "http.response.body",
                "body": (
                    b'{"detail":"request body too large",'
                    b'"code":"LLOYDK_BODY_TOO_LARGE"}'
                ),
            })

        # 1차: Content-Length 헤더 기반 사전 차단 (본문을 읽지 않음).
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    declared = int(value)
                except (TypeError, ValueError):
                    declared = -1
                if declared > self.max_bytes:
                    await send_413()
                    return
                break

        # 2차: Content-Length 없는 chunked 요청 — receive를 래핑해 누적 크기 가드.
        # body bytes는 그대로 통과시켜 다운스트림이 정상적으로 읽게 하되(가로채 캐시 금지),
        # 누적이 한도를 넘는 순간 **가드가 직접 413을 보내고** sentinel 예외로 다운스트림을
        # 중단한다. 그래야 다운스트림이 대용량 body를 끝까지 메모리에 모으기 전에 차단되어
        # OOM 방지가 실효를 갖는다. 가드가 413을 보낸 뒤에는 다운스트림이 풀리며 내보내는
        # 응답(예: 500)을 send에서 모두 억제해 단일 응답만 유지한다.
        received = 0
        guard_owns_response = False

        async def guarded_receive():
            nonlocal received, guard_owns_response
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b"") or b"")
                if received > self.max_bytes:
                    # 가드가 응답을 점유 — 413을 직접 쓰고 다운스트림을 unwind.
                    guard_owns_response = True
                    await send_413()
                    raise _BodyTooLarge()
            return message

        async def guarded_send(message):
            # 가드가 이미 413을 보냈으면 다운스트림 응답(중복 start·500 등)은 버린다.
            if guard_owns_response:
                return
            await send(message)

        try:
            await self.app(scope, guarded_receive, guarded_send)
        except _BodyTooLarge:
            # 413은 guarded_receive에서 이미 전송됨 — 여기서는 예외만 흡수.
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    # #30: 구조화(JSON) 로깅 — opt-in. LLOYDK_LOG_JSON truthy일 때만 root logger 교체.
    #      미설정(기본)이면 완전 no-op이라 기존 로깅·테스트 출력을 보존한다.
    try:
        from lloydk.obs.otel import setup_logging  # noqa: PLC0415
        setup_logging()
    except Exception as exc:  # noqa: BLE001
        logger.warning("structured logging setup skipped: %s", exc)
    # 배포 프로파일 + 적용된 backend 부팅 로그 — 운영 사고 사전 차단
    logger.info(
        "lloydk startup — profile=%s mode=%s llm=%s embedding=%s reranker=%s "
        "vector=%s storage=%s training=%s",
        settings.deploy_profile, settings.poc_mode,
        settings.llm_provider, settings.embedding_provider, settings.reranker_provider,
        settings.vector_backend, settings.storage_backend, settings.enable_training,
    )
    # J1: 운영 모드에서 빈 자격증명 차단 (dryrun/테스트는 우회)
    assert_production_credentials()
    # L-jwt-aud / L-apikey-honor: 운영 인증모드 confused-deputy 차단 설정 강제.
    # _is_production()=False(dev/test/dryrun)면 즉시 반환(비파괴).
    from lloydk.api._jwt_auth import assert_production_auth_config  # noqa: PLC0415
    assert_production_auth_config()
    # D2 (2026-05-29): OpenTelemetry 트레이싱 활성. OTEL_EXPORTER_OTLP_ENDPOINT
    # 미설정 또는 opentelemetry 미설치 시 silent no-op (반환값 False).
    try:
        from lloydk.obs.otel import setup_tracing  # noqa: PLC0415
        setup_tracing(app)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTel setup skipped: %s", exc)
    _warmup_models(settings)
    # health.py warmup_done 플래그 — lifespan 완료 시점에 True로 세팅.
    # uptime 기반 추정 대신 실제 startup 완료를 정확히 반영.
    from lloydk.api import health as _health_mod  # noqa: PLC0415
    _health_mod.STARTUP_COMPLETE = True
    yield


def _warmup_models(settings_obj) -> None:
    """임베더·reranker 모델을 부팅 시 1회 호출하여 cold start 비용 흡수.

    KURE/BGE 첫 호출에서 측정된 p95 32s가 운영 첫 요청에 노출되지 않도록.
    hash/noop provider 환경(lite-noapi·dryrun)에서는 즉시 반환되므로 사실상 no-op.
    실패해도 부팅은 막지 않음 — 모델 다운로드 차단·HF rate limit 등 환경 이슈와 분리.
    """
    if settings_obj.poc_mode == "dryrun":
        return
    try:
        from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
        emb = build_embedder()
        emb.embed(["warmup"])
        logger.info("embedder warmup ok — provider=%s", settings_obj.embedding_provider)
    except Exception as exc:  # noqa: BLE001
        # [require_real_embedder] 하드닝 실서빙에서 실 임베더 로드 실패(HashEmbedding 무음 폴백 거부)는
        # startup fail-clear 여야 한다 — best-effort warmup 이 이 fail-secure 신호를 삼키면 형제 게이트
        # (require_safety_gates)와 달리 첫 요청(lazy)까지 지연돼 검색품질 급락이 무음으로 흐른다.
        # require_real_embedder=True 면 re-raise(그 외 다운로드/rate-limit 등은 기존대로 best-effort 스킵).
        if getattr(settings_obj, "require_real_embedder", False):
            raise RuntimeError(
                f"require_real_embedder=True 인데 임베더 warmup 실패: {exc}. 실 임베더 없이 폐쇄망 "
                "실서빙 진입 불가(HashEmbedding 폴백=검색품질 급락 — fail-clear)."
            ) from exc
        logger.warning("embedder warmup skipped: %s", exc)
    if settings_obj.reranker_provider not in ("", "noop"):
        try:
            from lloydk.adapters.reranker import get_reranker  # noqa: PLC0415
            rr = get_reranker()
            rr.rerank("warmup", ["doc1", "doc2"], top_k=1)
            logger.info("reranker warmup ok — provider=%s", settings_obj.reranker_provider)
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker warmup skipped: %s", exc)


app = FastAPI(
    title="Lloydk AI Engine",
    version="0.1.0-poc",
    description="KIPRA AI 영업비밀관리시스템 — Lloydk 파트 PoC API",
    lifespan=lifespan,
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs",
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


# 감사 미들웨어 — 모든 요청에 audit_log 1건 best-effort 기록.
# request_id 미들웨어 뒤에 등록해야 request.state.request_id 사용 가능.
app.add_middleware(AuditMiddleware)

# Prometheus 메트릭 미들웨어 — 모든 요청에 lloydk_requests_total + duration 수집.
# AuditMiddleware보다 먼저 add_middleware 호출하면 starlette 스택상 더 바깥에 위치.
app.add_middleware(PrometheusMiddleware)

# 멱등성 미들웨어 — Idempotency-Key 헤더가 있는 변경성 요청을 1회만 처리.
# AuditMiddleware/Prometheus보다 바깥(나중 add)이라 캐시 히트 시 핸들러·감사 중복 실행 차단.
# 헤더 없으면 무영향(즉시 통과) — 기존 동작 비파괴.
app.add_middleware(IdempotencyMiddleware)

# CORS — 운영 origin allowlist는 settings.cors_allow_origins (.env).
# allow_credentials=False (allow_origins=["*"] 호환).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

# Rate-limit — slowapi Limiter 등록 + 429 핸들러.
# RATE_LIMIT_DISABLED=1로 비활성 가능 (TestClient·dryrun).
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

# #13: JSON 본문 크기 DoS 가드 — 가장 마지막 add_middleware = 스택 최외곽.
# body 파싱·다른 미들웨어보다 먼저 Content-Length를 검사해 대용량 body를 즉시 차단한다.
# 멀티파트 업로드(documents/guide)는 라우터에서 max_upload_mb로 처리되므로, 이 한도는
# 일반 JSON에 적용되되 정상 요청·dryrun/테스트는 통과하도록 충분히 크게(기본 25MB) 잡는다.
app.add_middleware(
    BodySizeLimitMiddleware,
    max_bytes=settings.max_request_body_mb * 1024 * 1024,
)


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # J2: 내부 예외 메시지를 클라이언트 응답에 노출하지 않음.
    # 서버 로그에만 상세 기록, 응답은 request_id로 추적 가능.
    rid = getattr(request.state, "request_id", None)
    logger.error(
        "unhandled exception (request_id=%s, path=%s): %s",
        rid, request.url.path, exc, exc_info=True,
    )
    err = Error(
        code="LLOYDK_INTERNAL",
        message="internal server error",  # 상세는 서버 로그 참조
        request_id=rid,
        retryable=True,  # 미처리 500은 일시적 장애일 수 있음 — 클라이언트 재시도 허용
    )
    return JSONResponse(status_code=500, content=err.model_dump())


app.include_router(health_api.router, prefix="/api/v1")
app.include_router(classify_api.router, prefix="/api/v1")
app.include_router(classify_stream_api.router, prefix="/api/v1")
app.include_router(explain_api.router, prefix="/api/v1")
app.include_router(async_classify_api.router, prefix="/api/v1")
app.include_router(answer_api.router, prefix="/api/v1")
app.include_router(confirm_api.router, prefix="/api/v1")
# 검수 액션(승급)은 재학습 무관 — enable_training과 무관하게 항상 등록(폐쇄망 운영 경로).
app.include_router(promotion_api.router, prefix="/api/v1")
# 학습 라우터는 settings.enable_training=True (full-train 프로파일)에서만 등록.
# lite-*·onprem에서는 OpenAPI에도 노출되지 않아 고객사가 "있는데 안 쓴다"는 인식 자체가 없음.
if settings.enable_training:
    app.include_router(
        training_api.router, prefix="/api/v1",
        dependencies=[Depends(require_role("admin", "kl_backend", "system"))],
    )
else:
    logger.info("training router disabled (deploy_profile=%s)", settings.deploy_profile)
app.include_router(synthesis_api.router, prefix="/api/v1")
app.include_router(golden_api.router, prefix="/api/v1")
app.include_router(guide_api.router, prefix="/api/v1")
app.include_router(documents_api.router, prefix="/api/v1")
app.include_router(
    schema_admin_api.router, prefix="/api/v1",
    dependencies=[Depends(_schema_grades_rbac)],
)
app.include_router(metrics_api.router, prefix="/api/v1")
app.include_router(prom_metrics_api.router, prefix="/api/v1")
app.include_router(admin_api.router, prefix="/api/v1")

# 백그라운드 메트릭 refresh — TESTING=1 또는 pytest 환경이면 자동 skip.
prom_metrics_api.register_background_refresh(app)

# 데모 콘솔 정적 마운트 — /demo/ 경로에 단일 페이지 SPA.
# OpenAPI 에는 노출되지 않음(StaticFiles 자동 제외). 디렉토리 없으면 silent skip.
_STATIC_DIR = Path(__file__).parent / "static"
if _STATIC_DIR.is_dir():
    app.mount("/demo", StaticFiles(directory=str(_STATIC_DIR), html=True), name="demo")
    logger.info("demo console mounted at /demo/ (static dir=%s)", _STATIC_DIR)
else:
    logger.info("demo console not mounted (no static dir at %s)", _STATIC_DIR)
