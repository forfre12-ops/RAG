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


def _strict_remote_storage_required() -> bool:
    try:
        from lloydk.config import settings  # noqa: PLC0415

        return str(getattr(settings, "poc_mode", "")).lower() == "full"
    except Exception:  # noqa: BLE001
        return False


def _build_minio() -> ObjectStorage:
    from lloydk.adapters.storage.minio_store import MinioStorage  # noqa: PLC0415

    try:
        return MinioStorage()
    except ModuleNotFoundError as exc:
        # minio 패키지는 optional extra [minio](폐쇄망 운영 기본은 local FS라 base 제외).
        # 미설치 상태로 minio backend 선택 시 원인을 명확히 — 호출부(_build_inner)가 이 메시지로
        # RuntimeWarning 후 LocalStorage 폴백한다(무음 아님).
        raise ModuleNotFoundError(
            "minio backend requires the 'minio' package — install with: "
            'pip install -e ".[minio]"  (폐쇄망 base는 local FS만; minio는 옵션)'
        ) from exc


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


def _build_inner(
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
        if _strict_remote_storage_required():
            raise RuntimeError(f"[storage] unknown backend {name!r} in full mode")
        logger.warning("[storage] unknown backend %r — falling back to LocalStorage", name)
        return LocalStorage(root=local_root or ".storage")

    try:
        return builder()
    except Exception as exc:  # noqa: BLE001
        if _strict_remote_storage_required():
            raise RuntimeError(f"[storage] {name} unavailable in full mode: {exc}") from exc
        import warnings  # noqa: PLC0415

        warnings.warn(
            f"[storage] {name} unavailable: {exc}. Falling back to LocalStorage.",
            RuntimeWarning,
            stacklevel=2,
        )
        return LocalStorage(root=local_root or ".storage")


def _maybe_encrypt(storage: ObjectStorage) -> ObjectStorage:
    """settings.storage_encryption_enabled면 EncryptingStorage로 래핑.

    대상 버킷(기본 documents-raw)만 at-rest 암호화한다. 키 미설정 시 EncryptingStorage가
    예외를 던져(fail-closed) 평문으로 조용히 떨어지지 않는다 — 운영 진입은
    assert_production_credentials가 키 존재를 사전 강제한다. settings 로드 실패·암호화
    비활성이면 원본 storage를 그대로 반환(드라이런·테스트 비파괴).
    """
    try:
        from lloydk.config import settings  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return storage
    if not getattr(settings, "storage_encryption_enabled", False):
        return storage
    from lloydk.adapters.storage.encrypted_store import EncryptingStorage  # noqa: PLC0415

    buckets = set(getattr(settings, "storage_encrypted_buckets", None) or ["documents-raw"])
    return EncryptingStorage(
        storage,
        key_material=getattr(settings, "storage_encryption_key", ""),
        buckets=buckets,
        strict_plaintext=_strict_remote_storage_required(),
    )


def build_storage(
    *,
    force_local: bool = False,
    local_root: str | None = None,
    backend: str | None = None,
) -> ObjectStorage:
    """storage_backend 기준 단일 팩토리 + 선택적 at-rest 암호화 래핑.

    내부 백엔드(_build_inner)를 구성한 뒤, settings.storage_encryption_enabled면
    EncryptingStorage로 감싸 대상 버킷(documents-raw 등)을 투명하게 암·복호화한다.
    호출부는 변경 없이 동일한 put/get 인터페이스를 쓴다.
    """
    return _maybe_encrypt(
        _build_inner(force_local=force_local, local_root=local_root, backend=backend)
    )
