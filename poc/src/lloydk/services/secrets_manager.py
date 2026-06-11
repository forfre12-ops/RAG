"""P2-C5: Secrets Manager 추상화.

운영 환경에서는 env 직접 read 금지 — 본 모듈이 backend(Vault/AWS SM/GCP SM/env) 추상화.

Usage:
    sm = get_secrets_manager()
    api_key = sm.get("api_key")
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# L-secrets-ttl (W3-D): Vault/AWS 백엔드가 secret을 프로세스 lifetime 무기한 캐싱하면
# 키 로테이션이 무력화된다. TTL이 지나면 캐시를 무효화하고 백엔드를 다시 읽는다.
# env LLOYDK_SECRETS_CACHE_TTL_SEC로 조정(기본 300초). 0 이하면 캐시 비활성(매번 조회).
def _default_cache_ttl() -> float:
    raw = os.getenv("LLOYDK_SECRETS_CACHE_TTL_SEC", "300").strip()
    try:
        return float(raw)
    except ValueError:
        return 300.0


@runtime_checkable
class SecretsManager(Protocol):
    name: str

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]: ...
    def get_required(self, key: str) -> str: ...


class EnvSecretsManager:
    """기본 — 환경변수 그대로 read. 로컬·CI용."""

    name = "env"

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return os.getenv(key, default)

    def get_required(self, key: str) -> str:
        v = os.getenv(key)
        if not v:
            raise RuntimeError(f"secret not found: {key}")
        return v


class VaultSecretsManager:
    """HashiCorp Vault KV v2 백엔드.

    의존성:
        pip install hvac
    """

    name = "vault"

    def __init__(
        self,
        *,
        url: str,
        token: str,
        mount: str = "secret",
        path: str = "lloydk",
        cache_ttl: Optional[float] = None,
    ):
        self._url = url
        self._token = token
        self._mount = mount
        self._path = path
        self._client = None
        # None 센티넬 = 미로드. 빈 dict({})는 "로드했으나 secret 없음" — 구분해야
        # 빈 결과를 매번 재조회하지 않는다(기존 `if self._cache:`는 빈 dict를 미로드로 오인).
        self._cache: Optional[dict[str, str]] = None
        self._cache_at: float = 0.0
        self._cache_ttl: float = _default_cache_ttl() if cache_ttl is None else cache_ttl

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            import hvac  # type: ignore
        except ImportError as e:
            raise RuntimeError("VaultSecretsManager requires hvac — pip install hvac") from e
        self._client = hvac.Client(url=self._url, token=self._token)
        if not self._client.is_authenticated():
            raise RuntimeError("Vault authentication failed")
        return self._client

    def _cache_fresh(self) -> bool:
        if self._cache is None:
            return False
        if self._cache_ttl <= 0:
            return False  # TTL 0 → 캐시 비활성, 항상 재조회
        return (time.monotonic() - self._cache_at) < self._cache_ttl

    def invalidate(self) -> None:
        """캐시 즉시 무효화 — 키 로테이션 후 강제 재로드용."""
        self._cache = None
        self._cache_at = 0.0

    def _load_all(self) -> dict[str, str]:
        if self._cache_fresh():
            return self._cache  # type: ignore[return-value]
        c = self._ensure()
        resp = c.secrets.kv.v2.read_secret_version(path=self._path, mount_point=self._mount)
        data = resp.get("data", {}).get("data", {}) or {}
        self._cache = {str(k): str(v) for k, v in data.items()}
        self._cache_at = time.monotonic()
        return self._cache

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        try:
            return self._load_all().get(key, default)
        except Exception as e:  # noqa: BLE001
            logger.warning("vault get failed for %s: %s", key, e)
            return default

    def get_required(self, key: str) -> str:
        v = self.get(key)
        if not v:
            raise RuntimeError(f"secret not found in vault: {key}")
        return v


class AwsSecretsManager:
    """AWS Secrets Manager 백엔드 (JSON 단일 secret).

    의존성:
        pip install boto3
    """

    name = "aws"

    def __init__(
        self,
        *,
        secret_id: str,
        region: str = "ap-northeast-2",
        cache_ttl: Optional[float] = None,
    ):
        self._secret_id = secret_id
        self._region = region
        self._client = None
        # None 센티넬 = 미로드 (빈 dict와 구분 — VaultSecretsManager 참고).
        self._cache: Optional[dict[str, str]] = None
        self._cache_at: float = 0.0
        self._cache_ttl: float = _default_cache_ttl() if cache_ttl is None else cache_ttl

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError("AwsSecretsManager requires boto3 — pip install boto3") from e
        self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def _cache_fresh(self) -> bool:
        if self._cache is None:
            return False
        if self._cache_ttl <= 0:
            return False
        return (time.monotonic() - self._cache_at) < self._cache_ttl

    def invalidate(self) -> None:
        """캐시 즉시 무효화 — 키 로테이션 후 강제 재로드용."""
        self._cache = None
        self._cache_at = 0.0

    def _load_all(self) -> dict[str, str]:
        if self._cache_fresh():
            return self._cache  # type: ignore[return-value]
        c = self._ensure()
        resp = c.get_secret_value(SecretId=self._secret_id)
        import json
        s = resp.get("SecretString", "{}")
        data = json.loads(s) if s else {}
        self._cache = {str(k): str(v) for k, v in data.items()}
        self._cache_at = time.monotonic()
        return self._cache

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        try:
            return self._load_all().get(key, default)
        except Exception as e:  # noqa: BLE001
            logger.warning("aws sm get failed for %s: %s", key, e)
            return default

    def get_required(self, key: str) -> str:
        v = self.get(key)
        if not v:
            raise RuntimeError(f"secret not found in aws sm: {key}")
        return v


_default: SecretsManager | None = None


def get_secrets_manager() -> SecretsManager:
    """settings.secrets_backend 기반 팩토리. (env | vault | aws)"""
    global _default
    if _default is not None:
        return _default

    backend = os.getenv("LLOYDK_SECRETS_BACKEND", "env").lower()
    if backend == "vault":
        _default = VaultSecretsManager(
            url=os.getenv("VAULT_ADDR", "http://localhost:8200"),
            token=os.getenv("VAULT_TOKEN", ""),
            mount=os.getenv("VAULT_MOUNT", "secret"),
            path=os.getenv("VAULT_PATH", "lloydk"),
        )
    elif backend == "aws":
        _default = AwsSecretsManager(
            secret_id=os.getenv("AWS_SECRET_ID", "lloydk/prod"),
            region=os.getenv("AWS_REGION", "ap-northeast-2"),
        )
    else:
        _default = EnvSecretsManager()
    return _default


def invalidate_secrets_cache() -> None:
    """전역 secrets manager의 캐시를 무효화 — 키 로테이션 후 강제 재로드용.

    백엔드가 invalidate()를 지원하면(Vault/AWS) 호출. env 백엔드는 캐시가 없어 no-op.
    """
    sm = _default
    if sm is not None:
        invalidate = getattr(sm, "invalidate", None)
        if callable(invalidate):
            invalidate()
