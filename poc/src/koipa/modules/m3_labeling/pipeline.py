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

from koipa.modules.m3_labeling.rule_engine import (
    LabelRuleEngine,
    RuleLabelResult,
    build_rule_engine_from_db,
)

# ClassifyService 호환 스키마 (있으면 import, 없으면 dataclass 폴백)
try:
    from koipa.schemas.classify import EvaluationFactors, EvidenceSpan
    from koipa.schemas.common import Grade, GradeRegistry

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
        secrecy: float = 0.0
        value: float = 0.0
        management: float = 0.0


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
            from koipa.schemas.common import FactorRegistry  # noqa: PLC0415
            return FactorRegistry.get_field_map()
        except Exception:  # noqa: BLE001
            pass
    return {
        "SECRECY": "secrecy",
        "VALUE": "value",
        "MANAGEMENT": "management",
    }


class LabelingPipeline:
    """rules_path 인자는 하위호환용 — 무시되며 KEYWORD_SEEDS를 사용."""

    def __init__(
        self,
        rules_path: str | Path | None = None,  # noqa: ARG002 (하위호환)
        *,
        rule_engine: LabelRuleEngine | None = None,
        use_llm_fallback: bool = False,
        llm_threshold: float = 0.3,
    ) -> None:
        # DB에 키워드가 있으면 DB 우선, 없으면 KEYWORD_SEEDS 폴백.
        # 다른 프로젝트는 level_keywords 테이블에 도메인 키워드를 등록하면 자동 반영.
        # An isolated batch may inject an immutable engine to avoid both a DB
        # connection attempt and time-varying operational keyword state.
        self.engine = rule_engine if rule_engine is not None else build_rule_engine_from_db()
        self.use_llm_fallback = use_llm_fallback
        self.llm_threshold = llm_threshold
        self._llm_labeler = None  # lazy

    def label(self, text: str) -> LabelingResult:
        r = self.engine.label(text)
        method = "rule"
        if self.use_llm_fallback and r.confidence < self.llm_threshold:
            try:
                from koipa.modules.m3_labeling.llm_labeler import LLMLabeler

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
                        # [M-merge-conf] LLM이 더 높은 등급을 제안해 승격할 때, rule의
                        # grade_scores/total_score(argmax가 rule 등급)를 그대로 쓰면 선택
                        # 등급과 confidence/argmax가 불일치한다. 선택 등급(=llm_out.grade)에
                        # 정합하도록 confidence를 재계산하고 grade_scores도 그 등급이 argmax가
                        # 되게 보정한다. FNR-safe 승격이므로 confidence는 LLM의 신뢰도를
                        # 우선 채택(rule confidence는 다른 등급에 대한 값이라 의미가 다름).
                        merged_factor_scores = {
                            k: max(v, llm_out.factor_scores.get(k, 0.0))
                            for k, v in r.factor_scores.items()
                        }
                        # llm_out에만 있는 factor_code도 누락 없이 포함
                        for k, v in llm_out.factor_scores.items():
                            if k not in merged_factor_scores:
                                merged_factor_scores[k] = v
                        chosen_grade = llm_out.grade
                        # grade_scores를 복사해 선택 등급이 최댓값(argmax)이 되도록 보정.
                        merged_grade_scores = dict(r.grade_scores)
                        prev_top = max(merged_grade_scores.values()) if merged_grade_scores else 0.0
                        merged_grade_scores[chosen_grade] = max(
                            merged_grade_scores.get(chosen_grade, 0.0), prev_top
                        )
                        merged_total = sum(merged_grade_scores.values())
                        # confidence: LLM 신뢰도와, 보정된 grade_scores에서의 비중 중 큰 값.
                        score_ratio = (
                            merged_grade_scores[chosen_grade] / merged_total
                            if merged_total > 0
                            else 0.0
                        )
                        merged_conf = round(max(llm_out.confidence, score_ratio), 4)
                        r = RuleLabelResult(
                            grade=chosen_grade,
                            confidence=merged_conf,
                            grade_scores={g: round(s, 4) for g, s in merged_grade_scores.items()},
                            factor_scores=merged_factor_scores,
                            matched_keywords=r.matched_keywords,
                            total_score=round(merged_total, 4),
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
        # [M-rule-evidence] m.keyword는 이제 실제 매치 substring(정규식 원문 아님).
        # 정확한 span(m.start/m.end)이 있으면 그대로 쓰고, 없으면 text.find로 첫 위치 탐색.
        evidence = []
        for m in r.matched_keywords[:50]:
            if getattr(m, "start", None) is not None and getattr(m, "end", None) is not None:
                start, end = m.start, m.end
            else:
                idx = text.find(m.keyword)
                if idx >= 0:
                    start, end = idx, idx + len(m.keyword)
                else:
                    start, end = 0, len(m.keyword)
            evidence.append(
                EvidenceSpan(
                    start=start,
                    end=end,
                    text=m.keyword,
                    weight=m.score,
                    tag=m.factor,
                )
            )
        grade_val = Grade(r.grade) if _HAS_SCHEMA else r.grade
        return LabelingResult(
            grade=grade_val,
            confidence=r.confidence,
            factors=factors,
            evidence=evidence,
            method=method,
            rule_result=r,
        )
