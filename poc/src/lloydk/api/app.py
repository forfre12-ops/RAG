from uuid import uuid4
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lloydk.schemas.common import Error
from lloydk.api import classify as classify_api
from lloydk.api import health as health_api
from lloydk.api import confirm as confirm_api
from lloydk.api import training as training_api
from lloydk.api import synthesis as synthesis_api
from lloydk.api import guide as guide_api
from lloydk.api import schema_admin as schema_admin_api
from lloydk.api import metrics as metrics_api
from lloydk.api import async_classify as async_classify_api
from lloydk.api.middleware import AuditMiddleware


@asynccontextmanager
async def lifespan(app: FastAPI):
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


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    err = Error(
        code="LLOYDK_INTERNAL",
        message=str(exc),
        request_id=getattr(request.state, "request_id", None),
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
