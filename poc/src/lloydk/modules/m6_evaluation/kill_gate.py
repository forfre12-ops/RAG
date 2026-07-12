"""Kill-gate — 죽음의 나선/품질붕괴 조건 **모니터** (비파괴: 경보만).

설계 결정(2026-06-29): 발동 시 **자동 중단하지 않는다** — 메트릭(lloydk_kill_gate_tripped)
+ 로그로 경보만 내고 운영자가 판단·조치한다(오작동으로 운영을 멈추는 위험 회피). 자동
중단(자동확정/재학습 정지)은 추후 opt-in.

발동 조건(하나라도 충족 시 tripped):
  ① TS/S1 미탐 — 고등급 underclass 교정(사람이 비밀을 올린 것) ≥ floor (기본 1=any).
     이 사업 최악의 오류라 절대 floor.
  ② 검수 과부하(fatigue) — 일 교정 ≥ capacity × multiplier (capacity=0이면 비활성).
     러버스탬프(자동화 편향)→죽음의 나선 트리거 신호.
  ③ overturn율 — 라벨변경 교정 비율(non-confirm/total) ≥ max (min 표본 미만이면 0).

신호는 corrections_rebuild._correction_admissible와 동일 기준(사람검수+finalized)으로 집계 —
'학습에 실제 들어갈' 교정 기준과 일치. 순수 결정함수(evaluate_kill_gate)는 DB 불요·단위테스트.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import time
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

_HIGH_GRADES = ("TS", "S1")
_OVERTURN_MIN_SAMPLES = 5  # overturn율은 표본이 이 미만이면 신뢰불가 → 0(미발동)

# [번들 E] 마지막 kill-gate 점검 결과 캐시 — 안전브레이크가 핫패스(classify)에서 DB 없이
# tripped 여부를 읽게 한다. run_kill_gate_check()가 주기 refresh에서 갱신. None=아직 미점검.
_LAST_RESULT: "KillGateResult | None" = None
_LAST_CHECK_TS: float = 0.0   # 마지막 점검 시각(monotonic) — celery 워커 지연갱신 신선도 판정용
_STALE_SEC: float = 60.0      # 캐시 신선도 임계 — 초과 시 지연 갱신(feature ON 일 때만 DB 조회)


def should_suppress_autoconfirm(grade: str | None) -> bool:
    """[번들 E] kill-gate 안전브레이크 — 이 등급의 자동확정을 억제해야 하는가.

    settings.kill_gate_suppress_autoconfirm=True 이고 마지막 점검이 tripped 이며 등급이
    고등급(TS/S1)일 때만 True. 캐시된 상태만 읽어 핫패스 DB 부하 없음. 미점검(None)·미발동·
    플래그 OFF 면 False(동작 보존). 조건이 풀리면(다음 refresh에서 tripped=False) 자동 해제.
    """
    from lloydk.config import settings  # noqa: PLC0415
    if not getattr(settings, "kill_gate_suppress_autoconfirm", False):
        return False
    # [#8kg] celery 워커 등 gauge-refresh 루프가 닿지 않는 프로세스는 _LAST_RESULT 가 영원히 None →
    # 안전브레이크 무음 미작동(fail-open). feature 가 켜진 경우에 한해, 캐시가 없거나 stale 이면 지연
    # 갱신(핫패스 부하는 feature ON + 60s 주기로 제한). 갱신 실패는 보수적으로 마지막 값 유지.
    global _LAST_CHECK_TS
    # 테스트(pytest)는 _LAST_RESULT 를 직접 세팅해 안전브레이크를 검증하므로 지연갱신을 건너뛴다
    # (테스트가 세팅한 값 보존). 운영 celery 워커(TESTING/pytest 아님)에서만 지연갱신이 돈다.
    if not os.environ.get("PYTEST_CURRENT_TEST"):
        now = time.monotonic()
        if _LAST_RESULT is None or (now - _LAST_CHECK_TS) > _STALE_SEC:
            try:
                run_kill_gate_check()
            except Exception:  # noqa: BLE001
                _LAST_CHECK_TS = now  # 실패해도 재시도 폭주 방지(다음 창까지 대기)
    if _LAST_RESULT is None or not _LAST_RESULT.tripped:
        return False
    return str(grade) in _HIGH_GRADES


@dataclass
class KillGateResult:
    tripped: bool = False
    reasons: tuple[str, ...] = ()
    signals: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"tripped": self.tripped, "reasons": list(self.reasons), "signals": self.signals}


def evaluate_kill_gate(
    *,
    high_grade_miss_count: int,
    daily_correction_count: int,
    overturn_rate: float,
    daily_capacity: int,
    fatigue_multiplier: float = 1.5,
    high_grade_miss_floor: int = 1,
    overturn_rate_max: float = 0.30,
) -> KillGateResult:
    """순수 결정 함수 — 신호 + 임계 → tripped/reasons. (DB 불요, 단위테스트 가능.)

    floor/capacity/max를 0(또는 음수)으로 두면 해당 조건 비활성.
    """
    reasons: list[str] = []
    if high_grade_miss_floor > 0 and high_grade_miss_count >= high_grade_miss_floor:
        reasons.append(f"high_grade_miss={high_grade_miss_count}>={high_grade_miss_floor}")
    if daily_capacity > 0 and daily_correction_count >= daily_capacity * fatigue_multiplier:
        reasons.append(
            f"fatigue daily={daily_correction_count}>=capacity{daily_capacity}x{fatigue_multiplier}"
        )
    if overturn_rate_max > 0 and overturn_rate >= overturn_rate_max:
        reasons.append(f"overturn_rate={overturn_rate:.2f}>={overturn_rate_max:.2f}")
    signals = {
        "high_grade_miss_count": high_grade_miss_count,
        "daily_correction_count": daily_correction_count,
        "overturn_rate": round(overturn_rate, 4),
    }
    return KillGateResult(tripped=bool(reasons), reasons=tuple(reasons), signals=signals)


def run_kill_gate_check() -> dict:
    """신호를 DB에서 best-effort 집계 → evaluate_kill_gate → 게이지 set + 로그(경보만).

    DB 미가용/오류 → 신호 0 + 미발동(안전). 자동 중단 없음(비파괴).
    """
    from lloydk.config import settings  # noqa: PLC0415
    from lloydk.db import session_scope  # noqa: PLC0415
    from lloydk.db.models import (  # noqa: PLC0415
        Classification,
        ClassificationLevel,
        Correction,
    )
    from lloydk.modules.m6_evaluation.corrections_rebuild import (  # noqa: PLC0415
        _correction_admissible,
    )

    high_grade_miss = 0
    daily = 0
    nonconfirm = 0
    total = 0
    try:
        with session_scope() as db:
            high_ids = {
                lv.level_id
                for lv in db.execute(select(ClassificationLevel)).scalars()
                if lv.level_code in _HIGH_GRADES
            }
            since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=24)
            # unconsumed(현 backlog) ∪ 최근 24h 교정만 적재 — 전수 로드 회피.
            rows = db.execute(
                select(
                    Correction.corrected_by,
                    Correction.direction,
                    Correction.corrected_level_id,
                    Correction.corrected_at,
                    Correction.consumed_in_run,
                    Classification.status,
                )
                .join(
                    Classification,
                    Correction.classification_id == Classification.classification_id,
                )
                .where(
                    or_(
                        Correction.consumed_in_run.is_(None),
                        Correction.corrected_at >= since,
                    )
                )
            ).all()
            for cb, direction, lvl, cat, consumed, status in rows:
                if not _correction_admissible(cb, status):
                    continue
                if cat is not None and cat >= since:
                    daily += 1
                if consumed is None:  # 미반영 backlog 기준 overturn/high-grade-miss
                    total += 1
                    if direction != "confirm":
                        nonconfirm += 1
                    if direction == "underclass" and lvl in high_ids:
                        high_grade_miss += 1
    except SQLAlchemyError as exc:
        logger.debug("kill-gate gather skipped: %s", exc)

    overturn_rate = (nonconfirm / total) if total >= _OVERTURN_MIN_SAMPLES else 0.0
    res = evaluate_kill_gate(
        high_grade_miss_count=high_grade_miss,
        daily_correction_count=daily,
        overturn_rate=overturn_rate,
        daily_capacity=int(getattr(settings, "kill_gate_daily_review_capacity", 0)),
        fatigue_multiplier=float(getattr(settings, "kill_gate_fatigue_multiplier", 1.5)),
        high_grade_miss_floor=int(getattr(settings, "kill_gate_high_grade_miss_floor", 1)),
        overturn_rate_max=float(getattr(settings, "kill_gate_overturn_rate_max", 0.30)),
    )
    # [번들 E] 캐시 갱신 — 안전브레이크(should_suppress_autoconfirm)가 읽는 tripped 상태.
    # _LAST_CHECK_TS 도 갱신해 gauge-loop/지연갱신 양쪽이 신선도를 공유(중복 refresh 방지).
    global _LAST_RESULT, _LAST_CHECK_TS
    _LAST_RESULT = res
    _LAST_CHECK_TS = time.monotonic()
    try:
        from lloydk.api.prom_metrics import KILL_GATE_TRIPPED  # noqa: PLC0415

        KILL_GATE_TRIPPED.set(1.0 if res.tripped else 0.0)
    except Exception:  # noqa: BLE001
        pass
    if res.tripped:
        logger.warning("kill-gate TRIPPED (경보·비파괴): %s", list(res.reasons))
    return res.to_dict()
