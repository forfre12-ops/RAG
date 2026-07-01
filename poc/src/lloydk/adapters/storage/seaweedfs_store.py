"""P2-C2: SeaweedFS storage adapter (S3 API 호환).

MinIO AGPL 회피용. SeaweedFS는 Apache-2.0 + Filer S3 게이트웨이로 boto3 호환.
도입 절차:
  1) docker-compose에 seaweedfs 서비스 추가 (s3 mode)
  2) settings.storage_backend = 'seaweedfs'
  3) ObjectStorage Protocol 그대로 사용

본 어댑터는 boto3(botocore)로 구현 — minio-py 의존 없이 어떤 S3 호환 백엔드에도 적용 가능.
의존성:
  pip install "boto3>=1.34"
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class SeaweedFSStore:
    name = "seaweedfs"

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
        verify_tls: bool = True,
    ):
        self._endpoint = endpoint_url
        self._access = access_key
        self._secret = secret_key
        self._region = region
        self._verify_tls = verify_tls
        self._client = None

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "SeaweedFSStore requires boto3 — pip install boto3"
            ) from e
        self._client = boto3.client(
            "s3",
            endpoint_url=self._endpoint,
            aws_access_key_id=self._access,
            aws_secret_access_key=self._secret,
            region_name=self._region,
            verify=self._verify_tls,
        )
        return self._client

    def put(self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        c = self._ensure()
        key = self._norm_key(key)
        c.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return self.uri(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        c = self._ensure()
        obj = c.get_object(Bucket=bucket, Key=self._norm_key(key))
        return obj["Body"].read()

    def exists(self, bucket: str, key: str) -> bool:
        c = self._ensure()
        try:
            c.head_object(Bucket=bucket, Key=self._norm_key(key))
            return True
        except Exception:  # noqa: BLE001
            return False

    def uri(self, bucket: str, key: str) -> str:
        return f"s3://{bucket}/{key}"

    # --- #36: 객체 열거·삭제 (보존정책·재처리·마이그레이션용) -----------------

    @staticmethod
    def _norm_key(key: str) -> str:
        # #36-(4): MinIO/SeaweedFS는 키 검증이 없어 put/get/list/delete 전반에 일관 정규화.
        # 선행 슬래시 제거·../ 세그먼트 차단(S3 키에 .. 가 섞이면 의도치 않은 객체 지목).
        k = key.lstrip("/")
        if ".." in k.split("/"):
            raise ValueError(f"invalid object key (path traversal): {key!r}")
        return k

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        # S3 ListObjectsV2 페이지네이터로 prefix 매칭 키를 전부 열거.
        c = self._ensure()
        prefix = self._norm_key(prefix) if prefix else ""
        keys: list[str] = []
        paginator = c.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return sorted(keys)

    def delete(self, bucket: str, key: str) -> None:
        # S3 delete_object — 없는 키도 멱등. 키는 정규화/검증 후 삭제.
        c = self._ensure()
        c.delete_object(Bucket=bucket, Key=self._norm_key(key))

    def ensure_bucket(self, bucket: str) -> None:
        c = self._ensure()
        try:
            c.head_bucket(Bucket=bucket)
        except Exception:
            try:
                c.create_bucket(Bucket=bucket)
            except Exception as e:  # noqa: BLE001
                logger.warning("create_bucket failed: %s", e)


def get_storage(provider: Optional[str] = None):
    """settings.storage_backend 기반 팩토리."""
    from lloydk.config import settings

    name = (provider or getattr(settings, "storage_backend", "minio") or "minio").lower()
    if name == "local":
        # 클래스명은 LocalStorage (이전 오타 LocalStore는 존재하지 않아 ImportError였음).
        from lloydk.adapters.storage.local_store import LocalStorage
        return LocalStorage()
    if name == "minio":
        # 클래스명은 MinioStorage (이전 오타 MinioStore는 존재하지 않았음).
        from lloydk.adapters.storage.minio_store import MinioStorage
        return MinioStorage()
    if name in ("seaweedfs", "s3"):
        endpoint = getattr(settings, "storage_endpoint", "") or "http://localhost:8333"
        return SeaweedFSStore(
            endpoint_url=endpoint,
            access_key=getattr(settings, "minio_access_key", ""),
            secret_key=getattr(settings, "minio_secret_key", ""),
            verify_tls=getattr(settings, "storage_verify_tls", True),
        )
    raise ValueError(f"unknown storage backend: {name}")
