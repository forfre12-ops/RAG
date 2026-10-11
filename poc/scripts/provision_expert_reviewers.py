#!/usr/bin/env python
"""Add reviewer accounts without resetting any existing password.

Run from ``poc`` and send the one-time stdout table through the approved
credential channel.  Plaintext passwords are generated only in memory and are
never written to the credential JSON (which contains bcrypt hashes only).
"""
from __future__ import annotations

import argparse
import secrets
import sys
from pathlib import Path


_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
if str(_POC / "src") not in sys.path:
    sys.path.insert(0, str(_POC / "src"))


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="신규 전문가 검수 계정을 추가 발급합니다.")
    parser.add_argument(
        "--username", action="append", required=True,
        help="새 검수자 ID (여러 번 지정). 기존 ID면 전체 작업을 중단합니다.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    from koipa.services import reviewer_credentials  # noqa: PLC0415

    usernames = [str(value).strip() for value in args.username]
    if len(usernames) != len(set(usernames)) or any(not value for value in usernames):
        raise SystemExit("--username must contain distinct non-empty values")
    # token_urlsafe(18) produces a high-entropy, login-safe temporary secret.
    passwords = {username: secrets.token_urlsafe(18) for username in usernames}
    try:
        created = reviewer_credentials.create_accounts(passwords)
    except reviewer_credentials.ReviewerCredentialError as exc:
        raise SystemExit(f"no account was changed: {exc}") from exc

    print("ONE-TIME NEW REVIEWER CREDENTIALS")
    for username in created:
        print(f"{username}\t{passwords[username]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
