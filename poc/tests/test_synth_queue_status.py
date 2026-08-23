"""[C1] /synth/queue 승인/반려 조회 — 종전 silent-empty([],0) 제거.

DB 없이 service 배선만 검증: session_scope·SynthRepo를 페이크로 대체해
queue(status=approved|rejected)가 해당 status로 조회하고 결과를 돌려주는지 본다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from contextlib import contextmanager
from types import SimpleNamespace

import koipa.services.synthesis_service as ss
from koipa.services.synthesis_service import SynthesisService


def _fake_sample(status: str) -> SimpleNamespace:
    return SimpleNamespace(
        sample_id=uuid.uuid4(),
        target_level_id=2,
        corrected_level_id=None,   # 교정 없음 — queue 응답의 corrected_grade 가 None 이어야 한다
        doc_type="인사",
        llm_provider="anthropic",
        llm_model="claude",
        quality_score=0.9,
        review_status=status,
        generated_content="본문 미리보기",
        created_at=dt.datetime(2026, 6, 30, tzinfo=dt.timezone.utc),
    )


class _FakeRepo:
    """list_by_status가 받은 status를 기록하고, 그 status의 샘플 1건을 돌려준다."""

    last_status: str | None = None

    def __init__(self, db):  # noqa: D401
        pass

    def list_by_status(self, status, *, limit=50, offset=0):
        _FakeRepo.last_status = status
        return [_fake_sample(status)]

    def count_by_status(self, status):
        return 1


@contextmanager
def _fake_scope():
    yield object()


def _patch(monkeypatch):
    monkeypatch.setattr(ss, "session_scope", _fake_scope)
    monkeypatch.setattr(ss, "SynthRepo", _FakeRepo)
    monkeypatch.setattr(
        SynthesisService, "_level_id_to_code", staticmethod(lambda db, lid: "S1")
    )
    _FakeRepo.last_status = None


def test_queue_approved_not_silently_empty(monkeypatch) -> None:
    _patch(monkeypatch)
    resp = SynthesisService().queue(status="approved")
    assert _FakeRepo.last_status == "approved"  # 'approved'로 실제 조회
    assert resp.total == 1
    assert len(resp.items) == 1
    assert resp.items[0].review_status == "approved"


def test_queue_rejected_not_silently_empty(monkeypatch) -> None:
    _patch(monkeypatch)
    resp = SynthesisService().queue(status="rejected")
    assert _FakeRepo.last_status == "rejected"
    assert resp.total == 1
    assert resp.items[0].review_status == "rejected"


def test_queue_pending_maps_to_pending_review(monkeypatch) -> None:
    _patch(monkeypatch)
    resp = SynthesisService().queue(status="pending")
    assert _FakeRepo.last_status == "pending_review"  # API 'pending' → DB 'pending_review'
    assert resp.total == 1
