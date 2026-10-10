#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""로컬/배포 환경파일에서 무인증 콘솔 자동로그인을 안전하게 끈다.

``CONSOLE_LOGIN_PREFILL_TOKEN`` 은 로그인 화면(무인증 HTML)에 토큰을 싣는다.
공용 시연에는 편하지만, 전문가별 책임 이력을 남겨야 하는 검수 운영에서는 사용하면 안 된다.
이 도구는 토큰 내용을 출력하거나 백업에 복사하지 않고 관련 설정 두 줄만 제거한다.
"""
from __future__ import annotations

import argparse
from pathlib import Path


PREFILL_PREFIXES = (
    "CONSOLE_LOGIN_PREFILL_TOKEN=",
    "CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE=",
)


def sanitize_env(path: Path, *, default_review_batch: str | None = None, dry_run: bool = False) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)

    original = path.read_text(encoding="utf-8")
    lines = original.splitlines()
    kept = [line for line in lines if not line.startswith(PREFILL_PREFIXES)]
    removed_prefill = len(lines) - len(kept)

    batch_changed = False
    if default_review_batch is not None:
        prefix = "GOLDEN_DEFAULT_REVIEW_BATCH="
        new_line = f"{prefix}{default_review_batch}"
        rewritten: list[str] = []
        found = False
        for line in kept:
            if line.startswith(prefix):
                rewritten.append(new_line)
                found = True
                batch_changed = batch_changed or line != new_line
            else:
                rewritten.append(line)
        if not found:
            rewritten.append(new_line)
            batch_changed = True
        kept = rewritten

    changed = original != "\n".join(kept) + "\n"
    if changed and not dry_run:
        path.write_text("\n".join(kept) + "\n", encoding="utf-8", newline="\n")
    return {
        "path": str(path),
        "changed": changed,
        "removed_autologin_settings": removed_prefill,
        "default_review_batch_changed": batch_changed,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--default-review-batch", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = sanitize_env(
        args.env_file,
        default_review_batch=args.default_review_batch,
        dry_run=args.dry_run,
    )
    print(
        "자동로그인 설정 {state}: {count}개 항목 제거 · 기본 검수 배치 변경={batch}".format(
            state="점검만" if result["dry_run"] else ("반영" if result["changed"] else "이미 해제"),
            count=result["removed_autologin_settings"],
            batch="예" if result["default_review_batch_changed"] else "아니오",
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
