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

from lloydk.modules.m3_labeling.rule_engine import RuleLabelResult, build_rule_engine_from_db

# ClassifyService 호환 스키마 (있으면 import, 없으면 dataclass 폴백)
try:
    from lloydk.schemas.classify import EvaluationFactors, EvidenceSpan
    from lloydk.schemas.common import Grade, GradeRegistry

    _HAS_SCHEMA = True
except Exception:  # noqa: BLE001
    _HAS_SCHEMA = False

    class Grade(str):  # type: ignore[no-redef]
        TS = "TS"
        S1 = "S1"
        S2 = "S2"
        S3 = "S3"

    class GradeRegistry:  # type: ignore[no-redef]
        @classmethod
        def get_order(cls) -> dict[str, int]:
            return {"TS": 1, "S1": 2, "S2": 3, "S3": 4}

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


def _get_factor_field_map() -> dict[str, str]:
    """FactorRegistry에서 현재 활성 factor_code → field_name 매핑 반환.

    DB에 커스텀 factor가 있으면 그것을 사용, 없으면 기본 4요소 반환.
    """
    if _HAS_SCHEMA:
        try:
            from lloydk.schemas.common import FactorRegistry  # noqa: PLC0415
            return FactorRegistry.get_field_map()
        except Exception:  # noqa: BLE001
            pass
    return {
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
        # DB에 키워드가 있으면 DB 우선, 없으면 KEYWORD_SEEDS 폴백.
        # 다른 프로젝트는 level_keywords 테이블에 도메인 키워드를 등록하면 자동 반영.
        self.engine = build_rule_engine_from_db()
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
                # GradeRegistry에서 현재 활성 등급 목록을 로드해 유효성 검사
                active_grade_order = GradeRegistry.get_order()
                if llm_out.grade in active_grade_order:
                    # FNR-safe merge: rule 등급과 LLM 등급 중 더 높은(낮은 order) 등급 선택
                    rule_order = active_grade_order.get(
                        r.grade.value if hasattr(r.grade, "value") else r.grade, 99
                    )
                    llm_order = active_grade_order.get(llm_out.grade, 99)
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

        if _HAS_SCHEMA:
            factors = EvaluationFactors.from_factor_scores(r.factor_scores)
        else:
            # 스키마 미가용 폴백 — dataclass EvaluationFactors에 직접 매핑
            field_map = _get_factor_field_map()
            factors = EvaluationFactors(
                **{field_map[k]: v for k, v in r.factor_scores.items() if k in field_map}
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
