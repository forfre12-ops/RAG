"""PostgreSQL 일별 백업 → MinIO `lloydk-backup/pg/` (doc/11 §6.2).

설계:
- pg_dump는 docker exec로 실행 (호스트에 psql 클라이언트 무관)
- 산출물: lloydk-YYYYMMDD-HHMMSS.dump (custom format, 압축)
- MinIO 적재는 본 모듈의 minio 클라이언트로 직접 (mc CLI 의존 회피)
- 30일 이상 백업은 자동 정리

cron 등록 예시:
  0 2 * * * /usr/bin/python /opt/lloydk/poc/scripts/backup_postgres.py --upload
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger("backup_postgres")

DEFAULT_RETENTION_DAYS = 30
DEFAULT_OUTPUT_DIR = Path("backups/pg")
DEFAULT_PG_CONTAINER = "lloydk-poc-postgres-1"
DEFAULT_DB = "lloydk"
DEFAULT_USER = "lloydk"
DEFAULT_BUCKET = "lloydk-backup"


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def run_pg_dump(
    *,
    container: str,
    db: str,
    user: str,
    output_dir: Path,
) -> Path:
    """docker exec pg_dump → 호스트 파일."""
    output_dir.mkdir(parents=True, exist_ok=True)
    name = f"{db}-{_ts()}.dump"
    out_path = output_dir / name
    cmd = [
        "docker", "exec", "-i", container,
        "pg_dump", "-U", user, "-d", db, "-F", "c", "--no-owner", "--no-privileges",
    ]
    logger.info("running: %s → %s", " ".join(cmd), out_path)
    with out_path.open("wb") as fh:
        proc = subprocess.run(cmd, stdout=fh, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError(f"pg_dump failed: {proc.stderr.decode(errors='replace')}")
    if out_path.stat().st_size == 0:
        out_path.unlink(missing_ok=True)
        raise RuntimeError("pg_dump produced empty file")
    return out_path


def upload_to_minio(
    path: Path,
    *,
    bucket: str = DEFAULT_BUCKET,
    object_prefix: str = "pg/",
    endpoint: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> str:
    """MinIO 적재 → 객체 경로 반환. 버킷이 없으면 자동 생성."""
    try:
        from minio import Minio  # noqa: PLC0415
    except ImportError as exc:
        raise RuntimeError("minio package not installed (pip install minio)") from exc

    endpoint = endpoint or os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    access_key = access_key or os.environ.get("MINIO_ACCESS_KEY", "lloydk")
    secret_key = secret_key or os.environ.get("MINIO_SECRET_KEY", "lloydk_dev_minio")
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("created bucket: %s", bucket)

    object_name = f"{object_prefix.rstrip('/')}/{path.name}"
    client.fput_object(bucket, object_name, str(path))
    logger.info("uploaded: s3://%s/%s (%d bytes)", bucket, object_name, path.stat().st_size)
    return f"s3://{bucket}/{object_name}"


def cleanup_old_backups(directory: Path, retention_days: int) -> int:
    """retention_days보다 오래된 파일 삭제. 반환: 삭제된 파일 수."""
    if not directory.exists():
        return 0
    cutoff = dt.datetime.now() - dt.timedelta(days=retention_days)
    cutoff_ts = cutoff.timestamp()
    removed = 0
    for f in directory.iterdir():
        if not f.is_file():
            continue
        if f.stat().st_mtime < cutoff_ts:
            f.unlink()
            removed += 1
            logger.info("cleaned: %s", f.name)
    return removed


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk Postgres backup")
    p.add_argument("--container", default=DEFAULT_PG_CONTAINER)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--upload", action="store_true", help="MinIO에 적재")
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--skip-dump", action="store_true", help="dry-run: dump 건너뜀")
    args = p.parse_args(argv)

    try:
        # Docker CLI 가용성 확인
        if not args.skip_dump and shutil.which("docker") is None:
            logger.error("docker CLI not found in PATH")
            return 2

        if args.skip_dump:
            logger.info("--skip-dump: dry-run only")
            return 0

        dump_path = run_pg_dump(
            container=args.container, db=args.db, user=args.user, output_dir=args.output_dir,
        )
        logger.info("dump OK: %s (%d bytes)", dump_path, dump_path.stat().st_size)

        if args.upload:
            upload_to_minio(dump_path, bucket=args.bucket)

        removed = cleanup_old_backups(args.output_dir, args.retention_days)
        if removed:
            logger.info("retention: removed %d old files", removed)

        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("backup failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
