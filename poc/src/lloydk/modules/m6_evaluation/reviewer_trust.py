"""검수자 신뢰도 추적 — C-cons 고도화 (doc/36).

검수자별로 "그의 교정이 해당 분류의 **최종 합의(최신 교정)**와 일치한 비율"을 신뢰도로 산출.
자주 번복되는 검수자는 신뢰도↓, 결정이 유지되는 검수자는 신뢰도↑. 2인검토 게이트(C-cons 1단계)와
함께 검수 거버넌스를 이룬다 — 고신뢰 시니어의 사인오프에 가중을 두거나, 저신뢰 검수자 교정을
추가 검토로 라우팅하는 정책의 근거 데이터.

최신 교정 판정은 corrected_at desc + correction_id desc(트랜잭션 동시각 대비, corrections_rebuild와
동일 기준). 순수 DB 집계 — 읽기 전용, DB 미가용 시 빈 결과로 graceful degrade.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from lloydk.db import session_scope
from lloydk.db.models import Correction

logger = logging.getLogger(__name__)


def compute_reviewer_reliability(*, min_corrections: int = 1) -> list[dict]:
    """전체 검수자 신뢰도 목록 (신뢰도 desc, 동률 시 교정수 desc).

    각 항목: reviewer_id · total(교정수) · agreed(최신합의 일치수) · reliability(=agreed/total) ·
    overturned(번복수=total-agreed) · last_correction_at.
    min_corrections 미만 검수자는 제외(표본 부족 노이즈 차단).
    """
    try:
        with session_scope() as db:
            corrs = list(
                db.execute(
                    select(Correction).order_by(
                        Correction.corrected_at.desc(), Correction.correction_id.desc()
                    )
                ).scalars()
            )
    except SQLAlchemyError as exc:
        logger.debug("reviewer reliability skipped: %s", exc)
        return []

    if not corrs:
        return []

    # 분류별 최신 교정 등급 (정렬 desc라 첫 등장이 최신)
    latest_level: dict = {}
    for c in corrs:
        latest_level.setdefault(c.classification_id, c.corrected_level_id)

    # 검수자별 집계
    agg: dict = {}  # reviewer -> [total, agreed, last_at]
    for c in corrs:
        a = agg.setdefault(c.corrected_by, [0, 0, None])
        a[0] += 1
        if c.corrected_level_id == latest_level.get(c.classification_id):
            a[1] += 1
        if a[2] is None or (c.corrected_at and c.corrected_at > a[2]):
            a[2] = c.corrected_at

    out: list[dict] = []
    for rv, (total, agreed, last_at) in agg.items():
        if total < min_corrections:
            continue
        out.append({
            "reviewer_id": rv,
            "total": total,
            "agreed": agreed,
            "overturned": total - agreed,
            "reliability": round(agreed / total, 4) if total else 0.0,
            "last_correction_at": last_at.isoformat() if last_at else None,
        })
    out.sort(key=lambda r: (-r["reliability"], -r["total"]))
    return out


def reviewer_reliability(reviewer_id: str) -> dict | None:
    """단일 검수자 신뢰도. 해당 검수자 교정이 없으면 None."""
    for r in compute_reviewer_reliability(min_corrections=1):
        if r["reviewer_id"] == reviewer_id:
            return r
    return None
