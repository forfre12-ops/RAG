"""룰 라벨러 + LLM 라벨러 합의 게이트 (gold 편입 판정).

make_llm_judge_gold.py에 인라인돼 있던 합의 로직을 importable 순수 함수로 추출.
골든셋 빌더·라벨링 자동화가 동일 규칙으로 재사용한다. 동작은 스크립트와 동일하게 보존.
(torch 등 무거운 의존 없음 — 순수 결정 함수)
"""
from __future__ import annotations

from dataclasses import dataclass

# rule_conf가 이 값 미만이면 룰 라벨러가 키워드를 못 찾은 것 → "의견 없음"
RULE_NO_OPINION_THRESHOLD = 0.05
# C 경로에서 룰 무의견 시 LLM 단독 gold를 허용하는 상위 등급.
# S3는 이미 다수라 편향 심화 방지를 위해 제외.
DEFAULT_UPPER_GRADES = ("TS", "S1", "S2")


@dataclass(frozen=True)
class ConsensusResult:
    """합의 게이트 판정 결과."""

    is_gold: bool
    status: str          # gold_consensus | gold_llm_primary | gold_llm_upper | low_conf | disagree
    label_source: str    # llm_judge_consensus | llm_judge_primary | llm_judge_uncertain
    review_status: str   # accepted | needs_review
    agree: bool
    rule_no_opinion: bool
    conf_ok: bool


def evaluate_consensus(
    rule_grade: str,
    rule_conf: float,
    llm_grade: str,
    llm_conf: float,
    *,
    min_rule_conf: float = 0.5,
    min_llm_conf: float = 0.7,
    rule_no_opinion_threshold: float = RULE_NO_OPINION_THRESHOLD,
    upper_grades: tuple[str, ...] = DEFAULT_UPPER_GRADES,
) -> ConsensusResult:
    """룰·LLM 두 라벨을 합의 게이트로 평가해 gold 편입 여부를 판정.

    Gold 판정 3경로:
      A) 두 판정자 동의 + 둘 다 신뢰도 충족             → gold_consensus / llm_judge_consensus
      B) 룰 무의견 + 동의 + LLM 신뢰도 높음              → gold_llm_primary / llm_judge_primary
      C) 룰 무의견 + LLM이 상위등급(TS/S1/S2) + 고신뢰   → gold_llm_upper / llm_judge_primary
    그 외: 동의면 low_conf, 불일치면 disagree → llm_judge_uncertain / needs_review.
    """
    agree = (rule_grade == llm_grade)
    rule_no_opinion = rule_conf < rule_no_opinion_threshold
    conf_ok = (rule_conf >= min_rule_conf and llm_conf >= min_llm_conf)
    llm_upper = llm_grade in upper_grades

    is_gold = (
        (agree and conf_ok)                                              # A
        or (agree and rule_no_opinion and llm_conf >= min_llm_conf)      # B
        or (rule_no_opinion and llm_upper and llm_conf >= min_llm_conf)  # C
    )

    if is_gold:
        if conf_ok:
            status = "gold_consensus"
        elif agree:
            status = "gold_llm_primary"
        else:
            status = "gold_llm_upper"   # C: 룰 무의견 + LLM 상위등급 단독
        label_source = "llm_judge_consensus" if conf_ok else "llm_judge_primary"
        review_status = "accepted"
    else:
        status = "low_conf" if agree else "disagree"
        label_source = "llm_judge_uncertain"
        review_status = "needs_review"

    return ConsensusResult(
        is_gold=is_gold,
        status=status,
        label_source=label_source,
        review_status=review_status,
        agree=agree,
        rule_no_opinion=rule_no_opinion,
        conf_ok=conf_ok,
    )
