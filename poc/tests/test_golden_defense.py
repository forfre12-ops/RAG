"""다층방어 liveness 감사 — 무음으로 꺼진 방어 축 폭로 회귀."""
from __future__ import annotations

from lloydk.golden_defense import assess_defense_liveness


def _rec(*, flags=(), shadow=None, sc=1.0, agree=True, llm="S1"):
    return {"flags": list(flags), "shadow_grade": shadow, "self_consistency": sc,
            "agreement": agree, "llm_grade": llm}


def test_empty_records():
    out = assess_defense_liveness([])
    assert out["verdict"] == "empty" and out["n"] == 0


def test_shadow_disabled_but_multi_axis():
    """e946233 실상황: shadow 전건 None(coverage 0)이나 disagree·ts_downgrade·self_consistency 발화."""
    recs = []
    recs += [_rec(flags=["ts_downgrade_suspect"]) for _ in range(5)]      # ts 발화
    recs += [_rec(agree=False) for _ in range(10)]                         # disagree 발화
    recs += [_rec(flags=["low_agreement"], sc=0.667) for _ in range(3)]    # self-consistency 발화
    recs += [_rec() for _ in range(20)]                                    # 통과분(gold)
    out = assess_defense_liveness(recs)
    assert out["axes"]["shadow"]["coverage"] == 0.0
    assert out["shadow_active"] is False
    assert out["blocking_axes_active"] == 3
    assert out["verdict"] == "shadow_disabled"
    assert "shadow" in out["recommendation"]


def test_single_axis_is_flagged_dangerous():
    """합의(disagree)만 작동, shadow off·self-consistency 전건 1.0·ts 미발화 → single_axis 경고."""
    recs = [_rec(agree=False) for _ in range(5)] + [_rec() for _ in range(20)]
    out = assess_defense_liveness(recs)
    assert out["verdict"] == "single_axis"
    assert "silent-miss" in out["recommendation"]


def test_full_multi_axis_when_shadow_on():
    """shadow 커버리지 있음 + 그 외 2축 발화 → full_multi_axis."""
    recs = []
    recs += [_rec(shadow="S1") for _ in range(20)]                          # shadow 실행됨
    recs += [_rec(shadow="TS", flags=["production_suspect"], llm="S1")]     # shadow 불일치 발화
    recs += [_rec(agree=False) for _ in range(5)]                           # disagree
    recs += [_rec(flags=["ts_downgrade_suspect"]) for _ in range(3)]        # ts
    out = assess_defense_liveness(recs)
    assert out["shadow_active"] is True
    assert out["axes"]["shadow"]["coverage"] > 0
    assert out["axes"]["shadow"]["divergent"] == 1
    assert out["verdict"] == "full_multi_axis"


def test_self_consistency_informative_detection():
    """self_consistency가 전건 1.0이면 informative=False(무정보), 하나라도 <1이면 True."""
    flat = assess_defense_liveness([_rec(sc=1.0) for _ in range(10)])
    assert flat["axes"]["self_consistency"]["informative"] is False
    varied = assess_defense_liveness([_rec(sc=1.0)] * 9 + [_rec(sc=0.667)])
    assert varied["axes"]["self_consistency"]["informative"] is True
    assert varied["axes"]["self_consistency"]["n_below_1"] == 1
