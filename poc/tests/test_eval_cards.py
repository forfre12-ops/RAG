"""eval_cards (C) — Wilson CI·출처×등급 카드·INCONCLUSIVE·단일숫자 금지 테스트."""
from __future__ import annotations

import pytest

from lloydk.modules.m6_evaluation.eval_cards import (
    VERDICT_FAIL,
    VERDICT_INCONCLUSIVE,
    VERDICT_NA,
    VERDICT_PASS,
    build_eval_cards,
    wilson_interval,
)


def test_wilson_zero_n_is_max_uncertainty():
    assert wilson_interval(0, 0) == (0.0, 1.0)


def test_wilson_clean_small_upper_bound():
    lo, hi = wilson_interval(0, 100)
    assert lo == 0.0
    assert hi < 0.05  # 0/100이면 상한도 5% 미만


def test_wilson_width_shrinks_with_n():
    _, hi_small = wilson_interval(1, 10)
    _, hi_big = wilson_interval(10, 100)  # 같은 비율, 큰 표본
    assert hi_big < hi_small  # 표본 클수록 구간 좁아짐


def _recs(source, gold, preds):
    """gold 한 등급에 대해 preds(예측 리스트)를 레코드로."""
    return [{"source": source, "gold": gold, "pred": p} for p in preds]


def test_lowest_grade_is_na_not_pass():
    # 전부 S3 → under-class 정의 불가 → N/A (전부-S3 분류기 거짓 PASS 방지)
    report = build_eval_cards(_recs("anchor", "S3", ["S3"] * 50))
    s3 = [c for c in report.cards if c.grade == "S3"][0]
    assert s3.verdict == VERDICT_NA
    assert report.counts[VERDICT_PASS] == 0


def test_small_n_inconclusive():
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 5), min_n=30)
    ts = [c for c in report.cards if c.grade == "TS"][0]
    assert ts.verdict == VERDICT_INCONCLUSIVE
    assert "표본 부족" in ts.cannot_measure


def test_clean_large_n_passes():
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 100))
    ts = [c for c in report.cards if c.grade == "TS"][0]
    assert ts.verdict == VERDICT_PASS
    assert ts.underclass_fnr == 0.0


def test_high_fnr_fails():
    # TS 100건 중 30건을 S3로 미탐 → 하한 CI > 천장 → FAIL
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 70 + ["S3"] * 30))
    ts = [c for c in report.cards if c.grade == "TS"][0]
    assert ts.verdict == VERDICT_FAIL
    assert ts.underclass_fnr == pytest.approx(0.30)


def test_straddle_ceiling_inconclusive():
    # fnr 점추정이 천장 근처라 CI가 천장을 가로지름 → INCONCLUSIVE
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 38 + ["S3"] * 2))
    ts = [c for c in report.cards if c.grade == "TS"][0]
    assert ts.verdict == VERDICT_INCONCLUSIVE


def test_combined_score_is_forbidden():
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 30))
    with pytest.raises(NotImplementedError):
        _ = report.combined_score


def test_no_headline_in_dict():
    report = build_eval_cards(_recs("anchor", "TS", ["TS"] * 30))
    d = report.to_dict()
    assert d["headline"] is None
    assert "단일 숫자" in d["note"]


def test_slices_separated_not_merged():
    # anchor=100건 깨끗(PASS 가능), holdout=30건 전부 미탐(FAIL) — 출처별로 갈림
    recs = _recs("anchor", "TS", ["TS"] * 100) + _recs("holdout", "TS", ["S3"] * 30)
    report = build_eval_cards(recs)
    by = report.by_slice()
    assert set(by) == {"anchor", "holdout"}
    assert by["anchor"][0].verdict == VERDICT_PASS
    assert by["holdout"][0].verdict == VERDICT_FAIL
