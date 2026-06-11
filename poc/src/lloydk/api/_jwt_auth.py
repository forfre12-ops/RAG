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
import os
import time
from dataclasses import dataclass

from fastapi import Header, HTTPException, Request

from lloydk.config import settings

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
    L-apikey-honor: api_key 모드는 단일 공유키라 본 함수에서 막진 않으나, 테넌트별
      api_key_hash 미등록 시 body tenant 무검증 통과(honor-system)임을 경고로 노출.
    """
    if not _is_production():
        return
    mode = (getattr(settings, "auth_mode", "api_key") or "api_key").lower()
    if mode in ("jwt", "both"):
        missing: list[str] = []
        if not getattr(settings, "jwt_issuer", ""):
            missing.append("LLOYDK_JWT_ISSUER")
        if not getattr(settings, "jwt_audience", ""):
            missing.append("LLOYDK_JWT_AUDIENCE")
        if missing:
            raise RuntimeError(
                f"SECURITY: auth_mode={mode!r} 운영 모드인데 {', '.join(missing)} 미설정. "
                "iss/aud 미검증 시 같은 키로 서명된 타 용도 JWT를 수락(confused deputy)합니다. "
                "JWT_ISSUER / JWT_AUDIENCE 를 명시하세요."
            )
    if mode in ("api_key", "both"):
        # 단일 공유키 자체는 운영에서 허용(서비스 호출자=신뢰). 다만 테넌트별 api_key_hash가
        # 등록돼 있지 않으면 X-Tenant-Id가 honor-system으로 통과됨을 운영자에게 경고.
        logger.warning(
            "auth_mode=%s 운영 — 테넌트별 api_key_hash 미등록 테넌트의 X-Tenant-Id는 "
            "honor-system으로 통과합니다(스코프 결속에는 미사용). 테넌트 격리가 필요하면 "
            "각 테넌트에 api_key_hash 를 등록하세요.", mode,
        )


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


def _check_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """저장된 hash와 raw key 비교.

    지원 포맷 (자동 감지):
      $2b$... — bcrypt (운영 표준)
      sha256:<hex> — 구형 포맷 (마이그레이션 경로)
    """
    if stored_hash.startswith("$2b$") or stored_hash.startswith("$2a$"):
        try:
            import bcrypt  # noqa: PLC0415
            return bcrypt.checkpw(raw_key.encode(), stored_hash.encode())
        except Exception:  # noqa: BLE001
            return False
    if stored_hash.startswith("sha256:"):
        import hashlib  # noqa: PLC0415
        return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash[7:]
    # 레거시: 64자 hex (SHA-256 plain)
    import hashlib  # noqa: PLC0415
    return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash


def hash_api_key(raw_key: str) -> str:
    """새 API 키를 bcrypt hash로 변환. 키 생성/교체 시 사용."""
    import bcrypt  # noqa: PLC0415
    return bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt(rounds=12)).decode()


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


def _verify_tenant_api_key(tenant_id: str, api_key: str) -> bool:
    """tenant.api_key_hash 와 요청 키를 비교. hash 미설정 시 통과 (하위호환).

    보안: hash가 설정된 테넌트는 DB 오류로 검증 불가 시 fail-closed(401) —
    DB 장애를 틈탄 검증 우회를 차단. DB 미가용 자체는 운영에서 서비스 불가 상태이므로
    fail-closed가 안전. 테넌트 부재·hash 미설정은 하위호환 통과.

    반환: hash가 설정돼 있고 일치 검증을 통과하면 ``True`` (= 이 tenant_id는
    인증상 authoritative). hash 미설정(하위호환 통과)이면 ``False`` (= tenant_id를
    신뢰할 근거 없음 — 테넌트 스코프 결속에 쓰지 않음). 불일치/오류는 예외.
    """
    try:
        from lloydk.db import session_scope  # noqa: PLC0415
        from lloydk.db.models import Tenant  # noqa: PLC0415
        with session_scope() as db:
            tenant = db.get(Tenant, tenant_id)
            if tenant is None or not tenant.api_key_hash:
                return False  # hash 미설정 → 검증 skip (하위호환), 신뢰 불가
            if not _check_api_key_hash(api_key, tenant.api_key_hash):
                raise HTTPException(status_code=401, detail="invalid tenant api key")
            return True  # hash 일치 → 이 tenant_id는 검증됨
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # DB 조회 실패 — hash 설정 여부를 알 수 없으므로 fail-closed.
        logger.error("tenant api key verification failed (DB error), denying: %s", exc)
        raise HTTPException(status_code=503, detail="tenant verification unavailable") from exc


def _stash_auth(
    request: Request,
    *,
    mode: str,
    tenant: str | None,
    actor: str | None,
    role: str | None,
) -> None:
    """해소된 인증 신원을 request.state에 저장.

    엔드포인트(tenant 결속)와 audit 미들웨어(위조 불가 actor/tenant 기록)가
    원시 헤더 대신 이 값을 사용한다. tenant는 *authoritative* 한 경우에만 채운다
    (서명된 JWT claim, 또는 hash 검증된 api_key tenant). 그렇지 않으면 None.
    """
    request.state.auth_mode = mode
    request.state.auth_tenant = tenant or None
    request.state.auth_actor = actor or None
    request.state.auth_role = role or None


def resolve_effective_tenant(request: Request, body_tenant_id: str | None) -> str | None:
    """요청 body의 tenant_id를 인증 컨텍스트에 결속해 *유효 tenant*를 반환.

    이후 모든 doc 조회(content fetch / verified label / 영속화)는 이 값으로
    스코프돼야 한다 — 객체 수준 권한(IDOR/BOLA) 차단의 핵심.

    정책:
    - jwt: claims.tenant(서명됨)가 진실. body가 다르면 403. tenant claim이 없는
      JWT가 tenant_id를 주장하면 스코프 보증 불가 → 403.
    - api_key + hash 검증된 X-Tenant-Id: 그 tenant가 진실. body가 다르면 403.
    - api_key + 미검증(단일 공유키 레거시): authoritative tenant 부재 → body를
      그대로 사용(하위호환). 이 경우에도 repo 계층이 body tenant로 스코프하므로
      "주장한 tenant의 문서"만 접근 가능.
    """
    auth_mode = getattr(request.state, "auth_mode", "api_key")
    auth_tenant = getattr(request.state, "auth_tenant", None)
    body_tenant_id = body_tenant_id or None

    if auth_mode == "jwt":
        if auth_tenant:
            if body_tenant_id and body_tenant_id != auth_tenant:
                raise HTTPException(
                    status_code=403,
                    detail="tenant_id does not match authenticated tenant",
                )
            return auth_tenant
        # tenant claim 없는 JWT — tenant 주장은 거부(스코프 보증 불가)
        if body_tenant_id:
            raise HTTPException(
                status_code=403,
                detail="JWT has no tenant claim; cannot assert tenant_id",
            )
        return None

    # api_key / both
    if auth_tenant:  # hash 검증된 X-Tenant-Id
        if body_tenant_id and body_tenant_id != auth_tenant:
            raise HTTPException(
                status_code=403,
                detail="tenant_id does not match verified X-Tenant-Id",
            )
        return auth_tenant
    return body_tenant_id


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
            # 보안: 역할은 서버 설정에서 결정 (X-Actor-Role 헤더 위조 차단).
            roles = _resolve_api_key_roles(request)
            # tenant API key hash 검증 (설정된 경우). hash 일치 시에만 tenant가
            # authoritative — 그때만 audit/스코프 결속에 사용(미검증 헤더 신뢰 금지).
            # L-apikey-honor: 운영(poc_mode=full)에서 X-Tenant-Id를 주장하는데 해당
            # 테넌트에 api_key_hash가 등록돼 있지 않으면 honor-system 통과를 막고 거부
            # (fail-closed). dev/단일테넌트는 비파괴(검증 skip, body tenant 그대로 사용).
            verified_tenant: str | None = None
            if x_tenant_id and x_api_key:
                if _verify_tenant_api_key(x_tenant_id, x_api_key):
                    verified_tenant = x_tenant_id
                elif _is_production():
                    raise HTTPException(
                        status_code=403,
                        detail=(
                            "tenant api_key_hash not registered; cannot honor X-Tenant-Id "
                            "in production"
                        ),
                    )
            _stash_auth(
                request, mode="api_key", tenant=verified_tenant, actor=None, role=roles[0]
            )
            return {"mode": "api_key", "actor_role": roles[0], "actor_roles": roles}
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

        # 보안: 서명된 roles claim을 RBAC 소스로 연결. VALID_ROLES만 채택.
        # roles가 비면 actor_role="" → require_role에서 권한 부족으로 403.
        jwt_roles = tuple(r for r in claims.roles if r in VALID_ROLES)
        _stash_auth(
            request,
            mode="jwt",
            tenant=claims.tenant,
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
