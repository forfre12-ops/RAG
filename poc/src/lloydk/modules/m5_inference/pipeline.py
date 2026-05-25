from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.config import settings
from lloydk.schemas.common import Grade
from lloydk.schemas.classify import EvidenceSpan, EvaluationFactors, RagContextHit
from lloydk.modules.m2_preprocess import split as _chunk_split
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline


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
        self._id2label: dict[int, Grade] = {i: g for i, g in enumerate(_LABELS)}
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

    def run(
        self,
        text: str,
        use_rag: bool = False,
        rag_namespace: Optional[str] = None,
        metadata: Optional[dict] = None,
        return_evidence: bool = True,
    ) -> InferenceResult:
        if self._model is not None:
            return self._run_model(text, use_rag, return_evidence)
        return self._run_rule_fallback(text, return_evidence)

    def _run_rule_fallback(self, text: str, return_evidence: bool) -> InferenceResult:
        lab = self.labeling.label(text)
        scores = {g.value: 0.05 for g in _LABELS}
        scores[lab.grade.value] = 0.85
        return InferenceResult(
            label=lab.grade,
            confidence=0.85,
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
        return InferenceResult(
            label=pred,
            confidence=conf,
            scores=scores,
            factors=lab.factors,
            evidence=lab.evidence if return_evidence else [],
            model_version=str(self.model_dir.name) if self.model_dir else "model",
        )
