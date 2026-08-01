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


@pytest.mark.parametrize(
    "profile,incr_expected,admin_expected",
    [
        ("lite-noapi", False, False),     # 순수 추론 — 학습·콘솔 없음
        ("lite-cloud", False, False),     # 데모(콘솔은 demo_console_enabled 축으로 켜짐)
        ("onprem-local", True, True),     # 고객사 — 야간 증분 재학습 + 하드닝 관리 콘솔
        ("full-train", False, True),      # 지재원 — enable_training 경로 학습 + 관리 콘솔
    ],
)
def test_incremental_retrain_and_admin_console_gated_by_profile(
    profile: str, incr_expected: bool, admin_expected: bool
) -> None:
    """[고객사 학습 2026-07] enable_incremental_retrain·serve_admin_console 프로파일 default.

    고객사(onprem-local)만 야간 증분 재학습 ON, 지재원은 enable_training 경로라 증분 플래그 OFF.
    관리 콘솔(serve_admin_console)은 두 프로덕션 노드(onprem-local·full-train) 모두 ON —
    demo_console_enabled(파괴적 purge)와 분리된 축이라 콘솔만 노출하고 purge 는 계속 OFF.
    """
    s = Settings(deploy_profile=profile, _env_file=None)
    apply_profile_defaults(s)
    assert s.enable_incremental_retrain is incr_expected, f"{profile}: enable_incremental_retrain"
    assert s.serve_admin_console is admin_expected, f"{profile}: serve_admin_console"
    # 안전 불변식: 야간 증분 재학습을 켠 노드도 파괴적 purge(demo_console_enabled)는 열지 않는다.
    if incr_expected:
        assert s.demo_console_enabled is False, f"{profile}: 증분 재학습 노드에 purge 노출 금지"


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
    "profile,en_train,en_incr,training_expected",
    [
        ("lite-noapi", False, False, False),    # 순수 추론 — 학습 노드 아님
        ("lite-cloud", False, False, False),
        ("onprem-local", False, True, True),    # 고객사 야간 증분 재학습 노드 → 라우터 등록
        ("full-train", True, False, True),      # 지재원 학습 노드 → 라우터 등록
        ("full-train", True, True, True),       # 둘 다 켜도 등록(OR)
        ("onprem-local", False, False, False),  # 두 플래그 모두 off → 미등록(음성 대조)
    ],
)
def test_training_router_visibility(
    profile: str, en_train: bool, en_incr: bool, training_expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """프로파일별 /api/v1/train* 라우터 등록 — 계약: enable_training OR enable_incremental_retrain.

    지재원(full-train)은 enable_training, 고객사(onprem-local)는 enable_incremental_retrain(야간
    증분 재학습, 2026-07)으로 학습 라우터가 등록된다. 순수 추론 노드(lite-*)는 미등록.
    app.py가 두 플래그를 import-time에 평가하므로 settings 모듈 reload + app 모듈 reload 순서로
    강제 재로딩. teardown 필수 — reload는 monkeypatch가 풀어주지 않으므로 env 정리 후 원상복구.
    """
    monkeypatch.setenv("SLOWAPI_SKIP_DOTENV", "1")
    monkeypatch.setenv("DEPLOY_PROFILE", profile)
    # [M-config-env] reload 경로의 Settings()는 레포 .env 를 읽고 explicit 판정이 강화돼 .env 값이
    # 프로파일을 이긴다. 이 테스트는 '두 학습 플래그 → 라우터 등록' OR 계약을 검증하므로 두 플래그를
    # os.environ 에 명시(explicit)해 프로파일 default·.env 간섭을 제거하고 파라미터대로 강제한다.
    monkeypatch.setenv("ENABLE_TRAINING", "true" if en_train else "false")
    monkeypatch.setenv("ENABLE_INCREMENTAL_RETRAIN", "true" if en_incr else "false")

    import lloydk.config as cfg_mod
    _orig_settings = cfg_mod.settings  # 원본 settings 객체 — import-bound 모듈(_jwt_auth 등 44개)이 참조
    importlib.reload(cfg_mod)
    import lloydk.api.app as app_mod
    importlib.reload(app_mod)

    try:
        # [starlette 호환] 신버전은 include_router 결과를 path 없는 `_IncludedRouter` 로 감싸서
        # app.routes 직접 순회로는 엔드포인트 path 를 얻을 수 없다(구버전 전제 코드가 AttributeError).
        # 내부 구조 대신 공개 API 인 openapi() 를 쓴다 — prefix 가 적용된 최종 path 를 주고,
        # "순수 추론 노드에서는 OpenAPI 에도 노출되지 않는다"는 이 테스트의 계약과도 정확히 일치한다.
        paths = set(app_mod.app.openapi().get("paths", {}))
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


def test_admin_console_mounts_without_enabling_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    """[하드닝 콘솔] serve_admin_console=True + demo_console_enabled=False → /demo 마운트되되
    파괴적 purge 는 계속 OFF. 관리 UI(검수→재학습→활성화)만 노출하고 데모/물리삭제 표면은 닫는다.

    마운트 축(serve_admin_console)과 purge 축(demo_console_enabled)이 분리됐음을 배선 레벨에서 잠근다.
    """
    monkeypatch.setenv("SLOWAPI_SKIP_DOTENV", "1")
    monkeypatch.setenv("DEPLOY_PROFILE", "onprem-local")
    # 두 축을 os.environ 에 명시(explicit)해 .env/프로파일 간섭 제거 — 하드닝 콘솔 조합을 강제.
    monkeypatch.setenv("SERVE_ADMIN_CONSOLE", "true")
    monkeypatch.setenv("DEMO_CONSOLE_ENABLED", "false")

    import lloydk.config as cfg_mod
    _orig_settings = cfg_mod.settings
    importlib.reload(cfg_mod)
    import lloydk.api.app as app_mod
    importlib.reload(app_mod)

    try:
        assert cfg_mod.settings.serve_admin_console is True
        assert cfg_mod.settings.demo_console_enabled is False   # purge 축은 OFF 유지
        # /demo 정적 마운트가 존재해야 한다(관리 콘솔 서빙).
        mount_paths = {getattr(r, "path", None) for r in app_mod.app.routes}
        assert "/demo" in mount_paths, f"관리 콘솔이 마운트돼야 함 — mounts={sorted(p for p in mount_paths if p)}"
    finally:
        monkeypatch.delenv("DEPLOY_PROFILE", raising=False)
        cfg_mod.settings = _orig_settings
        importlib.reload(app_mod)
