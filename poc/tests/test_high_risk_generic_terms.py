"""공개 문서에 흔한 일반 낱말은 고위험 부스트로 세지 않는다 (2026-09-29).

근거는 rule_engine.py `_HIGH_RISK_PATTERNS` 위 주석과 scripts/measure_rule_acronym_negation.py 이다.
정답이 있는 1,418건에서 PMI·IPO·valuation·Guide Book 은 빼면 룰 등급이 맞게 바뀐 문서만 있었고
(7건) 틀리게 바뀐 문서·필요한 상향을 잃는 문서는 없었다. 서빙 경로(배포 프로파일)에서는 최종
등급 변화 0건, 새 무음 미탐 0건.

남긴 토큰도 함께 고정한다 — 제거를 '더 많이 빼는' 쪽으로 밀지 않도록.
"""

from __future__ import annotations

from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine

# 영문만 쓴다 — 한글 시드 매칭이 끼면 부스트 매치와 섞인다.
_REMOVED_TEXT = (
    "The euro-zone PMI fell in September. The IPO valuation rose. "
    "See this week's Guide Book."
)


def _boosts(text: str) -> set[str]:
    """영문 약어 부스트 매치만 — start 가 채워진 매치가 부스트다(has_real_evidence 규약)."""
    res = LabelRuleEngine().label(text)
    return {m.keyword.lower() for m in res.matched_keywords if m.start is not None}


def test_removed_generic_terms_do_not_boost():
    hit = _boosts(_REMOVED_TEXT)
    assert not hit & {"pmi", "ipo", "valuation", "guide book"}, hit


def test_removed_generic_terms_leave_no_rule_signal():
    res = LabelRuleEngine().label(_REMOVED_TEXT)
    assert res.grade == "S3"
    assert res.total_score == 0.0


def test_kept_terms_still_boost():
    text = "EUV lithography, NDA-based M&A due diligence, Post-Merger integration."
    hit = _boosts(text)
    assert {"euv", "nda", "m&a", "post-merger"} <= hit, hit


def test_post_merger_still_reaches_ts_boost_weight():
    # 진짜 사후통합 문서는 PMI 약어 없이도 이 패턴이 계속 잡는다.
    res = LabelRuleEngine().label("Post-Merger integration plan.")
    assert res.grade_scores.get("TS", 0.0) >= 1.4
