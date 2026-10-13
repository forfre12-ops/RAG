"""규정 참고 표시 — 서비스·저장소·조회를 실제 PostgreSQL 로 잰다(fullstack).

시험용 DB: `make test-db-up` 또는 pgvector 이미지를 직접 띄우고 `DATABASE_URL` 을 준다.
세션은 시험마다 rollback 한다 — 서비스의 `with self._session()` 이 커밋 대신 flush 만 하는 가짜 범위를 주입한다.
임베더·벡터 저장소·저장소(파일)는 가짜다(DB 외 인프라는 필요 없다).
"""

from __future__ import annotations

import hashlib
import re
import types
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from koipa.adapters.embedding.base import EmbeddingResult
from koipa.adapters.storage.local_store import LocalStorage
from koipa.adapters.vectorstore.document_vectors import DocumentVector
from koipa.db import SessionLocal, engine
from koipa.db.models import AuditLog, Regulation, RegulationClause, RegulationSentence
from koipa.regulation import status as st
from koipa.regulation.splitter import split_regulation
from koipa.services.regulation_evidence_service import (
    REASON_DOC_NOT_INDEXED,
    REASON_EMBEDDER_MISMATCH,
    RegulationEvidenceService,
)
from koipa.services.regulation_service import RegulationError, RegulationService

pytestmark = pytest.mark.fullstack

FIXTURE = Path(__file__).parent / "fixtures" / "regulation" / "sample_org_regulation.md"
MODEL = "fake-emb"


class FakeEmbedder:
    """글자 2-gram 을 128차원에 해시한 결정형 가짜 임베더 — 글자가 겹치는 문장끼리 가깝다."""
    name = "fake"
    dim = 128

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            s = re.sub(r"\s+", "", t)
            for i in range(len(s) - 1):
                v[int(hashlib.md5(s[i:i + 2].encode()).hexdigest(), 16) % self.dim] += 1.0
            out.append(v.tolist())
        return EmbeddingResult(vectors=out, dim=self.dim, model=MODEL)


class FakeVectorStore:
    def __init__(self):
        self.docs: dict[str, DocumentVector] = {}

    def add_text(self, doc_id: str, text_: str, model: str = MODEL):
        self.docs[doc_id] = DocumentVector(doc_id, FakeEmbedder().embed([text_]).vectors[0], model)

    def get(self, doc_id):
        return self.docs.get(str(doc_id))


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except (OperationalError, Exception):  # noqa: BLE001
        return False


@pytest.fixture
def db():
    if not _pg_ok():
        pytest.skip("Postgres not reachable")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


SETTINGS = types.SimpleNamespace(max_upload_mb=1, regulation_max_sentences=3000, regulation_active_max=2,
                                 regulation_evidence_max_items=1, regulation_min_similarity=0.0)


@pytest.fixture
def env(db, tmp_path):
    @contextmanager
    def scope():
        yield db
        db.flush()

    RegulationEvidenceService.reset_singleton()
    holder: dict = {}
    svc = RegulationService(storage=LocalStorage(str(tmp_path)), embedder_factory=FakeEmbedder,
                            dispatcher=lambda rid: holder["svc"].index(rid), session_factory=scope,
                            settings_obj=SETTINGS)
    holder["svc"] = svc
    store = FakeVectorStore()
    evidence = RegulationEvidenceService(vector_store=store, session_factory=scope, settings_obj=SETTINGS,
                                         doc_text_provider=lambda d: store.texts.get(d, ""), document_exists=lambda d: True,
                                         ttl_seconds=0)
    store.texts = {}
    RegulationEvidenceService._instance = evidence          # 서비스의 캐시 무효화가 이 인스턴스를 향한다
    yield types.SimpleNamespace(db=db, svc=svc, store=store, evidence=evidence, tmp=tmp_path)
    RegulationEvidenceService.reset_singleton()


def _upload(env, *, name="문서보안 규정", version="v1", data=None, filename="sample.md"):
    data = FIXTURE.read_bytes() if data is None else data
    return env.svc.register(data=data, filename=filename, name=name, version_label=version, effective_date="2026-09-25",
                            actor_id="tester", actor_role="admin")


def _doc(env, doc_id: str, text_: str):
    env.store.add_text(doc_id, text_)
    env.store.texts[doc_id] = text_


# ── 등록·색인 ───────────────────────────────────────────────────────────────

def test_register_and_index_reaches_ready_with_progress_saved(env):
    res = _upload(env)
    assert res.status == st.INDEXING and not res.duplicate
    d = env.svc.get(res.reg_id)
    assert d["status"] == st.READY and d["split_mode"] == "article"
    assert d["clause_count"] == 55 and d["display_clause_count"] == 32
    assert d["embedded_count"] == d["embed_target_count"] > 0
    assert d["embed_model"] == MODEL and d["warnings"] == []


def test_same_file_is_deduplicated_and_returns_the_existing_id(env):
    a = _upload(env)
    b = _upload(env, name="다른 이름", version="v9")
    assert b.duplicate and b.reg_id == a.reg_id


def test_original_is_stored_in_the_regulations_bucket_and_audited(env):
    res = _upload(env)
    row = env.db.get(Regulation, res.reg_id) or env.db.query(Regulation).one()
    assert row.raw_uri and "regulations-raw" in row.raw_uri
    acts = {a.action for a in env.db.query(AuditLog).filter(AuditLog.target_type == "regulation")}
    assert {"regulation.upload", "regulation.indexed"} <= acts


@pytest.mark.parametrize("kw,code", [
    ({"filename": "x.exe"}, 422), ({"data": b""}, 422), ({"name": " "}, 422), ({"version": ""}, 422),
    ({"data": b"a" * (1024 * 1024 + 1)}, 413),
])
def test_register_rejects_bad_input_with_http_status(env, kw, code):
    with pytest.raises(RegulationError) as ei:
        _upload(env, **kw)
    assert ei.value.status_code == code


def test_no_queue_means_503_before_any_side_effect(env, tmp_path):
    bare = RegulationService(storage=LocalStorage(str(tmp_path / "x")), embedder_factory=FakeEmbedder,
                             session_factory=env.svc._session_factory, settings_obj=SETTINGS)   # dispatcher 없음 → TESTING 이면 큐 없음
    with pytest.raises(RegulationError) as ei:
        bare.register(data=FIXTURE.read_bytes(), filename="a.md", name="n", version_label="v", effective_date=None,
                      actor_id=None, actor_role=None)
    assert ei.value.status_code == 503
    assert env.db.query(Regulation).count() == 0                        # 행도 파일도 만들지 않았다


def test_text_without_clauses_fails_with_a_reason_instead_of_hanging(env):
    res = _upload(env, data="짧다".encode("utf-8"), filename="tiny.txt")
    d = env.svc.get(res.reg_id)
    assert d["status"] == st.FAILED and "조항" in (d["error_message"] or "")


def test_hash_embedder_is_refused_because_it_carries_no_meaning(env):
    class Hashy(FakeEmbedder):
        name = "hash"
    env.svc._embedder_factory = Hashy
    res = _upload(env)
    d = env.svc.get(res.reg_id)
    assert d["status"] == st.FAILED and "임베더" in d["error_message"]


def test_sentence_cap_fails_the_index(env):
    env.svc._settings_obj = types.SimpleNamespace(**{**vars(SETTINGS), "regulation_max_sentences": 5})
    res = _upload(env)
    d = env.svc.get(res.reg_id)
    assert d["status"] == st.FAILED and "상한" in d["error_message"]


def test_retry_continues_without_rebuilding_clauses(env):
    """색인 중간 실패 → 같은 판을 다시 색인하면 조항·문장은 그대로고 임베딩만 이어서 채운다."""
    calls = {"n": 0}
    real = FakeEmbedder()

    class Flaky(FakeEmbedder):
        def embed(self, texts):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("일시 오류")
            return real.embed(texts)
    env.svc._embedder_factory = Flaky
    env.svc._dispatcher = lambda rid: None                           # 발사만 막고 직접 부른다
    res = _upload(env)
    with pytest.raises(RuntimeError):
        env.svc.index(res.reg_id)
    mid = env.svc.get(res.reg_id)
    assert mid["status"] == st.INDEXING and 0 < mid["embedded_count"] < mid["embed_target_count"]
    ids_before = {str(c.id) for c in env.db.query(RegulationClause).all()}
    assert env.svc.index(res.reg_id)["status"] == st.READY
    assert {str(c.id) for c in env.db.query(RegulationClause).all()} == ids_before
    assert env.svc.get(res.reg_id)["embedded_count"] == mid["embed_target_count"]


# ── 활성화 ──────────────────────────────────────────────────────────────────

def test_activation_needs_scope_confirmation(env):
    res = _upload(env)
    with pytest.raises(RegulationError) as ei:
        env.svc.activate(res.reg_id, scope_confirmed=False, scope_note=None, actor_id="a", actor_role="admin")
    assert ei.value.status_code == 422


def test_activation_archives_the_previous_active_version_of_the_same_name(env):
    v1 = _upload(env, version="v1")
    env.svc.activate(v1.reg_id, scope_confirmed=True, scope_note="사내 업무 문서", actor_id="a", actor_role="admin")
    v2 = _upload(env, version="v2", data=FIXTURE.read_bytes() + "\n".encode("utf-8"))
    env.svc.activate(v2.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert env.svc.get(v1.reg_id)["status"] == st.ARCHIVED
    assert env.svc.get(v2.reg_id)["status"] == st.ACTIVE
    # 보관 판을 되살릴 수 있고, 그러면 v2 가 보관된다
    env.svc.activate(v1.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert env.svc.get(v2.reg_id)["status"] == st.ARCHIVED and env.svc.get(v1.reg_id)["status"] == st.ACTIVE


def test_active_cap_counts_distinct_regulation_names(env):
    ids = []
    for i, name in enumerate(("규정A", "규정B", "규정C")):
        r = _upload(env, name=name, data=FIXTURE.read_bytes() + f"\n<!-- {i} -->".encode("utf-8"))
        ids.append(r.reg_id)
    env.svc.activate(ids[0], scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    env.svc.activate(ids[1], scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    with pytest.raises(RegulationError) as ei:                            # 상한 2(SETTINGS)
        env.svc.activate(ids[2], scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert ei.value.status_code == 409


def test_cannot_activate_while_indexing_or_failed(env):
    env.svc._dispatcher = lambda rid: None
    res = _upload(env)                                                    # 색인 전(indexing)
    with pytest.raises(RegulationError) as ei:
        env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert ei.value.status_code == 409


# ── 조회 ────────────────────────────────────────────────────────────────────

def test_evidence_picks_the_relevant_clause_and_stays_empty_when_nothing_is_active(env):
    res = _upload(env)
    _doc(env, "doc-1", "차세대 제품의 도면 회로도 레이아웃 데이터 부품 명세 설계 문서 개발 초기 단계 극비 양산 기밀")
    before = env.evidence.find_for_document("doc-1")
    assert before.items == [] and before.reason == "no_active_regulation" and before.indexed is None
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    got = env.evidence.find_for_document("doc-1")
    assert got.indexed is True and got.reason is None and len(got.items) == 1
    assert got.items[0].article_no == "제41조" and got.items[0].rgltn_nm == "문서보안 규정"
    assert "도면" in got.items[0].sentences[0]


def test_missing_document_is_a_lookup_error_not_an_empty_answer(env):
    res = _upload(env)
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    env.evidence._document_exists = lambda d: False
    with pytest.raises(LookupError):
        env.evidence.find_for_document("없는-문서")
    # 미리보기는 요청 전체를 실패시키지 않고 그 문서만 사유를 단다
    assert env.evidence.preview(res.reg_id, ["없는-문서"])[0].reason == REASON_DOC_NOT_INDEXED


def test_evidence_reasons_for_unindexed_document_and_other_embedder(env):
    res = _upload(env)
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert env.evidence.find_for_document("없는 문서").reason == REASON_DOC_NOT_INDEXED
    env.store.add_text("doc-x", "외부 제공 승인", model="다른-모델")
    env.store.texts["doc-x"] = "외부 제공 승인"
    r = env.evidence.find_for_document("doc-x")
    assert r.reason == REASON_EMBEDDER_MISMATCH and r.items == []


def test_archiving_or_deleting_removes_the_regulation_from_lookups_at_once(env):
    res = _upload(env)
    _doc(env, "d", "외부에 문서를 제공할 때는 등급에 따라 승인을 받는다 협력사 고객 기관")
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    assert env.evidence.find_for_document("d").items
    env.svc.archive(res.reg_id, actor_id="a", actor_role="admin")
    assert env.evidence.find_for_document("d").reason == "no_active_regulation"


def test_grade_list_clause_is_shown_as_a_whole_list(env):
    res = _upload(env)
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    _doc(env, "d", "협력사 고객 기관 외부에 문서를 제공할 때는 다음 기준 극비 기밀 대외비 일반 제공 금지 대표이사 CISO 부서장 승인")
    it = env.evidence.find_for_document("d").items[0]
    assert it.article_no == "제34조" and it.is_grade_list and len(it.sentences) == 4


def test_preview_works_before_activation_with_the_candidate_only(env):
    res = _upload(env)
    _doc(env, "d", "차세대 제품의 도면 회로도 설계 문서 극비")
    out = env.evidence.preview(res.reg_id, ["d", "없는"])
    assert out[0].items and out[0].items[0].article_no == "제41조"
    assert out[1].reason == REASON_DOC_NOT_INDEXED
    assert env.evidence.find_for_document("d").reason == "no_active_regulation"     # 활성이 아니므로 실제 조회에는 안 나온다


# ── 조항 수정·삭제 ──────────────────────────────────────────────────────────

def test_toggling_display_updates_lookups_and_embeds_new_clauses_immediately(env):
    res = _upload(env)
    env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
    clauses = env.svc.list_clauses(res.reg_id, kind="handling", display=True, limit=200, offset=0)["items"]
    c41 = next(c for c in clauses if c["article_no"] == "제41조")
    _doc(env, "d", "차세대 제품의 도면 회로도 설계 문서 극비 양산 기밀")
    assert env.evidence.find_for_document("d").items[0].article_no == "제41조"
    env.svc.update_clause(res.reg_id, c41["clause_id"], display=False, kind=None, actor_id="a", actor_role="admin")
    assert env.evidence.find_for_document("d").items[0].article_no != "제41조"
    # 등급 정의 조항(제11조)을 표시 대상으로 바꾸면 그 자리에서 임베딩된다
    c11 = next(c for c in env.svc.list_clauses(res.reg_id, kind="grade_def", display=None, limit=200, offset=0)["items"]
               if c["article_no"] == "제11조")
    out = env.svc.update_clause(res.reg_id, c11["clause_id"], display=True, kind=None, actor_id="a", actor_role="admin")
    assert out["display"] is True and out["kind_source"] == "admin"
    row = env.db.query(RegulationClause).filter(RegulationClause.article_no == "제11조").one()
    assert row.embedding is not None


def test_update_clause_validates_input_and_status(env):
    env.svc._dispatcher = lambda rid: None
    res = _upload(env)
    with pytest.raises(RegulationError) as ei:                            # 색인 중에는 수정 불가
        env.svc.update_clause(res.reg_id, "00000000-0000-0000-0000-000000000000", display=True, kind=None,
                              actor_id="a", actor_role="admin")
    assert ei.value.status_code == 409
    env.svc.index(res.reg_id)
    with pytest.raises(RegulationError) as ei2:
        env.svc.update_clause(res.reg_id, "00000000-0000-0000-0000-000000000000", display=None, kind="엉뚱한", actor_id="a", actor_role="admin")
    assert ei2.value.status_code == 422


def test_delete_removes_clauses_sentences_and_original_and_allows_reupload(env):
    res = _upload(env)
    key = next(env.tmp.rglob("sample.md"), None)
    assert key is not None
    with pytest.raises(RegulationError) as ei:                            # 사용 중인 판은 지우지 못한다
        env.svc.activate(res.reg_id, scope_confirmed=True, scope_note=None, actor_id="a", actor_role="admin")
        env.svc.delete(res.reg_id, actor_id="a", actor_role="admin")
    assert ei.value.status_code == 409
    env.svc.archive(res.reg_id, actor_id="a", actor_role="admin")
    env.svc.delete(res.reg_id, actor_id="a", actor_role="admin")
    assert env.db.query(RegulationClause).count() == 0 and env.db.query(RegulationSentence).count() == 0
    assert not list(env.tmp.rglob("sample.md"))                           # 원본 파일도 지워졌다
    with pytest.raises(RegulationError) as ei2:
        env.svc.get(res.reg_id)
    assert ei2.value.status_code == 404
    again = _upload(env)                                                  # 삭제한 판은 중복 검사에서 빠진다
    assert not again.duplicate and again.reg_id != res.reg_id


def test_list_filters_by_status_and_excludes_deleted(env):
    a = _upload(env, name="A")
    _upload(env, name="B", data=FIXTURE.read_bytes() + b"\n<!-- b -->")
    out = env.svc.list(status=st.READY, limit=10, offset=0)
    assert out["total"] == 2
    env.svc.delete(a.reg_id, actor_id="a", actor_role="admin")
    assert env.svc.list(status=None, limit=10, offset=0)["total"] == 1
    with pytest.raises(RegulationError):
        env.svc.list(status="엉뚱", limit=10, offset=0)


def test_split_result_used_by_the_service_matches_the_pure_splitter(env):
    res = _upload(env)
    n = split_regulation(FIXTURE.read_text(encoding="utf-8"))
    assert env.svc.get(res.reg_id)["clause_count"] == len(n.clauses)
