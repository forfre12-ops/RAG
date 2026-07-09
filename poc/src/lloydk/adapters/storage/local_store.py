"""로컬 파일시스템 백엔드 — MinIO와 동일 API."""

from __future__ import annotations

import os
import tempfile
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

    def _path_readonly(self, bucket: str, key: str) -> Path:
        p = self.root / bucket / key
        resolved = p.resolve()
        try:
            resolved.relative_to(self._root_resolved)
        except ValueError as exc:
            raise ValueError(
                f"path traversal blocked: key escapes storage root "
                f"(bucket={bucket!r}, key={key!r})"
            ) from exc
        return p

    def put(self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        p = self._path(bucket, key)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{p.name}.", suffix=".tmp", dir=str(p.parent))
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, p)
        finally:
            try:
                os.unlink(tmp_name)
            except FileNotFoundError:
                pass
        return self.uri(bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self._path_readonly(bucket, key).read_bytes()

    def exists(self, bucket: str, key: str) -> bool:
        return self._path_readonly(bucket, key).exists()

    def uri(self, bucket: str, key: str) -> str:
        return f"file://{(self.root / bucket / key).as_posix()}"

    # --- #36: 객체 열거·삭제 (보존정책·재처리·마이그레이션용) -----------------

    def list_keys(self, bucket: str, prefix: str = "") -> list[str]:
        # 버킷 디렉토리를 walk 해 prefix 매칭 키를 열거. 키는 버킷 루트 기준 POSIX 상대경로.
        # bucket 자체에 ../ 가 섞여도 루트를 못 벗어나게 _path(_root 검증)를 재사용.
        # _path 는 bucket/key 합성 경로를 검증하므로, 더미 키로 검증만 거친 뒤 부모(=버킷 dir)를 취한다.
        bucket_dir = self._path(bucket, "__list__").parent
        if not bucket_dir.is_dir():
            return []
        keys: list[str] = []
        for p in bucket_dir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(bucket_dir).as_posix()
            if rel.startswith(prefix):
                keys.append(rel)
        return sorted(keys)

    def delete(self, bucket: str, key: str) -> None:
        # path-traversal 방어는 기존 _path 검증을 그대로 재사용. 멱등 — 없는 키는 무시.
        p = self._path(bucket, key)
        try:
            p.unlink()
        except FileNotFoundError:
            pass
