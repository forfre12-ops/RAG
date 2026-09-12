"""재지 않은 KPI 를 판정처럼 그리지 않는다 — 성능 보고서 렌더러.

왜 이 시험이 있는가(2026-09-12). 211 부분 실측(2026-09-11, `--only S1,S2,S8,S11,S12,S17`)을
렌더한 `doc/20_시나리오_성능_보고서.partial.html` §2.5 에는 이번 회차에 **돌리지도 않은**
시나리오의 KPI 가 이렇게 실렸다.

    S9.1  변형 일관성      최근값 0.0%   최근 판정 PASS
    S16.4 정상 키 200/201  최근값 0.0%   최근 판정 PASS
    S16.1 잘못된 키 401    최근값 False  최근 판정 PASS

값은 *이번 회차*(측정 없음 → 0)에서, 판정은 *그 KPI 가 마지막으로 나왔던 회차*에서 온
조합이었다. 각주로 "SKIP 된 KPI 는 측정값 0 으로 표시되니 컬러 무시"라고 적어 두었지만,
각주로 덮는 것은 고침이 아니다 — 표를 그대로 인용하면 "정상 키 통과율 0%" 가 된다.

규칙: **재지 않았으면 값도 판정도 내지 않는다.** 차트는 그 회차 점을 찍지 않아 선이 끊기고,
값·판정 칸은 `미측정` 이 된다. 하니스 쪽 같은 규칙은
tests/test_perf_ratelimit_not_recorded_as_failure.py 에 있다.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ----------------------------------------------------------------
# sparkline — 미측정 회차는 점도 선도 없다
# ----------------------------------------------------------------


def test_none_points_are_not_drawn_and_break_the_line():
    from render_perf_report import sparkline_with_threshold

    out = sparkline_with_threshold([1.0, None, 3.0], threshold=2.0)
    assert out.count("<circle") == 2, "실제로 잰 두 회차만 점을 찍는다"
    # 가운데가 비었으므로 두 점을 잇는 선이 있으면 안 된다(잰 적 없는 값을 지나간다).
    assert "<path" not in out


def test_consecutive_points_are_still_connected():
    from render_perf_report import sparkline_with_threshold

    out = sparkline_with_threshold([1.0, 2.0, None], threshold=2.0)
    assert out.count("<circle") == 2
    assert out.count("<path") == 1


def test_all_unmeasured_draws_nothing():
    from render_perf_report import sparkline_with_threshold

    assert sparkline_with_threshold([None, None], threshold=1.0) == ""


# ----------------------------------------------------------------
# §2.5 전체 KPI 추세 — 값과 판정이 다른 회차에서 오면 안 된다
# ----------------------------------------------------------------


def _kpi(kid: str, *, measured: float, status: str, unit: str = "ratio") -> dict:
    return {
        "kpi_id": kid,
        "scenario": kid.split(".")[0],
        "name": f"{kid} 이름",
        "unit": unit,
        "measured": measured,
        "threshold": 0.8,
        "compare": "ge",
        "passed": status == "PASS",
        "status": status,
        "skip_reason": "" if status != "SKIP" else "missing: trained_model",
        "n_samples": 5 if status != "SKIP" else 0,
    }


def _report(ts: str, scenarios: list[dict]) -> dict:
    n = sum(len(s["kpis"]) for s in scenarios)
    return {
        "ts": ts,
        "duration_sec": 1.0,
        "env": {
            "python": "3.11.9", "platform": "linux", "cpu_count": 8, "ram_gb": 46.0,
            "gpu": "L4", "git_sha": "d033516b", "git_branch": "main",
            "pytest_collected": 0, "llm_provider": "noop", "embedding_provider": "hash",
            "services": {"postgres": "UP", "elasticsearch": "N/A", "redis": "UP", "minio": "N/A"},
        },
        "scenarios": scenarios,
        "summary": {
            "total_kpis": n, "pass": n, "fail": 0, "skip": 0, "pass_rate": 1.0,
            "scenarios": {"total": len(scenarios), "pass": len(scenarios),
                          "fail": 0, "skip": 0, "error": 0},
        },
    }


def _scenario(sid: str, kpis: list[dict]) -> dict:
    return {"scenario": sid, "title": f"{sid} 제목", "status": "PASS",
            "duration_ms": 10.0, "kpis": kpis, "error": None, "skip_reason": ""}


def _rows_for(html_text: str, kpi_id: str) -> list[str]:
    """그 KPI 가 실린 **모든** 행. §1 매트릭스와 §2.5 추세 표 둘 다 검사한다 —
    한쪽만 고치면 다른 표가 같은 값을 계속 판정처럼 싣는다."""
    rows = [r for r in re.findall(r"<tr>.*?</tr>", html_text, re.S) if f">{kpi_id}<" in r]
    assert rows, f"{kpi_id} 행이 없다"
    return rows


def _render_two_runs(tmp_path):
    """직전 회차에는 S16 이 있었고, 이번 회차는 S1 만 돌린 상황."""
    from render_perf_report import render_html

    history_dir = tmp_path / "history"
    history_dir.mkdir()
    prev = _report("2026-09-11T00:00:00Z", [
        _scenario("S1", [_kpi("S1.1", measured=0.95, status="PASS")]),
        _scenario("S16", [_kpi("S16.4", measured=1.0, status="PASS")]),
    ])
    (history_dir / "perf_20260911000000Z_full_aaa.json").write_text(
        json.dumps(prev, ensure_ascii=False), encoding="utf-8")

    cur = _report("2026-09-12T00:00:00Z", [
        _scenario("S1", [
            _kpi("S1.1", measured=0.97, status="PASS"),
            _kpi("S1.9", measured=0.0, status="SKIP"),
        ]),
    ])
    out = tmp_path / "report.html"
    render_html(cur, out_path=out, history_dir=history_dir, mode="full")
    return out.read_text(encoding="utf-8")


def test_kpi_absent_from_this_run_is_shown_as_unmeasured(tmp_path):
    html_text = _render_two_runs(tmp_path)
    # 이번 회차 시나리오에 없으므로 §1 매트릭스에는 아예 안 실린다 — §2.5 추세 한 행뿐이다.
    rows = _rows_for(html_text, "S16.4")
    assert len(rows) == 1
    row = rows[0]

    assert "미측정" in row, "이번 회차에 안 돌린 KPI 는 값도 판정도 '미측정' 이다"
    assert ">PASS<" not in row, "지난 회차 판정을 '최근 판정' 으로 실으면 안 된다"
    assert ">0.0%<" not in row, "재지 않은 값을 0 으로 적으면 '통과율 0%' 로 읽힌다"
    # 왜 미측정인지는 남긴다 — 지난 판정이 무엇이었는지까지.
    assert "마지막 판정 PASS" in row


def test_skip_in_this_run_keeps_its_own_status_but_no_value(tmp_path):
    html_text = _render_two_runs(tmp_path)
    rows = _rows_for(html_text, "S1.9")
    assert len(rows) == 2, "§1 매트릭스와 §2.5 추세 양쪽에 실린다"

    for row in rows:
        assert ">SKIP<" in row, "이번 회차에 돌았지만 SKIP 이면 그 사실을 그대로 적는다"
        assert "미측정" in row, "SKIP 의 measured 0.0 은 자리표시다 — 값으로 적지 않는다"
        # 값 칸만 본다 — 합격선 칸은 `≥ 80.0%` 라 "0.0%" 를 포함한다.
        assert ">0.0%<" not in row


def test_measured_kpi_is_unaffected(tmp_path):
    """가드가 정상 측정까지 가리면 안 된다."""
    html_text = _render_two_runs(tmp_path)
    rows = _rows_for(html_text, "S1.1")
    assert len(rows) == 2

    for row in rows:
        assert ">PASS<" in row
        assert "97.0%" in row
        assert "미측정" not in row
