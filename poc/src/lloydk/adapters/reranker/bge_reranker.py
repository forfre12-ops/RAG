"""BgeReranker — BGE-reranker-v2-m3 / Qwen3-Reranker cross-encoder.

lazy import — FlagEmbedding 또는 sentence_transformers.CrossEncoder 둘 다 지원.
설치되어 있지 않으면 import 시점에 ImportError, get_reranker가 catch하여 noop 폴백 권장.
"""

from __future__ import annotations

import logging
from typing import Sequence

from lloydk.adapters.reranker.base import RerankResult

logger = logging.getLogger(__name__)

_DEFAULT_MODEL = "BAAI/bge-reranker-v2-m3"


class BgeReranker:
    name = "bge"

    def __init__(self, model_name: str = _DEFAULT_MODEL, device: str | None = None):
        self.model_name = model_name
        self._impl = None
        self._backend = None
        self._device = device

    def _ensure_loaded(self) -> None:
        if self._impl is not None:
            return

        # 1순위: FlagEmbedding.FlagReranker (BGE 공식)
        try:
            from FlagEmbedding import FlagReranker  # type: ignore

            self._impl = FlagReranker(self.model_name, use_fp16=True)
            self._backend = "flagembedding"
            logger.info("reranker loaded via FlagEmbedding: %s", self.model_name)
            return
        except Exception as e:  # noqa: BLE001
            logger.debug("FlagEmbedding 로드 실패, sentence_transformers로 폴백: %s", e)

        # 2순위: sentence_transformers.CrossEncoder
        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            self._impl = CrossEncoder(self.model_name, device=self._device)
            self._backend = "sentence_transformers"
            logger.info("reranker loaded via sentence_transformers: %s", self.model_name)
            return
        except Exception as e:
            raise RuntimeError(
                f"BgeReranker: 두 백엔드 모두 로드 실패 ({self.model_name}): {e}"
            ) from e

    def rerank(
        self,
        query: str,
        candidates: Sequence[str],
        *,
        top_k: int | None = None,
    ) -> list[RerankResult]:
        if not candidates:
            return []
        self._ensure_loaded()

        pairs = [(query, doc) for doc in candidates]
        if self._backend == "flagembedding":
            scores = self._impl.compute_score(pairs, normalize=True)  # type: ignore[union-attr]
        else:
            scores = self._impl.predict(pairs)  # type: ignore[union-attr]

        # numpy → Python float
        scores = [float(s) for s in scores]

        ranked = sorted(
            (RerankResult(index=i, score=s, document=doc) for i, (s, doc) in enumerate(zip(scores, candidates))),
            key=lambda r: r.score,
            reverse=True,
        )
        if top_k is not None:
            ranked = ranked[:top_k]
        return ranked
