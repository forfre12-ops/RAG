"""RAG context → ClassificationEvidence 영속화 단위 검증.

PG 없이 동작 — ClassifyRepo는 SQLAlchemy Session을 받지만 add()/flush()만 호출.
간단한 in-memory stub Session으로 동등 검증. UUID 파싱·score clamp·excerpt 길이도.
"""

from __future__ import annotations

import uuid

from lloydk.db.models import ClassificationEvidence
from lloydk.repositories.classify_repo import ClassifyRepo, _try_uuid
from lloydk.schemas.classify import RagContextHit


class _StubSession:
    """add/flush만 구현. ClassifyRepo.add_evidence는 그 둘만 사용."""

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)

    def flush(self):
        pass


def _hit(source_doc: str, chunk_id: str, score: float) -> RagContextHit:
    return RagContextHit(source_doc=source_doc, chunk_id=chunk_id, score=score)


# ---------------------------------------------------------------------------
# _try_uuid
# ---------------------------------------------------------------------------


def test_try_uuid_parses_valid():
    s = "12345678-1234-5678-1234-567812345678"
    assert _try_uuid(s) == uuid.UUID(s)


def test_try_uuid_returns_none_on_invalid():
    assert _try_uuid("not-a-uuid") is None
    assert _try_uuid("") is None
    assert _try_uuid(None) is None


# ---------------------------------------------------------------------------
# add_rag_evidence_from_hits
# ---------------------------------------------------------------------------


def test_add_rag_evidence_inserts_one_row_per_hit():
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    cls_id = uuid.uuid4()
    default_chunk = uuid.uuid4()
    hits = [
        _hit("11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222", 0.92),
        _hit("33333333-3333-3333-3333-333333333333", "44444444-4444-4444-4444-444444444444", 0.78),
    ]

    n = repo.add_rag_evidence_from_hits(cls_id, hits=hits, default_chunk_id=default_chunk)
    assert n == 2
    assert len(sess.added) == 2
    for ev in sess.added:
        assert isinstance(ev, ClassificationEvidence)
        assert ev.classification_id == cls_id
        assert ev.evidence_type == "rag_context"
        assert ev.rag_ref_doc_id is not None
        assert 0.0 <= float(ev.rag_similarity) <= 1.0


def test_add_rag_evidence_clamps_score_to_unit_range():
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    hits = [
        _hit("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb", 1.5),
        _hit("cccccccc-cccc-cccc-cccc-cccccccccccc", "dddddddd-dddd-dddd-dddd-dddddddddddd", -0.2),
    ]
    repo.add_rag_evidence_from_hits(uuid.uuid4(), hits=hits, default_chunk_id=uuid.uuid4())
    scores = [float(ev.rag_similarity) for ev in sess.added]
    assert scores[0] == 1.0  # clamp to 1
    assert scores[1] == 0.0  # clamp to 0


def test_add_rag_evidence_falls_back_default_chunk_on_invalid_chunk_id():
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    default = uuid.uuid4()
    hits = [_hit("not-uuid-source", "not-uuid-chunk", 0.5)]
    repo.add_rag_evidence_from_hits(uuid.uuid4(), hits=hits, default_chunk_id=default)
    ev = sess.added[0]
    assert ev.chunk_id == default
    assert ev.rag_ref_doc_id is None  # 파싱 실패 → None


def test_add_rag_evidence_excerpt_truncated():
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    long_id = "x" * 1000
    hits = [_hit(long_id, long_id, 0.5)]
    repo.add_rag_evidence_from_hits(
        uuid.uuid4(), hits=hits, default_chunk_id=uuid.uuid4(), excerpt_max_len=80,
    )
    assert len(sess.added[0].excerpt) == 80


def test_add_rag_evidence_empty_hits_no_rows():
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    n = repo.add_rag_evidence_from_hits(uuid.uuid4(), hits=[], default_chunk_id=uuid.uuid4())
    assert n == 0
    assert sess.added == []


def test_add_evidence_accepts_rag_fields_directly():
    """add_evidence에 rag_ref_doc_id/rag_similarity 직접 전달 경로 보존."""
    sess = _StubSession()
    repo = ClassifyRepo(sess)
    ref = uuid.uuid4()
    ev = repo.add_evidence(
        uuid.uuid4(),
        chunk_id=uuid.uuid4(),
        evidence_type="rag_context",
        excerpt="manual rag insert",
        contribution=0.7,
        rag_ref_doc_id=ref,
        rag_similarity=0.7,
    )
    assert ev.rag_ref_doc_id == ref
    assert float(ev.rag_similarity) == 0.7
