"""Elasticsearch 일별 스냅샷 (doc/11 §6.3).

설계:
- 저장소가 미등록이면 자동 등록 (S3 호환 MinIO 사용)
- 스냅샷명: snap-YYYYMMDD-HHMMSS
- 30일 retention — 오래된 snapshot 자동 정리
- ES `repository-s3` 플러그인 가정 (운영 ES에 사전 설치 필요)

cron 등록 예시:
  30 2 * * * /usr/bin/python /opt/lloydk/poc/scripts/backup_es_snapshot.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("backup_es_snapshot")

DEFAULT_REPO = "lloydk_repo"
DEFAULT_BUCKET = "lloydk-es-snapshots"
DEFAULT_RETENTION_DAYS = 30
DEFAULT_INDEX_PATTERN = "secrets-*"


def _ts() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")


def _get_client(es_url: Optional[str] = None):
    from elasticsearch import Elasticsearch  # noqa: PLC0415
    return Elasticsearch(es_url or os.environ.get("ES_URL", "http://localhost:9200"))


def ensure_repository(
    client,
    *,
    name: str = DEFAULT_REPO,
    bucket: str = DEFAULT_BUCKET,
    endpoint: str = "minio:9000",
) -> None:
    """저장소 미등록 시 S3 호환 (MinIO) 저장소 생성."""
    try:
        existing = client.snapshot.get_repository(name=name)
        if existing:
            logger.info("repository exists: %s", name)
            return
    except Exception:  # noqa: BLE001
        pass  # 미존재 → 등록

    body = {
        "type": "s3",
        "settings": {
            "bucket": bucket,
            "endpoint": endpoint,
            "path_style_access": "true",
            "protocol": "http",
        },
    }
    client.snapshot.create_repository(name=name, body=body)
    logger.info("repository created: %s → s3://%s @ %s", name, bucket, endpoint)


def take_snapshot(
    client,
    *,
    repo: str = DEFAULT_REPO,
    index_pattern: str = DEFAULT_INDEX_PATTERN,
    wait_for_completion: bool = True,
) -> str:
    snap_name = f"snap-{_ts()}"
    client.snapshot.create(
        repository=repo,
        snapshot=snap_name,
        wait_for_completion=wait_for_completion,
        body={
            "indices": index_pattern,
            "include_global_state": False,
            "metadata": {
                "taken_by": "backup_es_snapshot.py",
                "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
            },
        },
    )
    logger.info("snapshot taken: %s (pattern=%s)", snap_name, index_pattern)
    return snap_name


def cleanup_old_snapshots(client, *, repo: str, retention_days: int) -> int:
    """retention 초과 snapshot 삭제. 반환: 삭제 수."""
    resp = client.snapshot.get(repository=repo, snapshot="_all")
    snapshots = resp.get("snapshots", [])
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=retention_days)
    removed = 0
    for s in snapshots:
        start_ms = s.get("start_time_in_millis", 0)
        if start_ms == 0:
            continue
        start = dt.datetime.fromtimestamp(start_ms / 1000, tz=dt.timezone.utc)
        if start < cutoff:
            name = s["snapshot"]
            client.snapshot.delete(repository=repo, snapshot=name)
            logger.info("snapshot deleted: %s", name)
            removed += 1
    return removed


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    p = argparse.ArgumentParser(description="Lloydk Elasticsearch snapshot")
    p.add_argument("--es-url", default=None)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--bucket", default=DEFAULT_BUCKET)
    p.add_argument("--endpoint", default="minio:9000")
    p.add_argument("--pattern", default=DEFAULT_INDEX_PATTERN)
    p.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)
    p.add_argument("--ensure-only", action="store_true", help="저장소 등록만 (스냅샷 생략)")
    p.add_argument("--dry-run", action="store_true", help="ES 미연결 시에도 즉시 종료(코드 0)")
    args = p.parse_args(argv)

    try:
        client = _get_client(args.es_url)
        # ping
        if not client.ping():
            if args.dry_run:
                logger.warning("ES unreachable; --dry-run → exit 0")
                return 0
            logger.error("ES unreachable")
            return 2

        ensure_repository(client, name=args.repo, bucket=args.bucket, endpoint=args.endpoint)
        if args.ensure_only:
            return 0

        snap = take_snapshot(client, repo=args.repo, index_pattern=args.pattern)
        removed = cleanup_old_snapshots(client, repo=args.repo, retention_days=args.retention_days)
        logger.info("done: snapshot=%s removed=%d", snap, removed)
        return 0
    except Exception as exc:  # noqa: BLE001
        logger.error("snapshot failed: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
