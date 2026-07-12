"""API Rate-limit — slowapi 기반.

설계:
- key_func: 검증된 신원(request.state.auth_actor=KL cred) 우선, 없으면 IP 폴백
  (tenant 제거: 단일 KL 인증이라 버킷은 KL cred/actor 기준; 격리는 KL 포털 전담)
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


def cred_or_ip_key(request: Request) -> str:
    """rate-limit 키 — 검증된 KL 자격(actor) 우선, 없으면 IP.

    tenant 제거: 단일 고객사 엔진이라 per-tenant 버킷 개념이 없다. 버킷은 검증된
    KL cred(actor/API키 기준)로 잡고, 미인증이면 IP로 폴백한다.

    M-ratelimit-key (Track B): 키는 *authoritative* 신원만 쓴다(위조 가능한 원시
    헤더 금지 — 한도 우회/버킷 고갈 차단).
      1) request.state.auth_actor — 검증된 actor(JWT sub). api_key 모드는 단일 KL
         공유키라 actor가 비어 IP 폴백으로 자연 수렴(KL=단일 호출자).
      2) 그 외(미인증) → 클라이언트 IP. 검증 불가 헤더를 단독 키로 쓰지 않는다.
    """
    auth_actor = getattr(request.state, "auth_actor", None)
    if auth_actor:
        return f"actor:{auth_actor}"
    return f"ip:{get_remote_address(request)}"


# Limiter 인스턴스 — app.state.limiter 등록 + @limiter.limit 데코레이터에서 참조.
# 주의(#14 정직성): default_limits 는 SlowAPIMiddleware 가 등록돼 있어야만 미장식 라우트에 자동
# 적용된다. app.py 는 그 미들웨어를 등록하지 않으므로 default_limits(120/min)는 실효가 없고, 한도는
# @limiter.limit 데코레이터가 붙은 라우트(classify/answer/explain/synthesis/train/async/stream)에만
# 걸린다(그 외 변경성 라우트는 무제한). 배포모델(폐쇄망·단일 신뢰 KL 호출자·RBAC 게이트)상 블랭킷
# 한도가 불필요해 미들웨어를 의도적으로 안 켠 상태 — 필요해지면 SlowAPIMiddleware 등록 또는 비용 큰
# 변경성 라우트(documents/golden/confirm/promotion)에 명시 @limiter.limit 를 붙인다.
# headers_enabled=False — X-RateLimit-* 헤더 자동 주입을 비활성 (endpoint signature에
# response: Response 파라미터를 강제하지 않기 위함). 429 응답에는 핸들러가 Retry-After
# 헤더를 명시 주입하므로 클라이언트는 retry 정보를 받음.
#
# config_filename: 기본은 None (slowapi가 cwd `.env`를 자동 발견 후 starlette Config로
# 로드). Windows 한국어 로케일에서 .env가 UTF-8인데 starlette가 cp949로 강제 디코드해
# UnicodeDecodeError를 일으키는 사고가 보고됨 — SLOWAPI_SKIP_DOTENV=1로 우회 가능
# (존재하지 않는 sentinel 경로를 넘기면 starlette `os.path.isfile` 검사로 skip).
def _resolve_config_filename() -> str | None:
    if os.getenv("SLOWAPI_SKIP_DOTENV", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "/__lloydk_skip_dotenv__"  # 존재하지 않는 경로 → starlette skip
    return None


limiter = Limiter(
    key_func=cred_or_ip_key,
    default_limits=["120/minute"],
    enabled=not _is_disabled(),
    headers_enabled=False,
    config_filename=_resolve_config_filename(),
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
