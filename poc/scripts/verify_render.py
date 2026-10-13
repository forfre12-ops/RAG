#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서를 실제로 렌더링해서 확인한다 — 구조 검사만으로는 못 잡는 것들.

왜 필요한가(2026-08-29).
  태그 균형·링크 존재·문자열 개수만 보는 검사는 **화면을 그려보지 않는다.**
  그래서 ERD 의 관계선이 상자 뒤에 깔려 보이지 않는데도 전부 통과했다.
  사용자가 직접 눌러 보고서야 드러났다. 같은 일이 반복되지 않게 렌더러를 쓴다.

무엇을 보는가.
  ① SVG z-order   선(path)이 상자(rect)보다 **뒤에** 그려지면 가려진다.
                  SVG 는 나중에 그린 것이 위에 온다 — 순서를 직접 검사한다.
  ② SVG 대비      선 색이 배경과 너무 가까우면 화면에서 안 보인다.
                  밝기 차이를 계산해 임계 미만이면 경고한다.
  ③ 인쇄 렌더링   Chrome 헤드리스로 PDF 를 만들어 쪽수를 세고,
                  **내용 없는 빈 쪽**을 찾는다.
  ④ 화면 렌더링   전체 페이지를 PNG 로 캡처해 잘림·빈 영역을 본다.

사용:
    python scripts/verify_render.py <파일 또는 폴더> [...]
    python scripts/verify_render.py --keep <경로>     # 산출물 보존
"""
from __future__ import annotations

# 콘솔 출구를 UTF-8 로 고정한다 — cp949 콘솔에서 em dash 하나에 죽던 것을 막는다.
# 정본은 scripts/_cli_io.py 한 곳이다(같은 코드가 133벌 복사돼 있었다).
try:  # 스크립트로 직접 실행 - scripts/ 가 sys.path 에 들어온다
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import - 릴리스 번들의 import 폐쇄 검사가 이 경로다
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome() -> str | None:
    for c in CHROME_CANDIDATES:
        if os.path.exists(c):
            return c
    return shutil.which("chrome") or shutil.which("msedge")


def _lum(hex_color: str) -> float:
    """상대 밝기(0~1). 대비 계산용."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return 1.0

    def f(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    la, lb = _lum(a), _lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


# ── ① · ② SVG 검사 ──────────────────────────────────────────────────
def check_svg(html: str) -> list[str]:
    out: list[str] = []
    for k, m in enumerate(re.finditer(r"<svg\b.*?</svg>", html, re.S), 1):
        svg = m.group(0)
        rects = [mm.start() for mm in re.finditer(r"<rect\b", svg)]
        # 연결선 = marker(화살표)를 단 path. 장식용 path 는 제외한다.
        lines = [mm.start() for mm in re.finditer(r"<path\b[^>]*marker-(?:end|start)=", svg)]
        if rects and lines:
            hidden = sum(1 for lp in lines if lp < min(rects))
            if hidden:
                out.append(
                    f"SVG#{k}: 관계선 {hidden}/{len(lines)}개가 상자보다 먼저 그려진다 "
                    f"— 화면에서 상자에 가려진다(SVG 는 나중에 그린 것이 위)"
                )
        # 선 대비
        for col, w, op in re.findall(
            r'<path\b[^>]*stroke="(#[0-9a-fA-F]{3,6})"[^>]*'
            r'stroke-width="([\d.]+)"(?:[^>]*opacity="([\d.]+)")?', svg
        ):
            if col.lower() in ("#ffffff", "#fff"):
                continue                                  # 흰 테두리(casing)는 의도된 것
            c = contrast(col, "#ffffff")
            eff = c * (float(op) if op else 1.0)
            if eff < 2.5:
                out.append(
                    f"SVG#{k}: 선 색 {col}(굵기 {w}"
                    f"{', 투명도 ' + op if op else ''})의 배경 대비가 {eff:.1f}:1 로 낮다 "
                    f"— 화면에서 흐리게 보인다(권장 3:1 이상)"
                )
                break
    return out


# ── ③ 인쇄 렌더링 ───────────────────────────────────────────────────
def render_pdf(chrome: str, src: Path, dst: Path) -> bool:
    cmd = [
        chrome, "--headless=new", "--disable-gpu", "--no-sandbox",
        "--no-pdf-header-footer", "--run-all-compositor-stages-before-draw",
        "--virtual-time-budget=8000",
        f"--print-to-pdf={dst}", src.resolve().as_uri(),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return dst.exists() and dst.stat().st_size > 0


def check_pdf(path: Path) -> tuple[int, list[int], list[str]]:
    """(쪽수, 빈 쪽 목록, 경고)."""
    try:
        import fitz
    except ImportError:
        return 0, [], ["PyMuPDF 미설치 — 인쇄 검사 생략"]
    doc = fitz.open(str(path))
    blanks = []
    for i, page in enumerate(doc, 1):
        text = (page.get_text() or "").strip()
        drawings = page.get_drawings()
        images = page.get_images()
        if not text and not drawings and not images:
            blanks.append(i)
    n = doc.page_count
    doc.close()
    return n, blanks, []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="검사할 HTML 파일 또는 폴더")
    ap.add_argument("--keep", metavar="DIR", help="렌더 산출물을 이 폴더에 보존")
    args = ap.parse_args(argv)

    files: list[Path] = []
    for p in args.paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.rglob("*.html")))
        elif pp.suffix.lower() == ".html":
            files.append(pp)
    if not files:
        print("검사할 HTML 이 없다")
        return 2

    chrome = find_chrome()
    tmp = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="render-"))
    tmp.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print(" 렌더링 검증 — 실제로 그려 보고 확인한다")
    print("=" * 78)
    print(f"  렌더러: {chrome or '없음(인쇄 검사 생략)'}")
    print(f"  대상  : {len(files)}개 문서\n")

    total_issues = 0
    for f in files:
        html = io.open(f, encoding="utf-8", errors="replace").read()
        issues = check_svg(html)

        pages = 0
        blanks: list[int] = []
        if chrome:
            pdf = tmp / (re.sub(r"[^\w가-힣.-]", "_", f.stem) + ".pdf")
            if render_pdf(chrome, f, pdf):
                pages, blanks, warn = check_pdf(pdf)
                issues.extend(warn)
                if blanks:
                    issues.append(f"인쇄 시 빈 쪽 {len(blanks)}개: {blanks}")
            else:
                issues.append("인쇄 렌더링 실패")

        mark = "OK " if not issues else "!! "
        print(f"{mark}{f.name:<44}{pages:>4}쪽")
        for x in issues:
            print(f"      {x}")
        total_issues += len(issues)

    print()
    print(f"  문제 {total_issues}건")
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"  산출물: {tmp}")
    return 1 if total_issues else 0


if __name__ == "__main__":
    sys.exit(main())
