"""D1: secrets_manager → settings 보충 wiring 검증.

LLOYDK_SECRETS_BACKEND 환경별 (env/vault/aws) sm 객체가 잘 만들어지는지,
fill_from_secrets_manager가 빈 자리만 보충하고 채워진 자리는 보존하는지.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import lloydk.config as config_mod
from lloydk.services.secrets_manager import (
    EnvSecretsManager,
    SecretsManager,
    get_secrets_manager,
)


def test_env_backend_default():
    os.environ.pop("LLOYDK_SECRETS_BACKEND", None)
    # 캐시된 _default 초기화 (모듈 글로벌)
    import lloydk.services.secrets_manager as sm_mod
    sm_mod._default = None
    sm = get_secrets_manager()
    assert isinstance(sm, EnvSecretsManager)
    assert sm.name == "env"


def test_env_backend_reads_environment(monkeypatch):
    monkeypatch.setenv("LLOYDK_TEST_VAR", "value-from-env")
    import lloydk.services.secrets_manager as sm_mod
    sm_mod._default = None
    sm = get_secrets_manager()
    assert sm.get("LLOYDK_TEST_VAR") == "value-from-env"
    assert sm.get("LLOYDK_MISSING_VAR", "fallback") == "fallback"


def test_env_backend_get_required_raises_when_missing():
    sm = EnvSecretsManager()
    try:
        sm.get_required("LLOYDK_DEFINITELY_NOT_SET_XYZ")
    except RuntimeError as exc:
        assert "secret not found" in str(exc)
    else:
        raise AssertionError("get_required는 빈 값에 RuntimeError를 던져야 함")


# ---------------------------------------------------------------------------
# fill_from_secrets_manager — 빈 자리만 보충
# ---------------------------------------------------------------------------


class _FakeSM:
    name = "fake"

    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)

    def get_required(self, key):
        v = self.get(key)
        if not v:
            raise RuntimeError(f"missing {key}")
        return v


def test_fill_skips_already_set_fields():
    """env로 api_key가 이미 채워져 있으면 SM 값으로 덮어쓰지 않는다."""
    config_mod._SECRETS_FILLED = False
    config_mod.settings.api_key = "env-key"
    config_mod.settings.minio_secret_key = ""
    config_mod.settings.anthropic_api_key = ""

    fake = _FakeSM(
        LLOYDK_API_KEY="sm-key-should-be-ignored",
        LLOYDK_MINIO_SECRET_KEY="sm-minio",
    )
    with patch("lloydk.services.secrets_manager.get_secrets_manager", return_value=fake):
        sources = config_mod.fill_from_secrets_manager()

    assert config_mod.settings.api_key == "env-key"  # 보존
    assert config_mod.settings.minio_secret_key == "sm-minio"  # SM에서 보충
    assert sources["api_key"] == "env"
    assert sources["minio_secret_key"].startswith("sm-")


def test_fill_idempotent_second_call_noop():
    config_mod._SECRETS_FILLED = False
    config_mod.settings.api_key = ""

    fake = _FakeSM(LLOYDK_API_KEY="sm-key")
    with patch("lloydk.services.secrets_manager.get_secrets_manager", return_value=fake):
        sources_1 = config_mod.fill_from_secrets_manager()
    assert config_mod.settings.api_key == "sm-key"
    assert sources_1["api_key"].startswith("sm-")

    # 두 번째 호출은 이미 fill 됐음을 표시만 하고 settings 안 건드림
    config_mod.settings.api_key = "manually-changed"
    sources_2 = config_mod.fill_from_secrets_manager()
    assert sources_2.get("_status") == "already_filled"
    assert config_mod.settings.api_key == "manually-changed"


def test_assert_production_credentials_uses_sm_fill():
    """poc_mode=full + env 비었지만 SM에 있으면 통과."""
    config_mod._SECRETS_FILLED = False
    config_mod.settings.api_key = ""
    config_mod.settings.minio_secret_key = ""
    original_mode = config_mod.settings.poc_mode
    config_mod.settings.poc_mode = "full"

    fake = _FakeSM(
        LLOYDK_API_KEY="sm-key",
        LLOYDK_MINIO_SECRET_KEY="sm-minio",
    )
    try:
        with patch("lloydk.services.secrets_manager.get_secrets_manager", return_value=fake):
            config_mod.assert_production_credentials()
        # 통과 = 예외 없음
    finally:
        config_mod.settings.poc_mode = original_mode


def test_assert_production_credentials_raises_when_missing_everywhere():
    config_mod._SECRETS_FILLED = False
    config_mod.settings.api_key = ""
    config_mod.settings.minio_secret_key = ""
    original_mode = config_mod.settings.poc_mode
    config_mod.settings.poc_mode = "full"

    fake = _FakeSM()  # 빈 SM
    # TESTING/PYTEST_CURRENT_TEST를 falsy로 만들어 운영 경로(raise) 실행.
    # 10cea2e에서 추가된 pytest 환경 skip을 우회하여 함수 자체 동작을 검증.
    try:
        with patch("lloydk.services.secrets_manager.get_secrets_manager", return_value=fake), \
             patch.dict(os.environ, {"TESTING": "", "PYTEST_CURRENT_TEST": ""}):
            try:
                config_mod.assert_production_credentials()
            except RuntimeError as exc:
                assert "LLOYDK_API_KEY" in str(exc)
            else:
                raise AssertionError("운영 모드 + 모든 자격증명 부재 → RuntimeError 기대")
    finally:
        config_mod.settings.poc_mode = original_mode


def test_assert_production_credentials_blocks_trust_actor_role_header():
    """운영 모드 + API_KEY_TRUST_ACTOR_ROLE_HEADER=True → RuntimeError (위조 차단)."""
    config_mod._SECRETS_FILLED = True  # SM 보충 skip
    original_mode = config_mod.settings.poc_mode
    original_trust = config_mod.settings.api_key_trust_actor_role_header
    original_api = config_mod.settings.api_key
    original_minio = config_mod.settings.minio_secret_key
    original_cors = config_mod.settings.cors_allow_origins
    # 자격증명·CORS는 통과시키고, trust_header 검사에만 도달하도록 구성.
    config_mod.settings.poc_mode = "full"
    config_mod.settings.api_key = "x"
    config_mod.settings.minio_secret_key = "y"
    config_mod.settings.cors_allow_origins = ["https://app.example.com"]
    config_mod.settings.api_key_trust_actor_role_header = True
    try:
        with patch.dict(os.environ, {"TESTING": "", "PYTEST_CURRENT_TEST": "", "RATE_LIMIT_DISABLED": ""}):
            try:
                config_mod.assert_production_credentials()
            except RuntimeError as exc:
                assert "ACTOR_ROLE" in str(exc)
            else:
                raise AssertionError("운영 모드 + trust_header=True → RuntimeError 기대")
    finally:
        config_mod.settings.poc_mode = original_mode
        config_mod.settings.api_key_trust_actor_role_header = original_trust
        config_mod.settings.api_key = original_api
        config_mod.settings.minio_secret_key = original_minio
        config_mod.settings.cors_allow_origins = original_cors


def test_secrets_manager_protocol():
    """EnvSecretsManager가 SecretsManager Protocol 만족."""
    assert isinstance(EnvSecretsManager(), SecretsManager)
