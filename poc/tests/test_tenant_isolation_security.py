"""교차테넌트 객체 수준 권한(IDOR/BOLA) 회귀 테스트.

배경(2026-06-02): 인증은 통과해도 doc_id 소유권을 검증하지 않아, 자기 tenant로
인증한 호출자가 body에 타 tenant의 doc_id를 넣어 (a) 본문, (b) 검증 등급,
(c) 분류 결과를 열람할 수 있었다. 본 테스트는 두 방어선을 고정한다:
  1. resolve_effective_tenant: body tenant_id를 인증 컨텍스트에 결속(불일치 403).
  2. repo 계층: 모든 doc 조회가 tenant로 스코프(타 tenant면 미반환).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from lloydk.api._jwt_auth import resolve_effective_tenant


def _req(*, auth_mode: str = "api_key", auth_tenant: str | None = None):
    """request.state.auth_*만 가진 최소 요청 모사."""
    return SimpleNamespace(state=SimpleNamespace(auth_mode=auth_mode, auth_tenant=auth_tenant))


# ---------------------------------------------------------------------------
# resolve_effective_tenant — 인증 결속 정책 (lite, DB 불필요)
# ---------------------------------------------------------------------------

def test_jwt_matching_tenant_returns_auth_tenant():
    assert resolve_effective_tenant(_req(auth_mode="jwt", auth_tenant="A"), "A") == "A"


def test_jwt_none_body_defaults_to_auth_tenant():
    assert resolve_effective_tenant(_req(auth_mode="jwt", auth_tenant="A"), None) == "A"


def test_jwt_mismatched_body_tenant_is_403():
    with pytest.raises(HTTPException) as ei:
        resolve_effective_tenant(_req(auth_mode="jwt", auth_tenant="A"), "B")
    assert ei.value.status_code == 403


def test_jwt_without_tenant_claim_rejects_body_tenant():
    with pytest.raises(HTTPException) as ei:
        resolve_effective_tenant(_req(auth_mode="jwt", auth_tenant=None), "B")
    assert ei.value.status_code == 403


def test_jwt_without_tenant_claim_and_no_body_is_none():
    assert resolve_effective_tenant(_req(auth_mode="jwt", auth_tenant=None), None) is None


def test_api_key_verified_tenant_mismatch_is_403():
    with pytest.raises(HTTPException) as ei:
        resolve_effective_tenant(_req(auth_mode="api_key", auth_tenant="A"), "B")
    assert ei.value.status_code == 403


def test_api_key_verified_tenant_matches():
    assert resolve_effective_tenant(_req(auth_mode="api_key", auth_tenant="A"), "A") == "A"
    assert resolve_effective_tenant(_req(auth_mode="api_key", auth_tenant="A"), None) == "A"


def test_api_key_legacy_unverified_passes_body_through():
    """단일 공유키 레거시(authoritative tenant 없음): body를 그대로 사용(하위호환)."""
    assert resolve_effective_tenant(_req(auth_mode="api_key", auth_tenant=None), "B") == "B"
    assert resolve_effective_tenant(_req(auth_mode="api_key", auth_tenant=None), None) is None


# ---------------------------------------------------------------------------
# repo 계층 tenant 스코프 (fullstack — Postgres 필요)
# ---------------------------------------------------------------------------

pytestmark_fullstack = pytest.mark.fullstack


def _pg_ok() -> bool:
    try:
        from sqlalchemy import text

        from lloydk.db import engine
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def db():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")
    from lloydk.db import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def two_tenants_doc(db):
    """tenant A, tenant B, B 소유 문서 1건 — 교차테넌트 시나리오 준비."""
    from lloydk.db.models import Document, Tenant

    a = f"t-{uuid.uuid4().hex[:8]}"
    b = f"t-{uuid.uuid4().hex[:8]}"
    db.add(Tenant(tenant_id=a, name=f"Tenant {a}"))
    db.add(Tenant(tenant_id=b, name=f"Tenant {b}"))
    db.flush()
    doc_b = Document(tenant_id=b, filename="secret.pdf", source_format="pdf", processing_status="done")
    db.add(doc_b)
    db.flush()
    return SimpleNamespace(tenant_a=a, tenant_b=b, doc_b=doc_b)


@pytest.mark.fullstack
def test_document_repo_get_scoped_by_tenant(db, two_tenants_doc):
    from lloydk.repositories.document_repo import DocumentRepo

    repo = DocumentRepo(db)
    ctx = two_tenants_doc
    # 타 tenant(A)로는 B의 문서 조회 불가
    assert repo.get(ctx.doc_b.doc_id, tenant_id=ctx.tenant_a) is None
    # 소유 tenant(B)는 조회 가능
    assert repo.get(ctx.doc_b.doc_id, tenant_id=ctx.tenant_b) is not None
    # tenant_id 미지정(레거시)은 하위호환 — 조회됨
    assert repo.get(ctx.doc_b.doc_id) is not None


@pytest.mark.fullstack
def test_document_exists_scoped_by_tenant(db, two_tenants_doc):
    from lloydk.repositories.classify_repo import ClassifyRepo

    repo = ClassifyRepo(db)
    ctx = two_tenants_doc
    assert repo.document_exists(ctx.doc_b.doc_id, tenant_id=ctx.tenant_a) is False
    assert repo.document_exists(ctx.doc_b.doc_id, tenant_id=ctx.tenant_b) is True
    assert repo.document_exists(ctx.doc_b.doc_id) is True  # 레거시


@pytest.mark.fullstack
def test_verified_label_scoped_by_tenant(db, two_tenants_doc):
    from lloydk.db.models import ClassificationLevel, DocumentLabel
    from lloydk.repositories.classify_repo import ClassifyRepo

    ctx = two_tenants_doc
    ts_level = db.query(ClassificationLevel).filter_by(level_code="TS").first()
    if ts_level is None:
        pytest.skip("classification_levels not seeded")
    db.add(
        DocumentLabel(
            doc_id=ctx.doc_b.doc_id,
            level_id=ts_level.level_id,
            labeled_by="human_review",
            labeler_id="r1",
            is_verified=True,
        )
    )
    db.flush()
    repo = ClassifyRepo(db)
    # 타 tenant(A)는 B 문서의 검증 등급 못 봄
    assert repo.get_verified_document_label(ctx.doc_b.doc_id, tenant_id=ctx.tenant_a) is None
    # 소유 tenant(B)는 봄
    assert repo.get_verified_document_label(ctx.doc_b.doc_id, tenant_id=ctx.tenant_b) is not None


@pytest.mark.fullstack
def test_list_recent_for_doc_scoped_by_tenant(db, two_tenants_doc):
    from lloydk.repositories.classify_repo import ClassifyRepo

    ctx = two_tenants_doc
    repo = ClassifyRepo(db)
    level_id = repo.level_id_by_code("S3")
    if level_id is None:
        pytest.skip("classification_levels not seeded")
    repo.create_classification(
        doc_id=ctx.doc_b.doc_id,
        tenant_id=ctx.tenant_b,
        model_version="test",
        predicted_level_id=level_id,
        confidence=0.9,
        alternatives=[],
    )
    db.flush()
    # 타 tenant(A)는 B의 분류 결과 못 봄
    assert repo.list_recent_for_doc(ctx.doc_b.doc_id, tenant_id=ctx.tenant_a, limit=1) == []
    # 소유 tenant(B)는 봄
    assert len(repo.list_recent_for_doc(ctx.doc_b.doc_id, tenant_id=ctx.tenant_b, limit=1)) == 1


# ---------------------------------------------------------------------------
# confirm/relabel 서비스 계층 tenant 스코프 (lite, repo mock — DB 불필요)
# 회귀(2026-06-05): /confirm·/relabel의 _find_classification이 inference_id/doc_id를
# tenant 검증 없이 조회해, 타 tenant로 인증한 reviewer가 남의 분류를 confirm/relabel할
# 수 있었다(IDOR). 서비스가 유효 tenant로 스코프하는지 고정한다.
# ---------------------------------------------------------------------------

class _MockClassifyRepo:
    """ClassifyRepo 모사 — get()은 tenant 무관 반환(실제와 동일), list는 tenant 스코프."""

    def __init__(self, *, cls=None, recent=None):
        self._cls = cls
        self._recent = list(recent or [])
        self.list_tenant_calls: list = []

    def get(self, classification_id, *, for_update=False):  # noqa: ARG002
        return self._cls

    def list_recent_for_doc(self, doc_id, *, tenant_id=None, limit=10, for_update=False):  # noqa: ARG002
        self.list_tenant_calls.append(tenant_id)
        rows = [c for c in self._recent if tenant_id is None or c.tenant_id == tenant_id]
        return rows[:limit]


def _confirm_req(*, inference_id=None, doc_id=None):
    from lloydk.schemas.common import Actor, Grade
    from lloydk.schemas.confirm import ConfirmRequest

    return ConfirmRequest(
        doc_id=doc_id or str(uuid.uuid4()),
        inference_id=inference_id,
        confirmed_label=Grade.S3,
        actor=Actor(user_id="u1", role="reviewer", tenant_id="A"),
    )


def _relabel_req(*, inference_id=None, doc_id=None):
    from lloydk.schemas.common import Actor, Grade
    from lloydk.schemas.confirm import RelabelRequest

    return RelabelRequest(
        doc_id=doc_id or str(uuid.uuid4()),
        inference_id=inference_id,
        original_label=Grade.S2,
        corrected_label=Grade.S1,
        actor=Actor(user_id="u1", role="reviewer", tenant_id="A"),
    )


def test_confirm_find_classification_blocks_cross_tenant_by_inference_id():
    from lloydk.services.confirm_service import ConfirmService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(cls=cls_b)
    req = _confirm_req(inference_id=uuid.uuid4())
    # 타 tenant(A)가 inference_id로 B 소유 분류 조회 → 차단(None)
    assert ConfirmService._find_classification(repo, req, "A") is None
    # 소유 tenant(B)는 조회됨
    assert ConfirmService._find_classification(repo, req, "B") is cls_b
    # tenant_id=None(미검증 호출자)이 inference_id로 실-tenant(B) 분류 쓰기 시도 →
    # fail-CLOSED(None). (H10 2026-06-11: 쓰기 경로는 tenant 정확 일치만 허용 —
    # inference_id 추측을 통한 교차테넌트 confirm/relabel 차단.)
    assert ConfirmService._find_classification(repo, req, None) is None


def test_confirm_find_classification_scopes_doc_id_lookup_by_tenant():
    from lloydk.services.confirm_service import ConfirmService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(recent=[cls_b])
    req = _confirm_req(doc_id=str(uuid.uuid4()))
    # 타 tenant(A)로 doc 조회 → list_recent_for_doc에 tenant_id="A" 전달 → 빈 결과
    assert ConfirmService._find_classification(repo, req, "A") is None
    assert repo.list_tenant_calls[-1] == "A"
    # 소유 tenant(B)는 조회됨
    assert ConfirmService._find_classification(repo, req, "B") is cls_b


def test_relabel_find_classification_blocks_cross_tenant_by_inference_id():
    from lloydk.services.confirm_service import RelabelService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(cls=cls_b)
    req = _relabel_req(inference_id=uuid.uuid4())
    assert RelabelService._find_classification(repo, req, "A") is None
    assert RelabelService._find_classification(repo, req, "B") is cls_b
