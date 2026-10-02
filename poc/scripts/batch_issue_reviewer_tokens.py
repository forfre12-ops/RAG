#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""검수자 로그인 토큰을 여러 명 한 번에 발급한다.

`setup_console_test_login.py --sub <이름>` 은 한 번에 한 사람분만 발급한다 — 전문가
20명 분을 만들려면 이름을 바꿔 20번 실행해야 한다. 이 스크립트는 그 서명·파일 쓰기
로직을 그대로 두고 바꾸지 않는다(이미 `tests/test_console_token_issuance.py`·
`scripts/setup.sh`·`docs/INSTALL.md` 가 그 인터페이스를 쓴다) — 이름 목록을 받아
그 스크립트를 사람마다 그대로 다시 불러주는 얇은 래퍼일 뿐이다(이상민, 영업비밀보호센터
주임, 요청 #3 "전문가 자문용 계정 약 20개 생성" 대응).

KL 승인이나 요청이 필요한 작업이 아니다 — 이 토큰은 우리가 자체 RSA 키쌍으로 서명한다
(KL 연동은 X-API-Key 하나뿐이고 JWT 를 쓰지 않는다. 자세한 근거는
reviewer-account-random-assign-gap-2026-10-02 메모리 참고).

사용:
  python scripts/batch_issue_reviewer_tokens.py --names 김변호사,이교수,박포렌식 --days 45
  python scripts/batch_issue_reviewer_tokens.py --names-file experts.txt --days 45   # 한 줄에 한 명

첫 발급 때 없으면 키쌍이 자동으로 만들어지고(`setup_console_test_login.py` 의 기본
동작 그대로), 이후 사람들은 같은 키로 서명된다 — 중간에 `--regenerate-key` 를 주면
그 뒤 모든 토큰의 검증키(jwks)가 바뀌어 이미 나간 토큰이 깨지므로 이 래퍼는 그 옵션을
전달하지 않는다.

산출: secrets/console_jwt/tokens/{이름}.txt (이름마다 1개, setup_console_test_login.py 와 동일 형식)
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
_SETUP = Path(__file__).resolve().parent / "setup_console_test_login.py"


def read_names(names: str, names_file: str) -> list[str]:
    """이름 목록을 모으고 순서를 지키며 중복을 뗀다."""
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
    ap.add_argument("--names", default="", help="쉼표로 구분한 이름 목록")
    ap.add_argument("--names-file", default="", help="한 줄에 한 명씩 적은 파일")
    ap.add_argument("--roles", default="reviewer", help="setup_console_test_login.py 와 동일")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--until", default="")
    ap.add_argument("--iss", default="koipa-console")
    ap.add_argument("--aud", default="koipa-api")
    args = ap.parse_args(argv)

    names = read_names(args.names, args.names_file)
    if not names:
        raise SystemExit("[error] --names 또는 --names-file 로 최소 1명은 줘야 한다")

    print(f"발급 대상 {len(names)}명: {', '.join(names)}")
    issued: list[str] = []
    for i, name in enumerate(names, 1):
        cmd = [
            sys.executable, str(_SETUP),
            "--sub", name, "--roles", args.roles, "--days", str(args.days),
            "--iss", args.iss, "--aud", args.aud,
        ]
        if args.until:
            cmd += ["--until", args.until]
        result = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        if result.returncode != 0:
            print(f"  [{i}/{len(names)}] [실패] {name}: {result.stderr.strip()[-500:]}")
            continue
        issued.append(name)
        print(f"  [{i}/{len(names)}] {name} 발급 완료")

    print(f"\n발급 완료 {len(issued)}/{len(names)}명. 토큰 파일: secrets/console_jwt/tokens/*.txt")
    print("각자에게 1) 로그인 화면 주소, 2) 본인 파일의 토큰을 따로 전달할 것 — 한 토큰을")
    print("여러 명이 쓰면 원장에 같은 이름만 남아 개별 검수로 인정되지 않는다.")
    return 0 if len(issued) == len(names) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
