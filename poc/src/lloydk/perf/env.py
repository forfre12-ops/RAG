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
    postgres: str = "UNKNOWN"
    elasticsearch: str = "UNKNOWN"
    redis: str = "UNKNOWN"
    minio: str = "UNKNOWN"


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
    vector_backend: str = "inmemory"

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


def _svc_postgres() -> str:
    try:
        from sqlalchemy import text

        from lloydk.db import engine

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "UP"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _svc_es(url: str | None = None) -> str:
    try:
        import httpx

        from lloydk.config import settings

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

        from lloydk.config import settings

        url = getattr(settings, "redis_url", "redis://localhost:6379/0")
        r = redis.from_url(url, socket_connect_timeout=2)
        r.ping()
        return "UP"
    except Exception:  # noqa: BLE001
        return "DOWN"


def _svc_minio() -> str:
    try:
        import httpx

        from lloydk.config import settings

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
    vector_backend: str = "inmemory",
) -> EnvSnapshot:
    """현재 환경을 캡처. probe_services=False면 서비스 핑 생략 (빠른 dryrun용)."""

    svc = ServiceStatus()
    if probe_services:
        svc = ServiceStatus(
            postgres=_svc_postgres(),
            elasticsearch=_svc_es(),
            redis=_svc_redis(),
            minio=_svc_minio(),
        )

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
        vector_backend=vector_backend,
    )
