"""규정 참고 표시 API 시험 — 권한 표·오류 매핑·응답 모양. 서비스는 가짜라 DB 없이 돈다.

전 구간 흐름(업로드→색인→활성화→조회→삭제, 실제 PostgreSQL)은 test_regulation_service_db.py 의 API 시험이 맡는다.
"""

from __future__ import annotations

import io
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import Settings
from koipa.regulation.index import EvidenceItem
from koipa.services.regulation_evidence_service import EvidenceResult, get_regulation_evidence_service
from koipa.services.regulation_service import RegisterResult, RegulationError, get_regulation_service

API = "/api/v1"
RID = str(uuid.uuid4())
CID = str(uuid.uuid4())
DID = str(uuid.uuid4())


def hdr(role: str) -> dict:
    return {"X-API-Key": "test-key", "X-Actor-Role": role}


DETAIL = {"reg_id": RID, "name": "문서보안 규정", "version_label": "v1", "status": "ready", "clause_count": 3,
          "sentence_count": 9, "effective_date": None, "created_at": None, "activated_at": None,
          "filename": "a.md", "source_format": "md", "split_mode": "article", "embed_model": "m",
          "embed_target_count": 4, "embedded_count": 4, "display_clause_count": 2, "scope_note": None,
          "scope_confirmed": False, "warnings": [], "error_message": None}
CLAUSE = {"clause_id": CID, "seq": 0, "article_no": "제1조", "title": "목적", "chapter": "제1장", "kind": "handling",
          "kind_source": "auto", "display": True, "text": "제1조(목적)\n본문이다 본문이다."}


class FakeService:
    def __init__(self):
        self.calls = []
        self.error: RegulationError | None = None
        self.duplicate = False

    def _maybe_raise(self):
        if self.error:
            raise self.error

    def register(self, **kw):
        self.calls.append(("register", kw))
        self._maybe_raise()
        return RegisterResult(RID, "indexing", self.duplicate)

    def list(self, **kw):
        self._maybe_raise()
        return {"items": [{k: DETAIL[k] for k in ("reg_id", "name", "version_label", "status", "clause_count",
                                                  "sentence_count", "effective_date", "created_at", "activated_at")}],
                "total": 1}

    def get(self, reg_id):
        self._maybe_raise()
        return DETAIL

    def list_clauses(self, reg_id, **kw):
        self._maybe_raise()
        return {"items": [CLAUSE], "total": 1}

    def update_clause(self, reg_id, clause_id, **kw):
        self.calls.append(("update_clause", kw))
        self._maybe_raise()
        return CLAUSE

    def activate(self, reg_id, **kw):
        self.calls.append(("activate", kw))
        self._maybe_raise()
        return {**DETAIL, "status": "active", "scope_confirmed": True}

    def archive(self, reg_id, **kw):
        self._maybe_raise()
        return {**DETAIL, "status": "archived"}

    def delete(self, reg_id, **kw):
        self.calls.append(("delete", kw))
        self._maybe_raise()


class FakeEvidence:
    def __init__(self):
        self.result = EvidenceResult(DID, True, None, [EvidenceItem(
            "rid", "문서보안 규정", "v1", "cid", "제41조", "설계·공정 문서", ("① 도면은 극비로 취급한다.",), False)])
        self.raises: Exception | None = None

    def find_for_document(self, doc_id, *, max_items=None):
        if self.raises:
            raise self.raises
        return self.result

    def preview(self, reg_id, doc_ids):
        if self.raises:
            raise self.raises
        return [self.result for _ in doc_ids]


@pytest.fixture
def client():
    svc, ev = FakeService(), FakeEvidence()
    app.dependency_overrides[get_regulation_service] = lambda: svc
    app.dependency_overrides[get_regulation_evidence_service] = lambda: ev
    try:
        yield TestClient(app), svc, ev
    finally:
        app.dependency_overrides.pop(get_regulation_service, None)
        app.dependency_overrides.pop(get_regulation_evidence_service, None)


def _upload(c, role="admin", name="문서보안 규정", filename="a.md", content=b"x" * 10):
    return c.post(f"{API}/regulations", headers=hdr(role), data={"name": name, "version_label": "v1"},
                  files={"file": (filename, io.BytesIO(content), "text/markdown")})


# ── 기본값·라우트 ──────────────────────────────────────────────────────────

def test_code_default_is_off_and_env_default_flags_are_bounded():
    f = Settings.model_fields
    assert f["regulation_reference_enabled"].default is False               # 기본 꺼짐 — 코드 기본값
    assert f["regulation_evidence_max_items"].default == 1
    assert f["regulation_min_similarity"].default == 0.0
    assert f["regulation_llm_select_enabled"].default is False               # 로컬 LLM 판정 옵션도 기본 꺼짐


def test_every_regulation_route_is_registered_when_enabled():
    paths = set(app.openapi()["paths"])
    for p in ("/regulations", "/regulations/{reg_id}", "/regulations/{reg_id}/clauses",
              "/regulations/{reg_id}/clauses/{clause_id}", "/regulations/{reg_id}/preview",
              "/regulations/{reg_id}/activate", "/regulations/{reg_id}/archive",
              "/documents/{doc_id}/regulation-evidence"):
        assert API + p in paths, p


# ── 권한 ────────────────────────────────────────────────────────────────────

def test_unauthenticated_requests_are_rejected(client):
    c, _, _ = client
    assert c.get(f"{API}/regulations").status_code == 401
    assert c.get(f"{API}/documents/{DID}/regulation-evidence").status_code == 401


@pytest.mark.parametrize("method,path,kw", [
    ("get", "/regulations", {}), ("get", f"/regulations/{RID}", {}), ("get", f"/regulations/{RID}/clauses", {}),
    ("get", f"/documents/{DID}/regulation-evidence", {}),
])
def test_read_roles(client, method, path, kw):
    c, _, _ = client
    for role, ok in (("admin", True), ("reviewer", True), ("kl_backend", True), ("system", False)):
        r = getattr(c, method)(API + path, headers=hdr(role), **kw)
        assert (r.status_code == 200) is ok, (role, r.status_code)
        if not ok:
            assert r.status_code == 403


def test_write_roles_exclude_reviewer_and_system(client):
    c, _, _ = client
    for role, ok in (("admin", True), ("kl_backend", True), ("reviewer", False), ("system", False)):
        assert (_upload(c, role).status_code == 202) is ok, role
        assert (c.post(f"{API}/regulations/{RID}/activate", headers=hdr(role),
                       json={"scope_confirmed": True}).status_code == 200) is ok, role
        assert (c.post(f"{API}/regulations/{RID}/archive", headers=hdr(role)).status_code == 200) is ok, role
        assert (c.delete(f"{API}/regulations/{RID}", headers=hdr(role)).status_code == 204) is ok, role
        assert (c.patch(f"{API}/regulations/{RID}/clauses/{CID}", headers=hdr(role),
                        json={"display": False}).status_code == 200) is ok, role
        assert (c.post(f"{API}/regulations/{RID}/preview", headers=hdr(role),
                       json={"doc_ids": [DID]}).status_code == 200) is ok, role


# ── 등록 ────────────────────────────────────────────────────────────────────

def test_register_returns_202_and_duplicate_returns_200(client):
    c, svc, _ = client
    r = _upload(c)
    assert r.status_code == 202 and r.json() == {"reg_id": RID, "status": "indexing", "duplicate": False}
    svc.duplicate = True
    r2 = _upload(c)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    kw = svc.calls[0][1]
    assert kw["name"] == "문서보안 규정" and kw["filename"] == "a.md" and kw["data"] == b"x" * 10


@pytest.mark.parametrize("status", [413, 422, 503, 409, 404])
def test_service_errors_become_the_same_http_status(client, status):
    c, svc, _ = client
    svc.error = RegulationError(status, "사유")
    assert _upload(c).status_code == status
    assert c.get(f"{API}/regulations/{RID}", headers=hdr("admin")).status_code == status
    assert c.get(f"{API}/regulations/{RID}", headers=hdr("admin")).json()["detail"] == "사유"


def test_missing_form_fields_and_bad_ids_are_422(client):
    c, _, _ = client
    r = c.post(f"{API}/regulations", headers=hdr("admin"), data={"name": "n"},
               files={"file": ("a.md", io.BytesIO(b"x"), "text/markdown")})
    assert r.status_code == 422
    assert c.get(f"{API}/regulations/not-a-uuid", headers=hdr("admin")).status_code == 422
    assert c.get(f"{API}/documents/not-a-uuid/regulation-evidence", headers=hdr("admin")).status_code == 422


def test_activation_body_requires_the_confirmation_field(client):
    c, svc, _ = client
    assert c.post(f"{API}/regulations/{RID}/activate", headers=hdr("admin"), json={}).status_code == 422
    r = c.post(f"{API}/regulations/{RID}/activate", headers=hdr("admin"),
               json={"scope_confirmed": True, "scope_note": "사내 업무 문서"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert svc.calls[-1][1]["scope_note"] == "사내 업무 문서" and svc.calls[-1][1]["scope_confirmed"] is True


def test_preview_limits_the_number_of_documents(client):
    c, _, _ = client
    assert c.post(f"{API}/regulations/{RID}/preview", headers=hdr("admin"), json={"doc_ids": []}).status_code == 422
    assert c.post(f"{API}/regulations/{RID}/preview", headers=hdr("admin"),
                  json={"doc_ids": [DID] * 21}).status_code == 422
    r = c.post(f"{API}/regulations/{RID}/preview", headers=hdr("admin"), json={"doc_ids": [DID, DID]})
    assert r.status_code == 200 and len(r.json()["results"]) == 2


# ── 문서별 조회 ─────────────────────────────────────────────────────────────

def _no_score_keys(obj) -> bool:
    bad = ("score", "similar", "confidence", "distance", "probab")
    if isinstance(obj, dict):
        return all(not any(b in k.lower() for b in bad) and _no_score_keys(v) for k, v in obj.items())
    if isinstance(obj, list):
        return all(_no_score_keys(v) for v in obj)
    return True


def test_evidence_shape_and_no_score_anywhere(client):
    c, _, _ = client
    r = c.get(f"{API}/documents/{DID}/regulation-evidence", headers=hdr("reviewer"))
    assert r.status_code == 200
    body = r.json()
    assert body["items"][0]["clause"]["article_no"] == "제41조"
    assert body["items"][0]["sentences"] == ["① 도면은 극비로 취급한다."]
    assert body["items"][0]["regulation"] == {"reg_id": "rid", "name": "문서보안 규정", "version_label": "v1"}
    assert _no_score_keys(body)                                    # 점수·유사도·신뢰도 필드가 어디에도 없다


def test_empty_evidence_is_a_normal_answer_with_a_reason(client):
    c, _, ev = client
    ev.result = EvidenceResult(DID, None, "no_active_regulation", [])
    r = c.get(f"{API}/documents/{DID}/regulation-evidence", headers=hdr("reviewer"))
    assert r.status_code == 200 and r.json() == {"doc_id": DID, "indexed": None, "reason": "no_active_regulation", "items": []}


def test_missing_document_is_404_and_infrastructure_failure_is_503(client):
    c, _, ev = client
    ev.raises = LookupError("document not found")
    assert c.get(f"{API}/documents/{DID}/regulation-evidence", headers=hdr("admin")).status_code == 404
    ev.raises = RuntimeError("pgvector 없음")
    r = c.get(f"{API}/documents/{DID}/regulation-evidence", headers=hdr("admin"))
    assert r.status_code == 503 and "RuntimeError" in r.json()["detail"]


def test_max_items_is_bounded(client):
    c, _, _ = client
    assert c.get(f"{API}/documents/{DID}/regulation-evidence?max_items=0", headers=hdr("admin")).status_code == 422
    assert c.get(f"{API}/documents/{DID}/regulation-evidence?max_items=4", headers=hdr("admin")).status_code == 422
    assert c.get(f"{API}/documents/{DID}/regulation-evidence?max_items=3", headers=hdr("admin")).status_code == 200


def test_actor_identity_comes_from_the_authenticated_principal_not_the_body(client):
    """감사 신원은 인증 주체다 — 요청이 자기를 다른 사람이라 주장해도 서비스에는 그대로 안 간다."""
    c, svc, _ = client
    _upload(c)
    kw = svc.calls[0][1]
    assert set(kw) >= {"actor_id", "actor_role"} and kw["actor_role"] == "admin"
    assert json.dumps(kw, default=str).count("가짜") == 0


# ── 기동 시 전제 조건 경고 ─────────────────────────────────────────────────

def test_startup_warns_only_when_enabled_with_the_hash_embedder(caplog):
    """켰는데 해시 임베더면 규정 등록이 거절된다 — 등록을 눌러 보기 전에 기동 로그로 알린다. 다른 조합은 조용하다."""
    from types import SimpleNamespace

    from koipa.api.app import _warn_regulation_prerequisites

    with caplog.at_level("WARNING", logger="koipa.api.app"):
        _warn_regulation_prerequisites(SimpleNamespace(regulation_reference_enabled=True, embedding_provider="hash",
                                                       regulation_llm_select_enabled=False, llm_provider="noop"))
    assert "embedding_provider=hash" in caplog.text

    caplog.clear()
    with caplog.at_level("WARNING", logger="koipa.api.app"):
        for enabled, provider in ((False, "hash"), (True, "hf"), (False, "hf")):
            _warn_regulation_prerequisites(
                SimpleNamespace(regulation_reference_enabled=enabled, embedding_provider=provider,
                                regulation_llm_select_enabled=False, llm_provider="noop"))
    assert "regulation_reference_enabled" not in caplog.text


def test_startup_warns_when_the_llm_option_is_on_without_a_local_llm_provider(caplog):
    """로컬 LLM 공급자가 아니면 문서 본문을 보내지 않으므로 관련 규정이 안 뜬다 — 켜 놓고도 아무것도 안 보이는 상태를 기동 로그로 알린다."""
    from types import SimpleNamespace

    from koipa.api.app import _warn_regulation_prerequisites

    def run(**kw):
        caplog.clear()
        with caplog.at_level("WARNING", logger="koipa.api.app"):
            _warn_regulation_prerequisites(SimpleNamespace(regulation_reference_enabled=True, embedding_provider="hf",
                                                           regulation_llm_select_enabled=True, **kw))
        return caplog.text

    assert "llm_not_local" in run(llm_provider="noop")
    assert "llm_not_local" in run(llm_provider="anthropic")
    assert "llm_not_local" not in run(llm_provider="ollama")
    assert "llm_not_local" not in run(llm_provider="vllm")
