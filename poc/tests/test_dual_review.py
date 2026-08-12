"""고등급 2인검토 게이트 — C-cons.

순수 로직(_apply_dual_review_gate)은 fake repo로, relabel 통합은 Postgres 있을 때만.
기본(flag off)은 단일검수 즉시확정(동작 보존), on이면 고등급은 2인 합의 전 보류.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from koipa.services.confirm_service import _apply_dual_review_gate


class _FakeRepo:
    def __init__(self, reviewers):
        self._reviewers = set(reviewers)
        self.db = object()  # 신뢰도 게이트가 compute_reviewer_reliability(db=repo.db) 호출

    def distinct_reviewers_for_level(self, classification_id, level_id):
        return self._reviewers


class _Cls:
    classification_id = uuid.uuid4()


def _gate(reviewers, label, *, enabled, codes=("TS", "S1")):
    from koipa.config import settings
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


# ── SVC-2: 보류 해제 비대칭 우회 차단 ────────────────────────────────────────


def _gate_held(reviewers, label, prior_status):
    """prior_status 를 넘겨 게이트 호출(보류 해제 경로 검증)."""
    from koipa.config import settings
    warns: list[str] = []
    saved = settings.high_grade_dual_review, settings.high_grade_review_codes
    settings.high_grade_dual_review = True
    settings.high_grade_review_codes = ["TS", "S1"]
    try:
        status, second = _apply_dual_review_gate(
            _FakeRepo(reviewers), _Cls(), 1, label,
            base_status="confirmed", warns=warns, prior_status=prior_status,
        )
        return status, second, warns
    finally:
        settings.high_grade_dual_review, settings.high_grade_review_codes = saved


def test_held_review_low_grade_single_reviewer_stays_held():
    """보류(needs_second_review) 건은 저등급 1인 confirm 으로 해제 불가(비대칭 우회 차단)."""
    status, second, warns = _gate_held({"b"}, "S3", "needs_second_review")
    assert status == "needs_second_review"
    assert second is True
    assert any("held review" in w for w in warns)


def test_held_review_low_grade_two_reviewers_resolves():
    """보류 건도 2인 동의면 저등급 해제 가능(정상 2인 프로세스)."""
    status, second, _ = _gate_held({"b", "c"}, "S3", "needs_second_review")
    assert status == "confirmed"
    assert second is False


def test_not_held_low_grade_unchanged():
    """보류가 아니면 저등급은 종전대로 무게이트(동작 보존)."""
    status, second, _ = _gate_held({"b"}, "S3", "staging")
    assert status == "confirmed"
    assert second is False


# ── 신뢰도 배선 (C-cons 고도화) ──────────────────────────────────────────────


def _gate_trust(monkeypatch, reviewers, rel_map, min_rel):
    """min_reliability + reviewer_trust 모킹으로 게이트 호출."""
    from koipa.config import settings
    import koipa.modules.m6_evaluation.reviewer_trust as rt
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
    from koipa.db import engine
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
        from koipa.config import settings
        from koipa.db import SessionLocal, session_scope
        from koipa.db.models import (
            Classification, ClassificationLevel, Correction, Document,
        )
        from koipa.repositories import ClassifyRepo
        from koipa.schemas.common import Actor
        from koipa.schemas.confirm import RelabelRequest
        from koipa.services.confirm_service import RelabelService

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

    def test_low_grade_confirm_cannot_release_held_review(self, monkeypatch):
        """SVC-2: A가 S3→TS 로 보류시킨 건을 B 1인이 S3 confirm 으로 해제하지 못한다."""
        from koipa.config import settings
        from koipa.db import SessionLocal, session_scope
        from koipa.db.models import (
            Classification, ClassificationLevel, Correction, Document,
        )
        from koipa.repositories import ClassifyRepo
        from koipa.schemas.common import Actor
        from koipa.schemas.confirm import ConfirmRequest, RelabelRequest
        from koipa.services.confirm_service import ConfirmService, RelabelService

        monkeypatch.setattr(settings, "high_grade_dual_review", True)

        s = SessionLocal()
        levels = {lv.level_code: lv.level_id for lv in s.query(ClassificationLevel).all()}
        doc = Document(filename="y.pdf", source_format="pdf")
        s.add(doc)
        s.flush()
        cls = Classification(
            doc_id=doc.doc_id, model_version="v-dr2",
            predicted_level_id=levels["S3"], confidence=0.9, alternatives=[],
        )
        s.add(cls)
        s.commit()
        cid = cls.classification_id
        did = str(doc.doc_id)
        try:
            # A: S3→TS relabel → 보류
            RelabelService().relabel(RelabelRequest(
                doc_id=did, inference_id=cid, original_label="S3", corrected_label="TS",
                reason="비밀", actor=Actor(user_id="rev-A", role="reviewer"),
            ))
            with session_scope() as db:
                assert ClassifyRepo(db).get(cid).status == "needs_second_review"
            # B: 저등급 S3 confirm 1인 → 해제되면 안 됨(여전히 보류)
            res = ConfirmService().confirm(ConfirmRequest(
                doc_id=did, inference_id=cid, confirmed_label="S3",
                actor=Actor(user_id="rev-B", role="reviewer"),
            ))
            assert res.second_review_required is True
            with session_scope() as db:
                assert ClassifyRepo(db).get(cid).status == "needs_second_review"
        finally:
            with session_scope() as db:
                db.query(Correction).filter_by(classification_id=cid).delete()
                db.query(Classification).filter_by(classification_id=cid).delete()
        s.close()
