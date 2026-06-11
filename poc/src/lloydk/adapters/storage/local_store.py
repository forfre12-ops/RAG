"""로컬 파일시스템 백엔드 — MinIO와 동일 API."""

from __future__ import annotations

from pathlib import Path


class LocalStorage:
    name = "local"

    def __init__(self, root: str = ".storage") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        # 경로 탈출 검증 기준 — 심볼릭 링크까지 해소한 절대 루트.
        self._root_resolved = self.root.resolve()

    def _path(self, bucket: str, key: str) -> Path:
        p = self.root / bucket / key
        # 폐쇄망 기본 백엔드 — bucket/key 에 ../ 가 섞이면 루트 밖에 쓰일 수 있다.
        # 최종 해소 경로가 루트 하위가 아니면 거부(fail-closed).
        resolved = p.resolve()
        try:
            resolved.relative_to(self._root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"path traversal blocked: key escapes storage root "
                f"(bucket={bucket!r}, key={key!r})"
            ) from exc
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
