"""Object Storage — MinIO / SeaweedFS / Local FS.

build_storage()가 settings.storage_backend(local | minio | seaweedfs)를 단일 진입점으로
해석한다. 원격 백엔드 초기화 실패 시 LocalStorage로 폴백(드라이런·테스트 비파괴).
"""

from __future__ import annotations

import logging

from lloydk.adapters.storage.base import ObjectStorage
from lloydk.adapters.storage.local_store import LocalStorage

__all__ = ["ObjectStorage", "LocalStorage", "build_storage"]

logger = logging.getLogger(__name__)


def _build_minio() -> ObjectStorage:
    from lloydk.adapters.storage.minio_store import MinioStorage  # noqa: PLC0415

    return MinioStorage()


def _build_seaweedfs() -> ObjectStorage:
    from lloydk.adapters.storage.seaweedfs_store import SeaweedFSStore  # noqa: PLC0415
    from lloydk.config import settings  # noqa: PLC0415

    endpoint = getattr(settings, "storage_endpoint", "") or "http://localhost:8333"
    return SeaweedFSStore(
        endpoint_url=endpoint,
        access_key=getattr(settings, "minio_access_key", ""),
        secret_key=getattr(settings, "minio_secret_key", ""),
        verify_tls=getattr(settings, "storage_verify_tls", True),
    )


def build_storage(
    *,
    force_local: bool = False,
    local_root: str | None = None,
    backend: str | None = None,
) -> ObjectStorage:
    """storage_backend(local | minio | seaweedfs) 기준 단일 팩토리.

    Args:
        force_local: True면 backend 설정 무시하고 LocalStorage.
        backend: 명시 오버라이드(테스트용). None이면 settings.storage_backend.

    원격 백엔드(minio/seaweedfs) 초기화 실패 시 RuntimeWarning과 함께 LocalStorage로
    폴백한다(드라이런·테스트 비파괴). 알 수 없는 backend 값도 안전하게 local로 폴백.
    """
    if force_local:
        return LocalStorage(root=local_root or ".storage")

    if backend is None:
        from lloydk.config import settings  # noqa: PLC0415

        backend = getattr(settings, "storage_backend", "minio")
    name = (backend or "minio").lower()

    if name == "local":
        return LocalStorage(root=local_root or ".storage")

    if name == "minio":
        builder = _build_minio
    elif name in ("seaweedfs", "s3"):
        builder = _build_seaweedfs
    else:
        logger.warning("[storage] unknown backend %r — falling back to LocalStorage", name)
        return LocalStorage(root=local_root or ".storage")

    try:
        return builder()
    except Exception as exc:  # noqa: BLE001
        import warnings  # noqa: PLC0415

        warnings.warn(
            f"[storage] {name} unavailable: {exc}. Falling back to LocalStorage.",
            RuntimeWarning,
            stacklevel=2,
        )
        return LocalStorage(root=local_root or ".storage")
