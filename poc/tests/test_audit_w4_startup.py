"""W4: assert_production_auth_config()가 app lifespan startup에 배선됐는지 검증.

이전(Wave3 D)에 함수는 정의됐으나 어디서도 호출되지 않던 갭을 닫는다.
운영(poc_mode=full)에서 jwt 모드인데 iss/aud 미설정이면 startup에서 fail-fast 한다.
dev/test/dryrun(_is_production=False)에서는 no-op이라 비파괴.
"""

from __future__ import annotations

import pytest

import lloydk.api._jwt_auth as _jwt_auth


def test_assert_production_auth_config_noop_in_test_env():
    # _is_production()=False(test/dryrun)면 raise 없이 즉시 반환해야 한다(비파괴).
    _jwt_auth.assert_production_auth_config()


@pytest.mark.slow  # TestClient lifespan 이 모델 warmup 을 트리거 → lite 제외
def test_lifespan_invokes_production_auth_config(monkeypatch):
    calls: list[int] = []
    # lifespan 내부의 지연 import가 패치된 속성을 호출하도록 모듈 속성 교체.
    monkeypatch.setattr(_jwt_auth, "assert_production_auth_config", lambda: calls.append(1))
    from fastapi.testclient import TestClient

    from lloydk.api.app import app

    with TestClient(app):  # __enter__ 가 lifespan startup 을 실행
        pass
    assert calls, "app lifespan 이 assert_production_auth_config 를 호출해야 한다"


def test_assert_production_auth_config_fails_fast_on_jwt_without_aud(monkeypatch):
    # 운영 + jwt 모드 + iss/aud 미설정 → confused-deputy 차단 위해 fail-fast.
    monkeypatch.setattr(_jwt_auth, "_is_production", lambda: True)
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "jwt", raising=False)
    monkeypatch.setattr(_jwt_auth.settings, "jwt_issuer", "", raising=False)
    monkeypatch.setattr(_jwt_auth.settings, "jwt_audience", "", raising=False)
    import pytest

    with pytest.raises(RuntimeError, match="confused deputy|JWT_ISSUER|JWT_AUDIENCE"):
        _jwt_auth.assert_production_auth_config()
