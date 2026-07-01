"""고등급 2인검토 게이트 — C-cons.

순수 로직(_apply_dual_review_gate)은 fake repo로, relabel 통합은 Postgres 있을 때만.
기본(flag off)은 단일검수 즉시확정(동작 보존), on이면 고등급은 2인 합의 전 보류.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from lloydk.services.confirm_service import _apply_dual_review_gate


class _FakeRepo:
    def __init__(self, reviewers):
        self._reviewers = set(reviewers)
        self.db = object()  # 신뢰도 게이트가 compute_reviewer_reliability(db=repo.db) 호출

    def distinct_reviewers_for_level(self, classification_id, level_id):
        return self._reviewers


class _Cls:
    classification_id = uuid.uuid4()


def _gate(reviewers, label, *, enabled, codes=("TS", "S1")):
    from lloydk.config import settings
    warns: list[str] = []
    # monkeypatch 없이 직접 — 호출 후 원복은 _restore_settings가 일부만 하므로 명시 복원.
    saved_enabled = settings.high_grade_dual_review
    saved_codes = settings.high_grade_review_codes
    settings.high_grade_dual_review = enabled
    settings.high_grade_review_codes = list(codes)
    try:
        status, second = _apply_dual_review_gate(
            _FakeRepo(reviewers), _Cls(), 1, label, base_status="corrected", warns=warns
        )
        return status, second, warns
    finally:
        settings.high_grade_dual_review = saved_enabled
        settings.high_grade_review_codes = saved_codes


def test_disabled_is_behavior_preserving():
    status, second, _ = _gate({"a"}, "TS", enabled=False)
    assert status == "corrected"
    assert second is False


def test_low_grade_not_gated():
    status, second, _ = _gate({"a"}, "S2", enabled=True)
    assert status == "corrected"
    assert second is False


def test_high_grade_single_reviewer_held():
    status, second, warns = _gate({"a"}, "TS", enabled=True)
    assert status == "needs_second_review"
    assert second is True
    assert any("second distinct reviewer" in w for w in warns)


def test_high_grade_two_reviewers_confirmed():
    status, second, warns = _gate({"a", "b"}, "S1", enabled=True)
    assert status == "corrected"
    assert second is False
    assert any("dual-review satisfied" in w for w in warns)


# ── 신뢰도 배선 (C-cons 고도화) ──────────────────────────────────────────────


def _gate_trust(monkeypatch, reviewers, rel_map, min_rel):
    """min_reliability + reviewer_trust 모킹으로 게이트 호출."""
    from lloydk.config import settings
    import lloydk.modules.m6_evaluation.reviewer_trust as rt
    monkeypatch.setattr(settings, "high_grade_dual_review", True)
    monkeypatch.setattr(settings, "high_grade_review_codes", ["TS", "S1"])
    monkeypatch.setattr(settings, "high_grade_review_min_reliability", min_rel)
    monkeypatch.setattr(rt, "compute_reviewer_reliability",
                        lambda **kw: [{"reviewer_id": k, "reliability": v} for k, v in rel_map.items()])
    warns: list[str] = []
    return _apply_dual_review_gate(_FakeRepo(reviewers), _Cls(), 1, "TS",
                                   base_status="corrected", warns=warns) + (warns,)


def test_low_trust_reviewer_excluded_from_quorum(monkeypatch):
    # a=0.9(신뢰), b=0.2(저신뢰) · min_rel 0.5 → b 제외 → 정족수 1<2 → 보류
    status, second, warns = _gate_trust(monkeypatch, {"a", "b"}, {"a": 0.9, "b": 0.2}, 0.5)
    assert status == "needs_second_review"
    assert second is True
    assert any("low-trust" in w for w in warns)


def test_two_trusted_reviewers_confirmed(monkeypatch):
    # 둘 다 신뢰 ≥ 0.5 → 정족수 2 → 확정
    status, second, _ = _gate_trust(monkeypatch, {"a", "b"}, {"a": 0.9, "b": 0.8}, 0.5)
    assert status == "corrected"
    assert second is False


def test_new_reviewer_trusted_by_default(monkeypatch):
    # c는 신뢰도 이력 없음(map에 없음) → 1.0 취급(신뢰) → a(0.9)+c → 정족수 2 → 확정
    status, second, _ = _gate_trust(monkeypatch, {"a", "c"}, {"a": 0.9}, 0.5)
    assert status == "corrected"
    assert second is False


# ── DB-backed: relabel 경로 통합 ─────────────────────────────────────────────


def _pg_ok() -> bool:
    from lloydk.db import engine
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def db_backed(obj):
    # PG만 필요(ES 불요) — fullstack 게이트 대신 require_pg로 PG-down만 skip.
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


@db_backed
class TestRelabelDualReviewLive:
    def test_high_grade_relabel_held_until_second_reviewer(self, monkeypatch):
        from lloydk.config import settings
        from lloydk.db import SessionLocal, session_scope
        from lloydk.db.models import (
            Classification, ClassificationLevel, Correction, Document,
        )
        from lloydk.repositories import ClassifyRepo
        from lloydk.schemas.common import Actor
        from lloydk.schemas.confirm import RelabelRequest
        from lloydk.services.confirm_service import RelabelService

        monkeypatch.setattr(settings, "high_grade_dual_review", True)

        s = SessionLocal()
        levels = {lv.level_code: lv.level_id for lv in s.query(ClassificationLevel).all()}
        doc = Document(filename="x.pdf", source_format="pdf")
        s.add(doc)
        s.flush()
        cls = Classification(
            doc_id=doc.doc_id, model_version="v-dr",
            predicted_level_id=levels["S3"], confidence=0.9, alternatives=[],
        )
        s.add(cls)
        s.commit()
        cid = cls.classification_id
        did = str(doc.doc_id)
        try:
            svc = RelabelService()
            req1 = RelabelRequest(
                doc_id=did, inference_id=cid, original_label="S3", corrected_label="TS",
                reason="비밀", actor=Actor(user_id="reviewer-1", role="reviewer"),
            )
            r1 = svc.relabel(req1)
            assert r1.second_review_required is True
            with session_scope() as db:
                assert ClassifyRepo(db).get(cid).status == "needs_second_review"

            # 다른 검수자가 같은 등급에 동의 → 확정
            req2 = RelabelRequest(
                doc_id=did, inference_id=cid, original_label="S3", corrected_label="TS",
                reason="동의", actor=Actor(user_id="reviewer-2", role="reviewer"),
            )
            r2 = svc.relabel(req2)
            assert r2.second_review_required is False
            with session_scope() as db:
                assert ClassifyRepo(db).get(cid).status == "corrected"
        finally:
            with session_scope() as db:
                db.query(Correction).filter_by(classification_id=cid).delete()
                db.query(Classification).filter_by(classification_id=cid).delete()
        s.close()
