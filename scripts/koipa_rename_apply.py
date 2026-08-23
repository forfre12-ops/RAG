"""lloydk → kopia 리네임 실행. 영역을 지정해 단계적으로 돌린다.

발주처 확정(2026-08-12):
    코드 식별자   kopia          (KL 테스트서버 계정명과 일치)
    기관 도메인   www.koipa.re.kr (기관 공식 — 실존 확인, HTTP 302)
    한글 표기     한국지식재산보호원

⚠ **치환 순서가 중요하다.** `koipa` → `kopia` 를 전역으로 돌리면 도메인이
`www.kopia.re.kr` 로 망가진다(존재하지 않는 주소). 그래서:

    1) 도메인을 토큰으로 빼둔다      lloydk.co.kr / koipa.re.kr → \x00DOMAIN\x00
    2) 나머지 치환을 전부 돌린다      lloydk·Lloydk·LLOYDK·KOIPA·KIPRA·로이드케이
    3) 토큰을 최종 도메인으로 되돌린다 → www.koipa.re.kr

토큰에 NUL 을 쓰는 이유: 텍스트 소스에 나올 수 없어 오치환이 불가능하다.

사용:
    python scripts/kopia_rename_apply.py --area src      # 제품 소스만
    python scripts/kopia_rename_apply.py --area all --apply
기본은 dry-run(--apply 없으면 파일을 쓰지 않는다).
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_TOKEN = "\x00KOIPA_DOMAIN\x00"

SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "site-packages",
}
# 롤백 자산·재생성물·바이너리·데이터 본문
SKIP_PREFIXES = (
    "poc/backups",          # 롤백용 pg_dump — 여기 lloydk 가 남아야 복원된다
    "poc/.venv", "poc/artifacts", "poc/artifacts_out", "poc/models", "poc/mlruns",
    "poc/dist", "poc/logs", "poc/_artifacts", "poc/datasets",
    "docker-data", "sample", ".tmp_hwp_eval_venv",
    # 빌드 산출물 — pip install -e 가 다시 만든다(gitignore 대상)
    "poc/src/lloydk_ai.egg-info",
)
TEXT_SUFFIXES = {
    ".py", ".html", ".md", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini",
    ".toml", ".sh", ".sql", ".example", ".js", ".css",
}

AREAS = {
    "src": ("poc/src", "poc/scripts", "poc/tests", "poc/alembic"),
    "infra": ("poc/docker-compose", "poc/Dockerfile", "poc/deploy", "poc/infra", "poc/Makefile"),
    "docs": ("doc/",),
    "all": ("",),
}

# 순서 그대로 적용된다. 도메인은 위 토큰 단계에서 이미 빠져 있다.
RULES = (
    # 도메인 먼저 — 아래 lloydk 규칙이 lloydk.co.kr 을 koipa.co.kr 로 망치는 것을 막는다
    (re.compile(r"(?:www\.)?lloydk\.co\.kr"), "www.koipa.re.kr"),
    (re.compile(r"LLOYDK"), "KOIPA"),
    (re.compile(r"Lloydk"), "Koipa"),
    (re.compile(r"lloydk"), "koipa"),
    (re.compile(r"로이드케이"), "한국지식재산보호원"),
    # 표기 혼재 정정. KOIPA 는 이미 맞으므로 건드리지 않는다.
    (re.compile(r"KIPRA"), "KOIPA"),
    (re.compile(r"Kipra"), "Koipa"),
    (re.compile(r"kipra"), "koipa"),
)


def skipped(rel: str) -> bool:
    return any(rel.startswith(p) for p in SKIP_PREFIXES)


def in_area(rel: str, prefixes: tuple[str, ...]) -> bool:
    return any(rel.startswith(p) for p in prefixes)


def convert(text: str) -> tuple[str, int]:
    staged, hits = text, 0
    for pattern, repl in RULES:
        staged, n = pattern.subn(repl, staged)
        hits += n
    return staged, hits


def walk(prefixes: tuple[str, ...]):
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not skipped(f"{rel_dir}/{d}".lstrip("/"))
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if skipped(rel) or not in_area(rel, prefixes):
                continue
            yield path, rel


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="lloydk -> kopia 리네임")
    parser.add_argument("--area", choices=sorted(AREAS), default="src")
    parser.add_argument("--apply", action="store_true",
                        help="실제로 쓴다. 없으면 dry-run(파일 미변경)")
    args = parser.parse_args(argv)

    prefixes = AREAS[args.area]
    changed: Counter = Counter()
    total = 0
    for path, rel in walk(prefixes):
        try:
            if path.stat().st_size > 8_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        converted, hits = convert(text)
        if converted == text:
            continue
        changed[rel] = hits
        total += hits
        if args.apply:
            # 줄바꿈 유지 — CRLF 파일을 LF 로 바꿔 버리면 diff 가 전 줄로 부풀고
            # .gitattributes 와 어긋난다.
            with open(path, "r", encoding="utf-8", newline="") as handle:
                raw = handle.read()
            newline = "\r\n" if "\r\n" in raw else "\n"
            body = convert(raw.replace("\r\n", "\n"))[0]
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(body.replace("\n", newline) if newline == "\r\n" else body)

    mode = "적용" if args.apply else "DRY-RUN(미변경)"
    print(f"[{mode}] area={args.area} · {len(changed)} 파일 · {total:,} 회")
    for rel, n in changed.most_common(12):
        print(f"  {n:>5}  {rel}")
    if len(changed) > 12:
        print(f"  … 외 {len(changed)-12} 파일")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
