"""월별 RANGE 파티션 자동 롤오버 (#5) — PostgreSQL 전용.

⚠ 2026-09-05: MariaDB 는 파티션을 쓰지 않기로 했다. 실측 근거 — 223 실서버 30일치
감사로그가 71,161행·30MB(연 환산 약 87만행·360MB)로 파티션이 필요한 규모가 아니고,
파티션의 두 효용 중 보존기간 삭제는 애초에 구현돼 있지 않았으며(오래된 파티션을 떼는
코드 0건), 프루닝을 쓰는 질의는 감사 체인 조회 4곳뿐이다. 그 자리는 시간축 선두 인덱스
(idx_audit_occurred 등)와 보존기간 삭제(services/retention.py)가 대신 받는다.

아래 DDL 은 `CREATE TABLE ... PARTITION OF` 로 PostgreSQL 문법이다. dialect 가드가
없으면 MariaDB 에서 beat 가 매일 실패하고 롤오버 실패 게이지만 올라간다 — 고칠 것이
없는데 경보가 뜨는 상태가 된다. 그래서 PostgreSQL 이 아니면 no-op 으로 끝낸다.

baseline_init은 정적 월 파티션만 생성한다(audit_log는 2026-06, chunks/llm_usage는
2026-07까지). 그 이후 행은 모두 `*_default` 파티션에 쌓여 ① 파티션 프루닝이 무력화되고
② 나중에 정상 월 파티션을 attach할 때 default 검증으로 강한 락이 걸린다.

ensure_partitions가 향후 N개월치 월 파티션을 미리 만들어 둔다(beat
`ensure_partitions_tick`이 매일 호출 — `CREATE TABLE IF NOT EXISTS`라 멱등).

지연 케이스(이미 default에 해당 월 행이 쌓인 뒤): `CREATE ... PARTITION OF`가
default 행과 충돌해 실패할 수 있다. 이 경우 beat를 죽이지 않도록 savepoint로 격리해
경고만 남긴다(수동 detach/move 런북 필요). 정시 실행이면 default가 비어 충돌하지 않는다.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from koipa.db import session_scope

logger = logging.getLogger(__name__)

# (부모 테이블, RANGE 파티션 키 컬럼) — baseline_init의 PARTITION BY RANGE 정의와 일치.
# [2026-09-11] 표준 명명(migration 7b3e9d2a4f10). 자식 이름이 `부모_YYYY_MM` 이라 그 판이
# 기존 자식도 새 부모 이름으로 바꿔 두었다 — 여기만 바꾸면 이미 있는 월을 다시 만들려다 겹친다.
PARTITIONED_TABLES: list[tuple[str, str]] = [
    ("tad_cm_chnk_mng", "crt_dt"),
    ("tad_lm_llm_usqty_mng", "clot_dt"),
    ("tad_am_adt_log_mng", "ocrn_dt"),
]


def _month_ranges(today: dt.date, months_ahead: int):
    """이번 달 포함 향후 months_ahead 개월의 (start, next_month_start) 쌍."""
    y, m = today.year, today.month
    for _ in range(months_ahead + 1):
        start = dt.date(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        end = dt.date(ny, nm, 1)
        yield start, end
        y, m = ny, nm


def ensure_partitions(months_ahead: int = 3, *, today: dt.date | None = None) -> dict:
    """향후 months_ahead 개월 월 파티션을 보장한다(멱등).

    Returns: {"ensured": [...], "failed": [...]} — failed가 있으면 롤오버 지연(런북 필요).
    """
    today = today or dt.date.today()
    ensured: list[str] = []
    failed: list[str] = []

    try:
        with session_scope() as db:
            # [dialect 가드] PostgreSQL 전용 DDL 이다(위 docstring 참조). MariaDB 는
            # 파티션 미사용이라 실패가 아니라 '해당 없음' 이다 — failed 를 늘리지 않는다.
            dialect = db.get_bind().dialect.name
            if dialect != "postgresql":
                logger.debug(
                    "ensure_partitions skipped: %s 는 파티션을 쓰지 않는다"
                    "(2026-09-05 결정 · 시간축 인덱스+보존기간 삭제로 대체)", dialect,
                )
                return {"ensured": [], "failed": [], "status": "not_applicable"}
            for table, _key in PARTITIONED_TABLES:
                for start, end in _month_ranges(today, months_ahead):
                    part = f"{table}_{start:%Y_%m}"
                    ddl = (
                        f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF {table} "
                        f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
                    )
                    try:
                        # savepoint로 격리 — 한 건 실패가 트랜잭션 전체를 오염시키지 않게.
                        with db.begin_nested():
                            db.execute(text(ddl))
                        ensured.append(part)
                    except SQLAlchemyError as exc:  # noqa: PERF203
                        failed.append(part)
                        logger.warning(
                            "partition ensure failed for %s — %s에 해당 월 행이 이미 "
                            "쌓였을 수 있음(롤오버 지연). 수동 detach/move 런북 필요: %s",
                            part, f"{table}_default", exc,
                        )
    except SQLAlchemyError as exc:
        logger.warning("ensure_partitions skipped (db unavailable): %s", exc)
        return {"ensured": [], "failed": [], "status": "db_unavailable"}

    return {"ensured": ensured, "failed": failed, "status": "ok"}
