"""검수자 신뢰도 추적 — C-cons 고도화 (doc/36).

검수자별로 "그의 교정이 해당 분류의 **동료 합의(peer consensus)**와 일치한 비율"을 신뢰도로 산출.
자주 번복되는 검수자는 신뢰도↓, 결정이 유지되는 검수자는 신뢰도↑. 2인검토 게이트(C-cons 1단계)와
함께 검수 거버넌스를 이룬다 — 고신뢰 시니어의 사인오프에 가중을 두거나, 저신뢰 검수자 교정을
추가 검토로 라우팅하는 정책의 근거 데이터.

[#11 recency 편향 제거 — self-reference 차단]
과거 구현은 "분류의 최신 교정"을 그 자체로 합의로 삼고, 그 최신 보정자를 자동으로 합의에
"동의"로 카운트했다. 결과적으로 *마지막에 손댄* 보정자의 신뢰도가 무조건 상승하고,
min_corrections=1이면 단일 보정자가 1.0이 되어, 정확성이 아닌 recency를 보상해 게이밍이 가능했다.
이 점수는 confirm_service 쿼럼 게이트(high_grade_review_min_reliability)로 전파되므로 거버넌스 위험.

개선: 합의를 "본인 기여"가 아니라 **동료(다른 검수자)가 함께 도달한 최종 등급**으로 정의하고,
self-reference(자기 자신과의 자동 일치)는 카운트에서 제외한다. 즉 한 교정이 신뢰도 표본이 되려면
그 분류의 최종 등급을 **본인 이외의 검수자도** 선택했어야 한다(동료 corroboration). 동료 비교가
불가능한 교정(=자기만 손댄 분류)은 self-reference이므로 표본에서 제외한다.

표본 부족(동료 비교 가능 이력 < min_corrections) 검수자는 결과에서 제외 → 게이트는 부재를 신뢰
기본값(1.0)으로 취급하여 "신규/소수 보정자는 신뢰" 의도를 보존(게이밍 위험 없음).

정책 수치(min_corrections 기본값·임계)는 **발주처 협의 사항**이므로 기본 동작을 급변시키지 않는다.
이번 변경은 self-reference 편향 제거(메커니즘)에 한정하며, 기본값은 보존한다(min_corrections=1).
min_corrections의 의미는 "동료 비교가 가능한 최소 이력"으로 명확화했다.

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


def compute_reviewer_reliability(*, min_corrections: int = 1, db=None) -> list[dict]:
    """전체 검수자 신뢰도 목록 (신뢰도 desc, 동률 시 교정수 desc).

    각 항목: reviewer_id · total(전체 교정수) · agreed(동료 합의 일치수) · reliability(=agreed/comparable)
    · overturned(동료 비교에서 번복된 수=comparable-agreed) · last_correction_at.

    [#11] reliability는 **동료 비교 가능한 표본(comparable)** 기준으로 산정한다(self-reference 제외).
    total은 보고/정렬용 전체 교정수를 그대로 유지한다(시그니처·반환 키 불변 — 게이트 계약 보존).
    동료 비교 가능 이력이 min_corrections 미만인 검수자는 제외 → 게이트가 신뢰 기본값(1.0)으로 취급.
    min_corrections 기본값은 발주처 협의 전까지 보존(1.0 동작 유지).

    db 주입 시: 그 세션으로 조회(호출부의 미커밋 트랜잭션도 반영 — 2인검토 게이트가 방금 추가한
    교정을 보게). 미주입 시: 자체 session_scope(읽기 전용), DB 미가용 시 빈 목록.
    """
    if db is not None:
        return _reliability_from_session(db, min_corrections)
    try:
        with session_scope() as s:
            return _reliability_from_session(s, min_corrections)
    except SQLAlchemyError as exc:
        logger.debug("reviewer reliability skipped: %s", exc)
        return []


def _reliability_from_session(db, min_corrections: int) -> list[dict]:
    corrs = list(
        db.execute(
            select(Correction).order_by(
                Correction.corrected_at.desc(), Correction.correction_id.desc()
            )
        ).scalars()
    )
    if not corrs:
        return []

    # 분류별 최신 교정 등급 (정렬 desc라 첫 등장이 최신) — 잠정 합의 등급.
    latest_level: dict = {}
    for c in corrs:
        latest_level.setdefault(c.classification_id, c.corrected_level_id)

    # [#11] 분류별 "최종 합의 등급을 선택한 검수자 집합" — self-reference 차단의 핵심.
    # 한 검수자의 교정이 신뢰도 표본이 되려면, 그 분류의 최종 등급을 **본인 이외의 검수자도**
    # 선택했어야 한다(동료 corroboration). 본인만 그 등급을 선택했다면 self-reference이므로 제외.
    consensus_setters: dict = {}  # classification_id -> set(최종 등급을 선택한 검수자)
    for c in corrs:
        if c.corrected_level_id == latest_level.get(c.classification_id):
            consensus_setters.setdefault(c.classification_id, set()).add(c.corrected_by)

    # 검수자별 집계.
    #   total      : 전체 교정수(보고/정렬용 — 반환 키 불변)
    #   comparable : 동료 비교 가능한 교정수(신뢰도 분모, self-reference 제외)
    #   agreed     : 동료 합의와 일치한 교정수
    agg: dict = {}  # reviewer -> [total, comparable, agreed, last_at]
    for c in corrs:
        a = agg.setdefault(c.corrected_by, [0, 0, 0, None])
        a[0] += 1
        setters = consensus_setters.get(c.classification_id, set())
        # 동료(본인 외)가 함께 도달한 최종 등급이 있는 분류만 비교 표본으로 인정.
        peers = setters - {c.corrected_by}
        if peers:
            a[1] += 1  # comparable
            if c.corrected_level_id == latest_level.get(c.classification_id):
                a[2] += 1  # agreed (동료 합의와 일치)
        if a[3] is None or (c.corrected_at and c.corrected_at > a[3]):
            a[3] = c.corrected_at

    out: list[dict] = []
    for rv, (total, comparable, agreed, last_at) in agg.items():
        # [#11] min_corrections = "동료 비교 가능한 최소 이력". 표본 부족 검수자는 제외 →
        # 게이트가 부재를 신뢰 기본값(1.0)으로 취급(신규/소수 보정자 신뢰 의도 보존).
        if comparable < min_corrections:
            continue
        out.append({
            "reviewer_id": rv,
            "total": total,
            "agreed": agreed,
            "overturned": comparable - agreed,
            "reliability": round(agreed / comparable, 4) if comparable else 0.0,
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
