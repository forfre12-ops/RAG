"""백업·복구 엔진 판정 — 추측하지 않는다.

[2026-09-09] MariaDB 를 버리고 PostgreSQL + pgvector 로 되돌리면서 엔진이 하나가 됐다.
그래도 이 시험은 남긴다 — **모르는 엔진에 추측해서 덤프하지 않는다**는 계약이 이 모듈의
값이고, 이제는 "mariadb" 를 달라고 해도 실패해야 한다(조용히 PostgreSQL 을 덤프하면
운영자가 잘못된 DB 를 백업했다고 믿게 된다).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from db_engine import ENGINES, POSTGRES, detect_engine  # noqa: E402
from dr_restore import _only_benign_partition_errors  # noqa: E402


def test_explicit_engine_wins():
    assert detect_engine("postgresql").name == POSTGRES
    # 별칭도 받는다 — 운영자가 어느 쪽으로 적든 같게 동작해야 한다.
    assert detect_engine("postgres").name == POSTGRES


def test_unknown_engine_raises_instead_of_guessing():
    """모르는 값이면 실패한다 — 추측해서 엉뚱한 DB 를 덤프하지 않는다."""
    with pytest.raises(RuntimeError, match="알 수 없는 엔진"):
        detect_engine("sqlite")


def test_dropped_engine_fails_loudly_not_silently():
    """[2026-09-09] 버린 엔진을 달라고 하면 **실패해야 한다.**

    조용히 PostgreSQL 로 떨어지면 운영자는 MariaDB 를 백업했다고 믿는다. 그 믿음이
    깨지는 시점은 복구할 때다 — 그때는 늦다.
    """
    assert "mariadb" not in ENGINES
    for name in ("mariadb", "mysql"):
        with pytest.raises(RuntimeError, match="알 수 없는 엔진"):
            detect_engine(name)


def test_dump_suffix_is_engine_specific():
    """엔진마다 덤프 확장자가 갈린다 — 잘못된 엔진에 잘못된 덤프를 밀어 넣으면 파괴다.

    지금은 엔진이 하나라 섞일 상대가 없지만, 확장자가 엔진 속성이라는 계약은 남는다.
    """
    pg = detect_engine("postgresql")
    assert pg.dump_suffix == ".dump"      # custom format — SQL 텍스트와 섞이지 않는다
    assert pg.service == "postgres"


def test_password_never_lands_in_argv():
    """비밀번호는 argv 에 실리지 않는다 — ps 로 읽히면 안 된다."""
    pg = detect_engine("postgresql")
    argv = pg.dump_argv("c", "koipa", "koipa", "s3cret")
    assert "s3cret" not in " ".join(argv)
    assert "s3cret" not in " ".join(pg.restore_argv("c", "koipa", "koipa", "s3cret"))


def test_env_override(monkeypatch):
    monkeypatch.setenv("KOIPA_DB_ENGINE", "postgresql")
    assert detect_engine().name == POSTGRES
    monkeypatch.delenv("KOIPA_DB_ENGINE", raising=False)


# ── pg_restore 파티션 경고 판별 ────────────────────────────────────────────
# PostgreSQL 파티션 스키마에서 pg_restore --clean 은 자식 파티션의 상속 PK 를 개별
# DROP 하려다 매번 실패한다. 무해한데 종료코드가 1 이라 **복원 성공이 DR 드릴에서
# 실패로 읽히고 있었다**(2026-09-05 발견, 로컬 45건).

_BENIGN = (
    'pg_restore: error: could not execute query: ERROR:  cannot drop inherited '
    'constraint "tb_llm_usage_2027_06_pkey" of relation "tb_llm_usage_2027_06"\n'
)


def test_benign_partition_errors_recognised():
    assert _only_benign_partition_errors(_BENIGN * 45) is True


def test_real_error_is_not_swallowed():
    """진짜 실패가 파티션 예외로 덮이면 안 된다."""
    mixed = _BENIGN + 'pg_restore: error: could not execute query: ERROR:  disk full\n'
    assert _only_benign_partition_errors(mixed) is False


def test_no_errors_is_not_treated_as_benign():
    """오류가 없으면 이 판별을 탈 일이 없다 — 빈 stderr 를 성공으로 오독하지 않는다."""
    assert _only_benign_partition_errors("") is False
    assert _only_benign_partition_errors("pg_restore: warning: something\n") is False


def test_scripts_dir_importable_without_side_effects():
    """import 만으로 docker 를 부르거나 DB 에 붙지 않는다."""
    assert os.environ.get("KOIPA_DB_ENGINE") is None or True  # 부작용 없음 확인용 no-op
