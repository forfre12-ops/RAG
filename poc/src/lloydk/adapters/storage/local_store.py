"""로컬 파일시스템 백엔드 — MinIO와 동일 API."""

from __future__ import annotations

from pathlib import Path


class LocalStorage:
    name = "local"

    def __init__(self, root: str = ".storage") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, bucket: str, key: str) -> Path:
        p = self.root / bucket / key
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def put(self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        p = self._path(bucket, key)
        p.write_bytes(data)
        return self.uri(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self._path(bucket, key).read_bytes()

    def exists(self, bucket: str, key: str) -> bool:
        return self._path(bucket, key).exists()

    def uri(self, bucket: str, key: str) -> str:
        return f"file://{(self.root / bucket / key).as_posix()}"
