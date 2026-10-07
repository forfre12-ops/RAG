"""W2 소크 판정 로직 — 순수(부하 실행 불요)."""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from run_soak import soak_verdict  # noqa: E402


def test_pass_when_stable():
    v, reasons, growth = soak_verdict(100.0, 120.0, 0, 0.0, max_rss_growth_mb=150, max_error_rate=0.01)
    assert v == "PASS" and reasons == [] and growth == 20.0


def test_fail_high_grade_veto_miss():
    v, reasons, _ = soak_verdict(100, 100, 1, 0.0, max_rss_growth_mb=150, max_error_rate=0.01)
    assert v == "FAIL" and any("high_grade_veto_miss" in r for r in reasons)


def test_fail_rss_growth_leak():
    v, reasons, growth = soak_verdict(100, 400, 0, 0.0, max_rss_growth_mb=150, max_error_rate=0.01)
    assert v == "FAIL" and growth == 300.0 and any("rss_growth" in r for r in reasons)


def test_fail_error_rate():
    v, reasons, _ = soak_verdict(100, 110, 0, 0.05, max_rss_growth_mb=150, max_error_rate=0.01)
    assert v == "FAIL" and any("error_rate" in r for r in reasons)


def test_none_rss_is_zero_growth_and_pass():
    v, reasons, growth = soak_verdict(None, None, 0, 0.0, max_rss_growth_mb=150, max_error_rate=0.01)
    assert v == "PASS" and growth == 0.0
