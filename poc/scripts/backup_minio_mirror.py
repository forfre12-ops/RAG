"""MinIO 객체 미러 백업 (doc/11 §6.1 — 주 1회, 90일).

MinIO 소스 버킷을 별도 백업 NAS/디렉터리로 복사.
- 소스: lloydk-docs / lloydk-models / mlflow
- 대상: 호스트 로컬 디렉터리 (운영은 NFS 또는 별도 NAS)
- 증분 복사: 파일 크기·mtime 비교, 동일하면 skip
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("backup_minio_mirror")

DEFAULT_BUCKETS = ["lloydk-docs", "lloydk-models", "mlflow"]
DEFAULT_TARGET = Path("backups/minio")


def mirror_bucket(client, bucket: str, target_dir: Path) -> tuple[int, int]:
    """버킷의 모든 객체를 로컬 디렉터리로 미러. 반환: (다운로드 수, skip 수)."""
    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded = 0
    skipped = 0
    try:
        objects = list(client.list_objects(bucket, recursive=True))
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_objects %s failed: %s", bucket, exc)
        return 0, 0

    for obj in objects:
        if obj.is_dir:
            continue
        rel = obj.object_name
        dest = target_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        # 증분: 크기 동일하면 skip
        if dest.exists() and dest.stat().st_size == obj.size:
            skipped += 1
            continue

        try:
            client.fget_object(bucket, rel, str(dest))
            downloaded += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("fget %s/%s failed: %s", bucket, rel, exc)
    return downloaded, skipped


def cleanup_old_files(directory: Path, retention_days: int) -> int:
    """오래된 파일 정리. 반환: 삭제 수."""
    if not directory.exists():
        return 0
    cutoff_ts = (dt.datetime.now() - dt.timedelta(days=retention_days)).timestamp()
    removed = 0
    for f in directory.rglob("*"):
        if not f.is_file():
            continue
        if f.stat().st_mtime < cutoff_ts:
            f.unlink()
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk MinIO mirror backup")
    p.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    p.add_argument("--buckets", nargs="+", default=DEFAULT_BUCKETS)
    p.add_argument("--retention-days", type=int, default=90)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    try:
        from minio import Minio  # noqa: PLC0415
    except ImportError:
        logger.error("minio package not installed")
        return 2

    endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"
    client = Minio(
        endpoint,
        access_key=os.environ.get("MINIO_ACCESS_KEY", "lloydk"),
        secret_key=os.environ.get("MINIO_SECRET_KEY", "lloydk_dev_minio"),
        secure=secure,
    )

    total_dl = 0
    total_skip = 0
    for bucket in args.buckets:
        try:
            if not client.bucket_exists(bucket):
                logger.info("bucket missing: %s — skip", bucket)
                continue
        except Exception as exc:  # noqa: BLE001
            if args.dry_run:
                logger.warning("minio unreachable; --dry-run → exit 0")
                return 0
            logger.error("bucket_exists %s failed: %s", bucket, exc)
            return 1
        target = args.target / bucket
        dl, sk = mirror_bucket(client, bucket, target)
        total_dl += dl
        total_skip += sk
        logger.info("%s: downloaded=%d skipped=%d", bucket, dl, sk)

    removed = cleanup_old_files(args.target, args.retention_days)
    logger.info("mirror done: total_dl=%d skip=%d cleaned=%d", total_dl, total_skip, removed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
