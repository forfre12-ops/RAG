"""P2-C5: Secrets Manager 추상화.

운영 환경에서는 env 직접 read 금지 — 본 모듈이 backend(Vault/AWS SM/GCP SM/env) 추상화.

Usage:
    sm = get_secrets_manager()
    api_key = sm.get("api_key")
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


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

    def __init__(self, *, url: str, token: str, mount: str = "secret", path: str = "lloydk"):
        self._url = url
        self._token = token
        self._mount = mount
        self._path = path
        self._client = None
        self._cache: dict[str, str] = {}

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

    def _load_all(self) -> dict[str, str]:
        if self._cache:
            return self._cache
        c = self._ensure()
        resp = c.secrets.kv.v2.read_secret_version(path=self._path, mount_point=self._mount)
        data = resp.get("data", {}).get("data", {}) or {}
        self._cache = {str(k): str(v) for k, v in data.items()}
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

    def __init__(self, *, secret_id: str, region: str = "ap-northeast-2"):
        self._secret_id = secret_id
        self._region = region
        self._client = None
        self._cache: dict[str, str] = {}

    def _ensure(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError as e:
            raise RuntimeError("AwsSecretsManager requires boto3 — pip install boto3") from e
        self._client = boto3.client("secretsmanager", region_name=self._region)
        return self._client

    def _load_all(self) -> dict[str, str]:
        if self._cache:
            return self._cache
        c = self._ensure()
        resp = c.get_secret_value(SecretId=self._secret_id)
        import json
        s = resp.get("SecretString", "{}")
        data = json.loads(s) if s else {}
        self._cache = {str(k): str(v) for k, v in data.items()}
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
