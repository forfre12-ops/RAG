import logging
from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from lloydk.config import assert_production_credentials, settings
from lloydk.schemas.common import Error

logger = logging.getLogger(__name__)
from lloydk.api import classify as classify_api
from lloydk.api import health as health_api
from lloydk.api import confirm as confirm_api
from lloydk.api import training as training_api
from lloydk.api import synthesis as synthesis_api
from lloydk.api import guide as guide_api
from lloydk.api import schema_admin as schema_admin_api
from lloydk.api import metrics as metrics_api
from lloydk.api import async_classify as async_classify_api
from lloydk.api import prom_metrics as prom_metrics_api
from lloydk.api.middleware import AuditMiddleware
from lloydk.api.prom_metrics import PrometheusMiddleware
from lloydk.api.rate_limit import limiter, rate_limit_exceeded_handler


@asynccontextmanager
async def lifespan(app: FastAPI):
    # J1: 운영 모드에서 빈 자격증명 차단 (dryrun/테스트는 우회)
    assert_production_credentials()
    # warm-up hooks here (load model registry, etc.)
    yield


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
    )
    return JSONResponse(status_code=500, content=err.model_dump())


app.include_router(health_api.router, prefix="/api/v1")
app.include_router(classify_api.router, prefix="/api/v1")
app.include_router(async_classify_api.router, prefix="/api/v1")
app.include_router(confirm_api.router, prefix="/api/v1")
app.include_router(training_api.router, prefix="/api/v1")
app.include_router(synthesis_api.router, prefix="/api/v1")
app.include_router(guide_api.router, prefix="/api/v1")
app.include_router(schema_admin_api.router, prefix="/api/v1")
app.include_router(metrics_api.router, prefix="/api/v1")
app.include_router(prom_metrics_api.router, prefix="/api/v1")

# 백그라운드 메트릭 refresh — TESTING=1 또는 pytest 환경이면 자동 skip.
prom_metrics_api.register_background_refresh(app)
