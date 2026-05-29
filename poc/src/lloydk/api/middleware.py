"""FastAPI 미들웨어 — 모든 요청에 대한 audit_log 기록.

설계:
- 모든 응답(성공/실패) 후 audit_log 1건 insert (best-effort)
- DB 미가용·테이블 부재 시 silent skip (테스트·dryrun 환경 보호)
- payload는 SHA-256 해시만. 본문은 PG에 저장하지 않음.
- /healthz·/docs·/openapi.json은 audit 제외 (노이즈 차단)
- actor_id는 X-Actor-Id 헤더, role은 X-Actor-Role 헤더로 받음 (옵션)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from fastapi import Request, Response
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/healthz",
        "/docs",
        "/redoc",
        "/api/v1/openapi.json",
        "/openapi.json",
    }
)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _try_build_chained_hash(body: bytes) -> str | None:
    """A2: body → prev_row 결합 → chained hash 패킹.

    audit_chain 또는 DB 미가용 시 단순 sha256 폴백(하위호환).
    body가 비어도 prev_row만으로 chain 진행 가능 (empty payload).
    """
    import os  # noqa: PLC0415
    if os.getenv("AUDIT_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return _hash_bytes(body) if body else None
    try:
        from lloydk.services.audit_chain import build_chained_hash, get_last_hash  # noqa: PLC0415
    except ImportError:
        return _hash_bytes(body) if body else None
    try:
        prev = get_last_hash()
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit chain prev fetch failed (db unavailable): %s", exc)
        return _hash_bytes(body) if body else None
    try:
        # payload는 body 그대로(bytes → str sha256 사전 계산해서 넘김)
        body_hash = _hash_bytes(body) if body else ""
        return build_chained_hash(body_hash, prev)
    except Exception as exc:  # noqa: BLE001
        logger.debug("audit chain hash build failed: %s", exc)
        return _hash_bytes(body) if body else None


async def _read_body(request: Request) -> bytes:
    """body를 한 번만 읽고, 다운스트림 핸들러가 재사용할 수 있게 cache."""
    body = await request.body()
    # starlette는 body를 cache하므로 추가 작업 불필요 — 동일 request에서 await request.body()는 재사용됨
    return body


class AuditMiddleware(BaseHTTPMiddleware):
    """모든 요청을 audit_log에 기록 (best-effort)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        # /healthz·/docs 등은 skip
        if path in _EXCLUDED_PATHS:
            return await call_next(request)

        # body는 핸들러보다 먼저 읽어 payload_hash 계산
        try:
            body = await _read_body(request)
        except Exception as exc:  # noqa: BLE001
            # K3: 빈 swallow 제거, debug 로깅 — multipart 등 비표준 body는 정상적으로 실패 가능
            logger.debug("audit payload_hash skipped: %s", exc)
            body = b""

        # A2 (2026-05-29): 단순 sha256 대신 chained hash로 저장.
        # 형식 prev16:full32 — verify_chain이 진짜 재계산 가능. DB·chain 모듈 부재 시
        # 기존 sha256 폴백(하위호환). _try_build_chained_hash는 모든 예외를 silent 처리.
        payload_hash = _try_build_chained_hash(body)

        response: Response | None = None
        error_code: str | None = None
        success = True

        try:
            response = await call_next(request)
            if response.status_code >= 400:
                success = False
                error_code = f"HTTP_{response.status_code}"
            return response
        except Exception as exc:  # noqa: BLE001
            success = False
            error_code = type(exc).__name__
            raise
        finally:
            self._record(
                request=request,
                payload_hash=payload_hash,
                success=success,
                error_code=error_code,
            )

    @staticmethod
    def _record(
        *,
        request: Request,
        payload_hash: str | None,
        success: bool,
        error_code: str | None,
    ) -> None:
        """audit_log insert. DB 미가용·schema 부재 등 모든 예외는 silent."""
        import os  # noqa: PLC0415
        if os.getenv("AUDIT_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.repositories.audit_repo import AuditRepo  # noqa: PLC0415
        except ImportError:
            return

        action = _derive_action(request)
        actor_id = request.headers.get("x-actor-id")
        actor_role = request.headers.get("x-actor-role")
        tenant_id = request.headers.get("x-tenant-id")
        request_id = getattr(request.state, "request_id", None)
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")

        try:
            with session_scope() as db:
                AuditRepo(db).record(
                    action=action,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    tenant_id=tenant_id,
                    request_id=request_id,
                    payload_hash=payload_hash,
                    ip_address=ip,
                    user_agent=ua,
                    success=success,
                    error_code=error_code,
                )
        except SQLAlchemyError as exc:
            logger.debug("audit log skipped (db error): %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.debug("audit log skipped (unexpected): %s", exc)


def _derive_action(request: Request) -> str:
    """URL path → action 코드 매핑.

    /api/v1/classify        → classify
    /api/v1/classify/async  → classify_async
    /api/v1/confirm         → confirm
    /api/v1/relabel         → relabel
    /api/v1/train           → train
    ...
    """
    path = request.url.path.rstrip("/")
    prefix = "/api/v1/"
    if path.startswith(prefix):
        tail = path[len(prefix):]
        # /classify/{doc_id}·/classify/async 등 → 첫 세그먼트만
        segs = tail.split("/")
        if len(segs) >= 2 and segs[1] in {"async", "batch"}:
            return f"{segs[0]}_{segs[1]}"
        return segs[0] or "unknown"
    return path or "unknown"
