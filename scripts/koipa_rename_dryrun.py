"""lloydk → koipa 리네임 영향 범위 리포트 (dry-run · 파일을 고치지 않는다).

8,482회 치환 + 패키지 디렉터리 + 컨테이너 + DB 계정이 걸린 작업이라, **무엇이 바뀌는지
먼저 보고 승인한 뒤** 실행한다. 이 스크립트는 읽기만 한다.

치환 규칙(대소문자 보존):
    lloydk → koipa · Lloydk → Koipa · LLOYDK → KOIPA · 로이드케이 → 한국지식재산보호원
    KIPRA → KOIPA (표기 혼재 정정)
    lloydk.co.kr → **제거 대상**(대체 URL 미정 — 별도 결정 필요)

⛔ 건드리면 안 되는 것:
    · poc/backups/            방금 만든 롤백용 pg_dump. 여기 lloydk 가 남아야 복원된다
    · KL 서버 접속 계정 kopia  발주처 자산이다(우리 DB 계정과 별개)
    · .git / .venv / 바이너리
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "site-packages",
}
# 롤백 자산·재생성물·바이너리 — 치환하면 안 되거나 의미가 없다
SKIP_PREFIXES = (
    "poc/backups",           # 롤백용 덤프. lloydk 가 남아야 복원 가능
    "poc/.venv", "poc/artifacts", "poc/artifacts_out", "poc/models", "poc/mlruns",
    "poc/dist", "poc/logs", "poc/_artifacts", "docker-data", "sample",
    ".tmp_hwp_eval_venv",
    # 코퍼스 본문은 리네임 대상이 아니다(데이터이지 코드가 아니다). 그리고 639M+ 라
    # 전수 읽기가 스캔을 몇 분씩 잡아먹는다. 매니페스트만 별도로 확인한다.
    "poc/datasets",
)
TEXT_SUFFIXES = {
    ".py", ".html", ".md", ".yml", ".yaml", ".json", ".txt", ".cfg", ".ini",
    ".toml", ".sh", ".sql", ".env", ".example", ".js", ".css", ".jsonl",
}

# 확정 식별자는 **kopia** 다(발주처 지시, 2026-08-12). 기관 공식 약칭은 KOIPA 이지만
# 발주처가 kopia 를 지정했고 KL 테스트서버 계정명과도 일치한다. 따라서 코드에 이미 있는
# KOIPA 396회도 함께 KOPIA 로 바꾼다 — 안 바꾸면 두 표기가 남는다.
PATTERNS = {
    "lloydk": re.compile(r"lloydk"),
    "Lloydk": re.compile(r"Lloydk"),
    "LLOYDK": re.compile(r"LLOYDK"),
    "로이드케이": re.compile(r"로이드케이"),
    "KOIPA/Koipa/koipa": re.compile(r"KOIPA|Koipa|koipa"),
    "KIPRA": re.compile(r"KIPRA", re.IGNORECASE),
    "lloydk.co.kr": re.compile(r"lloydk\.co\.kr"),
}


def skipped(rel: str) -> bool:
    return any(rel.startswith(p) for p in SKIP_PREFIXES)


def walk():
    """os.walk 로 내려가며 제외 디렉터리는 **내려가기 전에** 쳐낸다.

    rglob 은 제외 대상 안까지 전부 stat 하다가 docker-data 의 심볼릭 캐시에서
    WinError 1920 으로 죽는다(실측). 제외는 순회 단계에서 해야 한다.
    """
    import os

    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel_dir = Path(dirpath).relative_to(ROOT).as_posix()
        rel_dir = "" if rel_dir == "." else rel_dir
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS
            and not skipped(f"{rel_dir}/{d}".lstrip("/"))
        ]
        for name in filenames:
            path = Path(dirpath) / name
            if path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            rel = path.relative_to(ROOT).as_posix()
            if skipped(rel):
                continue
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            yield path, rel


def main() -> int:
    totals: Counter = Counter()
    per_area: dict[str, Counter] = {}
    domain_files: list[str] = []
    unreadable = 0

    for path, rel in walk():
        try:
            if path.stat().st_size > 8_000_000:      # 8MB 초과는 코드가 아니다
                continue
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            unreadable += 1
            continue
        area = (
            "제품 소스 (poc/src)" if rel.startswith("poc/src") else
            "스크립트 (poc/scripts)" if rel.startswith("poc/scripts") else
            "테스트 (poc/tests)" if rel.startswith("poc/tests") else
            "인프라 (compose/Dockerfile)" if rel.startswith("poc/docker") or "Dockerfile" in rel else
            "제출문서 (doc/result)" if rel.startswith("doc/result") else
            "과거 릴리스 (doc/releases)" if rel.startswith("doc/releases") else
            "내부문서 (doc/internal)" if rel.startswith("doc/internal") else
            "기타"
        )
        bucket = per_area.setdefault(area, Counter())
        for name, pattern in PATTERNS.items():
            n = len(pattern.findall(text))
            if n:
                totals[name] += n
                bucket[name] += n
                bucket["_files"] += 0
        if any(p.search(text) for p in PATTERNS.values()):
            bucket["_files"] += 1
        if PATTERNS["lloydk.co.kr"].search(text):
            domain_files.append(rel)

    print("=" * 74)
    print("lloydk -> kopia 리네임 영향 범위 (dry-run · 파일 미변경)")
    print("=" * 74)
    print("\n[패턴별 총 출현]")
    for name in PATTERNS:
        print(f"  {name:16} {totals[name]:>7,} 회")

    print("\n[영역별]")
    for area in sorted(per_area, key=lambda a: -sum(v for k, v in per_area[a].items() if k != "_files")):
        b = per_area[area]
        n = sum(v for k, v in b.items() if k != "_files")
        if not n:
            continue
        print(f"  {area:28} {b['_files']:>4} 파일 · {n:>7,} 회")

    print(f"\n[lloydk.co.kr] {len(domain_files)} 파일 — **대체 URL 미정**")
    for rel in domain_files[:6]:
        print(f"    {rel}")
    if len(domain_files) > 6:
        print(f"    … 외 {len(domain_files)-6}건")

    print("\n[디렉터리 이동]")
    pkg = ROOT / "poc" / "src" / "lloydk"
    if pkg.is_dir():
        n = sum(1 for _ in pkg.rglob("*") if _.is_file())
        print(f"  poc/src/lloydk/ -> poc/src/kopia/   ({n} 파일)")

    print("\n[치환 제외 — 의도적]")
    for p in SKIP_PREFIXES:
        print(f"  {p}")
    print("  KL 서버 접속 계정 kopia (발주처 자산 · 우리 DB 계정과 별개)")
    if unreadable:
        print(f"\n(디코딩 불가로 건너뛴 파일 {unreadable}개)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
