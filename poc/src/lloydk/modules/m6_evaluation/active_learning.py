"""Active Learning — corrections 누적 → 재학습 큐 push.

설계 (doc/04 §6.3 — 과거 v_active_learning_status 뷰 로직을 코드로 단일화):
- unconsumed_corrections ≥ urgent_threshold 또는
  pending_underclass ≥ urgent_underclass_threshold → URGENT_RETRAIN
- unconsumed_corrections ≥ recommended_threshold → RETRAIN_RECOMMENDED
- 그 외 → OK

URGENT는 보안 미탐(underclass)이 누적된 상태라 즉시 학습 트리거가 권장됨.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import Correction

logger = logging.getLogger(__name__)

DEFAULT_URGENT_UNDERCLASS = 10
DEFAULT_RECOMMENDED = 50


@dataclass
class ActiveLearningStatus:
    unconsumed_total: int = 0
    pending_underclass: int = 0
    pending_overclass: int = 0
    pending_lateral: int = 0
    last_correction_at: Optional[str] = None
    retrain_status: str = "OK"  # OK / RETRAIN_RECOMMENDED / URGENT_RETRAIN
    reason: str = ""
    urgent_underclass_threshold: int = DEFAULT_URGENT_UNDERCLASS
    recommended_threshold: int = DEFAULT_RECOMMENDED
    sample_ids: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "unconsumed_total": self.unconsumed_total,
            "pending_underclass": self.pending_underclass,
            "pending_overclass": self.pending_overclass,
            "pending_lateral": self.pending_lateral,
            "last_correction_at": self.last_correction_at,
            "retrain_status": self.retrain_status,
            "reason": self.reason,
            "thresholds": {
                "urgent_underclass": self.urgent_underclass_threshold,
                "recommended": self.recommended_threshold,
            },
        }


def evaluate_retraining_need(
    *,
    urgent_underclass_threshold: int = DEFAULT_URGENT_UNDERCLASS,
    recommended_threshold: int = DEFAULT_RECOMMENDED,
) -> ActiveLearningStatus:
    """현재 unconsumed corrections 상태로 재학습 권장 여부 판단.

    DB 미가용 시 status='OK', reason='db_unavailable'.
    """
    try:
        with session_scope() as db:
            # direction별 counts (consumed_in_run is NULL만)
            rows = db.execute(
                select(Correction.direction, func.count())
                .where(Correction.consumed_in_run.is_(None))
                .group_by(Correction.direction)
            ).all()
            counts = {direction: count for direction, count in rows}
            last_at = db.execute(
                select(func.max(Correction.corrected_at)).where(
                    Correction.consumed_in_run.is_(None)
                )
            ).scalar_one_or_none()
            unconsumed_ids = list(
                db.execute(
                    select(Correction.correction_id)
                    .where(Correction.consumed_in_run.is_(None))
                    .order_by(Correction.corrected_at)
                ).scalars()
            )
    except SQLAlchemyError as exc:
        logger.debug("active learning eval skipped: %s", exc)
        return ActiveLearningStatus(reason=f"db_unavailable:{type(exc).__name__}")

    underclass = counts.get("underclass", 0)
    overclass = counts.get("overclass", 0)
    lateral = counts.get("lateral", 0)
    confirm = counts.get("confirm", 0)
    total = underclass + overclass + lateral + confirm

    status = "OK"
    reason = ""
    if underclass >= urgent_underclass_threshold:
        status = "URGENT_RETRAIN"
        reason = f"pending_underclass={underclass} >= urgent={urgent_underclass_threshold} (보안 미탐 누적)"
    elif total >= recommended_threshold:
        status = "RETRAIN_RECOMMENDED"
        reason = f"unconsumed_total={total} >= recommended={recommended_threshold}"
    else:
        reason = f"under urgent/recommended thresholds (underclass={underclass}, total={total})"

    return ActiveLearningStatus(
        unconsumed_total=total,
        pending_underclass=underclass,
        pending_overclass=overclass,
        pending_lateral=lateral,
        last_correction_at=last_at.isoformat() if last_at else None,
        retrain_status=status,
        reason=reason,
        urgent_underclass_threshold=urgent_underclass_threshold,
        recommended_threshold=recommended_threshold,
        sample_ids=[int(i) for i in unconsumed_ids],
    )


def consume_corrections_for_run(
    run_id: uuid.UUID,
    *,
    correction_ids: list[int] | None = None,
) -> int:
    """corrections.consumed_in_run = run_id로 마크. 학습 작업 시작 시 호출.

    correction_ids 미지정 시: 현재 모든 unconsumed corrections을 소비.
    반환: 마크된 행 수.
    """
    try:
        with session_scope() as db:
            if correction_ids is None:
                ids = list(
                    db.execute(
                        select(Correction.correction_id).where(Correction.consumed_in_run.is_(None))
                    ).scalars()
                )
            else:
                ids = list(correction_ids)
            if not ids:
                return 0
            now = dt.datetime.now(dt.timezone.utc)
            count = 0
            for cid in ids:
                corr = db.get(Correction, cid)
                if corr is None or corr.consumed_in_run is not None:
                    continue
                corr.consumed_in_run = run_id
                corr.consumed_at = now
                count += 1
            return count
    except SQLAlchemyError as exc:
        logger.debug("consume corrections skipped: %s", exc)
        return 0
