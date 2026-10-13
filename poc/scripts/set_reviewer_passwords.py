#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수자 아이디+비밀번호를 발급한다 — 토큰 대신 익숙한 로그인 방식을 쓰고 싶을 때.

비밀번호를 안 주면 무작위로 만들어 화면에 출력한다(관리자가 그대로 전달). 이름을 여러 번
돌려도 안전하다 — 이미 있는 계정은 비밀번호만 새로 덮어쓴다(원래 계정 자체가 사라지거나
새로 생기지 않는다).

산출(저장): datasets/proxy_gold/reviewer_credentials.json — 비밀번호는 bcrypt 해시로만
저장된다(원문은 이 화면 출력에만 있고 파일에는 없다). 로그인 API 가 켜지려면 서버 설정에
CONSOLE_JWT_PRIVATE_KEY_PATH 가 함께 있어야 한다(없으면 비밀번호가 맞아도 404 — 토큰
로그인만 켜진 배포에서는 이 기능 자체가 없다는 뜻).

사용:
  python scripts/set_reviewer_passwords.py --names reviewer-01,reviewer-02
  python scripts/set_reviewer_passwords.py --names-file reviewers.txt   # 한 줄에 한 명
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from koipa.services import reviewer_credentials  # noqa: E402

# [2026-10-02] 사용자 지시 — "잠깐 쓰는 임시 계정인데 비밀번호를 쉽게 만들어라".
# 소문자+숫자만, 헷갈리는 글자(0/o, 1/l/i) 뺀다 — 입으로 불러줄 수 있는 수준으로 낮춘다.
# reviewer_credentials.set_password 의 최소 길이(8자)에 맞춘다.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def _gen_password(n: int = 8) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def read_names(names: str, names_file: str) -> list[str]:
    raw: list[str] = []
    if names:
        raw.extend(n.strip() for n in names.split(",") if n.strip())
    if names_file:
        path = Path(names_file)
        if not path.is_absolute():
            path = ROOT / path
        raw.extend(ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip())
    seen: set[str] = set()
    out: list[str] = []
    for name in raw:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", default="", help="쉼표로 구분한 아이디 목록")
    ap.add_argument("--names-file", default="", help="한 줄에 한 명씩 적은 파일")
    ap.add_argument("--roles", default="reviewer")
    args = ap.parse_args(argv)

    names = read_names(args.names, args.names_file)
    if not names:
        raise SystemExit("[error] --names 또는 --names-file 로 최소 1명은 줘야 한다")

    roles = tuple(r.strip() for r in args.roles.split(",") if r.strip())
    print(f"발급 대상 {len(names)}명")
    rows: list[tuple[str, str]] = []
    for name in names:
        pw = _gen_password()
        reviewer_credentials.set_password(name, pw, roles=roles)
        rows.append((name, pw))

    print(f"\n발급 완료 {len(rows)}명. 아이디 / 비밀번호(이번에만 보임, 각자에게 따로 전달할 것):")
    for name, pw in rows:
        print(f"  {name:20s} {pw}")
    print(f"\n저장 위치: {reviewer_credentials.DEFAULT_PATH}")
    print("로그인 화면: <서버주소>/api/v1/golden/candidates/login.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
