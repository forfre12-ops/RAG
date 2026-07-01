"""FastAPI 미들웨어 — 모든 요청에 대한 audit_log 기록.

설계:
- 모든 응답(성공/실패) 후 audit_log 1건 insert (best-effort)
- DB 미가용·테이블 부재 시 silent skip (테스트·dryrun 환경 보호)
- payload는 SHA-256 해시만. 본문은 PG에 저장하지 않음.
- /healthz·/docs·/openapi.json은 audit 제외 (노이즈 차단)
- actor_id·role은 인증이 해소한 request.state.auth_*를 우선 사용.
  인증이 신원을 못 채운 요청(인증 실패 등)에만 X-Actor-Id 헤더로 폴백.
- tenant 제거: 단일 고객사 엔진(격리는 KL 포털 전담), audit에 tenant 기록 없음.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# L-audit-noalarm (W3-D): 감사 insert 실패가 silent(debug 후 skip)라 DB 일시장애 구간
# 요청이 감사 없이 성공한다. 실패를 Prometheus 카운터로 노출해 운영 알람에 연결한다.
# prom_metrics의 공용 registry에 등록 — /api/v1/metrics-prom 에서 함께 스크랩됨.
try:
    from lloydk.api.prom_metrics import registry as _prom_registry
    from prometheus_client import Counter as _Counter

    AUDIT_WRITE_FAILURE_TOTAL = _Counter(
        "lloydk_audit_write_failure_total",
        "Audit log insert failures (DB unavailable/error — request served without audit trail)",
        ["reason"],  # db_error | unexpected
        registry=_prom_registry,
    )
except Exception:  # noqa: BLE001 — prometheus 미설치/registry 부재 시 메트릭 없이 동작
    AUDIT_WRITE_FAILURE_TOTAL = None


def _inc_audit_failure(reason: str) -> None:
    if AUDIT_WRITE_FAILURE_TOTAL is not None:
        try:
            AUDIT_WRITE_FAILURE_TOTAL.labels(reason=reason).inc()
        except Exception as exc:  # noqa: BLE001
            logger.debug("audit failure counter inc failed: %s", exc)

_EXCLUDED_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/healthz",
        "/docs",
        "/redoc",
        "/api/v1/openapi.json",
        "/openapi.json",
    }
)

# L-audit-noalarm fail-closed 대상 — 보안/감사상 흔적이 반드시 남아야 하는 변경성 액션.
# 조회성(classify/answer 등)은 제외해 가용성 영향 최소화. _derive_action 코드 기준.
_HIGH_RISK_ACTIONS: frozenset[str] = frozenset(
    {"confirm", "relabel", "train", "ingest", "guide", "upload", "delete"}
)


def _fail_closed_enabled() -> bool:
    """고위험 액션 audit 실패 시 fail-closed 여부. 기본 off(비파괴).

    settings.audit_fail_closed_high_risk(실 필드) 우선, 없으면 env
    LLOYDK_AUDIT_FAIL_CLOSED_HIGH_RISK 폴백(컨테이너 재빌드 없이 운영 토글).
    """
    import os  # noqa: PLC0415
    try:
        from lloydk.config import settings  # noqa: PLC0415

        if getattr(settings, "audit_fail_closed_high_risk", False):
            return True
    except Exception:  # noqa: BLE001
        pass
    return os.getenv("LLOYDK_AUDIT_FAIL_CLOSED_HIGH_RISK", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _is_high_risk(request: Request) -> bool:
    return _derive_action(request) in _HIGH_RISK_ACTIONS


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


def _raw_body_hash(body: bytes) -> str:
    """체인 패킹 전 raw body sha256(hex). 빈 body는 ""(체인은 빈 payload로 계속).

    #4: 과거엔 dispatch에서 prev를 락 없이 미리 읽어 체인을 만들었다(payload_hash). 동시
    요청이 같은 prev를 읽어 체인이 분기됐다. 이제 dispatch는 raw 해시만 만들고, prev-read+
    패킹은 _record의 advisory-locked insert 트랜잭션 안에서 수행한다(build_chained_hash_locked).
    """
    return _hash_bytes(body) if body else ""


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

        # #4: dispatch는 raw body sha256만 계산. 체이닝(prev 읽기 + prev16:full32 패킹)은
        # _record의 advisory-locked insert 트랜잭션에서 수행해 동시요청 체인 분기를 막는다.
        payload_hash = _raw_body_hash(body)

        response: Response | None = None
        error_code: str | None = None
        success = True
        audit_ok = True

        try:
            response = await call_next(request)
            if response.status_code >= 400:
                success = False
                error_code = f"HTTP_{response.status_code}"
        except Exception as exc:  # noqa: BLE001
            # 핸들러 예외는 audit 기록 후 그대로 전파 (finally 대신 명시 기록 — 아래
            # fail-closed 분기가 정상 응답 경로에서만 도는 것을 보장).
            success = False
            error_code = type(exc).__name__
            self._record(
                request=request,
                payload_hash=payload_hash,
                success=success,
                error_code=error_code,
            )
            raise

        audit_ok = self._record(
            request=request,
            payload_hash=payload_hash,
            success=success,
            error_code=error_code,
        )

        # L-audit-noalarm: 고위험 액션 fail-closed (옵션, settings/env 플래그, 기본 off).
        # 감사 기록이 실패했고 그 외 요청은 성공한 고위험 액션이면 503으로 막아
        # 무감사 성공을 방지. 기본 비활성이라 dev/test/기존 동작은 비파괴.
        # BaseHTTPMiddleware에서 raise한 HTTPException은 라우트 예외핸들러에 잡히지
        # 않으므로 직접 JSONResponse(503)를 반환한다.
        if success and not audit_ok and _fail_closed_enabled() and _is_high_risk(request):
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "audit log unavailable; high-risk action blocked (fail-closed)"
                },
            )
        return response

    @staticmethod
    def _record(
        *,
        request: Request,
        payload_hash: str | None,
        success: bool,
        error_code: str | None,
    ) -> bool:
        """audit_log insert.

        반환: insert 성공(또는 의도적 skip=AUDIT_DISABLED/모듈부재)이면 True,
        DB 오류 등으로 *기록되지 못하면* False (L-audit-noalarm: 무감사 성공 신호).
        DB 미가용·schema 부재 등 모든 예외는 raise하지 않음(best-effort) — bool로만 보고.
        """
        import os  # noqa: PLC0415
        if os.getenv("AUDIT_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
            return True  # 의도적 비활성 — 실패 아님
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.repositories.audit_repo import AuditRepo  # noqa: PLC0415
        except ImportError:
            return True  # 환경상 audit 미탑재 — 실패로 카운트하지 않음

        action = _derive_action(request)
        # 보안: 인증이 해소한 신원(request.state.auth_*)을 우선 사용 — 위조 가능한
        # X-Actor-Id 헤더로 감사 기록이 날조되는 것을 차단. 인증이 신원을 못 채운
        # 경우(인증 실패/미인증 요청)에만 헤더로 폴백(success=False로 이미 구분됨).
        # tenant 제거: 단일 고객사 엔진(격리는 KL 포털 전담), audit에 tenant 기록 없음.
        auth_actor = getattr(request.state, "auth_actor", None)
        auth_role = getattr(request.state, "auth_role", None)
        actor_id = auth_actor if auth_actor is not None else request.headers.get("x-actor-id")
        actor_role = auth_role if auth_role is not None else request.headers.get("x-actor-role")
        request_id = getattr(request.state, "request_id", None)
        ip = request.client.host if request.client else None
        ua = request.headers.get("user-agent")

        # #4: prev-read + insert를 같은 advisory-locked 트랜잭션에 묶어 체인 분기 차단.
        try:
            from lloydk.services.audit_chain import build_chained_hash_locked  # noqa: PLC0415
        except ImportError:
            build_chained_hash_locked = None  # type: ignore[assignment]

        try:
            with session_scope() as db:
                ph = payload_hash
                if build_chained_hash_locked is not None:
                    # payload_hash는 raw body 해시(또는 ""). 같은 tx에서 lock→prev→패킹.
                    # tenant 제거: 전역 단일 체인(per-tenant 체인 없음).
                    ph = build_chained_hash_locked(db, payload_hash or "")
                AuditRepo(db).record(
                    action=action,
                    actor_id=actor_id,
                    actor_role=actor_role,
                    request_id=request_id,
                    payload_hash=ph,
                    ip_address=ip,
                    user_agent=ua,
                    success=success,
                    error_code=error_code,
                )
            return True
        except SQLAlchemyError as exc:
            # silent skip 유지하되 메트릭으로 노출 — 운영 알람 연결(무감사 성공 가시화).
            logger.warning("audit log skipped (db error): %s", exc)
            _inc_audit_failure("db_error")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit log skipped (unexpected): %s", exc)
            _inc_audit_failure("unexpected")
            return False


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
