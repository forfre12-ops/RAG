"""W5 RAG 인덱서 + GuideService 결합 테스트.

설계:
- TestInMemoryFlow: InMemoryStore + HashEmbedding 격리. ES·HF 모델 무관.
- TestEsFlow: 실 ES 가동 시 풀 인덱싱 + alias 스왑 검증 (skipif).
- TestGuideServiceBinding: GuideService.upload()이 RagIndexer 결과를 반영하는지.
"""

from __future__ import annotations

import time

import pytest

from lloydk.adapters.embedding import HashEmbedding
from lloydk.adapters.vectorstore import InMemoryStore
from lloydk.modules.m4_training.rag_indexer import (
    IndexResult,
    RagIndexer,
    _safe_token,
)


def _es_ok() -> bool:
    """Elasticsearch가 실제로 응답하는지 + 클라이언트가 호환되는지 빠른 체크.

    ES 9.x 클라이언트는 ES 8.x 서버 호환을 거부(Accept v9)하므로,
    실제 `info()` 호출까지 통과해야 진짜 사용 가능한 상태.
    """
    try:
        import urllib.request
        with urllib.request.urlopen("http://localhost:9200/_cluster/health", timeout=2.0) as r:  # noqa: S310
            if r.status != 200:
                return False
    except Exception:  # noqa: BLE001
        return False
    # 클라이언트-서버 호환 확인 (info() RTT)
    try:
        from elasticsearch import Elasticsearch  # noqa: PLC0415
        Elasticsearch(hosts=["http://localhost:9200"], request_timeout=3.0).info()
        return True
    except Exception:  # noqa: BLE001
        return False


def _purge_test_prefix(prefix: str = "secrets-guides-hash-") -> None:
    """테스트 시작 전 잔재 인덱스/alias 일괄 정리.

    `secrets-guides-hash-*` 패턴은 본 테스트 모듈만 생성(HashEmbedding 모델 토큰).
    실 운영 인덱스(kure 등)는 건드리지 않는다. 다른 차원의 매핑이 잔존하면
    매핑 충돌이 발생하므로, 매 세션 시작 시 wipe.
    """
    if not _es_ok():
        return
    try:
        from elasticsearch import Elasticsearch  # noqa: PLC0415
        client = Elasticsearch(hosts=["http://localhost:9200"], request_timeout=5.0)
        # wildcard delete (alias도 함께 사라짐)
        client.options(ignore_status=[404]).indices.delete(index=f"{prefix}*")
    except Exception:  # noqa: BLE001
        pass


# ============================================================
# Pure helpers
# ============================================================


class TestSafeToken:
    def test_alphanumeric_kept(self):
        assert _safe_token("koipa") == "koipa"

    def test_uppercase_lowered(self):
        assert _safe_token("KOIPA") == "koipa"

    def test_spaces_to_dash(self):
        assert _safe_token("KL Inc") == "kl-inc"

    def test_hangul_falls_back_to_hash(self):
        out = _safe_token("한국지식재산보호원")
        assert out != ""
        # 길이는 50 이하
        assert len(out) <= 50

    def test_long_string_truncated(self):
        assert len(_safe_token("a" * 100)) <= 50

    def test_empty_returns_default(self):
        assert _safe_token("") == "default"


# ============================================================
# InMemory + Hash (격리, ES 무관)
# ============================================================


class TestInMemoryFlow:
    def _make(self) -> RagIndexer:
        return RagIndexer(store=InMemoryStore(), embedder=HashEmbedding(dim=1024))

    def test_index_short_text_one_chunk(self):
        text = "본 가이드는 영업비밀의 등급 분류 기준을 정의한다."
        r = self._make().index_guide(
            guide_id="g1", version="v1.0", text=text,
        )
        assert isinstance(r, IndexResult)
        assert r.indexed is True
        assert r.chunk_count >= 1
        assert r.vector_count == r.chunk_count
        assert r.alias == "secrets-guides"
        assert r.index_name.startswith(r.alias)
        assert r.model == "hash"
        assert r.warnings == []

    def test_index_long_text_multiple_chunks(self):
        # 청크 분할이 작동하도록 길게
        text = "한 단락 본문. " * 300
        r = self._make().index_guide(guide_id="g2", version="v2.0", text=text)
        assert r.indexed is True
        assert r.chunk_count >= 2
        assert r.vector_count == r.chunk_count

    def test_empty_text_returns_not_indexed(self):
        r = self._make().index_guide(guide_id="g3", version="v1", text="")
        assert r.indexed is False
        assert r.chunk_count == 0
        assert any("no chunks" in w for w in r.warnings)

    def test_index_name_includes_safe_tokens(self):
        r = self._make().index_guide(
            guide_id="g4", version="V 2.1!", text="간단 본문 " * 50,
        )
        # version V 2.1! → v-2.1 (인덱스명 safe-token)
        assert r.alias == "secrets-guides"
        assert "v-2.1" in r.index_name


# ============================================================
# Live ES (8.15.3, docker compose up)
# ============================================================


@pytest.mark.fullstack
class TestEsFlow:
    def setup_method(self):
        if not _es_ok():
            pytest.skip("Elasticsearch not reachable on :9200")
        # 잔재 인덱스(이전 실패 run, 다른 차원 매핑 등) 일괄 정리
        _purge_test_prefix("secrets-guides-hash-")

    def teardown_method(self):
        # 후속 다른 모듈에 영향 없도록 다시 정리
        _purge_test_prefix("secrets-guides-hash-")

    def test_full_index_and_alias_swap(self):
        from lloydk.adapters.vectorstore import build_store

        store = build_store(backend="es")
        idx = RagIndexer(store=store, embedder=HashEmbedding(dim=1024))
        text = "본 가이드는 영업비밀의 등급 분류 기준을 정의한다. " * 50

        try:
            r = idx.index_guide(
                guide_id="es-guide-1", version="v1.0", text=text,
            )
            assert r.indexed is True
            assert r.chunk_count >= 1
            assert r.vector_count >= 1

            # alias가 새 인덱스를 가리키는지 확인
            client = store._client  # noqa: SLF001
            alias_resp = client.indices.get_alias(name=r.alias)
            assert r.index_name in alias_resp

            # 인덱스에 문서가 실제로 들어갔는지 (refresh wait_for로 즉시 가시)
            time.sleep(0.5)
            count = store.count(r.index_name)
            assert count >= 1

        finally:
            # cleanup — 테스트 인덱스 삭제
            try:
                client = store._client  # noqa: SLF001
                # alias 먼저 해제
                try:
                    client.indices.delete_alias(index=r.index_name, name=r.alias)
                except Exception:  # noqa: BLE001
                    pass
                client.options(ignore_status=[404]).indices.delete(index=r.index_name)
            except Exception:  # noqa: BLE001
                pass

    def test_second_version_replaces_alias(self):
        """v1 → v2 인덱싱 시 alias가 v2 인덱스로 스왑되는지."""
        from lloydk.adapters.vectorstore import build_store

        store = build_store(backend="es")
        idx = RagIndexer(store=store, embedder=HashEmbedding(dim=1024))
        text = "본문 한 줄. " * 50

        created_indices: list[str] = []
        try:
            r1 = idx.index_guide(guide_id="g-bg", version="v1.0", text=text)
            created_indices.append(r1.index_name)
            assert r1.indexed is True

            r2 = idx.index_guide(guide_id="g-bg", version="v2.0", text=text)
            created_indices.append(r2.index_name)
            assert r2.indexed is True
            assert r2.index_name != r1.index_name

            client = store._client  # noqa: SLF001
            alias_resp = client.indices.get_alias(name=r1.alias)
            # alias는 새 인덱스를 가리켜야 함
            assert r2.index_name in alias_resp
            # 옛 인덱스는 alias에서 빠졌어야 함 (swap_alias가 정확히 동작했다면)
            assert r1.index_name not in alias_resp

        finally:
            client = store._client  # noqa: SLF001
            for idx_name in created_indices:
                try:
                    client.options(ignore_status=[404]).indices.delete(index=idx_name)
                except Exception:  # noqa: BLE001
                    pass


# ============================================================
# GuideService ↔ RagIndexer 결합
# ============================================================


class TestGuideServiceBinding:
    pytestmark = pytest.mark.slow

    def setup_method(self):
        from lloydk.services.guide_service import GuideService
        # 각 테스트마다 fresh singleton + InMemory 인덱서
        GuideService.reset_singleton()

    def teardown_method(self):
        from lloydk.services.guide_service import GuideService
        GuideService.reset_singleton()

    def test_upload_uses_injected_indexer(self):
        from lloydk.services.guide_service import GuideService
        idx = RagIndexer(store=InMemoryStore(), embedder=HashEmbedding(dim=1024))
        svc = GuideService(indexer=idx)
        res = svc.upload(
            guide_id="g-x", version="v1.0",
            effective_date="2026-06-01", change_summary="initial",
            content_bytes=("본 가이드 본문. " * 100).encode("utf-8"),
            actor_user_id="u-1", doc_type="guideline",
        )
        assert res.indexed is True
        assert res.embedding_vector_count >= 1
        assert res.triggers_retraining is False

    def test_list_versions_after_upload(self):
        from lloydk.services.guide_service import GuideService
        idx = RagIndexer(store=InMemoryStore(), embedder=HashEmbedding(dim=1024))
        svc = GuideService(indexer=idx)
        for v in ("v1.0", "v1.1"):
            svc.upload(
                guide_id="g-y", version=v,
                effective_date=None, change_summary=None,
                content_bytes=b"\xed\x95\x9c\xea\xb8\x80 \xeb\xb3\xb8\xeb\xac\xb8 " * 50,  # UTF-8 한글
                actor_user_id="u",
            )
        vl = svc.list_versions("g-y")
        assert vl is not None
        assert [v.version for v in vl.versions] == ["v1.0", "v1.1"]

    def test_decoding_handles_cp949(self):
        from lloydk.services.guide_service import GuideService
        idx = RagIndexer(store=InMemoryStore(), embedder=HashEmbedding(dim=1024))
        svc = GuideService(indexer=idx)
        # CP949로 인코딩된 한글 — 운영에서 종종 들어옴
        text = "한국 영업비밀 가이드 본문 " * 50
        cp949 = text.encode("cp949")
        res = svc.upload(
            guide_id="g-cp", version="v1",
            effective_date=None, change_summary=None,
            content_bytes=cp949, actor_user_id="u",
        )
        assert res.indexed is True
