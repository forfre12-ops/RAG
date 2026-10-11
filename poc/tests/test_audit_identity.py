from __future__ import annotations

from types import SimpleNamespace

from koipa.api.middleware import _audit_identity


def _request(*, actor=None, role=None, mode=None, headers=None):
    return SimpleNamespace(
        state=SimpleNamespace(auth_actor=actor, auth_role=role, auth_mode=mode),
        headers={k.lower(): v for k, v in (headers or {}).items()},
    )


def test_audit_identity_keeps_the_authenticated_person_over_any_header():
    request = _request(
        actor="reviewer-07", role="reviewer", mode="jwt",
        headers={"X-Actor-Id": "forged-user"},
    )
    assert _audit_identity(request) == ("reviewer-07", "reviewer")


def test_audit_identity_marks_shared_api_key_as_a_service_not_a_blank_person():
    request = _request(role="admin", mode="api_key")
    assert _audit_identity(request) == ("service:api-key", "admin")


def test_audit_identity_keeps_a_declared_integration_principal():
    request = _request(role="kl_backend", mode="api_key", headers={"X-Actor-Id": "kl-portal"})
    assert _audit_identity(request) == ("kl-portal", "kl_backend")
