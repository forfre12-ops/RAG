"""P1-C3: JWT 인증 — 운영 모드(settings.auth_mode=jwt) 활성 시 사용.

기본 모드(X-API-Key)는 `_auth.py`를 그대로 사용. 본 모듈은 RS256 JWT 검증 기능을 제공.

운영 정책:
- 알고리즘 RS256만 허용 (HS256·none 거부)
- exp(만료) 강제 — 부재 또는 만료 시 401
- kid claim으로 키 로테이션 지원 (settings.jwt_jwks_path에서 JWKS 로드)
- claims: sub, tenant, roles, exp, iat, kid

PyJWT가 설치돼 있으면 그것을 사용, 아니면 표준 라이브러리만으로 검증(서명 부분은 cryptography 필요).
운영 환경: `pip install -e ".[jwt]"` 권장.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from lloydk.config import settings

logger = logging.getLogger(__name__)


@dataclass
class JWTClaims:
    sub: str
    tenant: str = ""
    roles: tuple[str, ...] = ()
    kid: str = ""
    exp: int = 0
    iat: int = 0


class JWTError(Exception):
    pass


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def _load_jwks() -> dict:
    """JWKS 캐시 — settings.jwt_jwks_path 또는 inline jwt_public_key."""
    path = getattr(settings, "jwt_jwks_path", "")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.error("JWKS load failed: %s", e)
            raise JWTError("jwks load failed") from e
    # 단일 키 fallback (PEM)
    pem = getattr(settings, "jwt_public_key", "")
    if pem:
        return {"keys": [{"kid": "default", "pem": pem, "kty": "RSA", "use": "sig", "alg": "RS256"}]}
    raise JWTError("jwks not configured")


def _find_key(jwks: dict, kid: str) -> dict:
    keys = jwks.get("keys", [])
    if not kid and len(keys) == 1:
        return keys[0]
    for k in keys:
        if k.get("kid") == kid:
            return k
    raise JWTError(f"kid not found: {kid}")


def verify_jwt(token: str) -> JWTClaims:
    """RS256 JWT 토큰 검증."""
    parts = token.split(".")
    if len(parts) != 3:
        raise JWTError("malformed jwt")
    header_b, payload_b, sig_b = parts

    try:
        header = json.loads(_b64url_decode(header_b))
        payload = json.loads(_b64url_decode(payload_b))
    except Exception as e:  # noqa: BLE001
        raise JWTError(f"jwt decode failed: {e}") from e

    alg = header.get("alg", "")
    if alg != "RS256":
        raise JWTError(f"algorithm not allowed: {alg}")

    kid = header.get("kid", "")
    jwks = _load_jwks()
    key = _find_key(jwks, kid)

    signing_input = (header_b + "." + payload_b).encode("ascii")
    signature = _b64url_decode(sig_b)

    _verify_signature(signing_input, signature, key)

    now = int(time.time())

    # exp — 필수. 없거나 만료된 토큰 즉시 거부.
    exp = payload.get("exp")
    if exp is None:
        raise JWTError("token missing exp claim")
    if int(exp) < now:
        raise JWTError("token expired")

    # nbf (not before) — 있으면 검증. 미래 토큰 조기 사용 차단.
    nbf = payload.get("nbf")
    if nbf is not None and int(nbf) > now:
        raise JWTError("token not yet valid (nbf)")

    # iss (issuer) — settings.jwt_issuer 설정 시 반드시 일치해야 함.
    expected_iss = getattr(settings, "jwt_issuer", "")
    if expected_iss:
        if str(payload.get("iss", "")) != expected_iss:
            raise JWTError(f"issuer mismatch: expected {expected_iss!r}")

    # aud (audience) — settings.jwt_audience 설정 시 payload aud에 포함되어야 함.
    expected_aud = getattr(settings, "jwt_audience", "")
    if expected_aud:
        aud = payload.get("aud")
        aud_list = [aud] if isinstance(aud, str) else (aud or [])
        if expected_aud not in aud_list:
            raise JWTError(f"audience mismatch: {expected_aud!r} not in {aud_list}")

    iat = int(payload.get("iat", 0))

    return JWTClaims(
        sub=str(payload.get("sub", "")),
        tenant=str(payload.get("tenant", "")),
        roles=tuple(payload.get("roles", []) or []),
        kid=kid,
        exp=int(exp),
        iat=iat,
    )


def _verify_signature(signing_input: bytes, signature: bytes, key: dict) -> None:
    """RS256 서명 검증 — cryptography 또는 PyJWT lazy."""
    pem = key.get("pem")
    if pem:
        try:
            from cryptography.hazmat.primitives import hashes  # type: ignore
            from cryptography.hazmat.primitives.asymmetric import padding  # type: ignore
            from cryptography.hazmat.primitives.serialization import load_pem_public_key  # type: ignore

            pub = load_pem_public_key(pem.encode("utf-8") if isinstance(pem, str) else pem)
            pub.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
            return
        except Exception as e:
            raise JWTError(f"signature verification failed: {e}") from e

    # JWK n/e 형태
    n_b64 = key.get("n")
    e_b64 = key.get("e")
    if n_b64 and e_b64:
        try:
            from cryptography.hazmat.primitives import hashes  # type: ignore
            from cryptography.hazmat.primitives.asymmetric import padding, rsa  # type: ignore

            n = int.from_bytes(_b64url_decode(n_b64), "big")
            e = int.from_bytes(_b64url_decode(e_b64), "big")
            pub = rsa.RSAPublicNumbers(e=e, n=n).public_key()
            pub.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
            return
        except Exception as e:  # noqa: F841
            raise JWTError("signature verification failed (jwk)") from None

    raise JWTError("no public key material")


def require_jwt(authorization: str = Header(...)) -> JWTClaims:
    """FastAPI Dependency — `Authorization: Bearer <jwt>`."""
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization[7:].strip()
    try:
        return verify_jwt(token)
    except JWTError as e:
        raise HTTPException(status_code=401, detail=f"invalid jwt: {e}")


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    x_tenant_id: str | None = Header(default=None),
):
    """모드 자동 선택 — settings.auth_mode=jwt|api_key.

    - api_key (default): X-API-Key 검증
    - jwt: Authorization: Bearer 검증
    - both: 둘 중 하나 만족

    JWT 모드에서는 X-Tenant-Id 헤더와 JWT claim의 tenant가 다르면 401 반환.
    api_key 모드에서는 X-Tenant-Id가 있어도 JWT claim이 없어 비교 불가 — 그대로 통과.
    """
    mode = (getattr(settings, "auth_mode", "api_key") or "api_key").lower()
    if mode in ("api_key", "both"):
        if x_api_key and x_api_key == settings.api_key:
            return {"mode": "api_key"}
        if mode == "api_key":
            raise HTTPException(status_code=401, detail="invalid api key")
    if mode in ("jwt", "both"):
        if not authorization:
            raise HTTPException(status_code=401, detail="missing authorization")
        try:
            claims = verify_jwt(
                authorization[7:].strip()
                if authorization.lower().startswith("bearer ")
                else authorization
            )
        except JWTError as e:
            raise HTTPException(status_code=401, detail=f"invalid jwt: {e}")

        # X-Tenant-Id 헤더와 JWT claim tenant 불일치 차단.
        # 헤더가 없으면 JWT claim을 신뢰 — 헤더가 있을 때만 검증.
        if x_tenant_id and claims.tenant and x_tenant_id != claims.tenant:
            logger.warning(
                "tenant mismatch: header=%r jwt_claim=%r path=%s",
                x_tenant_id, claims.tenant, request.url.path,
            )
            raise HTTPException(
                status_code=401,
                detail="tenant mismatch: X-Tenant-Id does not match JWT claim",
            )

        return {"mode": "jwt", "claims": claims}
    raise HTTPException(status_code=401, detail="unauthenticated")
