"""환경 캡처 — PSH §3 환경·재현 메타데이터.

모든 외부 의존을 try/except로 감싸 어떤 환경에서도 동작.
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class ServiceStatus:
    # [2026-09-05] db 로 이름을 바꿨다 — 재는 대상이 처음부터 koipa.db.engine 이었고,
    # 그것은 DATABASE_URL 이 가리키는 DB 다(현재 MariaDB). 이름만 PostgreSQL 이었다.
    db: str = "UNKNOWN"
    redis: str = "UNKNOWN"
    # elasticsearch·minio 는 재지 않는다. 쓰지 않는 백엔드라 늘 DOWN 이 찍혀
    # 환경 보고서가 "무언가 죽어 있다"로 읽혔다. 어느 KPI 도 요구하지 않는다
    # (kpis.py: "es·minio 를 요구하던 이전 정의는 쓰지 않는 백엔드를 전제해 영구 SKIP").
    elasticsearch: str = "N/A"
    minio: str = "N/A"


@dataclass
class EnvSnapshot:
    python: str = ""
    platform: str = ""
    cpu_count: int = 0
    ram_gb: float = 0.0
    gpu: str = "N/A"
    git_sha: str = ""
    git_branch: str = ""
    pytest_collected: int | None = None
    services: ServiceStatus = field(default_factory=ServiceStatus)
    llm_provider: str = "noop"
    embedding_provider: str = "hash"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def _git(args: list[str]) -> str:
    try:
        out = subprocess.check_output(
            ["git", *args],
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
        return out.decode("utf-8", errors="ignore").strip()
    except Exception:  # noqa: BLE001
        return ""


def _ram_gb() -> float:
    try:
        import psutil  # type: ignore

        return round(psutil.virtual_memory().total / (1024**3), 1)
    except Exception:  # noqa: BLE001
        pass
    if hasattr(os, "sysconf") and os.sysconf_names.get("SC_PAGE_SIZE"):
        try:
            pages = os.sysconf("SC_PHYS_PAGES")
            page_size = os.sysconf("SC_PAGE_SIZE")
            return round(pages * page_size / (1024**3), 1)
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def _gpu() -> str:
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            name = torch.cuda.get_device_name(0)
            mem_gb = round(torch.cuda.get_device_properties(0).total_memory / (1024**3), 1)
            return f"{name} {mem_gb}GB"
    except Exception:  # noqa: BLE001
        pass
    return "N/A"


def _svc_db() -> str:
    """설정된 DB 를 잰다 — DATABASE_URL 이 가리키는 곳(MariaDB 또는 PostgreSQL).

    [2026-09-05] 이름이 _svc_postgres 였는데 처음부터 koipa.db.engine 을 썼다.
    재는 대상은 맞았고 이름만 틀렸다.
    """
    try:
        from sqlalchemy import text

        from koipa.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "UP"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _svc_es(url: str | None = None) -> str:
    try:
        import httpx

        from koipa.config import settings

        u = url or getattr(settings, "es_url", None) or "http://localhost:9200"
        r = httpx.get(u, timeout=2.0)
        if r.status_code < 400:
            return "UP"
        return "DOWN"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _svc_redis() -> str:
    try:
        import redis  # type: ignore

        from koipa.config import settings

        url = getattr(settings, "redis_url", "redis://localhost:6379/0")
        r = redis.from_url(url, socket_connect_timeout=2)
        r.ping()
        return "UP"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _svc_minio() -> str:
    try:
        import httpx

        from koipa.config import settings

        endpoint = getattr(settings, "minio_endpoint", "localhost:9000")
        scheme = "https" if getattr(settings, "minio_secure", False) else "http"
        r = httpx.get(f"{scheme}://{endpoint}/minio/health/live", timeout=2.0)
        return "UP" if r.status_code < 400 else "DOWN"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _pytest_collected() -> int | None:
    try:
        out = subprocess.check_output(
            [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
            stderr=subprocess.STDOUT,
            timeout=30,
            cwd=os.getcwd(),
        )
        text = out.decode("utf-8", errors="ignore")
        for line in text.splitlines()[::-1]:
            if "tests collected" in line or "test collected" in line:
                tok = line.strip().split()
                for i, t in enumerate(tok):
                    if t in ("tests", "test") and i > 0:
                        try:
                            return int(tok[i - 1])
                        except ValueError:
                            pass
        for line in text.splitlines():
            if " collected" in line and not line.startswith("="):
                head = line.strip().split()
                if head and head[0].isdigit():
                    return int(head[0])
        return None
    except Exception:  # noqa: BLE001
        return None


def capture_env(
    *,
    probe_services: bool = True,
    probe_pytest: bool = False,
    llm_provider: str = "noop",
    embedding_provider: str = "hash",
) -> EnvSnapshot:
    """현재 환경을 캡처. probe_services=False면 서비스 핑 생략 (빠른 dryrun용)."""

    svc = ServiceStatus()
    if probe_services:
        # es·minio 는 재지 않는다(위 ServiceStatus 주석 참조).
        svc = ServiceStatus(db=_svc_db(), redis=_svc_redis())

    return EnvSnapshot(
        python=platform.python_version(),
        platform=f"{platform.system().lower()} {platform.release()}",
        cpu_count=os.cpu_count() or 0,
        ram_gb=_ram_gb(),
        gpu=_gpu(),
        git_sha=_git(["rev-parse", "--short", "HEAD"]),
        git_branch=_git(["rev-parse", "--abbrev-ref", "HEAD"]),
        pytest_collected=_pytest_collected() if probe_pytest else None,
        services=svc,
        llm_provider=llm_provider,
        embedding_provider=embedding_provider,
    )
