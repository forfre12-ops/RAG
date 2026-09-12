"""전역 직렬화 잠금 — 전용 표의 행 잠금(SELECT ... FOR UPDATE).

[2026-09-09] MariaDB 를 버리고 PostgreSQL 로 되돌렸지만 **이 설계는 그대로 둔다.**
행 잠금으로 바꾼 이유가 이식성만은 아니었다 — 아래 실측한 교착(24건 동시 삽입에서
감사 체인 분기)을 이 방식이 실제로 고쳤다. pg_advisory_xact_lock 으로 되돌리는 것은
그 수정을 되돌리는 것이라 하지 않는다.

왜 필요한가(2026-09-05). 두 임계영역이 `pg_advisory_xact_lock` 으로만 잠겨 있었고,
호출부가 `dialect == "postgresql"` 일 때만 실행하거나 예외를 흡수했다. MariaDB 로 옮기면
**둘 다 조용히 꺼진다**:

    services/audit_chain.build_chained_hash_locked
        prev 읽기 + INSERT 를 한 임계영역으로 묶어, 동시 요청이 같은 prev 를 읽고
        체인이 분기(fork)하는 것을 막는다. 풀리면 감사 체인 무결성 검증이 깨진다.
    services/training_service (3곳)
        모델 활성 전환 직렬화. 풀리면 두 재학습이 동시에 활성을 다투거나 서로의
        활성을 덮어쓴다.

■ 왜 GET_LOCK 이 아니라 행 잠금인가 — 실측으로 갈렸다
MariaDB 의 `GET_LOCK(name, timeout)` 은 **커넥션** 단위다. PostgreSQL 의
`pg_advisory_xact_lock` 은 **트랜잭션** 단위여서 commit/rollback 에 자동으로 풀린다.
호출부는 후자를 전제로 쓰여 있다(잠그기만 하고 푸는 코드가 없다).

그 차이를 이벤트(`after_transaction_end`)로 메우려 했으나 실측에서 두 번 깨졌다:
  ① 리스너 안에서 `event.remove()` 를 부르면 SQLAlchemy 가 순회 중인 deque 를 건드려
     "deque mutated during iteration" 으로 **커밋 자체가 실패**한다.
  ② 그것을 고쳐도 `Session.commit()` 이 내부적으로 커넥션을 반납한 뒤라
     `session.connection()` 이 **다른 커넥션**을 집어 온다. RELEASE_LOCK 이 소유자가
     아닌 커넥션에서 돌아 0 을 반환하고, 잠금은 원래 커넥션에 남는다
     (IS_USED_LOCK 이 커넥션 id 를 계속 돌려줬다).

행 잠금은 이 문제가 없다. `SELECT ... FOR UPDATE` 는 PostgreSQL·MariaDB 양쪽에서
**트랜잭션** 단위이고 commit/rollback 에 자동으로 풀린다 — 호출부가 이미 전제하던
바로 그 수명이다. 풀 코드도, 이벤트도, dialect 분기도 필요 없다.

■ 실패했을 때
잠금을 못 얻으면 **False 를 돌려주고 진행한다.** 잠금 획득 실패로 감사 기록이나 모델
등록 자체를 막으면 그것이 더 큰 사고다. 다만 조용히 넘기지 않고 경고를 남긴다.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

logger = logging.getLogger(__name__)

# 논리 잠금 이름들 — 같은 이름 = 같은 임계영역.
AUDIT_CHAIN = "audit_chain"
MODEL_ACTIVATION = "model_activation"

# 잠금 전용 표(models.py 의 AdvisoryLock). 행은 마이그레이션이 심는다.
# 표 이름을 f-string 변수로 감싸지 않고 그대로 적는다 — 감사 도구·grep 이 원시 SQL 의
# 표 사용을 정적으로 찾을 수 있어야 한다(scripts/audit_unused.py 가 이 문자열을 센다).
# [2026-09-11] 표준 명명(7b3e9d2a4f10): 옛 tb_advisory_locks.name → tad_sy_lck_mng.lck_nm.
# ⚠ 이 줄은 옛 이름을 일부러 남긴다 — 대응을 적은 문장이라 양쪽이 다 새 이름이면 뜻이 사라진다.
_SELECT_FOR_UPDATE = text("SELECT lck_nm FROM tad_sy_lck_mng WHERE lck_nm = :n FOR UPDATE")
_INSERT_PG = text("INSERT INTO tad_sy_lck_mng (lck_nm) VALUES (:n) ON CONFLICT DO NOTHING")


def _ensure_row(db, name: str, dialect: str) -> None:
    """잠금 행이 없으면 만든다(멱등).

    별도 트랜잭션(savepoint)에서 넣는다 — 두 요청이 동시에 처음 잠그면 한쪽이
    중복키로 실패하는데, 그 실패가 바깥 트랜잭션을 오염시키면 안 된다.
    """
    # [2026-09-09] MariaDB 분기(_INSERT_MARIA)를 뺐다 — PostgreSQL 로 되돌리면서
    # 닿는 경로가 없어졌다. dialect 인자는 호출부 계약 유지를 위해 남긴다.
    stmt = _INSERT_PG
    try:
        with db.begin_nested():
            db.execute(stmt, {"n": name})
    except SQLAlchemyError as exc:
        # 다른 요청이 먼저 넣었다 — 정상이다. 아래 SELECT FOR UPDATE 가 그 행을 잡는다.
        logger.debug("advisory lock 행 생성 건너뜀(이미 있음): name=%s err=%s", name, exc)


def advisory_xact_lock(db, name: str) -> bool:
    """트랜잭션이 끝날 때 자동으로 풀리는 전역 잠금을 얻는다. 얻었으면 True.

    Args:
        db: **열려 있는 트랜잭션 안의** Session. 잠금은 이 트랜잭션과 수명을 같이한다.
        name: 논리 잠금 이름(AUDIT_CHAIN·MODEL_ACTIVATION).

    Returns:
        True  잠금을 얻었다. 트랜잭션이 끝나면 자동으로 풀린다.
        False 못 얻었다(표 없음·DB 오류 등). 호출부는 직렬화 **없이** 진행할지 정한다 —
              이 함수는 막지 않는다.
    """
    try:
        dialect = db.get_bind().dialect.name
    except Exception as exc:  # noqa: BLE001
        logger.debug("advisory lock: dialect 확인 실패 — 잠금 없이 진행: %s", exc)
        return False

    if dialect != "postgresql":
        # SQLite 등 — 단일 프로세스 테스트 경로다. 직렬화 대상이 아니다.
        logger.debug("advisory lock 미지원 dialect(%s) — 잠금 없이 진행", dialect)
        return False

    try:
        # ⚠ 잠글 행을 먼저 **읽는다.** 종전에는 매번 INSERT(ON CONFLICT/IGNORE)로 행을
        #   보장한 뒤 FOR UPDATE 했는데, 이미 있는 행에 대한 INSERT 가 공유 잠금(S)을
        #   잡고 그 위에 FOR UPDATE 가 배타 잠금(X)을 요구해 **동시 요청끼리 교착**이
        #   났다. 교착은 SQLAlchemyError 로 잡혀 '잠금 미획득'이 되고, 그러면 임계영역이
        #   통째로 열린다 — 24건 동시 삽입에서 감사 체인이 실제로 분기했다(2026-09-05 실측).
        #   행은 마이그레이션(c5d6e7f8a9b0)이 심으므로 평상시엔
        #   이 SELECT 한 번으로 끝난다.
        row = db.execute(_SELECT_FOR_UPDATE, {"n": name}).first()
        if row is not None:
            return True
        # 여기까지 오면 행이 없다 — 판을 안 올린 DB 이거나 누가 지웠다. 한 번만 만들고
        # 다시 잡아 본다(첫 사용 경합은 savepoint 안에서 흡수된다).
        _ensure_row(db, name, dialect)
        row = db.execute(_SELECT_FOR_UPDATE, {"n": name}).first()
        if row is None:
            logger.warning(
                "advisory lock 행 없음 — 직렬화 없이 진행: name=%s "
                "(alembic 판 c5d6e7f8a9b0 이 적용됐는지 확인할 것)", name,
            )
            return False
        return True
    except SQLAlchemyError as exc:
        logger.warning("advisory lock 실패 — 직렬화 없이 진행: name=%s err=%s", name, exc)
        return False
