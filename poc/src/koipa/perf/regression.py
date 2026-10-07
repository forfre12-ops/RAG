"""PSH 회귀 자동 비교 — 직전 회차 대비 KPI 악화 탐지 + 시계열 분석.

설계:
- "악화"는 임계 통과 여부가 아니라 측정값의 상대 변화율로 판단
- compare="le": 값이 증가하면 악화 (latency, FNR 등)
- compare="ge": 값이 감소하면 악화 (recall, throughput 등)
- bool/count는 비교 대상 제외 (이산값은 회귀 개념 약함)
- SKIP/ERROR KPI는 비교 제외 (직전 또는 이번 회차)

산출:
- detect_regressions(current, previous, threshold_pct) → list[dict]
- detect_regression_trend(history, threshold_pct) → list[dict]
  최근 N회차 시계열로 회귀 빈도·연속성 패턴 분석
- summarize_skip_reasons(report) → dict
  SKIP 사유별 분류 (trained_model/pg/es/llm/측정 없음 등)
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


def detect_regression_trend(
    history: list[dict[str, Any]],
    *,
    threshold_pct: float = 30.0,
) -> list[dict[str, Any]]:
    """최근 N회차 시계열에서 회귀 빈도·연속성 분석.

    Args:
        history: 회차 N개 (최신순). [0]이 가장 최근.
        threshold_pct: 회귀 임계

    Returns:
        list of dict:
          - kpi_id, name, unit, compare
          - n_regressions: 인접 회차 비교에서 회귀로 잡힌 횟수 (N-1 중)
          - consecutive: 가장 최근 회차부터 연속으로 회귀인 횟수
          - latest_delta_pct: 가장 최근 회차의 변화율 (있을 때)
          - trend: "improving" | "degrading" | "flat" | "noisy"
    """
    if len(history) < 2:
        return []

    # 인접 회차 쌍별 detect_regressions 누적
    by_kpi: dict[str, dict[str, Any]] = {}
    pair_regs_per_step: list[set[str]] = []  # [step]에서 회귀였던 kpi_id 집합

    for i in range(len(history) - 1):
        curr = history[i]
        prev = history[i + 1]
        regs = detect_regressions(curr, prev, threshold_pct=threshold_pct)
        reg_ids = {r["kpi_id"] for r in regs}
        pair_regs_per_step.append(reg_ids)
        for r in regs:
            kid = r["kpi_id"]
            slot = by_kpi.setdefault(
                kid,
                {
                    "kpi_id": kid,
                    "name": r["name"],
                    "unit": r["unit"],
                    "compare": r["compare"],
                    "n_regressions": 0,
                    "consecutive": 0,
                    "latest_delta_pct": None,
                    "trend": "flat",
                },
            )
            slot["n_regressions"] += 1
            if slot["latest_delta_pct"] is None:
                slot["latest_delta_pct"] = r["delta_pct"]

    # 가장 최근 회차부터 연속 회귀 카운트
    for kid, slot in by_kpi.items():
        consec = 0
        for step_regs in pair_regs_per_step:
            if kid in step_regs:
                consec += 1
            else:
                break
        slot["consecutive"] = consec

    # trend 라벨링
    total_steps = len(pair_regs_per_step)
    for slot in by_kpi.values():
        n = slot["n_regressions"]
        if slot["consecutive"] >= 2:
            slot["trend"] = "degrading"
        elif n >= total_steps * 0.6:
            slot["trend"] = "noisy"
        elif n == 0:
            slot["trend"] = "improving"
        else:
            slot["trend"] = "flat"

    return sorted(
        by_kpi.values(),
        key=lambda x: (-x["consecutive"], -x["n_regressions"]),
    )


def summarize_skip_reasons(report: dict[str, Any]) -> dict[str, Any]:
    """SKIP KPI를 사유별로 분류.

    분류 카테고리:
    - trained_model: 학습 모델 미존재
    - pg / es / redis / minio / llm / gpu: 외부 서비스 미가용
    - no_measurement: 측정값 자체가 적재되지 않음 (시나리오 함수가 건너뜀)
    - other: 기타

    Returns:
        {
          "total_skip": int,
          "by_reason": {"trained_model": 3, "pg": 6, ...},
          "by_scenario": {"S1": 2, "S5": 1, ...},
        }
    """
    by_reason: dict[str, int] = {}
    by_scenario: dict[str, int] = {}
    total = 0

    for scen in report.get("scenarios", []) or []:
        scen_id = scen.get("scenario", "")
        for k in scen.get("kpis", []) or []:
            if k.get("status") != "SKIP":
                continue
            total += 1
            reason = (k.get("skip_reason") or "").lower()
            # 분류
            if "trained_model" in reason:
                cat = "trained_model"
            elif "no measurement" in reason or "no_measurement" in reason:
                cat = "no_measurement"
            elif "requires pg" in reason or "missing: pg" in reason or "missing:pg" in reason:
                cat = "pg"
            elif "requires es" in reason or "missing: es" in reason or "missing:es" in reason:
                cat = "es"
            elif "redis" in reason:
                cat = "redis"
            elif "minio" in reason:
                cat = "minio"
            elif "llm" in reason:
                cat = "llm"
            elif "gpu" in reason:
                cat = "gpu"
            elif reason == "":
                cat = "no_reason"
            else:
                cat = "other"
            by_reason[cat] = by_reason.get(cat, 0) + 1
            if scen_id:
                by_scenario[scen_id] = by_scenario.get(scen_id, 0) + 1

    return {
        "total_skip": total,
        "by_reason": by_reason,
        "by_scenario": by_scenario,
    }
