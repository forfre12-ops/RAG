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


def forked_pairs(probes: "list[tuple[int, str]]") -> "list[tuple[int, int]]":
    """같은 앞행을 주장하는 쌍을 찾는다 — **이것이 분기(fork)의 정의다.**

    잠금이 열리면 두 스레드가 같은 prev 를 읽고 각자 이어 붙여 체인이 갈라진다. 그러므로
    prev 중복이 곧 분기의 증거다. '앞뒤로 이어지는가'로 보지 않는 이유는 남의 감사 행이
    탐침 사이에 끼면 다음 탐침의 prev 가 **정상적으로** 그 남의 행을 가리키기 때문이다.

    DB 없이도 검증할 수 있게 따로 뺐다 — 실제 시험과 아래 자기검사가 같은 코드를 쓴다.
    두 곳이 갈리면 자기검사가 통과해도 실제 시험은 다른 것을 볼 수 있다.
    """
    from koipa.services.audit_chain import parse_chained_hash

    seen: dict[str, int] = {}
    forked: list[tuple[int, int]] = []
    for aid, packed in probes:
        prev16, _ = parse_chained_hash(packed or "")
        if prev16 in seen:
            forked.append((seen[prev16], aid))
        else:
            seen[prev16] = aid
    return forked


def test_fork_detector_actually_detects_a_fork():
    """검사기가 헛돌지 않는가 — 분기를 만들어 주면 잡아야 한다.

    이 자기검사가 없으면 위 동시성 시험이 **아무것도 안 보면서 통과**할 수 있다.
    빈 목록을 항상 돌려주는 검사기는 늘 초록불이다.
    """
    a16, b16 = "a" * 16, "b" * 16
    linear = [(1, f"{a16}:{'1' * 32}"), (2, f"{b16}:{'2' * 32}")]
    assert forked_pairs(linear) == [], "정상 체인을 분기로 봤다"

    same_prev = [(1, f"{a16}:{'1' * 32}"), (2, f"{a16}:{'2' * 32}")]
    assert forked_pairs(same_prev) == [(1, 2)], "두 행이 같은 앞행을 주장하는데 못 잡았다"


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

    ⚠ 그 '남의 사정'을 audit_id 구간으로만 막았더니 2026-09-10 에 다시 났다 — 전체 스위트
      3회 중 1회. 구간 안에 남의 행이 하나 끼자 `len(rows) == n` 이 깨지면서
      "삽입 17/16 — 일부가 유실됐다"고 말했다. 초과인데 유실이라고 말한 것이라, 다음 사람이
      없는 유실 버그를 쫓게 되는 메시지였다. 그래서 지금은 이렇게 본다.

          개수   **마커로** 센다 (남의 행이 껴도 흔들리지 않는다)
          분기   같은 앞행을 주장하는 탐침이 있는가 — 이것이 fork 의 정의다
          연결   남의 행이 하나도 안 낀 실행에서만 한 줄 연결까지 본다
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
                select(AuditLog.audit_id, AuditLog.action, AuditLog.payload_hash)
                .where(AuditLog.audit_id > start_id)
                .order_by(AuditLog.audit_id)
            ).all()

        # 개수는 **마커로** 센다. 감사 체인은 전역 단일 체인이라 이 구간에 남의 행이
        # 섞일 수 있는데, 종전에는 구간 전체를 세고 `len(rows) == n` 을 걸었다. 그래서
        # 외부 행이 하나만 껴도 "삽입 17/16 — 일부가 유실됐다"가 떴다 — 초과인데 유실이라고
        # 말하는 메시지라, 보는 사람이 없는 유실 버그를 쫓게 된다.
        # (실측 2026-09-10: 전체 스위트 3회 중 1회 이 문장으로 실패했고 단독 실행은 통과했다.)
        probes = [(aid, packed) for aid, action, packed in rows if action == marker]
        assert len(probes) == n, (
            f"탐침 {len(probes)}/{n} — 동시 삽입 일부가 유실됐다"
            f" (구간 전체 {len(rows)}건 중 남의 행 {len(rows) - len(probes)}건)"
        )

        # 분기(fork)의 정의: **두 행이 같은 앞행을 주장하는 것.** 잠금이 열리면 두 스레드가
        # 같은 prev 를 읽고 각자 이어 붙여 체인이 갈라진다. prev 중복이 곧 그 증거다.
        #
        # 왜 '앞뒤로 이어지는가'가 아니라 '중복이 없는가'를 보는가. 남의 행이 탐침 사이에
        # 끼면 다음 탐침의 prev 는 **정상적으로** 그 남의 행을 가리킨다. 탐침만 이어 보면
        # 그것을 끊김으로 오판한다. 중복 검사는 남의 행이 껴도 흔들리지 않는다.
        forked = forked_pairs(probes)
        assert not forked, (
            f"동시 삽입에서 감사 체인이 분기했다 — 잠금이 직렬화하지 못한다. "
            f"같은 앞행을 주장한 쌍 {len(forked)}/{n}: {forked[:3]}"
        )

        # 남의 행이 하나도 안 낀 실행에서는 더 강하게 본다 — 구간이 **한 줄로** 이어지는가.
        # 위 중복 검사가 못 잡는 어긋남(예: prev 가 엉뚱한 과거 행을 가리키는 경우)이 여기서
        # 걸린다. 남의 행이 끼면 이 검사는 성립하지 않으므로 건너뛴다.
        if len(rows) == n:
            broken = []
            prev_full = None
            for aid, packed in probes:
                prev16, full32 = parse_chained_hash(packed or "")
                # 첫 행의 prev 는 이 구간 밖(기존 데이터)을 가리키므로 검사하지 않는다.
                if prev_full is not None and prev16 != prev_full[:16]:
                    broken.append(aid)
                prev_full = full32
            assert not broken, (
                f"동시 삽입 구간이 한 줄로 이어지지 않는다 — 잠금 순서와 기록 순서가 어긋난다. "
                f"끊긴 지점 {len(broken)}/{n}: audit_id={broken[:5]}"
            )
    finally:
        with session_scope() as db:
            db.execute(delete(AuditLog).where(AuditLog.action == marker))


@pytest.mark.fullstack
def test_foreign_audit_rows_do_not_break_the_concurrency_probe():
    """탐침 구간에 **남의 감사 행**이 끼어도 위 시험이 실패하지 않는가.

    이것을 시험으로 못 박는 이유(2026-09-10). 위 시험은 전체 스위트에서 간헐적으로
    실패했다 — 단독 실행은 늘 통과했다. 원인은 코드가 아니라 검사 방식이었고, 고친 뒤에도
    **그 상황을 재현해 확인하지 않으면 같은 일이 또 난다.** 간헐 실패는 재현해서 못 박기
    전까지 고쳤다고 말할 수 없다.

    여기서는 남의 행을 실제로 끼워 넣고, 개수 검사와 분기 검사가 그것을 견디는지 본다.
    """
    from sqlalchemy import delete, func, select

    from koipa.db import session_scope
    from koipa.db.models import AuditLog
    from koipa.repositories.audit_repo import AuditRepo
    from koipa.services.audit_chain import build_chained_hash_locked

    marker = "lock-foreign-probe"
    foreign = "lock-foreign-outsider"
    n = 4

    with session_scope() as db:
        start_id = db.execute(select(func.max(AuditLog.audit_id))).scalar() or 0

    def record(action: str, i: int) -> None:
        with session_scope() as db:
            ph = build_chained_hash_locked(db, f"{action}-{i}")
            AuditRepo(db).record(
                action=action, actor_id=f"a{i}", actor_role="admin",
                request_id=f"r-{action}-{i}", payload_hash=ph,
            )

    try:
        # 탐침 사이에 남의 행을 끼워 넣는다 — 전체 스위트에서 실제로 일어나던 모양이다.
        for i in range(n):
            record(marker, i)
            if i == 1:
                record(foreign, i)

        with session_scope() as db:
            rows = db.execute(
                select(AuditLog.audit_id, AuditLog.action, AuditLog.payload_hash)
                .where(AuditLog.audit_id > start_id)
                .order_by(AuditLog.audit_id)
            ).all()

        probes = [(aid, packed) for aid, action, packed in rows if action == marker]
        assert len(rows) > len(probes), "남의 행이 안 끼었다 — 이 시험이 상황을 못 만들었다"
        assert len(probes) == n, (
            f"마커로 세는데도 개수가 틀렸다: {len(probes)}/{n} (구간 전체 {len(rows)})"
        )

        assert forked_pairs(probes) == [], (
            "남의 행이 낀 것만으로 분기로 판정했다 — 검사가 여전히 격리에 취약하다"
        )
    finally:
        with session_scope() as db:
            db.execute(delete(AuditLog).where(AuditLog.action.in_((marker, foreign))))
