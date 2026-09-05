"""운영 로그 보존기간 삭제 — 오래된 감사로그·LLM 사용량을 기간으로 지운다.

왜 필요한가(2026-09-05). 두 표는 무한히 자라고 있었고, 지우는 코드가 어디에도 없었다.
PostgreSQL 은 월별 RANGE 파티션을 만들어 두었지만 **오래된 파티션을 떼는 코드가 0건**
이라 파티션이 늘기만 했다. MariaDB 로 옮기면서 파티션을 쓰지 않기로 했으므로(실측 근거는
services/partitions.py 머리말), 그 자리를 이 배치가 받는다.

실측(223 실서버 2026-08-06~09-05, 30일):
    tb_audit_log   71,161행 · 30MB   → 연 환산 약 87만행 · 360MB
    tb_llm_usage      214행
    구성: dashboard 76.5% · healthz 12.2% · 업무 행위(classify·documents·golden 등) 9% 미만

즉 부피의 대부분은 감시 폴링이 남긴 것이다. 그래서 기본 보존기간을 길게 잡아도(감사 24개월)
표가 감당 못 할 크기가 되지 않는다.

⚠ 감사로그는 컴플라이언스 기록이다(NFR-SEC-01). 기본값은 **삭제하지 않음**(enabled=False)
으로 두고, 보존기간을 운영자가 명시적으로 정한 뒤에만 켠다. 잘못 켜서 증빙이 사라지는 것이
표가 커지는 것보다 나쁘다.

⚠ 감사 체인 무결성과의 관계. verify_chain 은 `prev16` 해시 체인을 이어 읽는다. 앞쪽을
잘라내면 남은 첫 행의 prev 가 가리키는 행이 없어져 **체인이 끊긴 것으로 보인다.** 그래서
삭제는 항상 **가장 오래된 쪽부터 앞으로 연속해서** 지운다 — 중간을 뚫지 않는다. 월 단위로
끊어 여러 틱에 나눠 지워도 남는 것은 언제나 뒤쪽 연속 구간이라 체인이 끊기지 않는다.
검증은 `since` 를 보존 경계 이후로 주어 돌린다. 이 규율을 깨면 무결성 경보가 상시로 뜬다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope

logger = logging.getLogger(__name__)

# (표, 시간 칼럼, 보존기간 설정 키) — 시간축 선두 인덱스가 있어야 DELETE 가 스캔을 피한다.
#   tb_audit_log  idx_audit_occurred (occurred_at, audit_id)
#   tb_llm_usage  idx_lu_called      (called_at)
RETAINED_TABLES: list[tuple[str, str, str]] = [
    ("tb_audit_log", "occurred_at", "retention_audit_log_days"),
    ("tb_llm_usage", "called_at", "retention_llm_usage_days"),
]


def _month_slices(start: dt.datetime, cutoff: dt.datetime):
    """start 부터 cutoff 까지를 월 경계로 끊어 [(from, to), ...] 로 준다.

    한 방 DELETE 로 87만 행을 지우면 잠금을 오래 잡아 운영 질의를 막는다. LIMIT 절은
    dialect 마다 문법이 달라(PostgreSQL 은 DELETE ... LIMIT 미지원) 이식이 안 되므로,
    **시간 창을 잘라** 나눠 지운다. 시간축 선두 인덱스를 그대로 타는 방식이다.
    """
    cur = dt.datetime(start.year, start.month, 1, tzinfo=start.tzinfo)
    while cur < cutoff:
        ny, nm = (cur.year + 1, 1) if cur.month == 12 else (cur.year, cur.month + 1)
        nxt = dt.datetime(ny, nm, 1, tzinfo=cur.tzinfo)
        yield cur, min(nxt, cutoff)
        cur = nxt


def purge_expired(
    *,
    max_slices: int = 120,
    today: dt.datetime | None = None,
    dry_run: bool = False,
) -> dict:
    """보존기간이 지난 행을 월 단위로 끊어 삭제한다.

    max_slices 로 한 틱의 상한을 둔다 — 처음 켤 때 몇 년치가 한꺼번에 걸려도 한 번에
    다 지우지 않고 다음 틱으로 넘긴다(상한에 걸리면 truncated 에 표를 싣는다).

    Returns:
        {"deleted": {표: 건수}, "cutoff": {표: ISO}, "truncated": [상한에 걸린 표],
         "status": "ok" | "disabled" | "db_unavailable"}
    """
    from koipa.config import settings  # noqa: PLC0415

    if not getattr(settings, "retention_enabled", False):
        return {"deleted": {}, "cutoff": {}, "truncated": [], "status": "disabled"}

    now = today or dt.datetime.now(dt.timezone.utc)
    deleted: dict[str, int] = {}
    cutoffs: dict[str, str] = {}
    truncated: list[str] = []

    try:
        with session_scope() as db:
            for table, col, key in RETAINED_TABLES:
                days = int(getattr(settings, key, 0) or 0)
                if days <= 0:
                    continue  # 0 이하 = 그 표는 보존 무제한(삭제 안 함)
                cutoff = now - dt.timedelta(days=days)
                cutoffs[table] = cutoff.isoformat()

                oldest = db.execute(
                    text(f"SELECT min({col}) FROM {table}")  # noqa: S608 - 표·칼럼은 상수표
                ).scalar()
                if oldest is None:
                    deleted[table] = 0
                    continue
                if oldest.tzinfo is None:
                    oldest = oldest.replace(tzinfo=dt.timezone.utc)
                if oldest >= cutoff:
                    deleted[table] = 0
                    continue

                if dry_run:
                    deleted[table] = int(
                        db.execute(
                            text(f"SELECT count(*) FROM {table} WHERE {col} < :c"),  # noqa: S608
                            {"c": cutoff},
                        ).scalar()
                        or 0
                    )
                    continue

                total = 0
                for i, (lo, hi) in enumerate(_month_slices(oldest, cutoff)):
                    if i >= max_slices:
                        truncated.append(table)
                        break
                    res = db.execute(
                        text(f"DELETE FROM {table} WHERE {col} >= :lo AND {col} < :hi"),  # noqa: S608
                        {"lo": lo, "hi": hi},
                    )
                    total += int(res.rowcount or 0)
                deleted[table] = total
    except SQLAlchemyError as exc:
        logger.warning("purge_expired skipped (db unavailable): %s", exc)
        return {"deleted": {}, "cutoff": {}, "truncated": [], "status": "db_unavailable"}

    if deleted:
        logger.info(
            "retention purge: %s (cutoff=%s, dry_run=%s)", deleted, cutoffs, dry_run
        )
    return {
        "deleted": deleted,
        "cutoff": cutoffs,
        "truncated": truncated,
        "status": "ok",
    }
