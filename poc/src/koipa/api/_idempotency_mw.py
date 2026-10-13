"""IdempotencyMiddleware — Idempotency-Key 헤더 기반 중복 처리 방지.

동작:
- 헤더 없거나 GET/HEAD/DELETE/OPTIONS면 **즉시 통과**(기존 동작 무영향).
- POST/PUT/PATCH + Idempotency-Key 헤더 시:
  - 완료된 응답이 있으면 그대로 재현(`X-Idempotent-Replay: true`).
  - 처리 중(in-flight)이면 409.
  - 아니면 처리 후 2xx 응답을 저장(SSE·비-2xx는 미저장).

스택 위치: AuditMiddleware보다 바깥(먼저 실행)에 둬, 캐시 히트 시 핸들러·감사
중복 실행을 막는다. (app.py 등록 순서로 제어.)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_MUTATING = frozenset({"POST", "PUT", "PATCH"})


def _principal_namespace(request: Request) -> str:
    """멱등성 키 네임스페이스 — 교차 주체(KL 자격) 충돌·응답 replay 누출 차단.

    tenant 제거: 단일 고객사 엔진이라 교차고객 replay 위험이 없다(고객사 격리는
    KL 포털 전담). 네임스페이스는 KL cred(Authorization/X-API-Key) 기준으로만 잡는다.

    이 미들웨어는 인증 의존성(require_auth)보다 **먼저** 실행되므로 그 시점엔
    request.state.auth_*가 아직 비어 있다. 따라서 미들웨어 시점에 가용한 자격증명
    헤더(Authorization/X-API-Key)를 SHA-256으로 해시해 키 prefix로 쓴다. 같은 주체의
    요청은 같은 헤더를 보내므로 네임스페이스가 일관되고(저장↔재현 매칭) 다른 cred는
    분리된다. 원시 비밀은 키에 노출하지 않는다(해시 16자만).
    """
    cred = (
        request.headers.get("authorization")
        or request.headers.get("x-api-key")
        or (request.client.host if request.client else "anon")
    )
    return hashlib.sha256(cred.encode()).hexdigest()[:16]


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        raw_key = request.headers.get("idempotency-key")
        if not raw_key or request.method not in _MUTATING:
            return await call_next(request)

        # cred별 네임스페이스로 격리 — 교차 KL cred 키 충돌·응답 replay 누출 차단.
        key = f"{_principal_namespace(request)}:{raw_key}"

        # 지연 import — 미들웨어 로드 시점에 redis 연결을 강제하지 않음.
        from koipa.services.idempotency import get_idempotency_store  # noqa: PLC0415

        try:
            store = get_idempotency_store()
        except Exception as exc:  # noqa: BLE001 — 저장소 장애 시 멱등성 미적용(가용성 우선)
            logger.warning("idempotency disabled (store error): %s", exc)
            return await call_next(request)

        # 1) 완료된 응답 재현
        cached = store.get(key)
        if cached is not None:
            status, body, ctype = cached
            return Response(
                content=body, status_code=status, media_type=ctype,
                headers={"X-Idempotent-Replay": "true"},
            )

        # 2) in-flight 락 — 동시 중복 차단
        if not store.acquire(key):
            return JSONResponse(
                status_code=409,
                content={"detail": "a request with this Idempotency-Key is already in progress"},
                headers={"X-Idempotent-Conflict": "true"},
            )

        # 3) 처리 + 결과 저장
        try:
            response = await call_next(request)
        except Exception:
            store.release(key)
            raise

        ctype = response.headers.get("content-type", "")
        is_stream = "text/event-stream" in ctype
        if 200 <= response.status_code < 300 and not is_stream:
            body = b"".join([chunk async for chunk in response.body_iterator])
            store.store(key, (response.status_code, body, response.media_type or "application/json"))
            # content-length는 body 재설정 시 starlette가 재계산하므로 제외
            passthru = {
                k: v for k, v in response.headers.items() if k.lower() != "content-length"
            }
            passthru["X-Idempotent-Stored"] = "true"
            return Response(
                content=body, status_code=response.status_code,
                media_type=response.media_type, headers=passthru,
            )
        # 비-2xx·스트리밍은 저장하지 않고 락만 해제(재시도 허용)
        store.release(key)
        return response
