"""CORS middleware tests.

These tests use a tiny FastAPI app with the same CORSMiddleware options instead
of importing the full Koipa API app. That keeps the suite fast and avoids model
or router startup work when only CORS behavior is under test.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from koipa.config import Settings


def _allowed_origin(origins: list[str]) -> str:
    return "https://example.com" if origins == ["*"] else origins[0]


def _cors_app(origins: list[str]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        allow_credentials=False,
    )

    @app.get("/ping")
    def ping():
        return {"ok": True}

    return app


def test_cors_preflight_options_allows_origin():
    origins = Settings.model_fields["cors_allow_origins"].default
    origin = _allowed_origin(origins)
    with TestClient(_cors_app(origins)) as cli:
        r = cli.options(
            "/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )

    assert r.status_code in (200, 204)
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert headers["access-control-allow-origin"] in {"*", origin}
    assert "access-control-allow-methods" in headers


def test_cors_preflight_allows_patch():
    origins = Settings.model_fields["cors_allow_origins"].default
    origin = _allowed_origin(origins)
    with TestClient(_cors_app(origins)) as cli:
        response = cli.options(
            "/ping",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "PATCH",
            },
        )

    assert response.status_code in (200, 204)
    assert "PATCH" in response.headers["access-control-allow-methods"]


def test_application_cors_configuration_allows_patch():
    """Keep the behavior fixture aligned with the middleware on the real app."""
    from koipa.api.app import app

    middleware = next(item for item in app.user_middleware if item.cls is CORSMiddleware)
    assert "PATCH" in middleware.kwargs["allow_methods"]


def test_cors_allowed_origin_returns_header():
    origins = Settings.model_fields["cors_allow_origins"].default
    with TestClient(_cors_app(origins)) as cli:
        r = cli.get("/ping", headers={"Origin": _allowed_origin(origins)})

    assert r.status_code == 200
    headers = {k.lower(): v for k, v in r.headers.items()}
    assert "access-control-allow-origin" in headers
    assert "access-control-allow-credentials" not in headers


def test_cors_disallowed_origin_no_header():
    with TestClient(_cors_app(["https://only.allowed.example"])) as cli:
        r = cli.get("/ping", headers={"Origin": "https://evil.example.com"})
        r_allowed = cli.get("/ping", headers={"Origin": "https://only.allowed.example"})

    headers = {k.lower(): v for k, v in r.headers.items()}
    assert r.status_code == 200
    assert "access-control-allow-origin" not in headers

    allowed_headers = {k.lower(): v for k, v in r_allowed.headers.items()}
    assert allowed_headers["access-control-allow-origin"] == "https://only.allowed.example"


def test_cors_settings_field_default_wildcard():
    assert Settings.model_fields["cors_allow_origins"].default == ["*"]
