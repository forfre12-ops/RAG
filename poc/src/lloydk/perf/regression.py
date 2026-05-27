"""PSH 회귀 자동 비교 — 직전 회차 대비 KPI 악화 탐지.

설계:
- "악화"는 임계 통과 여부가 아니라 측정값의 상대 변화율로 판단
- compare="le": 값이 증가하면 악화 (latency, FNR 등)
- compare="ge": 값이 감소하면 악화 (recall, throughput 등)
- bool/count는 비교 대상 제외 (이산값은 회귀 개념 약함)
- SKIP/ERROR KPI는 비교 제외 (직전 또는 이번 회차)

산출:
- detect_regressions(current, previous, threshold_pct) → list[dict]
- 각 항목: {kpi_id, name, prev, curr, delta_pct, direction, severity}
"""

from __future__ import annotations

from typing import Any


def _is_numeric_kpi(kpi: dict[str, Any]) -> bool:
    unit = (kpi.get("unit") or "").lower()
    return unit not in ("bool", "count")


def _index_kpis(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for scen in report.get("scenarios", []) or []:
        for k in scen.get("kpis", []) or []:
            kid = k.get("kpi_id")
            if kid:
                out[kid] = k
    return out


def detect_regressions(
    current: dict[str, Any],
    previous: dict[str, Any] | None,
    *,
    threshold_pct: float = 20.0,
) -> list[dict[str, Any]]:
    """직전 회차 대비 threshold_pct 이상 악화된 KPI 목록.

    Args:
        current: 이번 회차 report dict
        previous: 직전 회차 report dict (None이면 빈 리스트 반환)
        threshold_pct: 악화 임계 (예: 20.0 → 20% 이상 악화 시 회귀로 판정)
    """
    if not previous:
        return []

    curr_map = _index_kpis(current)
    prev_map = _index_kpis(previous)

    regressions: list[dict[str, Any]] = []
    for kid, curr_kpi in curr_map.items():
        prev_kpi = prev_map.get(kid)
        if not prev_kpi:
            continue
        # 양쪽 모두 정상 측정된 경우만 비교
        if curr_kpi.get("status") not in ("PASS", "FAIL"):
            continue
        if prev_kpi.get("status") not in ("PASS", "FAIL"):
            continue
        if not _is_numeric_kpi(curr_kpi):
            continue

        prev_val = float(prev_kpi.get("measured", 0.0))
        curr_val = float(curr_kpi.get("measured", 0.0))
        if prev_val == 0.0:
            # 0 → N으로 변한 경우는 비율 계산 의미 없음
            continue

        delta_pct = ((curr_val - prev_val) / abs(prev_val)) * 100.0
        compare = curr_kpi.get("compare", "le")
        # 악화 방향: le면 증가가 악화, ge면 감소가 악화
        worsened = (compare in ("le", "lt") and delta_pct > 0) or (
            compare in ("ge", "gt") and delta_pct < 0
        )
        if not worsened:
            continue

        if abs(delta_pct) < threshold_pct:
            continue

        regressions.append(
            {
                "kpi_id": kid,
                "name": curr_kpi.get("name", ""),
                "unit": curr_kpi.get("unit", ""),
                "prev": prev_val,
                "curr": curr_val,
                "delta_pct": round(delta_pct, 2),
                "direction": "up" if delta_pct > 0 else "down",
                "compare": compare,
                "threshold_pct": threshold_pct,
            }
        )

    return regressions
