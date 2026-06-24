"""Fix 1 — 검수 확정(confirm/relabel) 멱등성 회귀.

같은 검수자가 같은 분류를 같은 등급으로 두 번 확정/정정해도 correction이 1건만 남는다
(중복 클릭·재시도·동시 확정 방어). 행 잠금(for_update)은 PG에서 트랜잭션을 직렬화하고,
SQLite는 잠금 절을 생략하므로 본 테스트는 멱등 가드(correction_exists)를 검증한다.

ConfirmService는 자체 session_scope를 열어 커밋하므로, rollback 격리가 불가능하다 →
고유 doc로 seed 후 finally에서 명시 정리한다. PG 미가용 시 자동 skip.

tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text

from lloydk.db import engine, session_scope
from lloydk.db.models import Classification, Correction, Document
from lloydk.repositories import ClassifyRepo
from lloydk.schemas.common import Actor, Grade
from lloydk.schemas.confirm import ConfirmRequest, RelabelRequest
from lloydk.services.confirm_service import ConfirmService, RelabelService

pytestmark = pytest.mark.fullstack


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def seeded():
    if not _pg_ok():
        pytest.skip("Postgres not reachable - start docker compose up -d postgres")
    with session_scope() as db:
        doc = Document(
            filename="d.pdf", source_format="pdf", processing_status="done"
        )
        db.add(doc)
        db.flush()
        repo = ClassifyRepo(db)
        cls = repo.create_classification(
            doc_id=doc.doc_id,
            model_version="v-test",
            predicted_level_id=repo.level_id_by_code("S2"),
            confidence=0.6,
            alternatives=[],
        )
        ctx = {"cls_id": cls.classification_id, "doc_id": doc.doc_id}
    yield ctx
    with session_scope() as db:
        db.execute(delete(Correction).where(Correction.classification_id == ctx["cls_id"]))
        db.execute(delete(Classification).where(Classification.classification_id == ctx["cls_id"]))
        db.execute(delete(Document).where(Document.doc_id == ctx["doc_id"]))


def _count_corrections(cls_id, corrected_by=None) -> int:
    with session_scope() as db:
        q = db.query(Correction).filter_by(classification_id=cls_id)
        if corrected_by:
            q = q.filter_by(corrected_by=corrected_by)
        return q.count()


def test_confirm_idempotent(seeded):
    svc = ConfirmService()
    req = ConfirmRequest(
        doc_id=str(seeded["doc_id"]),
        inference_id=seeded["cls_id"],
        confirmed_label=Grade.TS,  # predicted S2와 다름 → underclass correction
        actor=Actor(user_id="rv@test", role="reviewer"),
    )
    r1 = svc.confirm(req)
    r2 = svc.confirm(req)
    assert r1.persisted and r2.persisted
    assert _count_corrections(seeded["cls_id"]) == 1  # 두 번 확정해도 1건
    assert any("idempotent" in w for w in r2.warnings)


def test_relabel_idempotent(seeded):
    svc = RelabelService()
    req = RelabelRequest(
        doc_id=str(seeded["doc_id"]),
        inference_id=seeded["cls_id"],
        original_label=Grade.S2,
        corrected_label=Grade.TS,
        actor=Actor(user_id="rv2@test", role="reviewer"),
    )
    svc.relabel(req)
    svc.relabel(req)
    assert _count_corrections(seeded["cls_id"], corrected_by="rv2@test") == 1
