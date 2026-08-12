#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""골든셋 관리 콘솔 테스트 로그인 세팅 — RS256 키쌍 발급 + 관리자 토큰 발급.

콘솔(`/api/v1/golden/candidates/manage.html`)은 공유 API 키를 거부하고 포털 JWT 만 받는다
("golden console requires a portal JWT login; shared API keys are not allowed").
누가 등급을 정했는지 실계정으로 남겨야 검수 기록이 성립하기 때문이며, 이 스크립트는 그 설계를
우회하지 않는다 — 테스트용 **실제 키쌍**을 만들고 그 키로 서명한 토큰을 발급한다.

산출:
  secrets/console_jwt/private.pem   서명키 (배포본에 넣지 않는다)
  secrets/console_jwt/jwks.json     검증키 → JWT_JWKS_PATH 로 지정
  secrets/console_jwt/token.txt     발급된 관리자 토큰

서버 설정:
  AUTH_MODE=both                    (api_key 경로 유지 + jwt 허용)
  JWT_JWKS_PATH=secrets/console_jwt/jwks.json

브라우저 사용: 포털이 `koipa_access_token` 쿠키로 토큰을 심는다. 쿠키만 넣으면 화면이 열린다.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "secrets" / "console_jwt"
KID = "console-test-1"


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sub", default="kl-admin-test", help="검수 기록에 남을 계정 ID")
    ap.add_argument("--roles", default="admin", help="쉼표 구분")
    ap.add_argument("--days", type=int, default=30)
    # auth_mode=both/jwt 운영 모드는 iss/aud 검증을 강제한다(_jwt_auth.assert_production_auth_config).
    # 미설정 시 같은 키로 서명된 타 용도 JWT 를 수락하게 되므로(confused deputy) 기본값을 준다.
    ap.add_argument("--iss", default="koipa-console", help="JWT_ISSUER 와 일치해야 한다")
    ap.add_argument("--aud", default="koipa-api", help="JWT_AUDIENCE 와 일치해야 한다")
    ap.add_argument("--regenerate-key", action="store_true", help="키쌍을 새로 만든다")
    # 토큰이 파일과 .env 두 곳에 살면 재발급 때 한쪽만 갱신돼 어긋난다.
    # 실측(2026-08-09): iss/aud 를 추가하며 재발급했는데 .env 에는 옛 토큰이 남아
    # 로그인 화면이 채워준 토큰이 그대로 401(signature verification failed) 이었다.
    # 발급과 동시에 갱신해 사람이 두 곳을 맞추지 않게 한다.
    ap.add_argument("--env-file", action="append", default=[],
                    help="CONSOLE_LOGIN_PREFILL_TOKEN 을 갱신할 .env (여러 번 지정 가능)")
    ap.add_argument("--also-copy-jwks", default="",
                    help="검증키를 복사할 경로. 컨테이너는 secrets/ 를 마운트하지 않으므로 "
                         "바인드되는 경로(예: datasets/_console_jwt/jwks.json)로 준다")
    args = ap.parse_args(argv)

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    OUT.mkdir(parents=True, exist_ok=True)
    priv_path = OUT / "private.pem"
    jwks_path = OUT / "jwks.json"

    if args.regenerate_key or not priv_path.is_file():
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        priv_path.write_bytes(
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
        jwks_path.write_text(
            json.dumps(
                {"keys": [{"kid": KID, "kty": "RSA", "use": "sig", "alg": "RS256", "pem": pub_pem}]},
                ensure_ascii=False, indent=2,
            ) + "\n",
            encoding="utf-8",
        )
        made = "새로 생성"
    else:
        key = serialization.load_pem_private_key(priv_path.read_bytes(), password=None)
        made = "기존 키 사용"

    now = int(dt.datetime.now(dt.timezone.utc).timestamp())
    header = {"alg": "RS256", "typ": "JWT", "kid": KID}
    payload = {
        "sub": args.sub,
        "roles": [r.strip() for r in args.roles.split(",") if r.strip()],
        "iss": args.iss,
        "aud": args.aud,
        "iat": now,
        "exp": now + args.days * 86400,
    }
    signing_input = f"{b64u(json.dumps(header, separators=(',', ':')).encode())}." \
                    f"{b64u(json.dumps(payload, separators=(',', ':')).encode())}".encode()
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    token = f"{signing_input.decode()}.{b64u(sig)}"
    (OUT / "token.txt").write_text(token + "\n", encoding="utf-8")

    updated: list[str] = []
    for env_name in args.env_file:
        env_path = Path(env_name)
        if not env_path.is_absolute():
            env_path = ROOT / env_path
        if not env_path.is_file():
            print(f"  [건너뜀] .env 없음: {env_path}")
            continue
        lines = [
            ln for ln in env_path.read_text(encoding="utf-8").splitlines()
            if not ln.startswith("CONSOLE_LOGIN_PREFILL_TOKEN=")
        ]
        lines.append(f"CONSOLE_LOGIN_PREFILL_TOKEN={token}")
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        updated.append(str(env_path))

    copied = ""
    if args.also_copy_jwks:
        dst = Path(args.also_copy_jwks)
        if not dst.is_absolute():
            dst = ROOT / dst
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(jwks_path.read_text(encoding="utf-8"), encoding="utf-8")
        # 권한 조정은 편의일 뿐이다. 바인드 마운트에서 파일 소유자가 컨테이너 uid(1000)이면
        # 호스트 계정(1001)은 chmod 를 못 하는데(실측 PermissionError), 내용은 이미 써졌고
        # 그룹 쓰기로 충분하다. 여기서 죽으면 토큰 발급 전체가 실패한 것처럼 보인다.
        try:
            dst.chmod(0o644)
        except OSError as exc:
            print(f"  [알림] jwks 권한 조정 생략({exc.__class__.__name__}) — 내용은 기록됨")
        copied = str(dst)

    print(json.dumps({
        "키": made,
        "jwks": str(jwks_path.relative_to(ROOT)).replace("\\", "/"),
        "sub": args.sub,
        "roles": payload["roles"],
        "만료": dt.datetime.fromtimestamp(payload["exp"], dt.timezone.utc).isoformat(),
        "token_file": str((OUT / "token.txt").relative_to(ROOT)).replace("\\", "/"),
        "env_updated": updated,
        "jwks_copied_to": copied or None,
    }, ensure_ascii=False, indent=2))
    print("\n서버 환경변수:")
    print("  AUTH_MODE=both")
    print(f"  JWT_JWKS_PATH={jwks_path.relative_to(ROOT).as_posix()}")
    print(f"  JWT_ISSUER={args.iss}")
    print(f"  JWT_AUDIENCE={args.aud}")
    print("\n브라우저: 개발자도구 콘솔에서")
    print(f"  document.cookie='koipa_access_token={token[:28]}...; path=/'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
