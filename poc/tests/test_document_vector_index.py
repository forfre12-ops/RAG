"""문서 대표 벡터 색인 — 무엇을 색인하고 무엇을 건너뛰는가.

■ 여기서 재지 않는 것

  SQL 자체(코사인 정렬·등급 조인·soft delete 제외·FK CASCADE)는 **실제 pgvector 에서
  확인했다**(2026-09-09, 211.233.204.32 의 pgvector/pgvector:pg16 임시 컨테이너):

      기준문서 자신 제외 · 지운 문서 제외 · 등급 조인 · verified_only · 거리순 정렬
      하드 삭제 시 CASCADE 로 벡터 동반 삭제

  SQLite 로는 vector 타입도 `<=>` 연산자도 없어 그 검증을 흉내 낼 수 없다. 흉내 낸
  가짜로 통과시키면 "시험은 통과하는데 배포에서 안 되는" 상태가 된다. 그래서 SQL 은
  실엔진에서 재고, 여기서는 **그 SQL 을 부르기 전후의 판단**을 잰다.
"""

from __future__ import annotations

import hashlib

import pytest

from koipa.services.document_vector_service import _mean_unit_vector, index_document


# ── 평균 벡터 ────────────────────────────────────────────────────────────────
def test_mean_is_unit_length():
    """문서 길이가 달라도 같은 자로 재려면 크기를 없애야 한다."""
    out = _mean_unit_vector([[3.0, 0.0], [0.0, 4.0]])
    assert pytest.approx(sum(x * x for x in out) ** 0.5, abs=1e-9) == 1.0


def test_mean_points_between_its_inputs():
    out = _mean_unit_vector([[1.0, 0.0], [0.0, 1.0]])
    assert out[0] == pytest.approx(out[1])          # 두 축 사이 정확히 가운데


def test_mixed_dimensions_raise_instead_of_corrupting():
    """임베더가 길이를 흔들면 평균이 **조용히** 망가진다 — 시끄럽게 실패해야 한다."""
    with pytest.raises(ValueError, match="차원이 섞였다"):
        _mean_unit_vector([[1.0, 0.0], [1.0]])


def test_zero_vector_does_not_divide_by_zero():
    assert _mean_unit_vector([[0.0, 0.0]]) == [0.0, 0.0]


def test_empty_input_is_empty_output():
    assert _mean_unit_vector([]) == []


# ── 색인 판단 ────────────────────────────────────────────────────────────────
class _Chunk:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeRepo:
    def __init__(self, chunks):
        self._chunks = chunks

    def __call__(self, _db):          # ChunkRepo(session) 흉내
        return self

    def get_by_doc_id(self, _doc_id):
        return self._chunks


class _FakeStore:
    def __init__(self, *, needs: bool = True):
        self._needs = needs
        self.upserts: list[dict] = []

    def needs_index(self, _doc_id, _sha):
        return self._needs

    def upsert(self, **kw):
        self.upserts.append(kw)


class _FakeEmbedder:
    name = "fake"

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.calls = 0

    def embed(self, texts):
        self.calls += 1

        class _R:
            vectors = [[1.0] + [0.0] * 3 for _ in texts]
            model = "nlpai-lab/KURE-v1"
        return _R()


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _factory():
    return _FakeSession()


@pytest.fixture
def patch_repo(monkeypatch):
    def _apply(chunks):
        import koipa.repositories.chunk_repo as cr
        monkeypatch.setattr(cr, "ChunkRepo", _FakeRepo(chunks))
    return _apply


def test_indexes_document_with_chunks(patch_repo):
    patch_repo([_Chunk("본문 하나"), _Chunk("본문 둘")])
    store, emb = _FakeStore(), _FakeEmbedder()

    out = index_document("doc-1", db_factory=_factory, store=store, embedder=emb)

    assert out["status"] == "indexed"
    assert out["chunk_count"] == 2
    assert len(store.upserts) == 1
    up = store.upserts[0]
    assert up["model"] == "nlpai-lab/KURE-v1"      # 어느 모델로 만든 벡터인지 남는다
    assert up["chunk_count"] == 2
    assert up["content_sha256"] == hashlib.sha256("본문 하나\n본문 둘".encode()).hexdigest()


def test_same_content_is_not_embedded_again(patch_repo):
    """재업로드가 임베딩을 다시 돌리면 100쪽 문서마다 2분을 또 쓴다."""
    patch_repo([_Chunk("그대로인 본문")])
    store, emb = _FakeStore(needs=False), _FakeEmbedder()

    out = index_document("doc-1", db_factory=_factory, store=store, embedder=emb)

    assert out["status"] == "skipped"
    assert emb.calls == 0          # 임베더를 아예 안 불렀다
    assert store.upserts == []


def test_force_reindexes_even_when_unchanged(patch_repo):
    """모델을 바꿔 전량 재색인할 때 쓰는 문."""
    patch_repo([_Chunk("그대로인 본문")])
    store, emb = _FakeStore(needs=False), _FakeEmbedder()

    out = index_document("doc-1", db_factory=_factory, store=store, embedder=emb, force=True)

    assert out["status"] == "indexed"
    assert emb.calls == 1


def test_document_without_chunks_is_not_an_error(patch_repo):
    """추출이 실패한 문서는 색인할 본문이 없다 — 실패가 아니라 대상 아님이다."""
    patch_repo([])
    store, emb = _FakeStore(), _FakeEmbedder()

    out = index_document("doc-1", db_factory=_factory, store=store, embedder=emb)

    assert out["status"] == "no_chunks"
    assert emb.calls == 0
    assert store.upserts == []


# ── 저장소 계약(엔진 없이 잴 수 있는 부분) ────────────────────────────────────
def test_vector_literal_needs_no_pgvector_package():
    """벡터는 텍스트 리터럴로 오간다 — pgvector 파이썬 패키지를 의존성에 넣지 않기 위해서다."""
    from koipa.adapters.vectorstore import DocumentVectorStore

    lit = DocumentVectorStore._vec_lit([1.0, -0.5, 0.0])
    assert lit.startswith("[") and lit.endswith("]")
    assert [float(x) for x in lit[1:-1].split(",")] == [1.0, -0.5, 0.0]


def test_wrong_dimension_is_refused_before_touching_db():
    """차원이 어긋난 벡터를 넣으면 PostgreSQL 이 거절하는데, 그 오류는 읽기 어렵다.

    임베딩 모델을 바꿨을 때 여기서 먼저 막고 무엇을 해야 하는지 알려 준다
    (vector 칼럼의 차원은 ALTER 로 못 바꾼다 — 새 마이그레이션이 필요하다).
    """
    from koipa.adapters.vectorstore import DocumentVectorStore

    store = DocumentVectorStore(engine=object())      # 엔진에 닿기 전에 막혀야 한다
    with pytest.raises(ValueError, match="임베딩 차원이 표와 다르다"):
        store.upsert(doc_id="d", embedding=[0.0] * 3, model="m")


def test_similarity_is_one_minus_distance():
    from koipa.adapters.vectorstore import SimilarDocument

    hit = SimilarDocument("d", "f.md", 0.25, "TS", True, None)
    assert hit.similarity == pytest.approx(0.75)
