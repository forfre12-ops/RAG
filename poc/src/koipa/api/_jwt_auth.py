"""P1-C3: JWT 인증 — 운영 모드(settings.auth_mode=jwt) 활성 시 사용.

기본 모드(X-API-Key)는 `_auth.py`를 그대로 사용. 본 모듈은 RS256 JWT 검증 기능을 제공.

운영 정책:
- 알고리즘 RS256만 허용 (HS256·none 거부)
- exp(만료) 강제 — 부재 또는 만료 시 401
- kid claim으로 키 로테이션 지원 (settings.jwt_jwks_path에서 JWKS 로드)
- claims: sub, roles, exp, iat, kid
  (tenant 제거: Koipa는 단일 고객사 엔진, 격리는 KL 포털 전담)

PyJWT가 설치돼 있으면 그것을 사용, 아니면 표준 라이브러리만으로 검증(서명 부분은 cryptography 필요).
운영 환경: `pip install -e ".[jwt]"` 권장.
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import os
import time
from dataclasses import dataclass

from fastapi import Cookie, Header, HTTPException, Request

from koipa.config import settings

logger = logging.getLogger(__name__)


def _is_production() -> bool:
    """운영 강제(fail-fast) 적용 여부.

    poc_mode=full 이면서 테스트(TestClient/pytest) 환경이 아닐 때만 True.
    dev/test/dryrun은 항상 False — 운영 전용 엄격성이 비파괴.
    """
    if getattr(settings, "poc_mode", "dryrun") != "full":
        return False
    if os.environ.get("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return True


def assert_production_auth_config() -> None:
    """운영(poc_mode=full) 진입 시 인증 모드별 confused-deputy 차단 설정을 강제.

    호출 위치: api/app.py startup hook(assert_production_credentials 인접) 권장. dev/test/
    dryrun은 _is_production()=False라 즉시 반환(비파괴).

    L-jwt-aud: jwt 모드인데 jwt_issuer/jwt_audience가 비면 같은 키로 서명된 타 용도
      토큰을 수락(confused deputy). 둘 다 설정돼야 함.

    tenant 제거: 격리는 KL 포털 전담(단일 고객사 엔진). 테넌트별 api_key_hash·
    honor-system 경고는 더 이상 없음.
    """
    if not _is_production():
        return
    mode = (getattr(settings, "auth_mode", "api_key") or "api_key").lower()
    if mode in ("jwt", "both"):
        missing: list[str] = []
        if not getattr(settings, "jwt_issuer", ""):
            missing.append("KOIPA_JWT_ISSUER")
        if not getattr(settings, "jwt_audience", ""):
            missing.append("KOIPA_JWT_AUDIENCE")
        if missing:
            raise RuntimeError(
                f"SECURITY: auth_mode={mode!r} 운영 모드인데 {', '.join(missing)} 미설정. "
                "iss/aud 미검증 시 같은 키로 서명된 타 용도 JWT를 수락(confused deputy)합니다. "
                "JWT_ISSUER / JWT_AUDIENCE 를 명시하세요."
            )


@dataclass
class JWTClaims:
    # tenant 제거: 단일 고객사 엔진, 격리는 KL 포털 전담.
    sub: str
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
    # L-jwt-aud: 운영(poc_mode=full)에서는 미설정 자체가 confused-deputy 위험이므로
    # fail-fast. dev/test/dryrun은 미설정 시 기존처럼 검증 skip(비파괴).
    expected_iss = getattr(settings, "jwt_issuer", "")
    if not expected_iss and _is_production():
        raise JWTError("jwt_issuer not configured in production (confused deputy risk)")
    if expected_iss:
        if str(payload.get("iss", "")) != expected_iss:
            raise JWTError(f"issuer mismatch: expected {expected_iss!r}")

    # aud (audience) — settings.jwt_audience 설정 시 payload aud에 포함되어야 함.
    expected_aud = getattr(settings, "jwt_audience", "")
    if not expected_aud and _is_production():
        raise JWTError("jwt_audience not configured in production (confused deputy risk)")
    if expected_aud:
        aud = payload.get("aud")
        aud_list = [aud] if isinstance(aud, str) else (aud or [])
        if expected_aud not in aud_list:
            raise JWTError(f"audience mismatch: {expected_aud!r} not in {aud_list}")

    iat = int(payload.get("iat", 0))

    return JWTClaims(
        sub=str(payload.get("sub", "")),
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


# tenant 제거: per-tenant api_key_hash 검증·생성 헬퍼(_check_api_key_hash·
# hash_api_key) 삭제. 인증은 단일 KL 자격(공유 X-API-Key 또는 서명 JWT)만 사용.


# 유효한 RBAC 역할 — schemas.common.Actor 패턴과 일치.
VALID_ROLES: frozenset[str] = frozenset({"admin", "reviewer", "system", "kl_backend"})

# 역할 우선순위 (높을수록 권한 큼) — JWT claim에 복수 역할 시 대표 역할 선정용.
_ROLE_RANK: dict[str, int] = {"system": 0, "reviewer": 1, "kl_backend": 2, "admin": 3}


def _highest_role(roles: tuple[str, ...]) -> str:
    """복수 역할 중 최고 권한 역할 반환. 빈 입력이면 빈 문자열."""
    valid = [r for r in roles if r in VALID_ROLES]
    if not valid:
        return ""
    return max(valid, key=lambda r: _ROLE_RANK.get(r, -1))


def _resolve_api_key_roles(request: Request) -> tuple[str, ...]:
    """api_key 인증 시 actor 역할 결정.

    보안: 기본은 서버 설정 settings.api_key_role 고정 (단일 공유키 = 신뢰된 호출자).
    X-Actor-Role 헤더 신뢰는 settings.api_key_trust_actor_role_header=True일 때만 허용
    (개발·테스트 편의). 운영(poc_mode=full)에서는 config가 startup fail-fast로 차단.
    헤더값은 항상 VALID_ROLES로 검증 — 임의 문자열 자칭 거부.
    """
    if getattr(settings, "api_key_trust_actor_role_header", False):
        hdr = (request.headers.get("x-actor-role") or "").strip()
        if hdr:
            if hdr not in VALID_ROLES:
                raise HTTPException(status_code=403, detail=f"invalid actor role: {hdr!r}")
            return (hdr,)
    role = (getattr(settings, "api_key_role", "system") or "system").strip()
    if role not in VALID_ROLES:
        raise HTTPException(status_code=403, detail=f"invalid configured api key role: {role!r}")
    return (role,)


def _stash_auth(
    request: Request,
    *,
    mode: str,
    actor: str | None,
    role: str | None,
) -> None:
    """해소된 인증 신원을 request.state에 저장.

    audit 미들웨어(위조 불가 actor 기록)와 rate-limit이 원시 헤더 대신 이 값을
    사용한다. tenant 제거: 단일 고객사 엔진(격리는 KL 포털 전담), per-customer
    경계 없음.
    """
    request.state.auth_mode = mode
    request.state.auth_actor = actor or None
    request.state.auth_role = role or None


def require_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    koipa_access_token: str | None = Cookie(default=None),
):
    """모드 자동 선택 — settings.auth_mode=jwt|api_key.

    - api_key (default): X-API-Key 검증
    - jwt: Authorization: Bearer 검증 (KL 서명 JWT, KL이 유일 발급자)
    - both: 둘 중 하나 만족

    tenant 제거: 단일 KL 인증이 곧 인증의 전부(서명검증). 고객사 격리는 KL 포털
    라우팅이 상류에서 전담하므로 Koipa 내부엔 per-customer 경계가 없다.
    """
    mode = (getattr(settings, "auth_mode", "api_key") or "api_key").lower()
    if mode in ("api_key", "both"):
        # 상수시간 비교(hmac.compare_digest) — 평문 `==`의 바이트단위 early-exit
        # 타이밍 사이드채널 차단. 빈 settings.api_key는 인증 불가(가드 유지).
        if x_api_key and settings.api_key and hmac.compare_digest(x_api_key, settings.api_key):
            # 보안: 역할은 서버 설정에서 결정 (X-Actor-Role 헤더 위조 차단).
            roles = _resolve_api_key_roles(request)
            _stash_auth(request, mode="api_key", actor=None, role=roles[0])
            return {"mode": "api_key", "actor_role": roles[0], "actor_roles": roles}
        if mode == "api_key":
            raise HTTPException(status_code=401, detail="invalid api key")
    if mode in ("jwt", "both"):
        # 포털이 동일 사이트 HttpOnly 쿠키로 전달한 JWT를 콘솔 브라우저 요청에도 사용한다.
        # 자바스크립트가 토큰을 읽거나 화면에 노출할 필요가 없으며, Authorization 헤더가 있으면
        # 그것이 우선한다(서비스 간 호출 호환성 유지).
        if not authorization and koipa_access_token:
            authorization = f"Bearer {koipa_access_token}"
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

        # 보안: 서명된 roles claim을 RBAC 소스로 연결. VALID_ROLES만 채택.
        # roles가 비면 actor_role="" → require_role에서 권한 부족으로 403.
        jwt_roles = tuple(r for r in claims.roles if r in VALID_ROLES)
        _stash_auth(
            request,
            mode="jwt",
            actor=claims.sub,
            role=_highest_role(jwt_roles),
        )
        return {
            "mode": "jwt",
            "claims": claims,
            "actor_role": _highest_role(jwt_roles),
            "actor_roles": jwt_roles,
        }
    raise HTTPException(status_code=401, detail="unauthenticated")
