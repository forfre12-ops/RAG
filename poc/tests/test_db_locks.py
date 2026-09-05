"""전역 직렬화 잠금 — 두 dialect 에서 배타적이고 트랜잭션 끝에 풀린다.

이 잠금이 없으면 감사 체인이 분기(fork)하고 모델 활성이 서로 덮어써진다. 종전에는
PostgreSQL 에서만 잠겼고 MariaDB 에서는 **조용히 열려** 있었다(2026-09-05).

라이브 DB 가 있어야 의미 있는 시험이라 fullstack 으로 표시한다. DB 없이 도는 것은
'미지원 dialect 는 False 를 준다'는 계약뿐이다.
"""

from __future__ import annotations

import threading
import time

import pytest

from koipa.db.locks import AUDIT_CHAIN, MODEL_ACTIVATION, advisory_xact_lock


def test_lock_names_are_distinct():
    """두 임계영역이 같은 잠금을 쓰면 감사 기록이 모델 등록을 기다리게 된다."""
    assert AUDIT_CHAIN != MODEL_ACTIVATION


def test_unsupported_dialect_returns_false_without_raising():
    """SQLite 등 미지원 dialect 는 막지 않고 False 만 준다 — 호출부가 진행한다."""

    class _Dialect:
        name = "sqlite"

    class _Bind:
        dialect = _Dialect()

    class _Session:
        def get_bind(self):  # noqa: ANN201
            return _Bind()

    assert advisory_xact_lock(_Session(), AUDIT_CHAIN) is False


def test_dialect_probe_failure_returns_false():
    """bind 를 못 얻어도 예외를 올리지 않는다 — 잠금 실패가 본 작업을 막으면 안 된다."""

    class _Broken:
        def get_bind(self):  # noqa: ANN201
            raise RuntimeError("no bind")

    assert advisory_xact_lock(_Broken(), AUDIT_CHAIN) is False


@pytest.mark.fullstack
def test_lock_is_exclusive_and_released_at_transaction_end():
    """두 커넥션이 동시에 못 들어가고, 트랜잭션이 끝나면 바로 풀린다.

    실측(2026-09-05) — PostgreSQL·MariaDB 양쪽에서 B 가 A 의 트랜잭션 종료를 기다렸고
    재획득이 0.01초 안에 됐다.
    """
    from koipa.db import session_scope

    events: list[tuple[float, str]] = []
    guard = threading.Lock()
    t0 = time.time()

    def rec(msg: str) -> None:
        with guard:
            events.append((time.time() - t0, msg))

    def worker(tag: str, hold_s: float) -> None:
        with session_scope() as db:
            got = advisory_xact_lock(db, AUDIT_CHAIN)
            rec(f"{tag}:acquired={got}")
            time.sleep(hold_s)
            rec(f"{tag}:end")

    a = threading.Thread(target=worker, args=("A", 1.5))
    a.start()
    time.sleep(0.4)
    b = threading.Thread(target=worker, args=("B", 0.05))
    b.start()
    a.join()
    b.join()

    names = [m for _t, m in events]
    assert "A:acquired=True" in names, events
    assert "B:acquired=True" in names, events
    assert names.index("A:end") < names.index("B:acquired=True"), (
        f"B 가 A 의 임계영역에 함께 들어갔다 — 직렬화가 안 된다: {events}"
    )

    # 해제 — A·B 가 끝난 뒤 즉시 다시 잡혀야 한다.
    start = time.time()
    with session_scope() as db:
        assert advisory_xact_lock(db, AUDIT_CHAIN) is True
    assert time.time() - start < 1.0, "트랜잭션이 끝났는데 잠금이 안 풀렸다"


@pytest.mark.fullstack
def test_audit_chain_does_not_fork_under_concurrency():
    """동시 감사 삽입에서 체인이 분기하지 않는다 — 잠금이 실제로 일하는지의 최종 판정.

    이 시험을 만들며 실측으로 결함 셋을 찾았다(2026-09-05):
      ① 잠금 행을 런타임에 INSERT 하면 첫 사용에서 임계영역이 통째로 열린다
         (동시 요청이 서로의 미커밋 행을 못 봐 FOR UPDATE 가 대상을 못 찾는다).
      ② 이미 있는 행에 매번 INSERT 를 걸면 S락 위에 X락을 요구해 **교착**이 나고,
         교착이 '잠금 미획득'으로 흡수돼 역시 임계영역이 열린다.
      ③ 잠금이 제대로 걸려도 prev 를 occurred_at 순으로 고르면 PostgreSQL 에서 분기한다
         (now() 가 트랜잭션 첫 명령 시작 시각이라 잠금 획득 순서와 어긋난다).
    셋 다 24건 동시 삽입에서만 드러났다 — 순차 시험은 전부 통과했다.

    ⚠ 전역 verify_chain 을 쓰지 않는다. 감사 체인은 전역 단일 체인이라 같은 DB 를 쓰는
      다른 시험이 남긴 행이 섞이고, 그중 build_chained_hash_locked 를 거치지 않은 것이
      있으면 이 시험이 남의 사정으로 실패한다(전체 스위트에서 실제로 그랬다).
      **내가 넣은 구간만** audit_id 로 잘라 링크를 확인한다.
    """
    from sqlalchemy import delete, func, select

    from koipa.db import session_scope
    from koipa.db.models import AuditLog
    from koipa.repositories.audit_repo import AuditRepo
    from koipa.services.audit_chain import build_chained_hash_locked, parse_chained_hash

    n = 16
    marker = "lock-concurrency-probe"

    with session_scope() as db:
        start_id = db.execute(select(func.max(AuditLog.audit_id))).scalar() or 0

    def insert(i: int) -> None:
        with session_scope() as db:
            ph = build_chained_hash_locked(db, f"body-{i}")
            AuditRepo(db).record(
                action=marker, actor_id=f"a{i}", actor_role="admin",
                request_id=f"r{i}", payload_hash=ph,
            )

    threads = [threading.Thread(target=insert, args=(i,)) for i in range(n)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with session_scope() as db:
            rows = db.execute(
                select(AuditLog.audit_id, AuditLog.payload_hash)
                .where(AuditLog.audit_id > start_id)
                .order_by(AuditLog.audit_id)
            ).all()

        assert len(rows) == n, f"삽입 {len(rows)}/{n} — 일부가 유실됐다"
        broken = []
        prev_full = None
        for aid, packed in rows:
            prev16, full32 = parse_chained_hash(packed or "")
            # 첫 행의 prev 는 이 구간 밖(기존 데이터)을 가리키므로 검사하지 않는다.
            if prev_full is not None and prev16 != prev_full[:16]:
                broken.append(aid)
            prev_full = full32
        assert not broken, (
            f"동시 삽입에서 감사 체인이 분기했다 — 잠금이 직렬화하지 못한다. "
            f"끊긴 지점 {len(broken)}/{n}: audit_id={broken[:5]}"
        )
    finally:
        with session_scope() as db:
            db.execute(delete(AuditLog).where(AuditLog.action == marker))
