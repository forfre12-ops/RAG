"""HuggingFace sentence-transformers / FlagEmbedding 임베딩.

KURE-v1, BGE-M3 등. 모델은 첫 호출 시 다운로드 (~2GB).
"""

from __future__ import annotations

from typing import Sequence

from lloydk.adapters.embedding.base import EmbeddingResult


class HFEmbedding:
    def __init__(self, model_name: str) -> None:
        # FlagEmbedding이 BGE 계열에 최적화됨. 실패 시 sentence-transformers fallback.
        self.name = model_name
        self._model = None
        self._backend = None
        try:
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(model_name, use_fp16=False)
            self._backend = "flag_bgem3"
            self.dim = 1024
        except Exception:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
            self._backend = "sentence_transformers"
            self.dim = self._model.get_sentence_embedding_dimension()

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if self._backend == "flag_bgem3":
            out = self._model.encode(list(texts), return_dense=True, return_sparse=False)
            vectors = [list(map(float, v)) for v in out["dense_vecs"]]
        else:
            vectors = [list(map(float, v)) for v in self._model.encode(list(texts), normalize_embeddings=True)]
        return EmbeddingResult(vectors=vectors, dim=self.dim, model=self.name)
