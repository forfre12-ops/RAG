"""그림자 자동확정 정책의 보수적 오프라인 시뮬레이터."""

from __future__ import annotations

from typing import Any, Iterable

from koipa.services.automation_report import GRADE_ORDER

_LOW_CONFIDENCE_GATE = "low-confidence"


def _candidate_for_policy(
    raw: dict[str, Any], *, threshold: float, min_margin: float,
) -> tuple[bool, bool, str | None]:
    """(잠정 자동확정, 전체 게이트 재실행 필요, 제외 사유)를 반환한다.

    low-confidence가 먼저 검수를 만들면 뒤쪽 게이트는 실행되지 않을 수 있다. 따라서
    낮은 threshold로 새로 열리는 건은 사람 정답이 맞더라도 실제 서빙 게이트 재실행 전에는
    배포 가능한 자동확정으로 취급하지 않는다.
    """
    assessment = raw.get("automation_assessment") or {}
    confidence = assessment.get("selected_confidence")
    margin = assessment.get("score_margin")
    if not isinstance(confidence, (int, float)):
        return False, False, "missing_confidence"
    if float(confidence) < threshold:
        return False, False, "below_threshold"
    if min_margin > 0 and (not isinstance(margin, (int, float)) or float(margin) < min_margin):
        return False, False, "below_margin"

    hits = {str(hit) for hit in assessment.get("review_gate_hits") or []}
    hard_hits = hits - {_LOW_CONFIDENCE_GATE}
    if hard_hits:
        return False, False, "known_hard_gate"

    initial_status = str(raw.get("initial_status") or "")
    opened_by_lowering = (
        initial_status != "staging"
        and assessment.get("causal_review_reason") == _LOW_CONFIDENCE_GATE
    )
    return True, opened_by_lowering, None


def simulate_policy(
    records: Iterable[dict[str, Any]],
    *,
    threshold: float,
    min_margin: float = 0.0,
    min_evaluated: int = 30,
) -> dict[str, Any]:
    """한 후보 정책의 성능 상한과 재실행 필요성을 계산한다.

    이 함수는 자동확정 결정을 변경하지 않는다. ``requires_full_gate_replay``가 0이 아니면
    새 threshold로 열린 문서에 대해 뒤쪽 안전 게이트가 관측되지 않았다는 뜻이므로, 결과가
    무음 미탐 0이어도 배포 권고를 내리지 않는다.
    """
    provisional: list[dict[str, Any]] = []
    excluded: dict[str, int] = {}
    replay_required = 0
    for raw in records:
        predicted = str(raw.get("predicted_label") or "")
        truth = str(raw.get("truth_label") or "")
        if predicted not in GRADE_ORDER or truth not in GRADE_ORDER:
            excluded["unsupported_grade"] = excluded.get("unsupported_grade", 0) + 1
            continue
        candidate, replay, reason = _candidate_for_policy(
            raw, threshold=threshold, min_margin=min_margin,
        )
        if not candidate:
            excluded[reason or "ineligible"] = excluded.get(reason or "ineligible", 0) + 1
            continue
        provisional.append(raw)
        replay_required += int(replay)

    correct = sum(
        str(row["predicted_label"]) == str(row["truth_label"])
        for row in provisional
    )
    silent = [
        row for row in provisional
        if row["truth_label"] in {"TS", "S1"}
        and GRADE_ORDER[str(row["predicted_label"])] > GRADE_ORDER[str(row["truth_label"])]
    ]
    n = len(provisional)
    return {
        "threshold": threshold,
        "min_score_margin": min_margin,
        "provisional_auto_confirmed": n,
        "provisional_precision": round(correct / n, 4) if n else None,
        "provisional_high_grade_silent_miss": len(silent),
        "requires_full_gate_replay": replay_required,
        "min_evaluated": min_evaluated,
        "sample_sufficient": n >= min_evaluated,
        "deployable": bool(
            n >= min_evaluated and not silent and replay_required == 0
        ),
        "excluded": dict(sorted(excluded.items())),
        "safety_note": (
            "requires_full_gate_replay가 0이 아닌 후보는 low-confidence 뒤에서 평가되지 않은 "
            "안전 게이트가 있을 수 있어 배포 불가다."
        ),
    }


def simulate_policy_grid(
    records: Iterable[dict[str, Any]],
    *,
    thresholds: Iterable[float],
    min_margin: float = 0.0,
    min_evaluated: int = 30,
) -> dict[str, Any]:
    frozen = list(records)
    simulations = [
        simulate_policy(
            frozen,
            threshold=float(threshold),
            min_margin=min_margin,
            min_evaluated=min_evaluated,
        )
        for threshold in thresholds
    ]
    deployable = [row for row in simulations if row["deployable"]]
    return {
        "schema_version": "automation-policy-simulation-v1",
        "simulations": simulations,
        "recommended": min(deployable, key=lambda row: row["threshold"]) if deployable else None,
        "policy_note": (
            "recommended가 null이면 관측 데이터만으로 안전하게 배포 가능한 완화 정책이 없다는 "
            "뜻이다. 전체 서빙 게이트 재실행 평가가 선행돼야 한다."
        ),
    }
