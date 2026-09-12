"""사람 확정 결과로 그림자 자동확정 정책을 평가하는 순수 집계."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Iterable


GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
_CONFIDENCE_BINS = (0.0, 0.50, 0.60, 0.65, 0.70, 0.80, 0.90, 1.01)
_MARGIN_BINS = (0.0, 0.05, 0.10, 0.20, 0.40, 1.01)


def _bin_label(value: float, edges: tuple[float, ...]) -> str:
    for left, right in zip(edges, edges[1:]):
        if left <= value < right:
            return f"{left:.2f}–{right if right < 1 else 1.0:.2f}"
    return "unknown"


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(rows)
    correct = sum(row["predicted_label"] == row["truth_label"] for row in rows)
    auto = [row for row in rows if row["initial_status"] == "staging"]
    auto_correct = sum(row["predicted_label"] == row["truth_label"] for row in auto)
    silent_miss = [
        row for row in auto
        if row["truth_label"] in {"TS", "S1"}
        and GRADE_ORDER[row["predicted_label"]] > GRADE_ORDER[row["truth_label"]]
    ]
    return {
        "reviewed": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else None,
        "initial_auto_confirmed": len(auto),
        "auto_confirm_rate": round(len(auto) / total, 4) if total else None,
        "auto_confirm_correct": auto_correct,
        "auto_confirm_precision": round(auto_correct / len(auto), 4) if auto else None,
        "high_grade_silent_miss": len(silent_miss),
    }


def _group_summary(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        groups[str(value) if value is not None else "unknown"].append(row)
    return {name: _summary(group) for name, group in sorted(groups.items())}


def build_automation_report(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """최종 사람 검수된 records만으로 자동확정 위험도를 집계한다.

    record 필수값은 ``predicted_label``, ``truth_label``, ``initial_status``,
    ``automation_assessment``다. TS/S1/S2/S3 밖의 등급 또는 불완전한 그림자 관측치는
    억지로 계산하지 않고 제외 수로 드러낸다.
    """
    accepted: list[dict[str, Any]] = []
    excluded = Counter()
    for raw in records:
        assessment = raw.get("automation_assessment") or {}
        predicted = str(raw.get("predicted_label") or "")
        truth = str(raw.get("truth_label") or "")
        initial_status = str(raw.get("initial_status") or "")
        confidence = assessment.get("selected_confidence")
        if not assessment:
            excluded["missing_assessment"] += 1
            continue
        if predicted not in GRADE_ORDER or truth not in GRADE_ORDER:
            excluded["unsupported_grade"] += 1
            continue
        if initial_status not in {"staging", "needs_review", "needs_second_review"}:
            excluded["missing_initial_status"] += 1
            continue
        if not isinstance(confidence, (int, float)):
            excluded["missing_confidence"] += 1
            continue
        margin = assessment.get("score_margin")
        row = {
            "model_version": str(raw.get("model_version") or "unknown"),
            "predicted_label": predicted,
            "truth_label": truth,
            "initial_status": initial_status,
            "confidence_bin": _bin_label(float(confidence), _CONFIDENCE_BINS),
            "margin_bin": (
                _bin_label(float(margin), _MARGIN_BINS)
                if isinstance(margin, (int, float)) else "unknown"
            ),
            "causal_review_reason": assessment.get("causal_review_reason"),
        }
        accepted.append(row)

    review_reason_counts = Counter(
        row["causal_review_reason"] or "auto_confirmed"
        for row in accepted
    )
    return {
        "schema_version": "automation-report-v1",
        "summary": _summary(accepted),
        "by_model_version": _group_summary(accepted, "model_version"),
        "by_predicted_label": _group_summary(accepted, "predicted_label"),
        "by_confidence_bin": _group_summary(accepted, "confidence_bin"),
        "by_score_margin_bin": _group_summary(accepted, "margin_bin"),
        "initial_review_reason_counts": dict(
            sorted(review_reason_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "included_records": len(accepted),
        "excluded_records": dict(sorted(excluded.items())),
        "policy_note": (
            "이 리포트는 사람 확정 완료 건만 사용한다. high_grade_silent_miss가 0이 아닌 "
            "정책은 자동확정 기준 완화 후보가 아니다."
        ),
    }


def load_reviewed_records_from_db(
    model_version: str | None = None, limit: int = 0,
) -> list[dict[str, Any]]:
    """확정 완료된 DB 행을 리포트/정책 시뮬레이터 공용 형식으로 읽는다.

    동일 등급 confirm은 예측값이 사람 확정값이고, correction이 있으면 가장 최근 정정
    등급이 최종 정답이다. 아직 2차 검수가 끝나지 않은 행은 의도적으로 제외한다.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from koipa.db import session_scope  # noqa: PLC0415
    from koipa.db.models import Classification, ClassificationLevel, Correction  # noqa: PLC0415

    with session_scope() as db:
        levels = {
            level_id: code
            for level_id, code in db.execute(
                select(ClassificationLevel.level_id, ClassificationLevel.level_code)
            ).all()
        }
        stmt = (
            select(Classification)
            .where(
                Classification.automation_assessment.is_not(None),
                Classification.status.in_(("confirmed", "corrected")),
            )
            .order_by(Classification.classified_at.desc())
        )
        if model_version:
            stmt = stmt.where(Classification.model_version == model_version)
        if limit:
            stmt = stmt.limit(limit)
        classifications = list(db.execute(stmt).scalars())
        ids = [row.classification_id for row in classifications]
        corrections = list(
            db.execute(
                select(Correction)
                .where(Correction.classification_id.in_(ids))
                .order_by(
                    Correction.classification_id,
                    Correction.corrected_at.desc(),
                    Correction.correction_id.desc(),
                )
            ).scalars()
        ) if ids else []
        latest_correction: dict[Any, Correction] = {}
        for correction in corrections:
            latest_correction.setdefault(correction.classification_id, correction)

        return [
            {
                "model_version": row.model_version,
                "predicted_label": levels.get(row.predicted_level_id),
                "truth_label": levels.get(
                    correction.corrected_level_id if (correction := latest_correction.get(row.classification_id))
                    else row.predicted_level_id
                ),
                "initial_status": row.initial_status,
                "automation_assessment": row.automation_assessment,
                "truth_source": "latest_correction" if correction else "confirmed_matching_prediction",
            }
            for row in classifications
        ]
