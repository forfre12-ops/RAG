"""백업·복구가 상대하는 DB 엔진을 가려내고, 엔진별 명령을 만든다.

왜(2026-09-05). 백업·복구·DR 드릴이 전부 `pg_dump`/`pg_restore` 를 직접 부르고 있었다.
MariaDB 로 옮기면 **백업도 복구도 돌지 않는다** — 운영 진입 전에 반드시 막아야 하는 자리다.
파일명(backup_postgres.py·dr_restore.py)은 그대로 둔다: 운영 런북·cron·내부 문서가 그
이름으로 참조하고 있어, 이름을 바꾸면 그 참조가 조용히 끊긴다.

엔진 판정 순서 — 앞이 이기고, 못 정하면 실패로 본다(추측하지 않는다):
  ① --engine 인자 / KOIPA_DB_ENGINE 환경변수
  ② settings.database_url 의 dialect
  ③ 실행 중 compose 컨테이너에 어느 서비스가 있는가(postgres / mariadb)

⚠ 덤프 형식이 엔진마다 다르다.
    PostgreSQL  pg_dump -F c   custom format(압축·순서 무관 복원) → *.dump
    MariaDB     mariadb-dump   SQL 텍스트                          → *.sql
  확장자를 갈라 두면 잘못된 엔진에 잘못된 덤프를 밀어 넣는 사고를 파일 목록에서 먼저 막는다.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

POSTGRES = "postgresql"
MARIADB = "mariadb"


@dataclass(frozen=True)
class Engine:
    """한 엔진의 백업·복구 계약."""

    name: str
    service: str            # compose 서비스명(dr_discovery.autodetect_container 인자)
    dump_suffix: str        # 덤프 파일 확장자 — 엔진이 다르면 파일도 갈린다
    probe_binary: str       # 컨테이너 안에 있어야 하는 복구 도구

    def dump_argv(self, container: str, db: str, user: str, password: str | None) -> list[str]:
        """stdout 으로 덤프를 뱉는 argv."""
        if self.name == POSTGRES:
            return [
                "docker", "exec", "-i", container,
                "pg_dump", "-U", user, "-d", db,
                "-F", "c", "--no-owner", "--no-privileges",
            ]
        # MariaDB — --single-transaction 으로 InnoDB 를 잠그지 않고 일관 덤프.
        # 비밀번호는 argv 에 두지 않는다(ps 노출) — 환경변수로 넘긴다(아래 env()).
        return [
            "docker", "exec", "-i", "-e", "MYSQL_PWD", container,
            "mariadb-dump", "-u", user, "--single-transaction", "--quick",
            "--routines", "--events", "--databases", db,
        ]

    def restore_argv(self, container: str, db: str, user: str, password: str | None) -> list[str]:
        """stdin 으로 덤프를 받아 복원하는 argv."""
        if self.name == POSTGRES:
            return [
                "docker", "exec", "-i", container,
                "pg_restore", "-U", user, "-d", db,
                "--clean", "--if-exists", "--no-owner", "--no-privileges",
            ]
        # --databases 로 뜬 덤프는 CREATE DATABASE/USE 를 품고 있어 DB 지정이 필요 없다.
        return [
            "docker", "exec", "-i", "-e", "MYSQL_PWD", container,
            "mariadb", "-u", user,
        ]

    def probe_argv(self, container: str) -> list[str]:
        """복구 도구가 컨테이너에 있는지 확인."""
        return ["docker", "exec", container, "sh", "-c", f"command -v {self.probe_binary}"]

    def readiness_argv(self, container: str, user: str, db: str) -> list[str]:
        """DB 가 접속을 받는가 — 배포 스크립트의 대기 조건."""
        if self.name == POSTGRES:
            return ["docker", "exec", "-i", container, "pg_isready", "-U", user, "-d", db]
        return [
            "docker", "exec", "-i", "-e", "MYSQL_PWD", container,
            "mariadb-admin", "ping", "-u", user, "--silent",
        ]

    def env(self, password: str | None) -> dict[str, str]:
        """subprocess 에 넘길 추가 환경변수. 비밀번호를 argv 에 두지 않기 위한 자리다."""
        e = dict(os.environ)
        if self.name == MARIADB and password:
            e["MYSQL_PWD"] = password
        return e


ENGINES = {
    POSTGRES: Engine(POSTGRES, "postgres", ".dump", "pg_restore"),
    MARIADB: Engine(MARIADB, "mariadb", ".sql", "mariadb"),
}


def _from_settings() -> str | None:
    try:
        import sys

        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from koipa.config import settings  # noqa: PLC0415
        from sqlalchemy.engine import make_url  # noqa: PLC0415

        backend = make_url(settings.database_url).get_backend_name()
    except Exception as exc:  # noqa: BLE001
        logger.debug("settings 에서 엔진 판정 실패: %s", exc)
        return None
    if backend == "postgresql":
        return POSTGRES
    if backend in ("mariadb", "mysql"):
        return MARIADB
    return None


def _from_running_containers() -> str | None:
    try:
        from dr_discovery import list_koipa_containers  # noqa: PLC0415

        services = {c["service"] for c in list_koipa_containers()}
    except Exception as exc:  # noqa: BLE001
        logger.debug("컨테이너에서 엔진 판정 실패: %s", exc)
        return None
    has_pg, has_maria = "postgres" in services, "mariadb" in services
    if has_pg and has_maria:
        # 둘 다 떠 있으면 고를 수 없다 — 추측하면 엉뚱한 DB 를 덤프한다.
        logger.warning("postgres·mariadb 가 함께 떠 있다 — --engine 으로 명시할 것")
        return None
    if has_pg:
        return POSTGRES
    if has_maria:
        return MARIADB
    return None


def detect_engine(explicit: str | None = None) -> Engine:
    """상대할 엔진을 정한다. 못 정하면 RuntimeError — 추측해서 백업하지 않는다."""
    for source, value in (
        ("--engine/KOIPA_DB_ENGINE", explicit or os.environ.get("KOIPA_DB_ENGINE")),
        ("settings.database_url", _from_settings()),
        ("실행 중 컨테이너", _from_running_containers()),
    ):
        if not value:
            continue
        key = value.strip().lower()
        key = MARIADB if key in ("mariadb", "mysql") else key
        key = POSTGRES if key in ("postgres", "postgresql") else key
        if key in ENGINES:
            logger.info("DB 엔진 = %s (근거: %s)", key, source)
            return ENGINES[key]
        raise RuntimeError(f"알 수 없는 엔진 {value!r} — postgresql | mariadb")
    raise RuntimeError(
        "DB 엔진을 정할 수 없다. --engine postgresql|mariadb 로 명시하거나 "
        "DATABASE_URL 을 설정할 것(추측해서 백업하지 않는다)."
    )


def run(argv: list[str], engine: Engine, password: str | None, **kw) -> subprocess.CompletedProcess:
    """엔진 환경변수를 실어 실행한다(비밀번호가 argv 에 남지 않게)."""
    return subprocess.run(argv, env=engine.env(password), check=False, **kw)  # noqa: S603
