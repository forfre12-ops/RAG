"""표적 2 — chunks 영속화 단위 검증.

DB 없이 동작 — ChunkRepo는 SQLAlchemy Session에 add/flush만 호출하고
delete/select 같은 DB-bound 메서드는 별도(test_document_cascade.py / PG 잡)에서.
"""

from __future__ import annotations

import uuid

from koipa.db.models import Chunk
from koipa.modules.m2_preprocess.chunker import Chunk as PreChunk
from koipa.repositories.chunk_repo import ChunkRepo, _estimate_token_count


class _StubSession:
    """add/flush + delete/select은 noop. upsert_chunks 단독 흐름만 검증.

    실제 SQL은 PG에서 test_document_cascade.py 회귀가 커버.
    """

    def __init__(self):
        self.added: list = []

    def add(self, obj):
        self.added.append(obj)
        # PG가 server_default로 채워주는 chunk_id를 stub에서 미리 부여
        if isinstance(obj, Chunk) and getattr(obj, "chunk_id", None) is None:
            obj.chunk_id = uuid.uuid4()

    def flush(self):
        pass

    def execute(self, _stmt):
        # delete_by_doc_id가 호출하는 경로 — stub은 rowcount=0 반환
        class _R:
            rowcount = 0
        return _R()


def _make_chunks(n: int = 3) -> list[PreChunk]:
    return [
        PreChunk(
            index=i,
            text=f"청크-{i} 본문 한국어 sample text",
            char_count=20 + i,
            overlap_prev=8 if i > 0 else 0,
            overlap_next=8 if i < n - 1 else 0,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# 단위
# ---------------------------------------------------------------------------


def test_estimate_token_count_heuristic():
    assert _estimate_token_count("") == 1  # 빈 문자열도 최소 1
    assert _estimate_token_count("abc") == 1  # 3//4=0 → max 1
    assert _estimate_token_count("a" * 20) == 5  # 20/4


def test_upsert_chunks_inserts_one_row_per_chunk():
    sess = _StubSession()
    repo = ChunkRepo(sess)
    chunks = _make_chunks(3)
    doc_id = uuid.uuid4()

    ids = repo.upsert_chunks(doc_id=doc_id, chunks=chunks)
    assert len(ids) == 3
    # 모두 Chunk 모델 객체
    assert all(isinstance(o, Chunk) for o in sess.added)
    # doc_id가 모두 일관 (tenant 제거: 격리는 KL 포털 전담)
    for row in sess.added:
        assert row.doc_id == doc_id
    # chunk_index 보존
    assert [r.chunk_index for r in sess.added] == [0, 1, 2]
    # text 비어있지 않음, char_count 보존
    assert sess.added[0].content.startswith("청크-0")
    assert sess.added[0].char_count == 20


def test_upsert_chunks_token_count_estimated_when_missing():
    sess = _StubSession()
    repo = ChunkRepo(sess)
    chunks = _make_chunks(1)
    repo.upsert_chunks(doc_id=uuid.uuid4(), chunks=chunks)
    row = sess.added[0]
    assert row.token_count >= 1


def test_upsert_chunks_skips_empty_text():
    sess = _StubSession()
    repo = ChunkRepo(sess)
    chunks = [
        PreChunk(index=0, text="", char_count=0),
        PreChunk(index=1, text="유효 텍스트", char_count=6),
    ]
    ids = repo.upsert_chunks(doc_id=uuid.uuid4(), chunks=chunks)
    assert len(ids) == 1
    assert sess.added[0].content == "유효 텍스트"


def test_upsert_chunks_replace_existing_invokes_delete():
    """replace_existing=True면 upsert 전 delete_by_doc_id 호출 (간접 — execute 호출 카운트)."""
    sess = _StubSession()
    calls: list = []
    original_execute = sess.execute

    def spy_execute(stmt):
        calls.append(stmt)
        return original_execute(stmt)

    sess.execute = spy_execute  # type: ignore[method-assign]
    repo = ChunkRepo(sess)
    repo.upsert_chunks(doc_id=uuid.uuid4(), chunks=_make_chunks(1),
                       replace_existing=True)
    # delete + insert 흐름 — execute 1회 (delete), add 1회
    assert len(calls) >= 1
    assert len(sess.added) == 1


def test_upsert_chunks_no_replace_skips_delete():
    sess = _StubSession()
    calls: list = []
    original_execute = sess.execute

    def spy_execute(stmt):
        calls.append(stmt)
        return original_execute(stmt)

    sess.execute = spy_execute  # type: ignore[method-assign]
    repo = ChunkRepo(sess)
    repo.upsert_chunks(doc_id=uuid.uuid4(), chunks=_make_chunks(1),
                       replace_existing=False)
    assert len(calls) == 0
