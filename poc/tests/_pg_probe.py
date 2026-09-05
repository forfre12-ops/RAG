# -*- coding: utf-8 -*-
"""DB 가용성 판정 — 시험 전체가 이 한 곳만 쓴다.

왜 따로 두는가(2026-08-29). 같은 판정이 conftest·test_observability·
test_synth_worker_persist 세 곳에 복제돼 있었고, 전부 `localhost:5432` 를 하드코딩했다.
그래서 `DATABASE_URL` 로 다른 포트를 가리켜도 fullstack 시험 109건이 **조용히 skip** 됐다.

실측 2026-08-29. 도커에 postgres 가 6일째 떠 있었는데 포트가 호스트에 매핑돼 있지
않아 전부 건너뛰었고, 그 상태를 "로컬에 DB 가 없다"로 넘겼다. 그 사이 alembic 리비전
번호 충돌(a1b2c3d4e5f6 중복)이 배포 직전까지 발견되지 않았다 — 단위 시험은 3,680건
전부 초록이었다. **시험이 안 도는 것을 시험이 알려주지 않으면 그렇게 된다.**

[2026-09-05] 같은 실패가 한 번 더 났다. 기본 DB 를 MariaDB 로 넘겼는데(A2) 이 모듈은
① 환경변수 DATABASE_URL 만 보고 ② 없으면 localhost:5432 로 폴백했다. 새 기본값은
설정(config.py)에 있고 환경변수엔 없으므로, MariaDB 가 떠 있는데도 fullstack 52건이
조용히 skip 됐다. 이제 **설정값을 폴백으로 쓰고** dialect 별 기본 포트를 안다.

conftest 에서 함수를 가져다 쓰려 했으나 시험 모듈 임포트 시점에 `conftest` 가
sys.modules 에 없어 실패한다. 그래서 평범한 모듈로 둔다.
이름(_pg_probe·postgres_available)은 호출부 호환으로 유지한다.
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

DEFAULT_HOST = "localhost"
# dialect 별 기본 포트 — URL 에 포트가 없을 때만 쓴다.
_DEFAULT_PORT = {"postgresql": 5432, "postgres": 5432, "mariadb": 3306, "mysql": 3306}
_FALLBACK_PORT = 5432

# 드라이버 접미사를 떼야 urlparse 가 스킴을 읽는다.
_DRIVER_SUFFIXES = ("+psycopg", "+asyncpg", "+psycopg2", "+pymysql", "+mysqldb", "+asyncmy")


def _configured_url() -> str:
    """환경변수 우선, 없으면 **설정 기본값**.

    설정을 안 보면 기본 DB 를 바꿨을 때 이 판정만 옛 값에 남아 시험이 조용히 꺼진다
    (2026-09-05 실측). config 임포트 실패는 빈 문자열 — 판정을 막지 않는다.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url:
        return url
    try:
        from koipa.config import settings  # noqa: PLC0415

        return settings.database_url or ""
    except Exception:  # noqa: BLE001
        return ""


def pg_endpoint() -> tuple[str, int]:
    """접속을 시도할 (host, port). 이름은 호출부 호환으로 유지한다."""
    url = _configured_url()
    if not url:
        return DEFAULT_HOST, _FALLBACK_PORT
    bare = url
    for suffix in _DRIVER_SUFFIXES:
        bare = bare.replace(suffix, "")
    parsed = urlparse(bare)
    port = parsed.port or _DEFAULT_PORT.get(parsed.scheme, _FALLBACK_PORT)
    return parsed.hostname or DEFAULT_HOST, port


def postgres_available(timeout: float = 0.5) -> bool:
    """접속 가능하면 True. 판정 실패는 '없음'으로 본다(시험을 막지 않는다).

    이름은 호출부 호환으로 유지한다 — 실제로는 PostgreSQL·MariaDB 양쪽을 본다.
    """
    host, port = pg_endpoint()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False
