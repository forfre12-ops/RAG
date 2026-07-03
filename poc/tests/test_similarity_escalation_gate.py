"""유사도 escalation 게이트 테스트 — Phase 2(비모수 안전 레버, 등급 무변경).

계약:
  · 들어온 문서가 권위(사람 검수·법적 지정) 출처가 더 높은(더 비밀) 등급으로 검증한 *다른*
    문서와 매우 유사(dense 코사인 ≥ τ)하면 needs_review 라우팅. 등급은 절대 안 바뀜(신호만).
  · FNR-safe: 이웃이 모델 예측보다 *엄격히 더 비밀*일 때만 발동(동급/하급 발동 안 함).
  · 기본 OFF(비파괴). raw-코사인 백엔드(pg/inmemory)에서만 동작(es=no-op). 임베딩/검색/DB
    오류는 전부 fail-open + fail-open 카운터로 가시화.
  · 자기 자신/중복 이웃 제외. 임계 미만 제외. 머신라벨 이웃 제외. 최고등급 예측은 스킵.

결정론 단위 테스트 — 실 임베더/벡터스토어/DB 불요(전부 monkeypatch). store.search·임베더·
이웃 등급조회를 canned 값으로 주입하고, 게이트를 fake-self에 bound로 호출한다.
"""

from __future__ import annotations

import types

import pytest

from lloydk.adapters.vectorstore.base import SearchHit
from lloydk.schemas.common import Grade, GradeRegistry
from lloydk.services.classify_service import ClassifyService

GATE = ClassifyService._similarity_escalation_gate  # 언바운드 — fake-self로 호출


def _order():
    # 낮을수록 더 비밀(TS=1 ... S3=4). 삽입순서 = get_order 계약(첫 키 = 최고등급).
    return {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


class _FakeSelf:
    """게이트가 self에서 쓰는 콜백만 가진 최소 객체(ClassifyService 풀 생성 회피)."""

    def __init__(self, neighbor_grade=None):
        self._neighbor_grade = neighbor_grade
        self.fail_open: list[str] = []
        self.actions: list[str] = []
        self.fired = 0

    def _verified_human_grade(self, nid):  # noqa: ARG002
        return self._neighbor_grade

    def _record_gate_fail_open(self, gate):
        self.fail_open.append(gate)

    def _inc_similarity_escalation(self, action="routed"):
        self.actions.append(action)
        if action == "routed":
            self.fired += 1


def _patch(
    monkeypatch,
    *,
    enabled=True,
    tau=0.92,
    hits=None,
    embed_ok=True,
    search_exc=None,
):
    from lloydk import config as cfg
    import lloydk.adapters.embedding as emb_mod
    import lloydk.adapters.vectorstore as vs_mod

    monkeypatch.setattr(cfg.settings, "similarity_escalation_enabled", enabled, raising=False)
    monkeypatch.setattr(cfg.settings, "similarity_escalation_tau", tau, raising=False)
    monkeypatch.setattr(cfg.settings, "vector_backend", "inmemory", raising=False)  # raw 코사인
    monkeypatch.setattr(cfg.settings, "rag_default_collection", "docs", raising=False)
    monkeypatch.setattr(cfg.settings, "rag_default_top_k", 5, raising=False)
    monkeypatch.setattr(GradeRegistry, "get_order", lambda: _order())

    class _Emb:
        def embed(self, texts):  # noqa: ARG002
            if not embed_ok:
                raise RuntimeError("embed boom")
            return types.SimpleNamespace(vectors=[[0.1, 0.2, 0.3]])

    monkeypatch.setattr(emb_mod, "build_embedder", lambda *a, **k: _Emb())

    class _Store:
        def search(self, collection, query, top_k=5, filter=None):  # noqa: ARG002
            if search_exc is not None:
                raise search_exc
            return list(hits or [])

    monkeypatch.setattr(vs_mod, "build_store", lambda *a, **k: _Store())


# ── 발동/비발동 핵심 ──────────────────────────────────────────────────────────


def test_off_default_is_noop(monkeypatch):
    """기본 OFF면 임베딩/검색 비용 전에 즉시 None(비파괴)."""
    from lloydk import config as cfg
    import lloydk.adapters.embedding as emb_mod

    monkeypatch.setattr(cfg.settings, "similarity_escalation_enabled", False, raising=False)

    def _boom(*a, **k):
        raise AssertionError("disabled gate must not build embedder")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == []  # 비활성은 fail-open 아님


def test_similar_higher_grade_escalates(monkeypatch):
    _patch(monkeypatch, enabled=True, tau=0.92,
           hits=[SearchHit("docA:c0", 0.95, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade="TS")  # 이웃 TS > 예측 S2
    out = GATE(fs, "docSelf", "본문", Grade.S2)
    assert out is not None
    assert "similarity-escalation" in out
    assert "FNR-safe, grade unchanged" in out
    assert "TS" in out
    assert fs.fired == 1
    assert fs.fail_open == []


@pytest.mark.parametrize("neighbor", ["S2", "S3"])
def test_same_or_lower_grade_noop(monkeypatch, neighbor):
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade=neighbor)  # 동급/하급
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fired == 0


def test_below_tau_noop(monkeypatch):
    _patch(monkeypatch, enabled=True, tau=0.92,
           hits=[SearchHit("docA:c0", 0.80, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade="TS")  # 더 비밀이지만 유사도 미달
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fired == 0


def test_self_match_excluded(monkeypatch):
    # 가장 유사한 이웃이 자기 자신(같은 doc_id) → 제외(또 다른 이웃 없음) → None.
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docSelf:c0", 0.99, {"doc_id": "docSelf"})])
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "docSelf", "본문", Grade.S2) is None
    assert fs.fired == 0


def test_non_human_neighbor_noop(monkeypatch):
    # _verified_human_grade가 None(머신라벨/미검증) → 참조 불가 → None.
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade=None)
    assert GATE(fs, "self", "본문", Grade.S2) is None


def test_already_top_grade_noop(monkeypatch):
    # 모델이 이미 최고등급(TS) → 과소분류 불가 → 검색조차 안 함.
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    import lloydk.adapters.embedding as emb_mod

    def _boom(*a, **k):
        raise AssertionError("top-grade prediction must not embed")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.TS) is None
    assert fs.fail_open == []  # 예외 아닌 정상 조기 종료


def test_no_hits_noop(monkeypatch):
    _patch(monkeypatch, enabled=True, hits=[])
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == []


def test_empty_text_noop(monkeypatch):
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "", Grade.S2) is None


def test_dedupe_neighbor_doc(monkeypatch):
    # 같은 doc의 청크 2개가 hit으로 와도 이웃 등급조회는 1회만(중복 제거).
    _patch(monkeypatch, enabled=True, hits=[
        SearchHit("docA:c0", 0.99, {"doc_id": "docA"}),
        SearchHit("docA:c1", 0.98, {"doc_id": "docA"}),
    ])
    fs = _FakeSelf(neighbor_grade=None)
    calls = {"n": 0}

    def _vhg(nid):  # noqa: ARG001
        calls["n"] += 1
        return None

    fs._verified_human_grade = _vhg
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert calls["n"] == 1


# ── fail-open(가시화) ─────────────────────────────────────────────────────────


def test_search_error_fail_open(monkeypatch):
    _patch(monkeypatch, enabled=True, search_exc=RuntimeError("store down"))
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == ["similarity_escalation"]


def test_embed_error_fail_open(monkeypatch):
    _patch(monkeypatch, enabled=True, embed_ok=False)
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == ["similarity_escalation"]


def test_empty_store_keyerror_fail_open(monkeypatch):
    # InMemoryStore가 빈 'docs' 컬렉션에서 KeyError 내는 사례 → fail-open(분류 진행).
    _patch(monkeypatch, enabled=True, search_exc=KeyError("docs"))
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == ["similarity_escalation"]


# ── config 검증 ───────────────────────────────────────────────────────────────


def test_config_defaults_off():
    from lloydk.config import Settings

    s = Settings(_env_file=None)
    assert s.similarity_escalation_enabled is False  # 기본 OFF(비파괴)
    assert s.similarity_escalation_tau == 0.92


@pytest.mark.parametrize("bad", [1.0, 0.0, 1.5, -0.1])
def test_config_validator_rejects_bad_tau(bad):
    from lloydk.config import Settings

    with pytest.raises(Exception):
        Settings(_env_file=None, similarity_escalation_tau=bad)


def test_config_accepts_valid_tau():
    from lloydk.config import Settings

    s = Settings(_env_file=None, similarity_escalation_tau=0.85)
    assert s.similarity_escalation_tau == 0.85


# ── classify() 배선(등급 무변경 + needs_review) ───────────────────────────────


def test_gate_wired_into_classify_no_grade_change(monkeypatch):
    """게이트가 classify() 체인에 실제로 배선됐고, 발동 시 등급 무변경·needs_review."""
    from lloydk import config as cfg
    from lloydk.schemas.classify import ClassifyRequest

    # 앞단 opt-in 게이트(합의·LLM 2차)를 꺼서 유사도 게이트만 status를 뒤집게 격리.
    monkeypatch.setattr(cfg.settings, "agreement_gate_enabled", False, raising=False)
    monkeypatch.setattr(cfg.settings, "model_secondopinion_llm_enabled", False, raising=False)

    svc = ClassifyService()
    pred = types.SimpleNamespace(
        label=Grade.S2,
        confidence=0.95,  # 저신뢰 라우팅 회피(게이트까지 도달)
        scores={"S2": 0.95},
        warnings=[],
        factors=None,
        evidence=[],
        rag_context=[],
        model_version="test",
        rule_grade=None,
    )
    monkeypatch.setattr(svc.inference, "run", lambda **k: pred)
    # 유사도 게이트가 발동했다고 가정(검색/DB 없이 배선만 검증).
    monkeypatch.setattr(
        ClassifyService, "_similarity_escalation_gate",
        lambda self, d, t, m: "similarity-escalation: ... (FNR-safe, grade unchanged)",
    )
    resp = svc.classify(
        ClassifyRequest(doc_id="non-uuid-xyz", content="본문", text_already_preprocessed=True)
    )
    assert resp.label == Grade.S2            # 등급 절대 무변경
    assert resp.status == "needs_review"     # 검수 라우팅
    assert any("similarity-escalation" in w for w in resp.warnings)


# ── 백엔드 가드(점수 의미 일치) + ran/routed 가시화 ──────────────────────────


def test_es_backend_noop(monkeypatch):
    # es는 _score=(1+cos)/2라 τ(코사인) 의미가 어긋남 → 검색조차 안 하고 no-op.
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    from lloydk import config as cfg
    import lloydk.adapters.embedding as emb_mod

    monkeypatch.setattr(cfg.settings, "vector_backend", "es", raising=False)

    def _boom(*a, **k):
        raise AssertionError("es backend must not embed/search")

    monkeypatch.setattr(emb_mod, "build_embedder", _boom)
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.fail_open == []
    assert fs.actions == []  # 검색 미수행


def test_ran_counter_on_search_without_escalation(monkeypatch):
    # 검색은 수행했으나 이웃이 동급 → 'ran' 1회·'routed' 0(enabled-but-inert 가시화).
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.99, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade="S2")
    assert GATE(fs, "self", "본문", Grade.S2) is None
    assert fs.actions == ["ran"]
    assert fs.fired == 0


def test_ran_and_routed_on_escalation(monkeypatch):
    _patch(monkeypatch, enabled=True,
           hits=[SearchHit("docA:c0", 0.95, {"doc_id": "docA"})])
    fs = _FakeSelf(neighbor_grade="TS")
    assert GATE(fs, "self", "본문", Grade.S2) is not None
    assert fs.actions == ["ran", "routed"]
    assert fs.fired == 1


# ── 실 prom 카운터 배선(메트릭명/라벨 오타 방지) ──────────────────────────────


def test_inc_similarity_escalation_real_counter():
    from lloydk.api import prom_metrics as pm

    labels = {"action": "routed"}
    before = pm.registry.get_sample_value("lloydk_similarity_escalation_total", labels) or 0.0
    ClassifyService._inc_similarity_escalation("routed")
    after = pm.registry.get_sample_value("lloydk_similarity_escalation_total", labels) or 0.0
    assert after == before + 1


def test_record_gate_fail_open_real_counter():
    from lloydk.api import prom_metrics as pm

    labels = {"gate": "similarity_escalation"}
    before = pm.registry.get_sample_value("lloydk_serving_gate_fail_open_total", labels) or 0.0
    ClassifyService._record_gate_fail_open("similarity_escalation")
    after = pm.registry.get_sample_value("lloydk_serving_gate_fail_open_total", labels) or 0.0
    assert after == before + 1


# ── DB-backed: 실 _verified_human_grade 참조셋 필터(stub 아닌 진짜 경로) ──────


def _pg_ok() -> bool:
    from sqlalchemy import text as _text

    from lloydk.db import engine

    try:
        with engine.connect() as conn:
            conn.execute(_text("SELECT 1"))
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
    from lloydk.db import SessionLocal

    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture
def levels(db):
    from lloydk.db.models import ClassificationLevel

    return {lv.level_code: lv.level_id for lv in db.query(ClassificationLevel).all()}


def _seed_doc(db):
    from lloydk.db.models import Document

    doc = Document(filename="sim.pdf", source_format="pdf", text_preview="유사도 테스트 문서")
    db.add(doc)
    db.flush()
    return doc


def _upsert_label(db, doc, level_id, labeled_by):
    from lloydk.repositories import ClassifyRepo

    ClassifyRepo(db).upsert_verified_label(
        doc_id=doc.doc_id, level_id=level_id, labeled_by=labeled_by,
        labeler_id="qa", verified_by="rev",
    )
    db.flush()


def _cleanup_docs(doc_ids):
    from lloydk.db import session_scope
    from lloydk.db.models import Document, DocumentLabel

    with session_scope() as s:
        s.query(DocumentLabel).filter(
            DocumentLabel.doc_id.in_(doc_ids)
        ).delete(synchronize_session=False)
        s.query(Document).filter(Document.doc_id.in_(doc_ids)).delete(synchronize_session=False)


class _RealRefSelf:
    """게이트가 self로 쓰는 콜백을 *실제* ClassifyService static 메서드로 위임(실 DB 경유)."""

    _verified_human_grade = staticmethod(ClassifyService._verified_human_grade)
    _inc_similarity_escalation = staticmethod(ClassifyService._inc_similarity_escalation)
    _record_gate_fail_open = staticmethod(ClassifyService._record_gate_fail_open)


def _patch_real_gate_deps(monkeypatch, neighbor_doc_id, *, score=0.97):
    from lloydk import config as cfg
    import lloydk.adapters.embedding as emb_mod
    import lloydk.adapters.vectorstore as vs_mod

    monkeypatch.setattr(cfg.settings, "similarity_escalation_enabled", True, raising=False)
    monkeypatch.setattr(cfg.settings, "similarity_escalation_tau", 0.92, raising=False)
    monkeypatch.setattr(cfg.settings, "vector_backend", "pg", raising=False)
    monkeypatch.setattr(cfg.settings, "rag_default_collection", "docs", raising=False)
    monkeypatch.setattr(cfg.settings, "rag_default_top_k", 5, raising=False)

    class _Emb:
        def embed(self, texts):  # noqa: ARG002
            return types.SimpleNamespace(vectors=[[0.1, 0.2, 0.3]])

    monkeypatch.setattr(emb_mod, "build_embedder", lambda *a, **k: _Emb())

    class _Store:
        def search(self, collection, query, top_k=5, filter=None):  # noqa: ARG002
            return [SearchHit("x:c0", score, {"doc_id": neighbor_doc_id})]

    monkeypatch.setattr(vs_mod, "build_store", lambda *a, **k: _Store())


@db_backed
class TestVerifiedHumanGradeLive:
    def test_human_label_returns_grade(self, db, levels):
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["TS"], "human_review")
        db.commit()
        try:
            assert ClassifyService._verified_human_grade(str(doc.doc_id)) == "TS"
        finally:
            _cleanup_docs([doc.doc_id])

    def test_legal_designated_label_returns_grade(self, db, levels):
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["TS"], "nkt_designated")  # 정부지정 = 권위 출처
        db.commit()
        try:
            assert ClassifyService._verified_human_grade(str(doc.doc_id)) == "TS"
        finally:
            _cleanup_docs([doc.doc_id])

    def test_koipa_label_demoted_returns_none(self, db, levels):
        # 2026-07-03 감사 강등: koipa=손작성 시나리오+판례 인용 조작 확인 → 권위 출처 아님.
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["S1"], "koipa_case_based")
        db.commit()
        try:
            assert ClassifyService._verified_human_grade(str(doc.doc_id)) is None
        finally:
            _cleanup_docs([doc.doc_id])

    def test_machine_label_returns_none(self, db, levels):
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["TS"], "llm_judge_primary")  # 머신 출처 → 참조 불가
        db.commit()
        try:
            assert ClassifyService._verified_human_grade(str(doc.doc_id)) is None
        finally:
            _cleanup_docs([doc.doc_id])

    def test_no_label_returns_none(self):
        import uuid as _uuid

        assert ClassifyService._verified_human_grade(str(_uuid.uuid4())) is None

    def test_invalid_uuid_returns_none(self):
        assert ClassifyService._verified_human_grade("not-a-uuid") is None

    def test_real_gate_escalates_on_human_neighbor(self, db, levels, monkeypatch):
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["TS"], "human_review")  # 이웃 = 사람검증 TS
        db.commit()
        try:
            _patch_real_gate_deps(monkeypatch, str(doc.doc_id))
            out = ClassifyService._similarity_escalation_gate(
                _RealRefSelf(), "other-doc", "본문", Grade.S2
            )
            assert out is not None
            assert "TS" in out
        finally:
            _cleanup_docs([doc.doc_id])

    def test_real_gate_skips_machine_neighbor(self, db, levels, monkeypatch):
        doc = _seed_doc(db)
        _upsert_label(db, doc, levels["TS"], "llm_judge_primary")  # 머신 검증 → 발동 안 함
        db.commit()
        try:
            _patch_real_gate_deps(monkeypatch, str(doc.doc_id))
            out = ClassifyService._similarity_escalation_gate(
                _RealRefSelf(), "other-doc", "본문", Grade.S2
            )
            assert out is None
        finally:
            _cleanup_docs([doc.doc_id])
