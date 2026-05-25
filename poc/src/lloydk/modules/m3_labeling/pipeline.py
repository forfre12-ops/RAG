"""M3 Labeling 통합 파이프라인.

전략:
  1. rule_engine (키워드 시드) → 1차 라벨
  2. confidence < threshold → llm_labeler 호출 (선택)
  3. 결과를 ClassifyService 호환 LabelingResult(Grade/EvaluationFactors/EvidenceSpan)로 변환
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine, RuleLabelResult
from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS

# ClassifyService 호환 스키마 (있으면 import, 없으면 dataclass 폴백)
try:
    from lloydk.schemas.classify import EvaluationFactors, EvidenceSpan
    from lloydk.schemas.common import Grade

    _HAS_SCHEMA = True
except Exception:  # noqa: BLE001
    _HAS_SCHEMA = False

    class Grade(str):  # type: ignore[no-redef]
        TS = "TS"
        S1 = "S1"
        S2 = "S2"
        S3 = "S3"

    @dataclass
    class EvidenceSpan:  # type: ignore[no-redef]
        start: int
        end: int
        text: str
        weight: float = 0.0
        tag: Optional[str] = None

    @dataclass
    class EvaluationFactors:  # type: ignore[no-redef]
        economic_value: float = 0.0
        non_publicity: float = 0.0
        management_level: float = 0.0
        leak_impact: float = 0.0


@dataclass
class LabelingResult:
    grade: str
    confidence: float
    factors: EvaluationFactors
    evidence: list[EvidenceSpan] = field(default_factory=list)
    method: str = "rule"          # rule | rule+llm
    rule_result: Optional[RuleLabelResult] = None


_FACTOR_FIELD_MAP = {
    "ECONOMIC_VALUE": "economic_value",
    "NON_PUBLICITY": "non_publicity",
    "MANAGEMENT_LEVEL": "management_level",
    "LEAK_IMPACT": "leak_impact",
}


class LabelingPipeline:
    """rules_path 인자는 하위호환용 — 무시되며 KEYWORD_SEEDS를 사용."""

    def __init__(
        self,
        rules_path: str | Path | None = None,  # noqa: ARG002 (하위호환)
        *,
        use_llm_fallback: bool = False,
        llm_threshold: float = 0.3,
    ) -> None:
        self.engine = LabelRuleEngine(KEYWORD_SEEDS)
        self.use_llm_fallback = use_llm_fallback
        self.llm_threshold = llm_threshold
        self._llm_labeler = None  # lazy

    def label(self, text: str) -> LabelingResult:
        r = self.engine.label(text)
        method = "rule"
        if self.use_llm_fallback and r.confidence < self.llm_threshold:
            try:
                from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler

                if self._llm_labeler is None:
                    self._llm_labeler = LLMLabeler()
                llm_out = self._llm_labeler.label(text)
                if llm_out.grade in {"TS", "S1", "S2", "S3"}:
                    # FNR-safe merge: rule 등급과 LLM 등급 중 더 높은(낮은 order) 등급 선택
                    from lloydk.modules.m3_labeling.seeds import GRADE_ORDER

                    rule_order = GRADE_ORDER[r.grade]
                    llm_order = GRADE_ORDER[llm_out.grade]
                    if llm_order < rule_order:
                        r = RuleLabelResult(
                            grade=llm_out.grade,
                            confidence=max(r.confidence, llm_out.confidence),
                            grade_scores=r.grade_scores,
                            factor_scores={
                                k: max(v, llm_out.factor_scores.get(k, 0.0))
                                for k, v in r.factor_scores.items()
                            },
                            matched_keywords=r.matched_keywords,
                            total_score=r.total_score,
                            method="rule+llm",
                        )
                    method = "rule+llm"
            except Exception:  # noqa: BLE001
                pass

        factors = EvaluationFactors(
            **{
                _FACTOR_FIELD_MAP[k]: v
                for k, v in r.factor_scores.items()
                if k in _FACTOR_FIELD_MAP
            }
        )
        evidence = [
            EvidenceSpan(
                start=text.find(m.keyword) if text.find(m.keyword) >= 0 else 0,
                end=(text.find(m.keyword) + len(m.keyword)) if text.find(m.keyword) >= 0 else len(m.keyword),
                text=m.keyword,
                weight=m.score,
                tag=m.factor,
            )
            for m in r.matched_keywords[:50]
        ]
        grade_val = Grade(r.grade) if _HAS_SCHEMA else r.grade
        return LabelingResult(
            grade=grade_val,
            confidence=r.confidence,
            factors=factors,
            evidence=evidence,
            method=method,
            rule_result=r,
        )
