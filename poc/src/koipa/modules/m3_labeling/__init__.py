"""M3 Labeling — 가이드라인 룰 + LLM fallback (FUN-023)."""

from koipa.modules.m3_labeling.pipeline import LabelingPipeline, LabelingResult
from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine, RuleLabelResult
from koipa.modules.m3_labeling.seeds import FACTOR_SEEDS, GRADE_ORDER, KEYWORD_SEEDS

__all__ = [
    "LabelingPipeline",
    "LabelingResult",
    "LabelRuleEngine",
    "RuleLabelResult",
    "KEYWORD_SEEDS",
    "FACTOR_SEEDS",
    "GRADE_ORDER",
]
