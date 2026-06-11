"""Track A — 멀티테넌트 격리 회귀 테스트 (H7/H8/H9/H10).

배경(2026-06-11): 비동기 작업/answer/confirm·relabel 경로에 객체 수준 권한
(IDOR/BOLA) 누락이 있었다. 본 테스트는 다음 4개의 방어선을 고정한다.

  H7  /answer가 body tenant_id를 그대로 신뢰하지 않고 resolve_effective_tenant로
      결속한 eff_tenant로만 벡터 검색을 스코프한다(불일치 403).
  H8  eff_tenant와 일치하지 않는 hit는 방어적 사후 필터로 버린다(벡터스토어
      filter 미적용 대비 fail-CLOSED). eff_tenant=None이면 multi-tenant hit 차단.
  H9  /classify/jobs/{id}가 job에 보존된 tenant와 호출자 tenant가 일치할 때만
      결과를 반환한다(교차테넌트 job_id 추측 차단).
  H10 confirm/relabel 쓰기 경로가 inference_id만으로 임의 테넌트 분류에 쓰지
      못한다(tenant 정확 일치만 통과, None 호출자 vs 실테넌트 분류는 거부).
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from lloydk.schemas.rag_answer import RagAnswerRequest
from lloydk.services.async_classify_service import AsyncClassifyService
from lloydk.services.job_store import reset_default_store


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _req(*, auth_mode: str = "api_key", auth_tenant: str | None = None):
    """request.state.auth_*만 가진 최소 요청 모사."""
    return SimpleNamespace(
        state=SimpleNamespace(auth_mode=auth_mode, auth_tenant=auth_tenant)
    )


class _StubHit:
    def __init__(self, _id: str, score: float, payload: dict):
        self.id = _id
        self.score = score
        self.payload = payload


# ===========================================================================
# H7/H8 — /answer tenant 결속 + 방어적 사후 필터
# ===========================================================================

def _patch_answer_search(monkeypatch, hits, capture):
    """answer._fetch_hits 내부의 lazy import 대상들을 stub으로 교체.

    capture['filter']에 expand_then_search에 전달된 filter를 기록한다.
    """
    import lloydk.adapters.embedding as emb_mod
    import lloydk.adapters.vectorstore as vs_mod
    import lloydk.services.retrieval as retr_mod

    monkeypatch.setattr(vs_mod, "build_store", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(
        emb_mod, "build_embedder",
        lambda *a, **k: SimpleNamespace(embed=lambda texts: SimpleNamespace(vectors=[[0.1] for _ in texts])),
        raising=False,
    )

    def _fake_search(*, filter=None, **kwargs):  # noqa: A002
        capture["filter"] = filter
        return list(hits)

    monkeypatch.setattr(retr_mod, "expand_then_search", _fake_search, raising=False)


def test_answer_binds_tenant_and_forces_filter(monkeypatch):
    """H7: api_key+검증된 tenant=A 호출자는 filter={'tenant_id':'A'}로만 검색."""
    from lloydk.api.answer import _fetch_hits

    capture: dict = {}
    hits = [_StubHit("c1", 0.9, {"doc_id": "d1", "tenant_id": "A", "text": "x"})]
    _patch_answer_search(monkeypatch, hits, capture)

    req = RagAnswerRequest(query="q", tenant_id="A")
    request = _req(auth_mode="api_key", auth_tenant="A")
    out = _fetch_hits(req, request)

    assert capture["filter"] == {"tenant_id": "A"}
    assert [h.source_doc for h in out] == ["d1"]


def test_answer_rejects_cross_tenant_body(monkeypatch):
    """H7: 검증된 tenant=A인데 body가 tenant_id=B면 403(누출 전에 차단)."""
    from lloydk.api.answer import _fetch_hits

    capture: dict = {}
    _patch_answer_search(monkeypatch, [], capture)

    req = RagAnswerRequest(query="q", tenant_id="B")
    request = _req(auth_mode="api_key", auth_tenant="A")
    with pytest.raises(HTTPException) as ei:
        _fetch_hits(req, request)
    assert ei.value.status_code == 403


def test_answer_postfilter_drops_foreign_tenant_hits(monkeypatch):
    """H8: 벡터스토어 filter가 새어 타 tenant hit를 돌려줘도 사후 필터가 버린다."""
    from lloydk.api.answer import _fetch_hits

    capture: dict = {}
    hits = [
        _StubHit("c1", 0.9, {"doc_id": "d1", "tenant_id": "A", "text": "ok"}),
        _StubHit("c2", 0.8, {"doc_id": "d2", "tenant_id": "B", "text": "leak"}),  # 누출 시도
        _StubHit("c3", 0.7, {"doc_id": "d3", "text": "no-tenant"}),  # tenant 누락 → 버림
    ]
    _patch_answer_search(monkeypatch, hits, capture)

    req = RagAnswerRequest(query="q", tenant_id="A")
    request = _req(auth_mode="api_key", auth_tenant="A")
    out = _fetch_hits(req, request)

    assert [h.source_doc for h in out] == ["d1"]  # B와 무-tenant는 제거


def test_answer_none_tenant_drops_multitenant_hits(monkeypatch):
    """H8: 단일 공유키 레거시(eff_tenant=None)에서 tenant 있는 hit를 무필터로 새지
    않게 — payload에 tenant_id 없는(단일테넌트) hit만 통과."""
    from lloydk.api.answer import _fetch_hits

    capture: dict = {}
    hits = [
        _StubHit("c1", 0.9, {"doc_id": "single", "text": "ok"}),  # 단일테넌트 → 통과
        _StubHit("c2", 0.8, {"doc_id": "d2", "tenant_id": "B", "text": "leak"}),  # 버림
    ]
    _patch_answer_search(monkeypatch, hits, capture)

    req = RagAnswerRequest(query="q")  # tenant 미지정
    request = _req(auth_mode="api_key", auth_tenant=None)  # 미검증 레거시
    out = _fetch_hits(req, request)

    assert capture["filter"] is None  # eff_tenant None → 벡터 filter 없음
    assert [h.source_doc for h in out] == ["single"]


def test_answer_reads_tenant_from_metadata(monkeypatch):
    """H7: body.tenant_id 없고 metadata.tenant_id만 있어도 결속 적용."""
    from lloydk.api.answer import _fetch_hits

    capture: dict = {}
    hits = [_StubHit("c1", 0.9, {"doc_id": "d1", "tenant_id": "A", "text": "x"})]
    _patch_answer_search(monkeypatch, hits, capture)

    req = RagAnswerRequest(query="q", metadata={"tenant_id": "A"})
    request = _req(auth_mode="api_key", auth_tenant="A")
    out = _fetch_hits(req, request)

    assert capture["filter"] == {"tenant_id": "A"}
    assert [h.source_doc for h in out] == ["d1"]


# ===========================================================================
# H9 — async job tenant 스코프
# ===========================================================================

@pytest.fixture(autouse=True)
def _fresh_store():
    reset_default_store()
    yield
    reset_default_store()


def _async_req(doc_id: str, tenant_id: str | None):
    from lloydk.schemas.classify_async import ClassifyAsyncRequest

    return ClassifyAsyncRequest(doc_id=doc_id, content="회사 내부 자료", tenant_id=tenant_id)


def test_job_status_blocks_cross_tenant(monkeypatch):
    """H9: tenant=A로 제출된 job을 tenant=B 호출자가 조회하면 None(→404)."""
    svc = AsyncClassifyService()
    res = svc.submit_async(_async_req("a-1", tenant_id="A"))

    # 소유 tenant(A)는 조회됨
    ok = svc.get_status(res.job_id, requester_tenant="A", enforce_tenant=True)
    assert ok is not None and ok.job_id == res.job_id
    # 타 tenant(B)는 차단
    assert svc.get_status(res.job_id, requester_tenant="B", enforce_tenant=True) is None
    # 미검증(None) 호출자도 실-tenant job 조회 차단
    assert svc.get_status(res.job_id, requester_tenant=None, enforce_tenant=True) is None


def test_job_status_legacy_none_matches():
    """H9: 단일테넌트(job tenant=None)는 None 호출자와 일치 — 하위호환 통과."""
    svc = AsyncClassifyService()
    res = svc.submit_async(_async_req("a-2", tenant_id=None))
    got = svc.get_status(res.job_id, requester_tenant=None, enforce_tenant=True)
    assert got is not None
    # 그러나 다른 tenant를 자칭하면 차단
    assert svc.get_status(res.job_id, requester_tenant="A", enforce_tenant=True) is None


def test_job_status_no_enforce_is_backward_compatible():
    """기존 내부 호출(enforce_tenant 미지정)은 tenant 무관 동작 유지."""
    svc = AsyncClassifyService()
    res = svc.submit_async(_async_req("a-3", tenant_id="A"))
    assert svc.get_status(res.job_id) is not None  # 레거시 시그니처


def test_batch_job_stores_tenant():
    """H9: 배치 job도 tenant 보존 — 명시 tenant_id 인자가 우선."""
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.schemas.classify_async import ClassifyBatchRequest

    svc = AsyncClassifyService()
    docs = [ClassifyRequest(doc_id=f"b-{i}", content="자료", tenant_id="A") for i in range(2)]
    res = svc.submit_batch(ClassifyBatchRequest(documents=docs), tenant_id="A")

    assert svc.get_status(res.job_id, requester_tenant="A", enforce_tenant=True) is not None
    assert svc.get_status(res.job_id, requester_tenant="B", enforce_tenant=True) is None


def test_classify_job_status_endpoint_enforces_tenant(monkeypatch):
    """H9(엔드포인트): classify_job_status가 resolve_effective_tenant로 호출자
    tenant를 구해 enforce_tenant=True로 조회 — 교차테넌트 job은 404."""
    import lloydk.api.async_classify as ac

    svc = AsyncClassifyService()
    res = svc.submit_async(_async_req("a-ep", tenant_id="A"))

    # B로 인증한 호출자(검증된 X-Tenant-Id=B) → 404
    request_b = _req(auth_mode="api_key", auth_tenant="B")
    with pytest.raises(HTTPException) as ei:
        ac.classify_job_status(res.job_id, request_b)
    assert ei.value.status_code == 404

    # A로 인증한 호출자 → 정상 반환
    request_a = _req(auth_mode="api_key", auth_tenant="A")
    out = ac.classify_job_status(res.job_id, request_a)
    assert out.job_id == res.job_id


# ===========================================================================
# H10 — confirm/relabel 쓰기 경로 fail-CLOSED
# ===========================================================================

class _MockClassifyRepo:
    """ClassifyRepo 모사 — get()은 tenant 무관 반환, list는 tenant 스코프."""

    def __init__(self, *, cls=None, recent=None):
        self._cls = cls
        self._recent = list(recent or [])
        self.list_tenant_calls: list = []

    def get(self, classification_id):  # noqa: ARG002
        return self._cls

    def list_recent_for_doc(self, doc_id, *, tenant_id=None, limit=10):  # noqa: ARG002
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


def test_confirm_inference_id_fail_closed_for_none_caller_real_tenant():
    """H10: 미검증(None) 호출자가 inference_id로 실-tenant(B) 분류를 confirm하려 하면
    거부(None) — inference_id 추측으로 타 테넌트 분류 쓰기 차단."""
    from lloydk.services.confirm_service import ConfirmService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(cls=cls_b)
    req = _confirm_req(inference_id=uuid.uuid4())
    # None 호출자 + 실-tenant 분류 → fail-CLOSED
    assert ConfirmService._find_classification(repo, req, None) is None
    # 타 tenant(A)도 차단
    assert ConfirmService._find_classification(repo, req, "A") is None
    # 소유 tenant(B)만 통과
    assert ConfirmService._find_classification(repo, req, "B") is cls_b


def test_confirm_inference_id_legacy_none_both_passes():
    """H10: 단일테넌트(분류 tenant=None) + None 호출자는 정확 일치 → 하위호환 통과."""
    from lloydk.services.confirm_service import ConfirmService

    cls_legacy = SimpleNamespace(tenant_id=None, classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(cls=cls_legacy)
    req = _confirm_req(inference_id=uuid.uuid4())
    assert ConfirmService._find_classification(repo, req, None) is cls_legacy


def test_relabel_inference_id_fail_closed_for_none_caller_real_tenant():
    """H10: relabel 쓰기 경로도 동일 — None 호출자 vs 실-tenant 분류 거부."""
    from lloydk.services.confirm_service import RelabelService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(cls=cls_b)
    req = _relabel_req(inference_id=uuid.uuid4())
    assert RelabelService._find_classification(repo, req, None) is None
    assert RelabelService._find_classification(repo, req, "A") is None
    assert RelabelService._find_classification(repo, req, "B") is cls_b


def test_confirm_doc_id_path_scoped_by_tenant():
    """H10: doc_id 경로는 list_recent_for_doc에 tenant를 전달해 repo 계층에서 스코프."""
    from lloydk.services.confirm_service import ConfirmService

    cls_b = SimpleNamespace(tenant_id="B", classification_id=uuid.uuid4())
    repo = _MockClassifyRepo(recent=[cls_b])
    req = _confirm_req(doc_id=str(uuid.uuid4()))
    assert ConfirmService._find_classification(repo, req, "A") is None
    assert repo.list_tenant_calls[-1] == "A"
    assert ConfirmService._find_classification(repo, req, "B") is cls_b
