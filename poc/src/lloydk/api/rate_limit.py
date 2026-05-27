"""API Rate-limit — slowapi 기반.

설계:
- key_func: X-Tenant-Id 헤더 우선, 없으면 클라이언트 IP 폴백
- 라우터별 다른 한도 (분당 60/10, 시간당 10 등) — 데코레이터로 명시
- RATE_LIMIT_DISABLED=1 → 모든 한도 비활성 (테스트·dryrun 환경 보호)
- TestClient는 host 헤더가 "testclient"라 IP 충돌 가능 → 환경변수 자동 비활성 권장
- 한도 초과 시 HTTP 429 + Retry-After 헤더 (slowapi 기본 제공)
"""

from __future__ import annotations

import os

from fastapi import Request
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import JSONResponse


def _is_disabled() -> bool:
    """RATE_LIMIT_DISABLED=1·true·yes 등 truthy 값 → True."""
    val = os.getenv("RATE_LIMIT_DISABLED", "").strip().lower()
    return val in {"1", "true", "yes", "on"}


def tenant_or_ip_key(request: Request) -> str:
    """X-Tenant-Id 우선, 없으면 클라이언트 IP."""
    tenant = request.headers.get("x-tenant-id")
    if tenant:
        return f"tenant:{tenant}"
    return f"ip:{get_remote_address(request)}"


# Limiter 인스턴스 — app.state.limiter 등록 + 데코레이터에서 참조.
# default_limits는 그 외 라우터에 자동 적용 (분당 120).
# headers_enabled=False — X-RateLimit-* 헤더 자동 주입을 비활성 (endpoint signature에
# response: Response 파라미터를 강제하지 않기 위함). 429 응답에는 핸들러가 Retry-After
# 헤더를 명시 주입하므로 클라이언트는 retry 정보를 받음.
limiter = Limiter(
    key_func=tenant_or_ip_key,
    default_limits=["120/minute"],
    enabled=not _is_disabled(),
    headers_enabled=False,
)


def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """429 + Retry-After 응답. slowapi 기본 핸들러를 감싸 일관된 JSON 형태로 반환."""
    # exc.detail: "60 per 1 minute" 등 limit 표현. retry_after는 limit window 끝까지의 초.
    retry_after = getattr(exc, "retry_after", None) or 60
    body = {
        "code": "LLOYDK_RATE_LIMIT",
        "message": f"rate limit exceeded: {exc.detail}",
        "retry_after_sec": int(retry_after),
    }
    response = JSONResponse(status_code=429, content=body)
    response.headers["Retry-After"] = str(int(retry_after))
    return response
