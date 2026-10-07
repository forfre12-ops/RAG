"""콘솔 화면 문구의 형식을 잠근다.

왜(2026-08-24). 같은 뜻을 화면마다 다른 말로 쓰고 있었다.
  진행 중  「불러오는 중…」 3곳 · 「조회 중…」 3곳
  버튼     「검수 시작」 19곳 · [검수 시작] 13곳 · "분류하기" 1곳
읽는 사람은 같은 것을 가리키는 말인지 매번 다시 판단해야 한다. 한 형식으로 모으고,
되돌아오지 않게 여기서 막는다.

빈 상태 문구 형식(코드 리뷰용 기준 — 기계로 재지 않는다):
    <무엇>이 없습니다 — <다음에 할 일>        문장 끝 마침표 없음
    「<버튼>」을 누르면 <무엇>이 나옵니다     아직 조회하지 않은 상태
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "koipa"
FILES = [
    ROOT / "api" / "static" / "admin.html",
    ROOT / "api" / "static" / "index.html",
    ROOT / "api" / "static" / "app.js",
    ROOT / "api" / "static" / "golden_jobs.js",
    ROOT / "api" / "static" / "upload_progress.js",
    ROOT / "golden_review_html.py",
    ROOT / "console_nav.py",
]

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
_PY_DOCSTRING = re.compile(r'"""(?:(?!""").)*"""', re.S)
_LINE_COMMENT = re.compile(r"^\s*(?://|#).*$", re.M)


def _code_only(text: str) -> str:
    """주석·독스트링을 뺀다 — 주석의 표기까지 강제할 생각은 없다."""
    text = _BLOCK_COMMENT.sub(" ", text)
    text = _HTML_COMMENT.sub(" ", text)
    text = _PY_DOCSTRING.sub(" ", text)
    return _LINE_COMMENT.sub(" ", text)


def test_progress_wording_is_one_phrase():
    """진행 중은 「불러오는 중…」 하나로 말한다."""
    bad = []
    for f in FILES:
        if not f.exists():
            continue
        n = _code_only(f.read_text(encoding="utf-8")).count("조회 중…")
        if n:
            bad.append(f"{f.name} x{n}")
    assert not bad, (
        "진행 중 표시에 「조회 중…」이 남아 있다: " + ", ".join(bad)
        + ". 「불러오는 중…」 하나로 쓴다."
    )


def test_button_labels_use_one_bracket_style():
    """화면에서 버튼·탭을 가리킬 때는 「」 만 쓴다."""
    square = re.compile(r"\[[가-힣][가-힣 ·]{1,18}\]")
    bad = []
    for f in FILES:
        if not f.exists():
            continue
        hits = sorted(set(square.findall(_code_only(f.read_text(encoding="utf-8")))))
        # 예시 문서 본문에 실린 등급 표기는 버튼이 아니다(시연 샘플 설명).
        hits = [h for h in hits if h not in ("[특급기밀]",)]
        if hits:
            bad.append(f"{f.name}: {', '.join(hits)}")
    assert not bad, (
        "버튼·탭을 대괄호로 적었다: " + " / ".join(bad) + ". 「」 로 통일한다."
    )
