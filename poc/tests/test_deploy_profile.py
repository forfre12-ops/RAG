"""배포 프로파일 단위 테스트.

검증 항목:
1. apply_profile_defaults가 4 프로파일 각각의 default를 정확히 채운다.
2. 명시값(env)은 프로파일 default를 override한다.
3. 알 수 없는 프로파일은 경고 후 skip (예외 X).
4. 빈 프로파일은 no_profile 상태 반환.
5. training 라우터는 enable_training=False면 OpenAPI에서 사라진다.
6. training 라우터는 enable_training=True면 등록된다.
"""

from __future__ import annotations

import importlib

import pytest

from lloydk.config import (
    DEPLOY_PROFILES,
    Settings,
    _PROFILE_DEFAULTS,
    apply_profile_defaults,
)


@pytest.mark.parametrize("profile", DEPLOY_PROFILES)
def test_profile_applies_defaults(profile: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # 환경변수 청소 — 명시값 간섭 제거
    for field in _PROFILE_DEFAULTS[profile].keys():
        monkeypatch.delenv(field.upper(), raising=False)
    # Settings는 .env도 자동 로드. 테스트 격리 위해 env_file 끄고 인스턴스화.
    monkeypatch.setenv("PYDANTIC_SETTINGS_NO_ENV_FILE", "1")

    s = Settings(deploy_profile=profile)
    sources = apply_profile_defaults(s)

    assert sources["_status"] == "ok"
    assert sources["_profile"] == profile
    for field, default in _PROFILE_DEFAULTS[profile].items():
        assert getattr(s, field) == default, f"{profile}.{field}"
        assert sources[field] == "profile"


def test_explicit_env_wins_over_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    # lite-noapi default는 llm_provider=noop. env로 anthropic 명시 시 그대로 유지.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    s = Settings(deploy_profile="lite-noapi", llm_provider="anthropic")
    sources = apply_profile_defaults(s)

    assert s.llm_provider == "anthropic"
    assert sources["llm_provider"] == "explicit"
    # 다른 필드는 그대로 default
    assert s.embedding_provider == "hash"
    assert sources["embedding_provider"] == "profile"


def test_unknown_profile_is_skipped() -> None:
    s = Settings(deploy_profile="weird-mode")
    sources = apply_profile_defaults(s)
    assert sources["_status"].startswith("unknown_profile:")


def test_empty_profile_is_noop() -> None:
    s = Settings(deploy_profile="")
    sources = apply_profile_defaults(s)
    assert sources["_status"] == "no_profile"


@pytest.mark.parametrize(
    "profile,training_expected",
    [
        ("lite-noapi", False),
        ("lite-cloud", False),
        ("onprem-local", False),
        ("full-train", True),
    ],
)
def test_training_router_visibility(
    profile: str, training_expected: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    """프로파일별 /api/v1/training/* 라우터 등록 여부.

    app.py가 settings.enable_training을 import-time에 평가하므로
    settings 모듈 reload + app 모듈 reload 순서로 강제 재로딩.

    teardown 필수 — reload는 monkeypatch가 풀어주지 않으므로 env 정리 후
    한 번 더 reload하여 settings.poc_mode/enable_training을 원상복구.
    안 그러면 후속 테스트(test_smoke 등)가 import-time에 production
    자격증명 차단에 걸림.
    """
    monkeypatch.setenv("SLOWAPI_SKIP_DOTENV", "1")
    monkeypatch.setenv("DEPLOY_PROFILE", profile)
    monkeypatch.delenv("ENABLE_TRAINING", raising=False)

    import lloydk.config as cfg_mod
    importlib.reload(cfg_mod)
    import lloydk.api.app as app_mod
    importlib.reload(app_mod)

    try:
        paths = {route.path for route in app_mod.app.routes}
        # training router의 실제 path는 /api/v1/train, /api/v1/train/jobs 등
        training_paths = {p for p in paths if p.startswith("/api/v1/train")}

        if training_expected:
            assert training_paths, f"{profile}: 학습 라우터가 등록되어야 함"
        else:
            assert not training_paths, f"{profile}: 학습 라우터가 노출되면 안됨 — {training_paths}"
    finally:
        # env 원상복구 후 모듈 재reload — 후속 테스트의 settings 오염 차단
        monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
        importlib.reload(cfg_mod)
        importlib.reload(app_mod)
