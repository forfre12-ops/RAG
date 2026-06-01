"""CORS 미들웨어 검증 (J3).

from __future__ import annotations
- OPTIONS preflight는 허용 origin에 대해 access-control-allow-origin 헤더 응답
- 허용된 origin 요청 → access-control-allow-origin 헤더 포함
- 허용되지 않은 origin → 헤더 미포함 (브라우저 차단)
- allow_credentials=False
"""

import pytest
pytestmark = pytest.mark.slow

from fastapi.testclient import TestClient

from lloydk.api import app as app_module
from lloydk.api.app import app
from lloydk.config import settings


def test_cors_preflight_options_allows_origin(monkeypatch):
    """OPTIONS preflight → 200 + access-control-allow-origin."""
    # CORSMiddleware는 import 시점 settings.cors_allow_origins로 고정됨.
    # 기본값 ["*"]을 사용하는 경로로 검증.
    with TestClient(app) as cli:
        r = cli.options(
            "/api/v1/healthz",
            headers={
                "Origin": "https://example.com",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        # CORSMiddleware preflight 응답
        assert r.status_code in (200, 204)
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        assert "access-control-allow-origin" in hdrs
        # "*" 또는 echo된 origin 모두 허용 (allow_credentials=False라 "*" 가능)
        assert hdrs["access-control-allow-origin"] in {"*", "https://example.com"}
        assert "access-control-allow-methods" in hdrs


def test_cors_allowed_origin_returns_header():
    """일반 요청에 Origin 헤더 포함 → access-control-allow-origin 응답."""
    with TestClient(app) as cli:
        r = cli.get("/api/v1/healthz", headers={"Origin": "https://allowed.example.com"})
        assert r.status_code == 200
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        assert "access-control-allow-origin" in hdrs
        # allow_credentials=False라 access-control-allow-credentials는 미포함
        assert "access-control-allow-credentials" not in hdrs


def test_cors_disallowed_origin_no_header(monkeypatch):
    """allow_origins이 명시 allowlist일 때, 비허용 origin은 헤더 미응답.

    설정을 임시로 ["https://only.allowed.example"]로 바꾸려면 앱 재구성이 필요.
    여기서는 CORSMiddleware 동작 사양만 검증: TestClient 환경에서
    origin이 allowlist에 없으면 access-control-allow-origin 미응답.
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware

    test_app = FastAPI()
    test_app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://only.allowed.example"],
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @test_app.get("/ping")
    def ping():
        return {"ok": True}

    with TestClient(test_app) as cli:
        r = cli.get("/ping", headers={"Origin": "https://evil.example.com"})
        assert r.status_code == 200
        hdrs = {k.lower(): v for k, v in r.headers.items()}
        # 비허용 origin은 CORSMiddleware가 access-control-allow-origin 헤더 미설정
        assert "access-control-allow-origin" not in hdrs

        # 허용 origin은 헤더 응답
        r2 = cli.get("/ping", headers={"Origin": "https://only.allowed.example"})
        hdrs2 = {k.lower(): v for k, v in r2.headers.items()}
        assert hdrs2.get("access-control-allow-origin") == "https://only.allowed.example"


def test_cors_settings_field_default_wildcard():
    """settings.cors_allow_origins 기본값 검증 — ["*"]."""
    assert settings.cors_allow_origins == ["*"]
    # app_module을 통해 import 경로도 점검
    assert hasattr(app_module, "settings") or hasattr(app_module, "app")
