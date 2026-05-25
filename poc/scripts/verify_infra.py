"""인프라 헬스 체크. docker compose up 후 실행.

검증:
- PostgreSQL 연결 + pgvector extension 존재 + 핵심 테이블 4종
- Qdrant 헬스
- MinIO 버킷 존재
- Redis PING
- MLflow API
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _http_ok(url: str, timeout: float = 3.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.status == 200, f"HTTP {resp.status}"
    except urllib.error.URLError as exc:
        return False, f"unreachable: {exc.reason}"
    except Exception as exc:  # noqa: BLE001
        return False, f"error: {exc}"


def check_postgres() -> CheckResult:
    url = os.environ.get("DATABASE_URL", "postgresql+psycopg://lloydk:lloydk_dev@localhost:5432/lloydk")
    try:
        from sqlalchemy import create_engine, text
    except ImportError:
        return CheckResult("postgres", False, "sqlalchemy not installed")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            ext = conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).scalar()
            tables = conn.execute(
                text(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                    "AND tablename IN ('tenants','documents','classifications','model_versions')"
                )
            ).scalars().all()
        ok = ext == "vector" and len(tables) == 4
        return CheckResult("postgres", ok, f"vector={ext}, tables={sorted(tables)}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("postgres", False, f"error: {exc}")


def check_qdrant() -> CheckResult:
    base = os.environ.get("QDRANT_URL", "http://localhost:6333")
    ok, detail = _http_ok(f"{base}/healthz")
    if not ok:
        ok, detail = _http_ok(f"{base}/")
    return CheckResult("qdrant", ok, detail)


def check_minio() -> CheckResult:
    try:
        from minio import Minio
    except ImportError:
        return CheckResult("minio", False, "minio package not installed")
    endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    try:
        client = Minio(
            endpoint,
            access_key=os.environ.get("MINIO_ROOT_USER", "lloydk"),
            secret_key=os.environ.get("MINIO_ROOT_PASSWORD", "lloydk_dev_minio"),
            secure=False,
        )
        needed = {"lloydk-docs", "lloydk-models", "mlflow"}
        present = {b.name for b in client.list_buckets()}
        missing = needed - present
        return CheckResult("minio", not missing, f"present={sorted(present)}, missing={sorted(missing)}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("minio", False, f"error: {exc}")


def check_redis() -> CheckResult:
    try:
        import redis  # noqa: PLC0415
    except ImportError:
        return CheckResult("redis", False, "redis package not installed")
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    try:
        client = redis.from_url(url, socket_timeout=2)
        pong = client.ping()
        return CheckResult("redis", bool(pong), "PONG" if pong else "no pong")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("redis", False, f"error: {exc}")


def check_mlflow() -> CheckResult:
    base = os.environ.get("MLFLOW_TRACKING_URI", "http://localhost:5000")
    ok, detail = _http_ok(f"{base}/api/2.0/mlflow/experiments/list")
    if not ok:
        ok, detail = _http_ok(f"{base}/")
    return CheckResult("mlflow", ok, detail)


def main() -> int:
    checks = [check_postgres(), check_qdrant(), check_minio(), check_redis(), check_mlflow()]
    print(json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=2))
    failed = [c for c in checks if not c.ok]
    if failed:
        print(f"\n[verify_infra] FAILED: {[c.name for c in failed]}", file=sys.stderr)
        return 1
    print("\n[verify_infra] ALL GREEN", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
