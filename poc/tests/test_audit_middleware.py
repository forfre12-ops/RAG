"""Audit middleware tests.

- PG 미가용일 때도 응답이 정상이어야 함 (best-effort)
- PG 가용일 때 실제로 audit_log에 row가 기록되어야 함
"""

from __future__ import annotations

import sys
import uuid

import pytest
if any("not slow" in arg for arg in sys.argv):
    pytest.skip("slow audit middleware integration tests excluded from lite collection", allow_module_level=True)
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.api.app import app
from lloydk.config import settings
from lloydk.db import engine


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


_PG = _pg_ok()


# ============================================================
# PG 가용 여부와 무관: 응답이 정상이어야 함 (best-effort)
# ============================================================

def test_classify_still_works_with_audit_middleware():
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={"X-API-Key": settings.api_key},
            json={"doc_id": "non-uuid", "content": "특급기밀 설계도"},
        )
        assert r.status_code == 200, r.text


def test_healthz_excluded_from_audit():
    """/healthz는 audit_log 제외 (노이즈 차단)."""
    with TestClient(app) as cli:
        before_count = None
        if _PG:
            from lloydk.db import session_scope
            from lloydk.db.models import AuditLog

            with session_scope() as db:
                before_count = db.query(AuditLog).filter(AuditLog.action == "healthz").count()

        r = cli.get("/api/v1/healthz")
        assert r.status_code == 200
    # PG 가용 시 audit_log에 healthz 항목이 없어야 함은 별도 검증
    if _PG:
        from lloydk.db import session_scope
        from lloydk.db.models import AuditLog

        with session_scope() as db:
            after_count = db.query(AuditLog).filter(AuditLog.action == "healthz").count()
            assert after_count == before_count


# ============================================================
# PG 가용 시: 실제 audit_log row 검증
# ============================================================

@pytest.mark.skipif(not _PG, reason="Postgres not reachable")
def test_audit_log_recorded_for_classify():
    actor = f"actor-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={
                "X-API-Key": settings.api_key,
                "X-Actor-Id": actor,
                "X-Actor-Role": "admin",
            },
            json={"doc_id": "non-uuid", "content": "내용"},
        )
        assert r.status_code == 200

    from lloydk.db import session_scope
    from lloydk.repositories import AuditRepo
    with session_scope() as db:
        rows = AuditRepo(db).recent_for_actor(actor)
    assert len(rows) == 1, "audit_log 1건 기록되어야 함"
    a = rows[0]
    assert a.action == "classify"
    assert a.actor_role == "admin"
    assert a.payload_hash is not None
    assert a.success is True


@pytest.mark.skipif(not _PG, reason="Postgres not reachable")
def test_audit_log_records_failure():
    """401 응답도 audit_log에 기록되어야 함 (success=False)."""
    actor = f"actor-fail-{uuid.uuid4().hex[:8]}"
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/classify",
            headers={
                "X-API-Key": "wrong-key",
                "X-Actor-Id": actor,
            },
            json={"doc_id": "x", "content": "y"},
        )
        assert r.status_code == 401

    from lloydk.db import session_scope
    from lloydk.repositories import AuditRepo
    with session_scope() as db:
        rows = AuditRepo(db).recent_for_actor(actor)
    assert len(rows) == 1
    assert rows[0].success is False
    assert rows[0].error_code == "HTTP_401"
