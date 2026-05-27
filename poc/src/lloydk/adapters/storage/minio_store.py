"""MinIO/S3 호환 backend."""

from __future__ import annotations

import io


class MinioStorage:
    name = "minio"

    def __init__(self) -> None:
        from minio import Minio

        from lloydk.config import settings

        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )

    def put(self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
        self._client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
        return self.uri(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        resp = self._client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.stat_object(bucket, key)
            return True
        except Exception as exc:  # noqa: BLE001
            # K3: NoSuchKey가 정상 케이스이므로 debug 수준만 기록
            import logging as _logging
            _logging.getLogger(__name__).debug("minio stat miss %s/%s: %s", bucket, key, exc)
            return False

    def uri(self, bucket: str, key: str) -> str:
        return f"s3://{bucket}/{key}"
