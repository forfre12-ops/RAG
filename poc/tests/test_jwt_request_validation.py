"""[보안] JWT 요청검증 음성테스트 — verify_jwt가 잘못된 토큰을 거부하는지.

기동 검증(assert_production_auth_config)은 이미 테스트되나, 요청시 JWT 검증(잘못된
iss/aud claim, 비RS256 alg, exp/nbf, 서명위조, malformed)의 음성테스트가 부재했다.
JWT는 B레벨 보안경계 — RS256 키쌍으로 서명한 뒤 claim/alg/서명을 망가뜨려 거부를 고정한다.
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from koipa.api._jwt_auth import JWTError, verify_jwt

# 모듈 1회 키쌍(테스트 전용).
_PRIV = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUB_PEM = _PRIV.public_key().public_bytes(
    encoding=serialization.Encoding.PEM,
    format=serialization.PublicFormat.SubjectPublicKeyInfo,
).decode("ascii")


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _make_jwt(header: dict, payload: dict, *, priv=_PRIV) -> str:
    h = _b64u(json.dumps(header).encode("utf-8"))
    p = _b64u(json.dumps(payload).encode("utf-8"))
    sig = priv.sign((h + "." + p).encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
    return f"{h}.{p}.{_b64u(sig)}"


@pytest.fixture(autouse=True)
def _jwt_keys(monkeypatch):
    """단일 PEM 공개키로 검증하도록 설정 + iss/aud 미설정(테스트별로 켬)."""
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "jwt_jwks_path", "", raising=False)
    monkeypatch.setattr(cfg.settings, "jwt_public_key", _PUB_PEM, raising=False)
    monkeypatch.setattr(cfg.settings, "jwt_issuer", "", raising=False)
    monkeypatch.setattr(cfg.settings, "jwt_audience", "", raising=False)


def _valid_payload(**over) -> dict:
    p = {"sub": "kl-user", "roles": ["admin"], "exp": int(time.time()) + 3600,
         "iat": int(time.time())}
    p.update(over)
    return p


# ── 양성(통과) ────────────────────────────────────────────────────────────────

def test_valid_token_passes():
    tok = _make_jwt({"alg": "RS256", "kid": "default"}, _valid_payload())
    claims = verify_jwt(tok)
    assert claims.sub == "kl-user" and "admin" in claims.roles


# ── 음성(거부) ────────────────────────────────────────────────────────────────

def test_non_rs256_alg_rejected():
    tok = _make_jwt({"alg": "HS256"}, _valid_payload())
    with pytest.raises(JWTError, match="algorithm not allowed"):
        verify_jwt(tok)


def test_alg_none_rejected():
    tok = _make_jwt({"alg": "none"}, _valid_payload())
    with pytest.raises(JWTError, match="algorithm not allowed"):
        verify_jwt(tok)


def test_missing_exp_rejected():
    p = _valid_payload()
    p.pop("exp")
    with pytest.raises(JWTError, match="missing exp"):
        verify_jwt(_make_jwt({"alg": "RS256"}, p))


def test_expired_token_rejected():
    tok = _make_jwt({"alg": "RS256"}, _valid_payload(exp=int(time.time()) - 10))
    with pytest.raises(JWTError, match="expired"):
        verify_jwt(tok)


def test_future_nbf_rejected():
    tok = _make_jwt({"alg": "RS256"}, _valid_payload(nbf=int(time.time()) + 3600))
    with pytest.raises(JWTError, match="not yet valid"):
        verify_jwt(tok)


def test_issuer_mismatch_rejected(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "jwt_issuer", "https://kl.example.com", raising=False)
    tok = _make_jwt({"alg": "RS256"}, _valid_payload(iss="https://evil.example.com"))
    with pytest.raises(JWTError, match="issuer mismatch"):
        verify_jwt(tok)


def test_audience_mismatch_rejected(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "jwt_audience", "koipa-api", raising=False)
    tok = _make_jwt({"alg": "RS256"}, _valid_payload(aud="other-service"))
    with pytest.raises(JWTError, match="audience mismatch"):
        verify_jwt(tok)


def test_issuer_match_passes(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "jwt_issuer", "https://kl.example.com", raising=False)
    tok = _make_jwt({"alg": "RS256"}, _valid_payload(iss="https://kl.example.com"))
    assert verify_jwt(tok).sub == "kl-user"


def test_malformed_token_rejected():
    with pytest.raises(JWTError, match="malformed"):
        verify_jwt("only.twoparts")


def test_tampered_payload_signature_rejected():
    tok = _make_jwt({"alg": "RS256"}, _valid_payload())
    h, p, s = tok.split(".")
    forged_p = _b64u(json.dumps(_valid_payload(roles=["admin"], sub="attacker")).encode())
    with pytest.raises(JWTError, match="signature verification failed"):
        verify_jwt(f"{h}.{forged_p}.{s}")
