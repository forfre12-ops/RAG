"""W9 일반화 — 임베딩 캐시 레이어 어댑터.

목표:
- underlying EmbeddingProvider를 wrap, 동일 텍스트 재호출 시 underlying 호출 0회
- in-memory LRU (기본 1024, env ``EMB_CACHE_SIZE`` 로 조정)
- 선택적 디스크 캐시 (json)
- 캐시 키는 텍스트의 SHA-1 (짧고 안정)
- 배치 중 hit/miss를 분리하여 miss 텍스트만 underlying에 전달

scripts/cache_kure_v1.py 는 HF Hub 모델 가중치 다운로드 스크립트이지 임베딩 결과
캐시가 아니므로, 본 모듈이 실제 결과 캐시 레이어이다.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Sequence

from lloydk.adapters.embedding.base import EmbeddingResult


def _hash_key(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _resolve_cache_size(explicit: int | None) -> int:
    if explicit is not None:
        return max(1, int(explicit))
    raw = os.getenv("EMB_CACHE_SIZE")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return 1024


class _LRU:
    """Thread-safe LRU. dict-of-list[float]."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._data: "OrderedDict[str, list[float]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> list[float] | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def put(self, key: str, value: list[float]) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
                return
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def __len__(self) -> int:  # 디버그용
        with self._lock:
            return len(self._data)


class CachedEmbedding:
    """EmbeddingProvider wrapper with in-memory LRU + optional disk cache.

    Parameters
    ----------
    underlying:
        실제 임베딩 provider. ``embed(texts) -> EmbeddingResult`` 또는
        ``embed(texts) -> Sequence[Sequence[float]]`` 형태 모두 허용.
    cache_size:
        LRU 최대 항목 수. ``None`` 이면 env ``EMB_CACHE_SIZE`` 또는 1024.
    disk_path:
        ``str|Path|None``. 지정 시 종료 후에도 유지되는 JSON 디스크 캐시.
    """

    name = "cached"

    def __init__(
        self,
        underlying,
        *,
        cache_size: int | None = None,
        disk_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._underlying = underlying
        self._lru = _LRU(_resolve_cache_size(cache_size))
        self._disk_path: Path | None = Path(disk_path) if disk_path else None
        self._load_disk()
        # underlying 의 dim/name 을 best-effort 로 전파 (Protocol 호환)
        self.dim = getattr(underlying, "dim", None)
        self._underlying_name = getattr(underlying, "name", "unknown")

    # ---- disk -------------------------------------------------------------

    def _load_disk(self) -> None:
        if self._disk_path is None or not self._disk_path.exists():
            return
        try:
            raw = json.loads(self._disk_path.read_text(encoding="utf-8"))
            for k, v in raw.items():
                self._lru.put(k, list(v))
        except Exception:
            # 손상된 캐시는 무시 (다음 쓰기로 덮어씀)
            return

    def _flush_disk(self) -> None:
        if self._disk_path is None:
            return
        try:
            self._disk_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in self._lru._data.items()}  # snapshot
            self._disk_path.write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            return

    # ---- core -------------------------------------------------------------

    @staticmethod
    def _coerce_vectors(result) -> tuple[list[list[float]], int | None, str | None]:
        """underlying 의 반환을 (vectors, dim, model) 로 정규화."""
        if isinstance(result, EmbeddingResult):
            return result.vectors, result.dim, result.model
        # 예: list[list[float]] / numpy ndarray / MagicMock(return_value=...)
        vectors = [list(map(float, v)) for v in result]
        dim = len(vectors[0]) if vectors else None
        return vectors, dim, None

    def embed(self, texts: Sequence[str] | Iterable[str]) -> EmbeddingResult:
        texts_list = list(texts)
        n = len(texts_list)
        keys = [_hash_key(t) for t in texts_list]

        # 1) hit/miss 분리 — 동일 텍스트가 배치 내 중복돼도 underlying 호출은 1회만
        out_vectors: list[list[float] | None] = [None] * n
        miss_indices: list[int] = []
        miss_texts: list[str] = []
        miss_seen: dict[str, int] = {}  # key -> miss_texts 내 위치
        for i, (t, k) in enumerate(zip(texts_list, keys)):
            cached = self._lru.get(k)
            if cached is not None:
                out_vectors[i] = list(cached)
                continue
            if k in miss_seen:
                # 같은 배치 안 중복 — underlying 재호출 없이 동일 결과 공유
                continue
            miss_seen[k] = len(miss_texts)
            miss_indices.append(i)
            miss_texts.append(t)

        dim_hint: int | None = self.dim
        model_hint: str | None = None

        # 2) miss 만 underlying 호출 (miss 가 없으면 호출 0회 — 캐시 hit 핵심)
        if miss_texts:
            raw = self._underlying.embed(miss_texts)
            new_vectors, dim_hint2, model_hint = self._coerce_vectors(raw)
            if dim_hint2 is not None:
                dim_hint = dim_hint2
            if len(new_vectors) != len(miss_texts):
                raise RuntimeError(
                    f"underlying embed returned {len(new_vectors)} vectors "
                    f"for {len(miss_texts)} inputs"
                )
            for mi, t, vec in zip(miss_indices, miss_texts, new_vectors):
                key = keys[mi]
                vec_list = list(map(float, vec))
                self._lru.put(key, vec_list)
            self._flush_disk()

        # 3) 결과 재구성 — None 자리(중복된 miss / 새 miss) 모두 LRU 에서 채움
        final: list[list[float]] = []
        for i, k in enumerate(keys):
            if out_vectors[i] is not None:
                final.append(out_vectors[i])  # type: ignore[arg-type]
                continue
            cached = self._lru.get(k)
            if cached is None:
                raise RuntimeError("cache fill invariant violated")
            final.append(list(cached))

        if dim_hint is None:
            dim_hint = len(final[0]) if final else 0

        return EmbeddingResult(
            vectors=final,
            dim=int(dim_hint or 0),
            model=model_hint or self._underlying_name or "cached",
        )

    # ---- 디버그/내성 ------------------------------------------------------

    def cache_info(self) -> dict[str, int]:
        return {"size": len(self._lru), "capacity": self._lru._capacity}

    def clear(self) -> None:
        with self._lru._lock:
            self._lru._data.clear()
        if self._disk_path is not None and self._disk_path.exists():
            try:
                self._disk_path.unlink()
            except OSError:
                pass


# 테스트 모듈 후보 목록 호환 alias
#   tests/test_embedding_cache.py 는
#     ("lloydk.adapters.embedding.cache_layer", "EmbeddingCache")
#   를 첫 번째로 시도하므로 동일 클래스를 양쪽 이름으로 노출.
EmbeddingCache = CachedEmbedding


__all__ = ["CachedEmbedding", "EmbeddingCache"]
