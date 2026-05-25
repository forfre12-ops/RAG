"""키워드 매칭 기반 1차 라벨링 + 4대 평가요소 점수화.

알고리즘:
  1. 텍스트에서 각 키워드의 빈도(또는 정규식 매칭 수) 측정
  2. 등급별 점수 = Σ (매칭수 × keyword.weight)
  3. 등급 = argmax (점수)
  4. 4대 평가요소 점수 = Σ (factor별 매칭 가중치) → 0~5 스케일로 normalize
  5. FNR 최소화 전략: 동점일 때 더 높은 등급(낮은 order)을 선택
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from lloydk.modules.m3_labeling.seeds import (
    FACTOR_SEEDS,
    GRADE_ORDER,
    KEYWORD_SEEDS,
)


@dataclass
class MatchedKeyword:
    keyword: str
    grade: str
    factor: str
    count: int
    weight: float
    score: float                # = count * weight


@dataclass
class RuleLabelResult:
    grade: str                  # TS|S1|S2|S3
    confidence: float
    grade_scores: dict[str, float]
    factor_scores: dict[str, float]      # 4대 요소 0.0~5.0
    matched_keywords: list[MatchedKeyword]
    total_score: float
    method: str = "rule_keyword"
    warnings: list[str] = field(default_factory=list)


class LabelRuleEngine:
    def __init__(self, seeds: list[dict] | None = None, *, fnr_safe: bool = True) -> None:
        self.seeds = seeds or KEYWORD_SEEDS
        self.fnr_safe = fnr_safe
        self._factor_weights = {f["code"]: f["weight"] for f in FACTOR_SEEDS}

    def label(self, text: str) -> RuleLabelResult:
        if not text:
            return RuleLabelResult(
                grade="S3",
                confidence=0.0,
                grade_scores={g: 0.0 for g in GRADE_ORDER},
                factor_scores={f["code"]: 0.0 for f in FACTOR_SEEDS},
                matched_keywords=[],
                total_score=0.0,
                warnings=["empty text → default S3"],
            )

        matches: list[MatchedKeyword] = []
        grade_scores: dict[str, float] = {g: 0.0 for g in GRADE_ORDER}
        factor_raw: dict[str, float] = {f["code"]: 0.0 for f in FACTOR_SEEDS}

        for seed in self.seeds:
            kw = seed["keyword"]
            grade = seed["grade"]
            factor = seed["factor"]
            weight = float(seed["weight"])
            pattern_type = seed.get("pattern_type", "exact")
            count = self._count(text, kw, pattern_type)
            if count == 0:
                continue
            score = count * weight
            matches.append(
                MatchedKeyword(keyword=kw, grade=grade, factor=factor, count=count, weight=weight, score=score)
            )
            grade_scores[grade] += score
            factor_raw[factor] += score

        # 등급 결정
        total = sum(grade_scores.values())
        if total == 0:
            chosen = "S3"
            conf = 0.0
            warnings = ["no keyword matched → default S3"]
        else:
            # argmax. 동점이면 FNR-safe 옵션에 따라 더 높은 등급(낮은 order) 선택
            top_score = max(grade_scores.values())
            tops = [g for g, s in grade_scores.items() if s == top_score]
            chosen = min(tops, key=lambda g: GRADE_ORDER[g]) if self.fnr_safe else tops[0]
            conf = top_score / total
            warnings = []

        # 4대 평가요소 정규화 (0~5)
        max_factor = max(factor_raw.values()) if factor_raw and max(factor_raw.values()) > 0 else 1.0
        factor_scores = {k: round(min(5.0, (v / max_factor) * 5.0), 2) for k, v in factor_raw.items()}

        return RuleLabelResult(
            grade=chosen,
            confidence=round(conf, 4),
            grade_scores={g: round(s, 4) for g, s in grade_scores.items()},
            factor_scores=factor_scores,
            matched_keywords=matches,
            total_score=round(total, 4),
            warnings=warnings,
        )

    @staticmethod
    def _count(text: str, kw: str, pattern_type: str) -> int:
        if pattern_type == "regex":
            return len(re.findall(kw, text))
        # exact: 단순 부분 문자열 (한국어는 word boundary가 영문과 다름)
        if not kw:
            return 0
        return text.count(kw)
