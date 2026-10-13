"""빈 DB 를 head 까지 올린 결과가 ORM(models.py)과 같은가 — `alembic check` 를 시험으로 지킨다.

왜 이 시험이 있는가(2026-09-22). CI 의 `alembic check` 단계(scripts/ci_alembic_drift_check.sh)가
25건(NULL 허용 19 · 기본값 6)으로 붉었는데, 그 단계는 CI 안에서만 돌아서 로컬에서 시험을 돌리는
사람에게는 보이지 않았다. 원인은 둘이었다.

    UUID 기본키 6곳   ef294c56(MariaDB 이식)이 선언에서 server_default=gen_random_uuid() 를 지웠고
                      DB 는 그대로여서 어긋났다. MariaDB 를 버린 뒤에도 되돌리지 않았다.
    NULL 허용 19칼럼   선언은 Mapped[X] 표기만으로 NOT NULL 이 되는데 DB(baseline) 는 NULL 을 허용했다.

그래서 **모델을 고치고 마이그레이션을 빠뜨리는 실수**를 이 시험이 바로 잡는다. 이 시험은 실행 중인
DB 를 건드리지 않는다 — 같은 서버에 임시 데이터베이스를 새로 만들어 head 까지 올리고, 끝나면 지운다.

시험이 조용히 항상 통과하는 것을 막으려고 **반대 방향도** 본다: 올린 DB 에 ORM 에 없는 칼럼을 하나
얹어 `alembic check` 가 그것을 실제로 잡는지 확인한 뒤 되돌린다.

전제: fullstack 마커(Postgres 필요). Postgres 가 없으면 conftest 가 건너뛴다. 임시 DB 를 만들
권한이 없으면 사유를 밝히고 건너뛴다. `CREATE EXTENSION`(pgvector·pg_trgm)이 마이그레이션에 있어
운영과 같은 이미지(pgvector/pgvector:pg16)에 붙어야 한다.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

pytestmark = pytest.mark.fullstack

_POC_ROOT = Path(__file__).resolve().parents[1]  # alembic.ini 가 있는 자리
_NO_DRIFT = "No new upgrade operations detected"
# alembic 이 op 마다 남기는 INFO 한 줄(Detected NOT NULL … · … detected server default …).
# 실패 메시지에는 이 줄만 골라 싣는다 — 같은 목록이 ERROR·FAILED 줄에 두 번 더 찍혀 길다.
_OP_LINE = re.compile(r"\[alembic\.autogenerate\.[^\]]+\] .*[Dd]etected")


def _alembic(db_url: str, *args: str) -> tuple[int, str]:
    env = {**os.environ, "DATABASE_URL": db_url, "PYTHONIOENCODING": "utf-8", "TESTING": "1"}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=_POC_ROOT, env=env, capture_output=True, timeout=300,
    )
    out = proc.stdout.decode("utf-8", "replace") + proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out


def _op_lines(out: str) -> list[str]:
    # 기본값 줄에는 "Dialect impl <… object at 0x…>" 가 붙는데 주소가 실행마다 달라 뗀다.
    lines = (ln.split("] ", 1)[-1].strip() for ln in out.splitlines() if _OP_LINE.search(ln))
    return sorted({re.sub(r"^Dialect impl <[^>]*> ", "", ln) for ln in lines})


@pytest.fixture(scope="module")
def head_db_url():
    """임시 데이터베이스를 만들어 head 까지 올린 접속 주소를 준다. 끝나면 지운다."""
    from koipa.config import settings  # noqa: PLC0415

    base = make_url(settings.database_url)
    if not base.drivername.startswith("postgresql"):
        pytest.skip(f"PostgreSQL 이 아니다: {base.drivername}")
    name = f"koipa_drift_{uuid.uuid4().hex[:10]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except SQLAlchemyError as exc:
        admin.dispose()
        pytest.skip(f"임시 데이터베이스를 만들 수 없다({type(exc).__name__}) — 권한 또는 접속 확인")
    url = base.set(database=name).render_as_string(hide_password=False)
    try:
        rc, out = _alembic(url, "upgrade", "head")
        assert rc == 0, f"빈 DB 를 head 까지 올리지 못했다(rc={rc}):\n{out[-3000:]}"
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def test_head_schema_has_no_orm_drift(head_db_url: str):
    """마이그레이션을 끝까지 올린 스키마 == models.py. 다르면 어느 칼럼이 다른지 그대로 적는다."""
    rc, out = _alembic(head_db_url, "check")
    ops = _op_lines(out)
    assert rc == 0 and _NO_DRIFT in out, (
        f"DB 와 ORM 이 {len(ops)}건 어긋난다(alembic check rc={rc}). models.py 를 고쳤다면 "
        "마이그레이션이 빠졌는지, 마이그레이션을 더했다면 models.py 를 같이 고쳤는지 본다:\n  "
        + "\n  ".join(ops or [out[-1500:]])
    )


def test_drift_check_actually_detects_a_mismatch(head_db_url: str):
    """검사가 항상 통과하는 것이 아님을 보인다 — DB 에 ORM 에 없는 칼럼을 얹으면 잡아야 한다.

    NULL 허용·기본값 같은 정책 칼럼을 일부러 어긋나게 하지 않는다 — 나중에 그 정책이 바뀌면
    이 시험이 같이 깨진다. 칼럼 하나를 더하는 것은 어느 정책에서도 어긋남이다.
    """
    engine = create_engine(head_db_url, isolation_level="AUTOCOMMIT")
    try:
        # 앞의 시험이 실패해 이미 어긋남이 있어도 이 시험은 스스로 서도록, 시작 상태와 견준다.
        baseline = _op_lines(_alembic(head_db_url, "check")[1])
        with engine.connect() as conn:
            conn.execute(text("ALTER TABLE tad_dm_doc_mng ADD COLUMN drift_probe integer"))
        try:
            rc, out = _alembic(head_db_url, "check")
            added = [ln for ln in _op_lines(out) if ln not in baseline]
            assert rc != 0 and _NO_DRIFT not in out, "ORM 에 없는 칼럼을 얹었는데 alembic check 가 통과했다"
            assert any("tad_dm_doc_mng.drift_probe" in ln for ln in added), _op_lines(out)
        finally:
            with engine.connect() as conn:
                conn.execute(text("ALTER TABLE tad_dm_doc_mng DROP COLUMN IF EXISTS drift_probe"))
        after = _op_lines(_alembic(head_db_url, "check")[1])
        assert after == baseline, "되돌린 뒤 어긋남 목록이 시작 상태와 다르다:\n" + "\n".join(after)
    finally:
        engine.dispose()
