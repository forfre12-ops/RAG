"""Object Storage — MinIO (기본) / Local FS (드라이런)."""

from __future__ import annotations

from lloydk.adapters.storage.base import ObjectStorage
from lloydk.adapters.storage.local_store import LocalStorage

__all__ = ["ObjectStorage", "LocalStorage", "build_storage"]


def build_storage(*, force_local: bool = False, local_root: str | None = None) -> ObjectStorage:
    if force_local:
        return LocalStorage(root=local_root or ".storage")
    try:
        from lloydk.adapters.storage.minio_store import MinioStorage

        return MinioStorage()
    except Exception as exc:  # noqa: BLE001
        import warnings

        warnings.warn(
            f"[storage] MinIO unavailable: {exc}. Falling back to LocalStorage.",
            RuntimeWarning,
            stacklevel=2,
        )
        return LocalStorage(root=local_root or ".storage")
