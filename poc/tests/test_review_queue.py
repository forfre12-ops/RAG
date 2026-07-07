"""GET /review-queue — 검수(승인) 대기 목록 조회 (FUN-024 갭 마감).

admin 콘솔의 세션-only 큐가 못 보던 'DB에 쌓인 needs_review'를 서버측에서 반환하는
list_review_queue + 엔드포인트 매핑을 잠근다. PG 불요 — 순수 판정 + 서비스 스텁 + best-effort.
"""

from __future__ import annotations

from sqlalchemy.exc import SQLAlchemyError

from lloydk.api import confirm as confirm_api
from lloydk.schemas.confirm import ReviewQueueItem
from lloydk.services import confirm_service as cs
from lloydk.services.confirm_service import resolve_review_statuses


# ── 상태 해석 (순수) ─────────────────────────────────────────────────────────
def test_resolve_statuses_default_returns_both():
    both = ("needs_review", "needs_second_review")
    assert resolve_review_statuses("pending") == both
    assert resolve_review_statuses("") == both
    assert resolve_review_statuses(None) == both
    assert resolve_review_statuses("all") == both


def test_resolve_statuses_specific():
    assert resolve_review_statuses("needs_review") == ("needs_review",)
    assert resolve_review_statuses("needs_second_review") == ("needs_second_review",)


def test_resolve_statuses_garbage_falls_back_to_pending():
    # confirmed 등 '대기 아님' 임의 status 주입 → 기본(대기만)으로 폴백(노출 차단).
    both = ("needs_review", "needs_second_review")
    assert resolve_review_statuses("confirmed") == both
    assert resolve_review_statuses("staging") == both
    assert resolve_review_statuses("'; DROP TABLE") == both


# ── DB 미가용 best-effort ────────────────────────────────────────────────────
def test_list_review_queue_db_unavailable_is_best_effort(monkeypatch):
    def _boom(*a, **k):
        raise SQLAlchemyError("db down")

    monkeypatch.setattr(cs, "session_scope", _boom)
    items, total, warnings = cs.list_review_queue()
    # 빈 큐(정상 0건)가 아니라 '조회 실패'임을 warning 으로 구분 — 무음 실패 아님.
    assert items == []
    assert total == 0
    assert warnings and "unavailable" in warnings[0]


# ── 엔드포인트 매핑 (핸들러 직접 호출 · Depends 미경유) ──────────────────────
def test_endpoint_maps_service_output(monkeypatch):
    sample = ReviewQueueItem(
        classification_id="c1", doc_id="d1", filename="a.pdf",
        grade="S1", confidence=0.62, model_version="v-x", status="needs_review",
    )
    monkeypatch.setattr(
        confirm_api, "list_review_queue",
        lambda limit, offset, statuses: ([sample], 7, []),
    )
    resp = confirm_api.review_queue(limit=10, offset=5, status="pending", auth={"mode": "api_key"})
    assert resp.total == 7 and resp.limit == 10 and resp.offset == 5
    assert len(resp.items) == 1 and resp.items[0].grade == "S1"
    assert resp.warnings == []


def test_endpoint_surfaces_warning(monkeypatch):
    monkeypatch.setattr(
        confirm_api, "list_review_queue",
        lambda limit, offset, statuses: ([], 0, ["db unavailable: OperationalError"]),
    )
    resp = confirm_api.review_queue(limit=50, offset=0, status="pending", auth={"mode": "api_key"})
    assert resp.items == [] and resp.total == 0
    assert resp.warnings and "unavailable" in resp.warnings[0]


def test_endpoint_passes_resolved_statuses(monkeypatch):
    seen = {}

    def _capture(limit, offset, statuses):
        seen["statuses"] = statuses
        return [], 0, []

    monkeypatch.setattr(confirm_api, "list_review_queue", _capture)
    confirm_api.review_queue(limit=50, offset=0, status="needs_review", auth={"mode": "api_key"})
    assert seen["statuses"] == ("needs_review",)
