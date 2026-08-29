# -*- coding: utf-8 -*-
"""Postgres 가용성 판정 — 시험 전체가 이 한 곳만 쓴다.

왜 따로 두는가(2026-08-29). 같은 판정이 conftest·test_observability·
test_synth_worker_persist 세 곳에 복제돼 있었고, 전부 `localhost:5432` 를 하드코딩했다.
그래서 `DATABASE_URL` 로 다른 포트를 가리켜도 fullstack 시험 109건이 **조용히 skip** 됐다.

실측 2026-08-29. 도커에 postgres 가 6일째 떠 있었는데 포트가 호스트에 매핑돼 있지
않아 전부 건너뛰었고, 그 상태를 "로컬에 DB 가 없다"로 넘겼다. 그 사이 alembic 리비전
번호 충돌(a1b2c3d4e5f6 중복)이 배포 직전까지 발견되지 않았다 — 단위 시험은 3,680건
전부 초록이었다. **시험이 안 도는 것을 시험이 알려주지 않으면 그렇게 된다.**

conftest 에서 함수를 가져다 쓰려 했으나 시험 모듈 임포트 시점에 `conftest` 가
sys.modules 에 없어 실패한다. 그래서 평범한 모듈로 둔다.
"""
from __future__ import annotations

import os
import socket
from urllib.parse import urlparse

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 5432


def pg_endpoint() -> tuple[str, int]:
    """DATABASE_URL 이 있으면 그 host·port, 없으면 localhost:5432."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return DEFAULT_HOST, DEFAULT_PORT
    # postgresql+psycopg://user:pw@host:port/db — 드라이버 접미사를 떼야 urlparse 가 읽는다
    parsed = urlparse(url.replace("+psycopg", "").replace("+asyncpg", ""))
    return parsed.hostname or DEFAULT_HOST, parsed.port or DEFAULT_PORT


def postgres_available(timeout: float = 0.5) -> bool:
    """접속 가능하면 True. 판정 실패는 '없음'으로 본다(시험을 막지 않는다)."""
    host, port = pg_endpoint()
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except OSError:
        return False
