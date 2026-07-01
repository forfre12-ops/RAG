"""교정→검증라벨 승급 테스트 — Option 2(비모수 안전 레버).

핵심 계약:
  · 큐(list_pending): admission 통과(사람+finalized) 교정이 있고 같은 등급의 검증라벨이 없는
    문서만. 머신/미확정/이중검수대기 교정은 제외.
  · promote: 최신 admissible 교정을 검증 DocumentLabel(is_verified=True,
    labeled_by='human_review')로 써서 서빙 override(get_verified_document_label)가 그 등급을
    반환하게 한다. 재학습·가중치 변경·전파 없음(폭발반경=문서 1건).
  · 안전: 머신/미확정 교정은 not_promotable. 멱등. expected_label 불일치 가드. 최신
    admissible 규칙(머신이 더 최신이어도 더 옛 사람 교정이 승계).

순수 경로(grade/응답/잘못된 uuid/db 미가용)는 인프라 없이, 전체 흐름은 Postgres 있을 때만.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.db import SessionLocal, engine, session_scope
from lloydk.db.models import (
    Classification,
    ClassificationLevel,
    Correction,
    Document,
    DocumentLabel,
)
from lloydk.repositories import ClassifyRepo
from lloydk.schemas.common import Actor, Grade
from lloydk.schemas.promotion import PromoteRequest
from lloydk.services import promotion_service as ps
from lloydk.services.promotion_service import (
    PromoteResult,
    PromotionService,
    _grade_ok,
    _try_uuid,
    to_promote_response,
)


def _actor(user_id="reviewer_kim", role="reviewer"):
    return Actor(user_id=user_id, role=role)


# ── 순수 경로 (DB 불요) ───────────────────────────────────────────────────────


def test_grade_ok():
    assert _grade_ok("TS") is True
    assert _grade_ok("S3") is True
    assert _grade_ok("XX") is False
    assert _grade_ok(None) is False
    assert _grade_ok("") is False


def test_to_promote_response_roundtrip():
    r = PromoteResult("doc-1", "promoted", Grade.S1, "qa_human", ["w1"])
    resp = to_promote_response(r)
    assert resp.doc_id == "doc-1"
    assert resp.status == "promoted"
    assert resp.promoted_label == Grade.S1
    assert resp.labeler_id == "qa_human"
    assert resp.warnings == ["w1"]


def test_try_uuid():
    u = uuid.uuid4()
    assert _try_uuid(str(u)) == u
    assert _try_uuid("not-a-uuid") is None
    assert _try_uuid(None) is None


def test_promote_invalid_uuid_not_promotable():
    # 잘못된 doc_id는 DB를 건드리지 않고 즉시 not_promotable.
    res = PromotionService().promote(PromoteRequest(doc_id="not-a-uuid", actor=_actor()))
    assert res.status == "not_promotable"
    assert res.promoted_label is None
    assert any("UUID" in w for w in res.warnings)


def test_promote_db_unavailable_graceful(monkeypatch):
    """session_scope가 SQLAlchemyError를 던지면 예외 대신 not_promotable + 사유."""

    class _Boom:
        def __enter__(self):
            raise OperationalError("x", {}, Exception("db down"))

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ps, "session_scope", lambda: _Boom())
    res = PromotionService().promote(
        PromoteRequest(doc_id=str(uuid.uuid4()), actor=_actor())
    )
    assert res.status == "not_promotable"
    assert any("persistence skipped" in w for w in res.warnings)


# ── DB-backed ─────────────────────────────────────────────────────────────────


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


def db_backed(obj):
    return pytest.mark.usefixtures("require_pg")(obj)


@pytest.fixture
def require_pg():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def levels(db):
    return {lv.level_code: lv.level_id for lv in db.query(ClassificationLevel).all()}


def _seed_doc(db, *, preview="승급 테스트 본문 충분히 김") -> Document:
    doc = Document(filename="p.pdf", source_format="pdf", text_preview=preview)
    db.add(doc)
    db.flush()
    return doc


def _seed_cls(db, levels, doc, code, version, *, status="confirmed") -> Classification:
    cls = Classification(
        doc_id=doc.doc_id,
        model_version=version,
        predicted_level_id=levels[code],
        confidence=0.9,
        alternatives=[],
        status=status,
    )
    db.add(cls)
    db.flush()
    return cls


def _add_corr(db, cls, levels, orig, new, by, *, direction="underclass") -> Correction:
    c = Correction(
        classification_id=cls.classification_id,
        original_level_id=levels[orig],
        corrected_level_id=levels[new],
        direction=direction,
        corrected_by=by,
    )
    db.add(c)
    db.flush()
    return c


def _cleanup(version, doc_ids, corr_ids):
    with session_scope() as s:
        if corr_ids:
            s.query(Correction).filter(
                Correction.correction_id.in_(corr_ids)
            ).delete(synchronize_session=False)
        s.query(Classification).filter(
            Classification.model_version == version
        ).delete(synchronize_session=False)
        if doc_ids:
            s.query(DocumentLabel).filter(
                DocumentLabel.doc_id.in_(doc_ids)
            ).delete(synchronize_session=False)
            s.query(Document).filter(
                Document.doc_id.in_(doc_ids)
            ).delete(synchronize_session=False)


def _level_code(level_id):
    with session_scope() as s:
        return s.query(ClassificationLevel.level_code).filter_by(level_id=level_id).scalar()


@db_backed
class TestPromotionLive:
    def test_promote_creates_verified_human_label(self, db, levels):
        version = f"v-prom-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c = _add_corr(db, cls, levels, "S3", "S1", "qa_human")
        db.commit()
        try:
            res = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor("reviewer_kim"))
            )
            assert res.status == "promoted"
            assert res.promoted_label == Grade.S1
            assert res.labeler_id == "qa_human"
            # 서빙 hook(get_verified_document_label)이 보는 라벨이 정확히 박혔는가.
            with session_scope() as s:
                dl = ClassifyRepo(s).get_verified_document_label(doc.doc_id)
                assert dl is not None
                assert dl.is_verified is True
                assert dl.labeled_by == "human_review"   # 서빙 override 1순위 출처
                assert dl.verified_by == "reviewer_kim"
                assert dl.labeler_id == "qa_human"
                assert _level_code(dl.level_id) == "S1"
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_serving_override_fires_after_promote(self, db, levels):
        """승급 후 ClassifyService.classify가 모델을 스킵하고 승급 등급을 반환한다(E2E)."""
        version = f"v-srv-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c = _add_corr(db, cls, levels, "S3", "TS", "qa_human")
        db.commit()
        try:
            PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor("reviewer_kim"))
            )
            from lloydk.schemas.classify import ClassifyRequest
            from lloydk.services.classify_service import ClassifyService

            resp = ClassifyService().classify(
                ClassifyRequest(
                    doc_id=str(doc.doc_id),
                    content="임의 본문 — 검증라벨이 있으면 모델 추론은 스킵돼야 한다",
                    text_already_preprocessed=True,
                )
            )
            assert resp.label == Grade.TS
            assert any("verified_label_applied" in w for w in resp.warnings)
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_machine_correction_not_promotable(self, db, levels):
        version = f"v-mac-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c = _add_corr(db, cls, levels, "S3", "TS", "ai_assist")  # 머신 검수자
        db.commit()
        try:
            res = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor())
            )
            assert res.status == "not_promotable"
            # 큐에도 없다.
            pending_docs = {it.doc_id for it in PromotionService().list_pending(limit=1000)}
            assert str(doc.doc_id) not in pending_docs
            # 검증라벨도 안 생겼다.
            with session_scope() as s:
                assert ClassifyRepo(s).get_verified_document_label(doc.doc_id) is None
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_needs_second_review_not_promotable(self, db, levels):
        """고등급 1인 동의(needs_second_review)는 admission 아님 → 승급 불가(dual-review 자동 존중)."""
        version = f"v-nsr-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="needs_second_review")
        c = _add_corr(db, cls, levels, "S3", "TS", "qa_human")
        db.commit()
        try:
            res = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor())
            )
            assert res.status == "not_promotable"
            pending_docs = {it.doc_id for it in PromotionService().list_pending(limit=1000)}
            assert str(doc.doc_id) not in pending_docs
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_promote_idempotent(self, db, levels):
        version = f"v-idem-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c = _add_corr(db, cls, levels, "S3", "S1", "qa_human")
        db.commit()
        try:
            r1 = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor())
            )
            r2 = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor())
            )
            assert r1.status == "promoted"
            assert r2.status == "already_promoted"
            assert r2.promoted_label == Grade.S1
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_expected_label_mismatch_guard(self, db, levels):
        """expected_label이 현재 최신 교정 등급과 다르면 거부(라벨 안 씀)."""
        version = f"v-mis-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c = _add_corr(db, cls, levels, "S3", "S1", "qa_human")  # 최신 교정 = S1
        db.commit()
        try:
            res = PromotionService().promote(
                PromoteRequest(
                    doc_id=str(doc.doc_id),
                    actor=_actor(),
                    expected_label=Grade.TS,  # 큐에서 본 등급과 어긋남
                )
            )
            assert res.status == "mismatch"
            with session_scope() as s:
                assert ClassifyRepo(s).get_verified_document_label(doc.doc_id) is None
        finally:
            _cleanup(version, [doc.doc_id], [c.correction_id])

    def test_latest_admissible_human_wins_over_newer_machine(self, db, levels):
        """같은 분류에 더 최신 머신 교정(TS)이 있어도, 더 옛 사람 교정(S1)이 제안 등급."""
        version = f"v-win-{uuid.uuid4().hex[:6]}"
        doc = _seed_doc(db)
        cls = _seed_cls(db, levels, doc, "S3", version, status="confirmed")
        c_human = _add_corr(db, cls, levels, "S3", "S1", "qa_human")   # 먼저(더 옛, 낮은 id)
        c_machine = _add_corr(db, cls, levels, "S3", "TS", "ai_assist")  # 나중(더 최신, 높은 id)
        db.commit()
        try:
            res = PromotionService().promote(
                PromoteRequest(doc_id=str(doc.doc_id), actor=_actor())
            )
            assert res.status == "promoted"
            assert res.promoted_label == Grade.S1  # 사람 교정 승계(머신 최신 무시)
            assert res.labeler_id == "qa_human"
        finally:
            _cleanup(version, [doc.doc_id], [c_human.correction_id, c_machine.correction_id])

    def test_list_pending_includes_admissible_excludes_promoted(self, db, levels):
        version = f"v-list-{uuid.uuid4().hex[:6]}"
        doc1 = _seed_doc(db)
        cls1 = _seed_cls(db, levels, doc1, "S3", version, status="confirmed")
        c1 = _add_corr(db, cls1, levels, "S3", "S1", "qa_human")
        doc2 = _seed_doc(db)
        cls2 = _seed_cls(db, levels, doc2, "S3", version, status="confirmed")
        c2 = _add_corr(db, cls2, levels, "S3", "TS", "qa_human")
        db.commit()
        try:
            svc = PromotionService()
            before = {it.doc_id: it for it in svc.list_pending(limit=1000)}
            assert str(doc1.doc_id) in before
            assert str(doc2.doc_id) in before
            assert before[str(doc1.doc_id)].proposed_label == Grade.S1
            assert before[str(doc1.doc_id)].corrected_by == "qa_human"

            # doc1 승급 후엔 큐에서 빠지고, doc2는 남는다.
            svc.promote(PromoteRequest(doc_id=str(doc1.doc_id), actor=_actor()))
            after = {it.doc_id for it in svc.list_pending(limit=1000)}
            assert str(doc1.doc_id) not in after
            assert str(doc2.doc_id) in after
        finally:
            _cleanup(version, [doc1.doc_id, doc2.doc_id], [c1.correction_id, c2.correction_id])


# ── API 라우터 (TestClient — RBAC·파싱·response_model) ────────────────────────


def _hdr(role="admin"):
    from lloydk.config import settings

    return {"X-API-Key": settings.api_key, "X-Actor-Role": role}


@pytest.mark.slow
class TestPromotionAPI:
    def test_pending_requires_auth(self):
        from fastapi.testclient import TestClient

        from lloydk.api.app import app

        with TestClient(app) as cli:
            r = cli.get("/api/v1/promotions/pending")
            assert r.status_code in (401, 422)

    @pytest.mark.parametrize("role", ["admin", "reviewer"])
    def test_pending_allowed_roles_ok(self, role):
        from fastapi.testclient import TestClient

        from lloydk.api.app import app

        with TestClient(app) as cli:
            r = cli.get("/api/v1/promotions/pending", headers=_hdr(role))
            assert r.status_code == 200, r.text
            body = r.json()
            assert "count" in body and "items" in body
            assert body["count"] == len(body["items"])

    def test_pending_forbidden_role(self):
        from fastapi.testclient import TestClient

        from lloydk.api.app import app

        with TestClient(app) as cli:
            r = cli.get("/api/v1/promotions/pending", headers=_hdr("system"))
            assert r.status_code == 403, r.text

    def test_promote_non_uuid_not_promotable(self):
        from fastapi.testclient import TestClient

        from lloydk.api.app import app

        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/promotions/promote",
                headers=_hdr("reviewer"),
                json={"doc_id": "non-uuid", "actor": {"user_id": "u", "role": "reviewer"}},
            )
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "not_promotable"
