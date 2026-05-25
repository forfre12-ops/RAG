from __future__ import annotations
import re
import yaml
from dataclasses import dataclass, field
from pathlib import Path

from lloydk.schemas.common import Grade
from lloydk.schemas.classify import EvidenceSpan, EvaluationFactors


DEFAULT_RULES = """
version: "2026-05"
factor_weights:
  economic_value: 0.30
  non_publicity:  0.25
  management_level: 0.15
  leak_impact:    0.30
keywords:
  economic_value:
    - {pattern: "(매출|수익|마진|단가|원가)", weight: 1.0}
    - {pattern: "(특허출원|영업이익률|투자유치)", weight: 1.2}
  non_publicity:
    - {pattern: "(미공개|대외비|기밀|내부전용)", weight: 1.3}
  management_level:
    - {pattern: "(접근권한|보안등급|보호조치)", weight: 1.0}
  leak_impact:
    - {pattern: "(소송|손해배상|제재|평판)", weight: 1.5}
    - {pattern: "(전략|핵심기술|차별화)", weight: 1.0}
grade_thresholds:
  TS: 3.6
  S1: 2.8
  S2: 1.8
  S3: 0.0
"""


@dataclass
class LabelingResult:
    grade: Grade
    factors: EvaluationFactors
    evidence: list[EvidenceSpan] = field(default_factory=list)


class LabelingPipeline:
    def __init__(self, rules_path: str | Path | None = None):
        text = Path(rules_path).read_text(encoding="utf-8") if rules_path else DEFAULT_RULES
        self.rules = yaml.safe_load(text)
        self._compiled = self._compile_rules()

    def _compile_rules(self):
        out = {}
        for factor, items in self.rules["keywords"].items():
            out[factor] = [(re.compile(it["pattern"]), float(it.get("weight", 1.0))) for it in items]
        return out

    def label(self, text: str) -> LabelingResult:
        factor_scores: dict[str, float] = {}
        evidence: list[EvidenceSpan] = []

        for factor, patterns in self._compiled.items():
            score = 0.0
            for pat, w in patterns:
                for m in pat.finditer(text):
                    score += w
                    evidence.append(EvidenceSpan(
                        start=m.start(), end=m.end(), text=m.group(0),
                        weight=w, tag=factor,
                    ))
            factor_scores[factor] = min(score, 5.0)

        weights = self.rules["factor_weights"]
        weighted_sum = sum(factor_scores[f] * weights.get(f, 0) for f in factor_scores)

        grade = Grade.S3
        for code in ("TS", "S1", "S2", "S3"):
            if weighted_sum >= self.rules["grade_thresholds"][code]:
                grade = Grade(code)
                break

        factors = EvaluationFactors(
            economic_value=factor_scores.get("economic_value", 0),
            non_publicity=factor_scores.get("non_publicity", 0),
            management_level=factor_scores.get("management_level", 0),
            leak_impact=factor_scores.get("leak_impact", 0),
        )
        return LabelingResult(grade=grade, factors=factors, evidence=evidence[:50])
