#!/usr/bin/env python
"""KL 제출 묶음(doc/result/KL_AI자료_2026-08) 자체 검증 — open/ 비의존.

2026-08-02 방침 변경: KL 묶음은 이제 **자체 원본**이다. 첨부문서/ 사본을 정본
(doc/result/open/)에서 더 이상 끌어오지 않는다(덮어쓰기 없음). 문서는 이 폴더 안에서
직접 편집하고, 이 스크립트는 **묶음이 완결(내부 링크 0 깨짐·인용 문서 전부 존재)인지
검증만** 한다. open/ 은 감리 릴리스(doc/releases/…) 트랙 전용으로 분리됐다.

  python scripts/sync_kl_bundle.py          # 검증(쓰기 없음). 문제 있으면 exit 1

첨부 대상은 "루트 응답문서가 인용하는 문서 + 그 문서들이 인용하는 문서"의 전이 폐포로
계산하되, 폐포 추적은 **묶음 내부 파일**(첨부문서/)만 읽는다. 검증 항목:
  · 누락  — 인용됐으나 첨부문서/ 에 없는 문서(있으면 깨진 링크가 됨)
  · 깨진 링크 — 묶음 안 상대경로 href 가 실제 파일로 해석 안 됨
  · 고아  — 첨부문서/ 에 있으나 아무 문서도 인용하지 않음(정리 후보 — 자동 삭제 안 함)
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KL = ROOT / "doc" / "result" / "KL_AI자료_2026-08"
ATTACH = KL / "첨부문서"

_HREF = re.compile(r'href="([^"#][^"]*)"')


def _links(html: str) -> list[str]:
    out = []
    for h in _HREF.findall(html):
        if h.startswith(("http", "mailto:", "data:", "javascript:")):
            continue
        out.append(urllib.parse.unquote(h.split("#")[0]))
    return out


def closure() -> set[str]:
    """루트 응답문서에서 출발해 묶음 내부만 따라간 첨부 인용 폐포(파일명 집합)."""
    want: set[str] = set()
    queue: list[str] = []
    for p in sorted(KL.glob("*.html")):
        for h in _links(p.read_text(encoding="utf-8", errors="replace")):
            if h.startswith("첨부문서/"):
                n = h.split("/", 1)[1]
                if n not in want:
                    want.add(n)
                    queue.append(n)
    while queue:
        cur = queue.pop()
        src = ATTACH / cur          # 정본(open/)이 아니라 묶음 내부 사본을 읽는다
        if not src.exists():
            continue
        for h in _links(src.read_text(encoding="utf-8", errors="replace")):
            if "/" in h or not h.endswith(".html"):
                continue
            if h not in want:
                want.add(h)
                queue.append(h)
    return want


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="(호환용) 항상 검증만 수행 — 이 스크립트는 더 이상 쓰지 않는다")
    ap.parse_args()

    if not ATTACH.exists():
        print("첨부문서/ 폴더가 없습니다:", ATTACH)
        return 1

    want = closure()
    present = {f.name for f in ATTACH.iterdir() if f.is_file()}

    missing = sorted(want - present)          # 인용됐는데 파일 없음
    orphan = sorted(present - want)            # 파일 있는데 인용 없음

    broken = []
    for p in sorted(KL.rglob("*.html")):
        for h in _links(p.read_text(encoding="utf-8", errors="replace")):
            if not (p.parent / h).exists():
                broken.append(f"{p.relative_to(KL).as_posix()} -> {h}")

    for n in missing:
        print("누락:    " + n)
    for b in broken:
        print("깨진 링크:", b)
    for n in orphan:
        print("고아(정리 후보):", n)

    total = sum(f.stat().st_size for f in ATTACH.iterdir() if f.is_file()) / 1048576
    print(f"\n첨부문서/ {len(present)}종 · {total:.1f} MB · "
          f"누락 {len(missing)} · 깨진 링크 {len(broken)} · 고아 {len(orphan)}")
    if missing or broken:
        print("→ 발송 불가: 누락/깨진 링크 해소 필요")
        return 1
    print("→ 발송 가능(자체 완결·링크 완결)" + (" · 고아 파일 검토 권장" if orphan else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
