from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.config import settings
from lloydk.schemas.common import Grade, GradeRegistry
from lloydk.schemas.classify import EvidenceSpan, EvaluationFactors, RagContextHit
from lloydk.modules.m2_preprocess import split as _chunk_split
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline

logger = logging.getLogger(__name__)


def chunk_text(text: str, size: int = 512, overlap: int = 64):
    """하위호환 wrapper — 새 split()로 위임."""
    return _chunk_split(text, size=size, overlap=overlap)


@dataclass
class InferenceResult:
    label: Grade
    confidence: float
    scores: dict[str, float]
    factors: Optional[EvaluationFactors] = None
    evidence: list[EvidenceSpan] = field(default_factory=list)
    rag_context: list[RagContextHit] = field(default_factory=list)
    model_version: str = "poc"
    warnings: list[str] = field(default_factory=list)


_LABELS = [Grade.TS, Grade.S1, Grade.S2, Grade.S3]


def _get_labels() -> list:
    """GradeRegistry에서 현재 활성 등급 목록 반환.

    DB에 커스텀 등급이 있으면 그것을, 없으면 기본 _LABELS를 반환.
    Grade enum에 없는 코드는 문자열로 반환 (다른 프로젝트 호환).
    """
    codes = GradeRegistry.get_codes()
    labels = []
    for code in codes:
        try:
            labels.append(Grade(code))
        except ValueError:
            labels.append(code)
    return labels or _LABELS


class InferencePipeline:
    """
    PoC 추론기.
    - 학습 가중치가 있으면 transformers 로드, 없으면 M3 라벨링 규칙 기반 점수로 폴백.
    - 청크 어그리게이션: 청크별 softmax 평균.
    """

    def __init__(self, model_dir: str | Path | None = None):
        self.model_dir = Path(model_dir) if model_dir else None
        self.labeling = LabelingPipeline()
        self._model = None
        self._tokenizer = None
        # GradeRegistry에서 동적 로드 — 다른 프로젝트의 커스텀 등급 자동 반영
        active = _get_labels()
        self._id2label: dict[int, object] = dict(enumerate(active))
        if self.model_dir and self.model_dir.exists():
            self._load_model()

    def _load_model(self):
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        import torch
        self._tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        self._model = AutoModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self._model.eval()
        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        self._model.to(self._device)

    # FNR-safe override: rule engine이 이 점수 이상으로 TS를 잡으면 모델 결과 무시.
    # 모델이 TS 미학습 도메인(M&A·암호·국방 등)을 S1/S2/S3로 내릴 때 방어.
    _FNR_RULE_TS_THRESHOLD = 3.0

    def run(
        self,
        text: str,
        use_rag: bool = False,
        rag_namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
        return_evidence: bool = True,
    ) -> InferenceResult:
        if self._model is not None:
            result = self._run_model(text, use_rag, return_evidence)
            # FNR-safe: rule engine이 TS를 강하게 잡는데 모델이 낮은 등급을 줬으면 TS로 올림.
            if result.label != Grade.TS:
                try:
                    rule_res = self.labeling.engine.label(text)
                    ts_score = rule_res.grade_scores.get("TS", 0.0)
                    if ts_score >= self._FNR_RULE_TS_THRESHOLD:
                        from lloydk.modules.m3_labeling.seeds import GRADE_ORDER  # noqa: PLC0415
                        if GRADE_ORDER.get(rule_res.grade, 99) < GRADE_ORDER.get(result.label.value, 99):
                            result = InferenceResult(
                                label=Grade.TS,
                                confidence=max(result.confidence, rule_res.confidence),
                                scores={**result.scores, "TS": max(result.scores.get("TS", 0), 0.7)},
                                factors=result.factors,
                                evidence=result.evidence,
                                rag_context=result.rag_context,
                                model_version=result.model_version,
                                warnings=result.warnings + [f"fnr-safe override: rule TS score={ts_score:.1f}"],
                            )
                except Exception:  # noqa: BLE001
                    pass
        else:
            result = self._run_rule_fallback(text, return_evidence)

        # 표적 1 (2026-05-29): use_rag=True면 retrieval facade 호출하여 rag_context 채움.
        # rule-fallback / _run_model 어느 경로든 동일하게 RAG context 보강 — 분류 본문은 안 건드림.
        # 모든 외부 의존(벡터스토어/임베더/reranker) 실패는 silent + 빈 컨텍스트 폴백.
        if use_rag:
            try:
                hits = self._build_rag_context(
                    query=text,
                    namespace=rag_namespace,
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("rag_context build failed: %s", exc)
                hits = []
            if hits:
                result.rag_context = hits
            else:
                # 빈 결과는 운영 신호 — warning에 한 줄 남기되 분류는 그대로 진행.
                result.warnings = list(result.warnings) + ["rag_context empty (store/encoder unavailable or no hits)"]

        return result

    def _run_rule_fallback(self, text: str, return_evidence: bool) -> InferenceResult:
        try:
            from lloydk.api.prom_metrics import RULE_FALLBACK_TOTAL  # noqa: PLC0415
            RULE_FALLBACK_TOTAL.inc()
        except Exception:  # noqa: BLE001
            pass
        lab = self.labeling.label(text)
        # grade_scores를 합계로 나눠 [0,1] 확률로 변환 (rule engine은 원시 점수 반환)
        raw = (lab.rule_result.grade_scores if lab.rule_result else {}) or {}
        total_raw = sum(raw.values())
        if total_raw > 0:
            scores = {g.value: round(raw.get(g.value, 0.0) / total_raw, 4) for g in _LABELS}
        else:
            scores = {g.value: 0.0 for g in _LABELS}
        return InferenceResult(
            label=lab.grade,
            confidence=lab.confidence,
            scores=scores,
            factors=lab.factors,
            evidence=lab.evidence if return_evidence else [],
            model_version="rule-fallback-v0",
            warnings=["model weights not loaded — using rule-based fallback"],
        )

    def _run_model(self, text: str, use_rag: bool, return_evidence: bool) -> InferenceResult:
        import torch
        import torch.nn.functional as F

        chunks = chunk_text(text, settings.max_seq_len * 3, settings.chunk_overlap)
        chunk_texts = [c.text for c in chunks] or [text]

        probs = []
        with torch.no_grad():
            for batch_start in range(0, len(chunk_texts), 8):
                batch = chunk_texts[batch_start:batch_start + 8]
                enc = self._tokenizer(
                    batch, truncation=True, max_length=settings.max_seq_len,
                    padding=True, return_tensors="pt",
                ).to(self._device)
                logits = self._model(**enc).logits
                probs.append(F.softmax(logits, dim=-1).cpu())
        doc_prob = torch.cat(probs).mean(dim=0)
        scores = {g.value: float(doc_prob[i]) for i, g in self._id2label.items()}
        pred_idx = int(doc_prob.argmax().item())
        pred = self._id2label[pred_idx]
        conf = float(doc_prob[pred_idx])

        lab = self.labeling.label(text)  # 보조 evidence/factors
        # use_rag은 run() 레벨에서 통합 처리 — 본 함수 시그니처는 호환 위해 유지.
        del use_rag
        return InferenceResult(
            label=pred,
            confidence=conf,
            scores=scores,
            factors=lab.factors,
            evidence=lab.evidence if return_evidence else [],
            model_version=str(self.model_dir.name) if self.model_dir else "model",
        )

    # ------------------------------------------------------------
    # 표적 1 — RAG context builder
    # ------------------------------------------------------------

    def _build_rag_context(
        self,
        *,
        query: str,
        namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
    ) -> list[RagContextHit]:
        """retrieval facade를 안전하게 호출해 RagContextHit 리스트 반환.

        모든 외부 의존(벡터스토어/임베더/reranker)이 실패하거나 부재하면 빈 리스트.
        - store: build_store() — ES 미가용 시 InMemoryStore 자동 폴백 (이미 어댑터가 처리)
        - encode: build_embedder() — HF 모델 로드 실패 시 HashEmbedding 폴백 (이미 어댑터가 처리)
        - collection: namespace 우선, 없으면 settings.rag_default_collection
        - top_k: settings.rag_default_top_k 또는 5
        """
        if not query or not query.strip():
            return []

        # collection 결정
        collection = (namespace or "").strip() or getattr(settings, "rag_default_collection", "docs")
        top_k = int(getattr(settings, "rag_default_top_k", 5))
        method = getattr(settings, "rag_query_expansion_method", "rule")

        # 어댑터 lazy import — m5_inference 모듈 로드 시점 의존 차단
        try:
            from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
            from lloydk.adapters.vectorstore import build_store  # noqa: PLC0415
            from lloydk.services.retrieval import expand_then_search  # noqa: PLC0415
        except Exception as exc:  # noqa: BLE001
            logger.debug("rag adapters unavailable: %s", exc)
            return []

        try:
            store = build_store()
        except Exception as exc:  # noqa: BLE001
            logger.warning("vectorstore build failed: %s", exc)
            return []

        try:
            embedder = build_embedder()
        except Exception as exc:  # noqa: BLE001
            logger.warning("embedder build failed: %s", exc)
            return []

        def _encode(t: str):
            try:
                result = embedder.embed([t])
                # EmbeddingResult.vectors 또는 list[list[float]]
                vectors = getattr(result, "vectors", None) or result
                return vectors[0] if vectors else []
            except Exception as exc:  # noqa: BLE001
                logger.debug("encode failed: %s", exc)
                return []

        def _encode_batch(texts: list[str]):
            """§1: 확장 쿼리 N개 1회 forward — KURE p50 629ms × 4쿼리 단축."""
            if not texts:
                return []
            try:
                result = embedder.embed(texts)
                vectors = getattr(result, "vectors", None) or result
                return list(vectors)
            except Exception as exc:  # noqa: BLE001
                logger.debug("encode_batch failed: %s", exc)
                return []

        # metadata로 필터 전달 가능 (tenant_id 등 호출자가 보낸 거)
        filter_ = None
        if isinstance(metadata, dict):
            tenant = metadata.get("tenant_id")
            if tenant:
                filter_ = {"tenant_id": tenant}

        try:
            hits = expand_then_search(
                store=store,
                collection=collection,
                query_text=query,
                encode_batch=_encode_batch,
                encode=_encode,
                method=method,
                top_k=top_k,
                filter=filter_,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("expand_then_search failed: %s", exc)
            return []

        # SearchHit → RagContextHit 변환
        out: list[RagContextHit] = []
        for h in hits:
            payload = h.payload or {}
            source_doc = str(payload.get("doc_id") or payload.get("source_doc") or h.id)
            out.append(RagContextHit(
                source_doc=source_doc,
                chunk_id=str(h.id),
                score=float(h.score),
            ))
        return out
