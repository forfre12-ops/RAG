"""W4 섀도우런 집계 — 순수 aggregate_shadow + 무반출(집계-only) 계약."""

from __future__ import annotations

import numbers
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from run_shadow import aggregate_shadow  # noqa: E402


def _sample_stats():
    return [
        {"grade": "TS", "status": "needs_review", "confidence": 0.9, "parse_ok": True, "latency_ms": 10, "rule_fallback": False},
        {"grade": "S1", "status": "confirmed", "confidence": 0.8, "parse_ok": True, "latency_ms": 20, "rule_fallback": False},
        {"grade": "S3", "status": "confirmed", "confidence": 0.6, "parse_ok": True, "latency_ms": 5, "rule_fallback": True},
        {"grade": None, "status": "parse_fail", "confidence": 0.0, "parse_ok": False, "latency_ms": None, "rule_fallback": False},
    ]


def test_basic_ratios():
    agg = aggregate_shadow(_sample_stats())
    assert agg["n"] == 4
    assert agg["parse_ok_rate"] == 0.75
    assert agg["escalation_rate"] == 0.25
    assert agg["by_grade_count"]["TS"] == 1 and agg["by_grade_count"]["UNKNOWN"] == 1
    assert agg["high_grade_pred_rate"] == 0.5           # TS+S1 = 2/4
    assert agg["high_grade_escalation_rate"] == 0.5     # of {TS,S1}, TS was needs_review → 1/2
    assert agg["low_confidence_rate"] == 0.5            # conf<0.7: 0.6, 0.0
    assert agg["rule_fallback_rate"] == 0.25


def test_empty_input():
    assert aggregate_shadow([])["n"] == 0


def test_high_grade_escalation_none_when_no_high_pred():
    stats = [{"grade": "S3", "status": "confirmed", "confidence": 0.9, "parse_ok": True, "latency_ms": 1, "rule_fallback": False}]
    assert aggregate_shadow(stats)["high_grade_escalation_rate"] is None


def test_no_raw_export_contract_numeric_only():
    """반출물은 수치/None + known-string(grade code·계약문구)만 — 원문·식별자 유입 불가."""
    agg = aggregate_shadow(_sample_stats())
    known_keys = {
        "n", "parse_ok_rate", "by_grade_count", "by_grade_ratio", "escalation_rate",
        "high_grade_pred_rate", "high_grade_escalation_rate", "confidence_p50",
        "confidence_p95", "low_confidence_rate", "latency_p50_ms", "latency_p95_ms",
        "rule_fallback_rate", "export_contract", "note",
    }
    assert set(agg).issubset(known_keys)

    allowed_grade = {"TS", "S1", "S2", "S3", "UNKNOWN"}

    def _check(val, key=None):
        if isinstance(val, dict):
            for k, v in val.items():
                if key in ("by_grade_count", "by_grade_ratio"):
                    assert k in allowed_grade, f"unexpected grade key: {k!r}"
                _check(v, key)
        elif isinstance(val, str):
            assert key == "export_contract", f"unexpected string under {key!r}: {val!r}"
        else:
            assert val is None or isinstance(val, numbers.Number), f"non-numeric leaf: {val!r}"

    for k, v in agg.items():
        _check(v, k)
