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

    @staticmethod
    def _norm_key(key: str) -> str:
        # #36-(4)/F: MinIO도 S3 호환이라 키 검증이 없어 SeaweedFS와 동일하게 일관 정규화.
        # 선행 슬래시 제거·../ 세그먼트 차단(키에 .. 가 섞이면 의도치 않은 객체 지목).
        k = key.lstrip("/")
        if ".." in k.split("/"):
            raise ValueError(f"invalid object key (path traversal): {key!r}")
        return k

    def put(self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        key = self._norm_key(key)
        if not self._client.bucket_exists(bucket):
            self._client.make_bucket(bucket)
        self._client.put_object(bucket, key, io.BytesIO(data), length=len(data), content_type=content_type)
        return self.uri(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        resp = self._client.get_object(bucket, self._norm_key(key))
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self._client.stat_object(bucket, self._norm_key(key))
            return True
        except Exception as exc:  # noqa: BLE001
            # K3: NoSuchKey가 정상 케이스이므로 debug 수준만 기록
            import logging as _logging
            _logging.getLogger(__name__).debug("minio stat miss %s/%s: %s", bucket, key, exc)
            return False

    def uri(self, bucket: str, key: str) -> str:
        return f"s3://{bucket}/{key}"

    # --- #36: 객체 열거·삭제 (보존정책·재처리·마이그레이션용) -----------------

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        # list_objects(prefix, recursive) 로 키를 열거. 버킷 미존재 시 빈 목록.
        if not self._client.bucket_exists(bucket):
            return []
        prefix = self._norm_key(prefix) if prefix else ""
        objs = self._client.list_objects(bucket, prefix=prefix, recursive=True)
        return sorted(o.object_name for o in objs)

    def delete(self, bucket: str, key: str) -> None:
        # remove_object — 없는 키도 예외 없이 멱등 처리되는 S3 시맨틱. 키는 정규화/검증 후 삭제.
        self._client.remove_object(bucket, self._norm_key(key))
