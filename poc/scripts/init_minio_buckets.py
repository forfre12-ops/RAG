"""MinIO 초기 버킷 생성. docker compose up 직후 1회 실행.

생성 버킷:
- lloydk-docs   : 원본/정규화 문서 (raw/, normalized/, sample/)
- lloydk-models : 학습된 모델 가중치 스냅샷
- mlflow        : MLflow artifact root
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

try:
    from minio import Minio
    from minio.error import S3Error
except ImportError:  # pragma: no cover
    print("[init_minio_buckets] minio package not installed. pip install minio", file=sys.stderr)
    sys.exit(1)


BUCKETS: tuple[str, ...] = (
    os.environ.get("MINIO_BUCKET_DOCS", "lloydk-docs"),
    os.environ.get("MINIO_BUCKET_MODELS", "lloydk-models"),
    os.environ.get("MINIO_BUCKET_MLFLOW", "mlflow"),
)


def ensure_buckets(client: Minio, names: Iterable[str]) -> list[str]:
    created: list[str] = []
    for name in names:
        if client.bucket_exists(name):
            print(f"[init_minio_buckets] exists: {name}")
            continue
        client.make_bucket(name)
        created.append(name)
        print(f"[init_minio_buckets] created: {name}")
    return created


def main() -> int:
    endpoint = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
    access_key = os.environ.get("MINIO_ROOT_USER", "lloydk")
    secret_key = os.environ.get("MINIO_ROOT_PASSWORD", "lloydk_dev_minio")
    secure = os.environ.get("MINIO_SECURE", "false").lower() == "true"

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
    try:
        ensure_buckets(client, BUCKETS)
    except S3Error as exc:
        print(f"[init_minio_buckets] FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
