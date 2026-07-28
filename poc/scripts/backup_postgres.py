"""PostgreSQL 일별 백업 → 로컬FS `backups/pg/` (선택: 두 번째 매체 / MinIO).

설계:
- pg_dump는 docker exec로 실행 (호스트에 psql 클라이언트 무관)
- 대상 컨테이너는 실행 중 스택에서 자동탐지(--container 로 명시 가능). 하드코딩 기본값이
  airgap(lloydk-airgap-*)·dual(lloydk-jjw-*/cust-*)에서 빗나가던 문제를 자동탐지로 해소.
- 산출물: {db}-YYYYMMDD-HHMMSS.dump (custom format, 압축) → backups/pg/
- 오프사이트 사본: 폐쇄망은 MinIO 미사용 → --mirror-dir 로 별도 디스크/NAS 에 사본.
  --upload(MinIO)는 dev/연결망 선택지일 뿐(airgap 에선 minio 미기동이라 해당 없음).
- 30일 이상 백업은 자동 정리.

권장: pg+storage 를 한 번에 도는 backup_dr.py 를 cron/systemd 에 등록(단일 진입점).
단독 cron 예시(pg 만):
  0 2 * * * /usr/bin/python /opt/lloydk/poc/scripts/backup_postgres.py
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

from dr_discovery import autodetect_container

logger = logging.getLogger("backup_postgres")

DEFAULT_RETENTION_DAYS = 30
DEFAULT_OUTPUT_DIR = Path("backups/pg")
DEFAULT_PG_CONTAINER = "lloydk-poc-postgres-1"   # 자동탐지 실패 시 최후 폴백(dev 컨테이너명)


def autodetect_pg_container() -> str | None:
    """실행 중 스택의 postgres 컨테이너 자동탐지. 2스택+면 모호 → None."""
    return autodetect_container(("postgres",))
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


def mirror_to_second_media(path: Path, mirror_dir: Path) -> Path:
    """두 번째 매체(별도 디스크/NAS)로 사본 — 폐쇄망 오프사이트(MinIO 미사용)."""
    mirror_dir.mkdir(parents=True, exist_ok=True)
    dest = mirror_dir / path.name
    shutil.copy2(path, dest)
    logger.info("mirrored: %s", dest)
    return dest


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk Postgres backup")
    p.add_argument("--container", default=None,
                   help="postgres 컨테이너명(미지정 시 자동탐지; 2스택+면 모호→명시 필요)")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--user", default=DEFAULT_USER)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--mirror-dir", type=Path, default=None,
                   help="두 번째 매체 사본 경로(별도 디스크/NAS). 지정 시 오프사이트 사본 생성.")
    p.add_argument("--upload", action="store_true", help="MinIO에 적재(dev/연결망 전용)")
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

        container = args.container or autodetect_pg_container()
        if container is None:
            logger.error(
                "postgres 컨테이너 자동탐지 실패(미가동 또는 2스택+ 모호) — --container 로 명시하세요"
            )
            return 2

        dump_path = run_pg_dump(
            container=container, db=args.db, user=args.user, output_dir=args.output_dir,
        )
        logger.info("dump OK: %s (%d bytes)", dump_path, dump_path.stat().st_size)

        if args.mirror_dir is not None:
            mirror_to_second_media(dump_path, args.mirror_dir)

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
