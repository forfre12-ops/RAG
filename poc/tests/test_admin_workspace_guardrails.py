"""P0 admin-workspace privacy, authority, and explanation boundaries."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest
from fastapi import HTTPException


_ADMIN_HTML = (
    Path(__file__).resolve().parent.parent
    / "src"
    / "koipa"
    / "api"
    / "static"
    / "admin.html"
)


def test_admin_console_has_no_third_party_analytics_or_browser_beacon() -> None:
    """The console may call its same-origin API, never a tracking collector."""
    source = _ADMIN_HTML.read_text(encoding="utf-8").lower()

    for prohibited in (
        "google-analytics",
        "googletagmanager",
        "posthog",
        "mixpanel",
        "sentry.io",
        "hotjar",
        "amplitude",
        "navigator.sendbeacon",
    ):
        assert prohibited not in source


def test_rag_and_llm_are_explicitly_explanatory_not_grade_authority() -> None:
    source = _ADMIN_HTML.read_text(encoding="utf-8")

    assert source.count("RAG는 등급을 바꾸지 않고 참고 원문만 보여 줍니다.") >= 2
    assert "규정 참고 RAG 사용" in source
    assert "후보를 걸러내기만 하고" in source


def test_candidate_decision_route_is_server_role_guarded() -> None:
    """A browser-side role selector must not become the authorization boundary."""
    from koipa.api import golden

    source = inspect.getsource(golden.proxy_gold_candidate_decision)
    assert 'Depends(require_role("admin", "kl_backend", "reviewer"))' in source

    from koipa.api._rbac import require_role

    check = require_role("admin")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(check({"actor_role": "viewer", "actor_roles": ("viewer",)}))
    assert exc_info.value.status_code == 403
    assert asyncio.run(check({"actor_role": "admin", "actor_roles": ("admin",)}))["actor_role"] == "admin"
