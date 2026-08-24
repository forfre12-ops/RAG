"""콘솔 화면의 글자 대비를 잠근다.

왜 테스트가 필요한가(2026-08-24). 대비를 한 번 고쳤다고 보고했는데 admin.html 에만
`--text-faint`(#a1a1aa · 흰 배경 대비 2.56) 가 37곳 남아 있었다. 그중 대부분이 **빈 상태
안내문**이었다 — 아무것도 없다고 알려 주는 유일한 글자가 화면에서 가장 흐렸다.
고친 것을 잠그지 않으면 다음 편집에서 되돌아온다.

기준(사용자 지시): 본문은 최소 #52525b, 보조 안내도 적어도 #71717a.
따라서 **회색 계열 글자는 흰 배경 대비 4.83(=#71717a) 이상**이어야 한다.
등급색·상태색(빨강/초록/주황/파랑)은 대비 대상이 아니다 — 배경 색을 깔고 쓰거나
아이콘·배지에 붙는 강조색이라 이 규칙의 취지(읽히지 않는 안내문)와 다르다.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "src" / "koipa"
FILES = [
    ROOT / "api" / "static" / "admin.html",
    ROOT / "api" / "static" / "index.html",
    ROOT / "api" / "static" / "styles.css",
    ROOT / "api" / "static" / "app.js",
    ROOT / "api" / "static" / "golden_jobs.js",
    ROOT / "api" / "static" / "upload_progress.js",
    ROOT / "api" / "static" / "deploy_badge.js",
    ROOT / "golden_review_html.py",
    ROOT / "console_nav.py",
]

# 어두운 배경 위에 얹는 글자 — 흰 배경으로 재면 안 된다. 배경을 함께 적어 **그 배경에서**
# 다시 잰다(허용목록이 검사 면제가 되지 않게).
#   .logdock(시연 화면 요청 로그) 배경 #0b1020 · .logdock(관리자 콘솔) 배경 #09090b
DARK_BG = {
    ("styles.css", "#7c8a9e"): "#0b1020",   # .live-log .ll-time · .logdock-hint · .ll-empty
    ("styles.css", "#94a3b8"): "#0b1020",   # #logdock-toggle · .ll-ev
    ("styles.css", "#38bdf8"): "#0b1020",   # 요청 줄
    ("styles.css", "#f87171"): "#0b1020",   # 오류 줄
    ("styles.css", "#e2e8f0"): "#0b1020",   # .logdock-head b
    ("styles.css", "#cbd5e1"): "#0b1020",   # .logdock 본문
    ("styles.css", "#d4d4d8"): "#09090b",   # .sec-parse .log
    ("styles.css", "#4ade80"): "#0b1020",
    ("styles.css", "#fdba74"): "#0b1020",
    ("styles.css", "#eab308"): "#0b1020",
    ("admin.html", "#e4e4e7"): "#09090b",   # .logline 본문
    ("admin.html", "#86efac"): "#09090b",   # .logline.ok
    ("admin.html", "#fca5a5"): "#09090b",   # .logline.err
    ("admin.html", "#fcd34d"): "#09090b",   # .logline.warn
    ("admin.html", "#93c5fd"): "#09090b",   # .logline.req
}


def _luminance(hexstr: str) -> float:
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))

    def lin(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _ratio(fg: str, bg: str = "#ffffff") -> float:
    a, b = _luminance(fg), _luminance(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


MIN_RATIO = _ratio("#71717a")   # 4.83 — 사용자가 정한 보조 안내 하한


def _is_grey(hexstr: str) -> bool:
    """등급색·상태색을 걸러내기 위한 회색 판정 — 채널 폭이 좁으면 회색."""
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    ch = [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    return max(ch) - min(ch) <= 40


_HEX = re.compile(r"color:\s*(#[0-9a-fA-F]{3,6})\b")
_VAR = re.compile(r"color:\s*var\((--[\w-]+)\)")
_TOKEN = re.compile(r"(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,6})\b")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_grey_text_meets_the_minimum_contrast(path: Path):
    """회색 글자는 흰 배경에서 #71717a 만큼은 읽혀야 한다."""
    if not path.exists():
        pytest.skip(f"{path.name} 없음")
    text = path.read_text(encoding="utf-8")
    tokens = dict(_TOKEN.findall(text))

    seen: set[str] = {m.group(1).lower() for m in _HEX.finditer(text)}
    seen |= {tokens[m.group(1)].lower() for m in _VAR.finditer(text) if m.group(1) in tokens}

    bad = []
    for color in sorted(seen):
        bg = DARK_BG.get((path.name, color))
        if bg is None and not _is_grey(color):
            continue            # 등급색·상태색 — 이 규칙의 대상이 아니다
        r = _ratio(color, bg or "#ffffff")
        if r >= MIN_RATIO - 0.01:
            continue
        if bg is None and r < 1.6:
            continue            # 흰색에 가까운 값 = 배경을 못 찾은 글자(허용목록에 적을 것)
        where = f" on {bg}" if bg else ""
        bad.append(f"{color}{where}(대비 {r:.2f})")
    assert not bad, (
        f"{path.name}: 흰 배경에서 대비 {MIN_RATIO:.2f} 에 못 미치는 회색 글자 — " + ", ".join(bad)
        + ". 보조 안내는 최소 #71717a, 본문은 #52525b."
    )


def test_faint_token_is_never_used_for_text():
    """--text-faint(#a1a1aa)는 점·표식 배경 전용이다. 글자에 쓰면 안 된다."""
    offenders = []
    for path in FILES:
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        n = len(re.findall(r"color:\s*var\(--text-faint\)", text))
        if n:
            offenders.append(f"{path.name} x{n}")
    assert not offenders, (
        "--text-faint 를 글자 색으로 쓰고 있다: " + ", ".join(offenders)
        + ". 빈 상태·표 안내처럼 정보를 전달하는 글자는 최소 --text-dim(#71717a)."
    )
