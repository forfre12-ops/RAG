"""인프라 헬스 체크. docker compose up 후 실행.

검증(배포 프로파일 인지 — 없는 인프라는 skip 하여 오탐 방지):
- PostgreSQL 연결 + 핵심 테이블(tb_documents/tb_classifications/tb_model_versions/tb_audit_log)
- MinIO 버킷 존재 — STORAGE_BACKEND=minio 일 때만(폐쇄망 로컬FS 는 skip)
- Redis PING
- MLflow API — MLFLOW_TRACKING_URI 설정 시에만(폐쇄망 추론전용 배포는 skip)
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
        from sqlalchemy import bindparam, create_engine, text
    except ImportError:
        return CheckResult("postgres", False, "sqlalchemy not installed")
    # 실제 스키마 테이블명은 tb_ 접두어다(예: tb_documents). 과거의 'documents'/'classifications'/
    # 'model_versions' 는 존재하지 않는 이름이었고 'tenants' 는 테넌트 전면 제거로 드롭됐다 —
    # 그래서 이 검사가 정상 배포에서도 항상 0/4 로 FAIL 했다(무의미한 alarm).
    core = ("tb_documents", "tb_classifications", "tb_model_versions", "tb_audit_log")
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            stmt = text(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' "
                "AND tablename IN :names"
            ).bindparams(bindparam("names", expanding=True))
            tables = conn.execute(stmt, {"names": list(core)}).scalars().all()
        missing = sorted(set(core) - set(tables))
        return CheckResult("postgres", not missing, f"present={sorted(tables)} missing={missing}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("postgres", False, f"error: {exc}")


def check_minio() -> CheckResult:
    # 폐쇄망/기본 배포는 로컬FS(storage_backend=local)라 MinIO 를 쓰지 않는다 → 검사 건너뜀(정상).
    # 과거엔 무조건 검사해 MinIO 없는 정상 배포에서도 verify_infra 가 항상 FAIL 했다.
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend != "minio":
        return CheckResult("minio", True, f"skipped (storage_backend={backend})")
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
    # MLflow 는 지재원 full-train 환경 전용. 폐쇄망/운영 배포는 추론 전용이라 MLFLOW_TRACKING_URI
    # 를 설정하지 않는다 → 미설정이면 검사 건너뜀(정상). 과거엔 기본값 localhost:5000 을 무조건
    # 찔러 MLflow 없는 정상 배포에서도 FAIL 했다.
    base = os.environ.get("MLFLOW_TRACKING_URI")
    if not base:
        return CheckResult("mlflow", True, "skipped (no MLFLOW_TRACKING_URI)")
    ok, detail = _http_ok(f"{base}/api/2.0/mlflow/experiments/list")
    if not ok:
        ok, detail = _http_ok(f"{base}/")
    return CheckResult("mlflow", ok, detail)


def main() -> int:
    checks = [
        check_postgres(),
        check_minio(),
        check_redis(),
        check_mlflow(),
    ]
    print(json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=2))
    failed = [c for c in checks if not c.ok]
    if failed:
        print(f"\n[verify_infra] FAILED: {[c.name for c in failed]}", file=sys.stderr)
        return 1
    print("\n[verify_infra] ALL GREEN", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
