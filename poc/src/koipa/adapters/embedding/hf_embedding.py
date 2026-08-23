"""HuggingFace sentence-transformers / FlagEmbedding 임베딩.

KURE-v1, BGE-M3 등. 모델은 첫 호출 시 다운로드 (~2GB).
"""

from __future__ import annotations

from typing import Sequence

from koipa.adapters.embedding.base import EmbeddingResult


class HFEmbedding:
    def __init__(self, model_name: str) -> None:
        # FlagEmbedding이 BGE 계열에 최적화됨. 실패 시 sentence-transformers fallback.
        self.name = model_name
        self._model = None
        self._backend = None
        # #20: dim 하드코딩 제거. None 이면 "아직 미확정" 의미 — 첫 embed 출력
        # 벡터 길이로 실측 보정한다(EmbeddingResult.dim 이 실제 차원과 항상 일치).
        self.dim: int | None = None
        try:
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(model_name, use_fp16=False)
            self._backend = "flag_bgem3"
            # #20: 1024 하드코딩 폐기. 모델 로드 시점엔 차원을 단정할 수 없으므로
            # None 으로 두고 첫 임베딩 출력 길이로 확정한다(아래 _resolve_dim).
        except Exception:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(model_name)
            self._backend = "sentence_transformers"
            # #20: get_sentence_embedding_dimension 은 deprecated 경고 → 신 API 우선,
            # 없으면 구 API fallback, 둘 다 실패하면 첫 임베딩 때 실측으로 확정.
            self.dim = self._sentence_dim()

    def _sentence_dim(self) -> int | None:
        """sentence-transformers 모델의 출력 차원을 신 API 우선으로 조회.

        #20: get_embedding_dimension() (신) → get_sentence_embedding_dimension()
        (구, deprecated) 순으로 시도. 둘 다 없거나 None 이면 None 반환 → 첫
        임베딩 후 실측 보정에 맡긴다.
        """
        for attr in ("get_embedding_dimension", "get_sentence_embedding_dimension"):
            fn = getattr(self._model, attr, None)
            if callable(fn):
                try:
                    d = fn()
                except Exception:
                    continue
                if d:
                    return int(d)
        return None

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        if self._backend == "flag_bgem3":
            out = self._model.encode(list(texts), return_dense=True, return_sparse=False)
            vectors = [list(map(float, v)) for v in out["dense_vecs"]]
        else:
            vectors = [list(map(float, v)) for v in self._model.encode(list(texts), normalize_embeddings=True)]
        # #20: dim 실측 확정. 미확정(None)이거나 실제 출력 길이와 어긋나면
        # 첫 출력 벡터 길이로 보정하고 이후 호출에서 일관 유지한다.
        if vectors:
            actual = len(vectors[0])
            if self.dim != actual:
                self.dim = actual
        return EmbeddingResult(vectors=vectors, dim=int(self.dim or 0), model=self.name)
