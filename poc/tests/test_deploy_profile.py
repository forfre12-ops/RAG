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
    # [Wave3 M-config-env] explicit 판정이 model_fields_set로 강화돼, 레포 .env가
    # 프로파일 필드를 명시하면 그 값이 정당하게 '우선'된다(.env 지정값 보존이 새 계약).
    # 이 테스트는 '미설정 키만 프로파일이 채운다'는 격리 동작을 검증하므로 .env를
    # 실제로 끈다(_env_file=None). 옛 PYDANTIC_SETTINGS_NO_ENV_FILE 플래그는 무효였음.
    s = Settings(deploy_profile=profile, _env_file=None)
    sources = apply_profile_defaults(s)

    assert sources["_status"] == "ok"
    assert sources["_profile"] == profile
    for field, default in _PROFILE_DEFAULTS[profile].items():
        assert getattr(s, field) == default, f"{profile}.{field}"
        assert sources[field] == "profile"


@pytest.mark.parametrize(
    "profile,expected",
    [
        ("lite-noapi", True),
        ("lite-cloud", True),
        ("onprem-local", False),
        ("full-train", False),
    ],
)
def test_demo_console_gated_by_profile(profile: str, expected: bool) -> None:
    """데모 콘솔·파괴적 POST /admin/demo/purge 노출은 하드닝 프로파일서 꺼져야 한다(SEC-2/3).

    demo_console_enabled 가 Settings 에 미선언이던 탓에 admin.py 의 getattr(...,True) 게이트와
    app.py /demo 마운트가 죽은 no-op(항상 True)였다 — 이제 데모/파일럿(lite-*)만 True, 고객사
    운영/모델공장(onprem-local·full-train)은 프로파일 default False 로 실제 비활성(마운트·purge 404).
    """
    s = Settings(deploy_profile=profile, _env_file=None)
    apply_profile_defaults(s)
    assert s.demo_console_enabled is expected, f"{profile}: demo_console_enabled"


def test_explicit_env_wins_over_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    # lite-noapi default는 llm_provider=noop. env로 anthropic 명시 시 그대로 유지.
    # [Wave3 M-config-env] 다른 프로파일 필드(embedding_provider 등)의 'default 채움'을
    # 검증하려면 레포 .env(EMBEDDING_PROVIDER=hf 등)와 격리해야 하므로 _env_file=None.
    # llm_provider는 kwarg로 명시 → model_fields_set에 포함 → explicit로 인식돼야 한다.
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    s = Settings(deploy_profile="lite-noapi", llm_provider="anthropic", _env_file=None)
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
    # [Wave3 M-config-env] 모듈 reload 경로의 Settings()는 레포 .env(ENABLE_TRAINING=false,
    # onprem-local)를 읽는다. explicit 판정이 강화돼 .env의 false가 프로파일을 정당하게
    # 이긴다(=새 계약). 따라서 ENABLE_TRAINING을 지우면 .env의 false가 남아 full-train도
    # 학습이 꺼진다. 이 테스트의 본 목적은 'settings.enable_training → train 라우터 등록'
    # 배선 검증이므로, 기대값을 os.environ에 명시(explicit)해 .env 간섭을 제거한다.
    monkeypatch.setenv("ENABLE_TRAINING", "true" if training_expected else "false")

    import lloydk.config as cfg_mod
    _orig_settings = cfg_mod.settings  # 원본 settings 객체 — import-bound 모듈(_jwt_auth 등 44개)이 참조
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
        # env 원상복구 후 원본 settings 객체를 '복원'한다. reload는 새 settings 객체를 만들어
        # 정체성이 달라지고, import-bound 모듈(_jwt_auth 등 44개)이 stale settings를 참조하게 돼
        # 후속 테스트(JWT 검증·reranker probe 등)를 결정론적으로 오염시킨다(테스트 순서 의존 red).
        # 원본 객체를 되돌린 뒤 app만 재빌드해 import-bound 참조와 정합시킨다.
        monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
        cfg_mod.settings = _orig_settings
        importlib.reload(app_mod)
