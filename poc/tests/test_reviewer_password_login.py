"""검수자 아이디+비밀번호 로그인 — POST /golden/candidates/login.

사용자 요청(2026-10-02): 토큰을 붙여넣는 방식 대신 아이디+비밀번호로 로그인하게 해달라.
서버가 비밀번호를 확인한 뒤 **직접** 세션 토큰을 서명해 쿠키로 심는다 — 검수자는 토큰이라는
개념을 몰라도 된다. 기존 "토큰 붙여넣기" 경로(CONSOLE_LOGIN_PREFILL_TOKEN 등)는 그대로 둔다
(두 로그인 경로가 공존한다).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import settings
from koipa.services import reviewer_credentials as rc

API = "/api/v1"


@pytest.fixture
def signing_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """임시 RSA 키쌍 — 개인키는 서명(console_jwt_private_key_path), 공개키는 검증
    (jwt_public_key)에 꽂는다. kid 는 비워 둔다 — verify_jwt._find_key 가 "kid 없고 키 1개면
    그걸 쓴다"는 지름길을 타게 해서, 매번 jwks 파일을 새로 안 만들어도 된다."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    path = tmp_path / "private.pem"
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    pub_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    monkeypatch.setattr(settings, "console_jwt_private_key_path", str(path))
    monkeypatch.setattr(settings, "console_jwt_kid", "")
    monkeypatch.setattr(settings, "jwt_public_key", pub_pem)
    monkeypatch.setattr(settings, "jwt_jwks_path", "")
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    return path


@pytest.fixture
def credential_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "reviewer_credentials.json"
    monkeypatch.setattr(rc, "DEFAULT_PATH", path)
    return path


@pytest.fixture
def client(signing_key, credential_store) -> TestClient:
    return TestClient(app)


def test_correct_password_logs_in_and_sets_cookie(client: TestClient):
    rc.set_password("reviewer-01", "correct-horse-battery")
    r = client.post(
        f"{API}/golden/candidates/login",
        json={"username": "reviewer-01", "password": "correct-horse-battery"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "reviewer-01"
    assert "koipa_access_token" in r.cookies


def test_wrong_password_rejected_401(client: TestClient):
    rc.set_password("reviewer-01", "correct-horse-battery")
    r = client.post(
        f"{API}/golden/candidates/login",
        json={"username": "reviewer-01", "password": "wrong"},
    )
    assert r.status_code == 401
    assert "koipa_access_token" not in r.cookies


def test_unknown_username_rejected_with_same_401(client: TestClient):
    """계정이 없는지 비밀번호가 틀린지 구분해 알려주지 않는다 — 계정 탐지 통로가 되면 안 된다."""
    r = client.post(
        f"{API}/golden/candidates/login",
        json={"username": "no-such-reviewer", "password": "whatever123"},
    )
    assert r.status_code == 401


def test_login_issues_a_usable_session(client: TestClient):
    """로그인 뒤 쿠키로 /session 을 부르면 그 신원으로 인증된다 — 발급된 토큰이 실제로 동작."""
    rc.set_password("reviewer-01", "correct-horse-battery")
    login = client.post(
        f"{API}/golden/candidates/login",
        json={"username": "reviewer-01", "password": "correct-horse-battery"},
    )
    assert login.status_code == 200
    r = client.get(f"{API}/golden/candidates/session")
    assert r.status_code == 200
    assert r.json()["actor_id"] == "reviewer-01"
    assert r.json()["actor_role"] == "reviewer"


def test_signing_unavailable_gives_404_not_500(credential_store, monkeypatch: pytest.MonkeyPatch):
    """개인키 설정이 없으면(토큰 로그인만 켜진 배포) 404 — 비밀번호가 맞아도 500 으로 죽지 않는다."""
    monkeypatch.setattr(settings, "console_jwt_private_key_path", "")
    rc.set_password("reviewer-01", "correct-horse-battery")
    client = TestClient(app)
    r = client.post(
        f"{API}/golden/candidates/login",
        json={"username": "reviewer-01", "password": "correct-horse-battery"},
    )
    assert r.status_code == 404


def test_password_change_overwrites_not_duplicates(credential_store):
    rc.set_password("reviewer-01", "first-password")
    rc.set_password("reviewer-01", "second-password")
    assert rc.verify_password("reviewer-01", "first-password") is None
    assert rc.verify_password("reviewer-01", "second-password") == ("reviewer",)
    assert rc.list_usernames() == ["reviewer-01"]


def test_short_password_rejected(credential_store):
    with pytest.raises(rc.ReviewerCredentialError):
        rc.set_password("reviewer-01", "short")


def test_remove_account(credential_store):
    rc.set_password("reviewer-01", "correct-horse-battery")
    assert rc.remove_account("reviewer-01") is True
    assert rc.verify_password("reviewer-01", "correct-horse-battery") is None
    assert rc.remove_account("reviewer-01") is False
