"""고위험 패턴 가중치 배수 — B1 (동작 보존 + 튜닝 레버).

기본 배수 1.0은 기존 점수와 동일(동작 보존), <1.0은 범용 약어 영향 하향, 0은 비활성.
"""

from __future__ import annotations

from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine

# 범용 약어 2개(EUV→TS w1.6, M&A→TS w1.4) 포함 — 한글 시드 매칭은 피하려 영문 위주.
_TEXT = "The EUV process and the M&A valuation were reviewed."


def _ts_score(mult):
    eng = LabelRuleEngine(high_risk_weight_multiplier=mult)
    return eng.label(_TEXT).grade_scores.get("TS", 0.0)


def test_default_multiplier_is_one_behavior_preserving():
    # 명시 1.0 == 미지정(설정 기본 1.0)
    assert _ts_score(1.0) == _ts_score(None)
    assert _ts_score(1.0) > 0


def test_lower_multiplier_scales_down_ts_boost():
    base = _ts_score(1.0)
    half = _ts_score(0.5)
    assert half < base
    assert abs(half - base * 0.5) < 1e-6


def test_zero_multiplier_disables_boost():
    eng = LabelRuleEngine(high_risk_weight_multiplier=0.0)
    res = eng.label(_TEXT)
    # 고위험 부스트 비활성 → EUV/M&A 매치가 TS에 더해지지 않음
    assert res.grade_scores.get("TS", 0.0) == 0.0


def test_matched_keyword_weight_reflects_multiplier():
    eng = LabelRuleEngine(high_risk_weight_multiplier=0.5)
    res = eng.label(_TEXT)
    euv = [m for m in res.matched_keywords if m.keyword.upper() == "EUV"]
    assert euv, "EUV 매치 있어야"
    # 원래 1.6 → 0.5배 = 0.8
    assert abs(euv[0].weight - 0.8) < 1e-6


def test_korean_ma_due_diligence_terms_feed_s1_floor():
    text = (
        "M&A \uB300\uC0C1 \uAE30\uC5C5 \uC2E4\uC0AC \uC608\uBE44 \uAC80\uD1A0 "
        "\uBCF4\uACE0\uC11C\n"
        "\uC7A0\uC815 \uD3C9\uAC00: \uC778\uC218 \uAC00\uACA9 \uBC94\uC704 "
        "\uBC0F \uC8FC\uC694 \uB9AC\uC2A4\uD06C \uC694\uC18C"
    )
    res = LabelRuleEngine().label(text)

    assert res.grade_scores.get("S1", 0.0) >= 2.2
    assert {m.keyword for m in res.matched_keywords if m.grade == "S1"} >= {
        "\uAE30\uC5C5 \uC2E4\uC0AC",
        "\uC778\uC218 \uAC00\uACA9",
    }
