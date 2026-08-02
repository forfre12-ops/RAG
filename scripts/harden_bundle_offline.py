#!/usr/bin/env python
"""제출 묶음 오프라인 하드닝 — 외부 리소스 제거 + JS-off 판독성 확보.

감리 제출 묶음은 폐쇄망/오프라인/인쇄/문서관리시스템에서 열린다. 두 가지가 깨진다:

  1) 외부 웹폰트(Google Fonts) — 26개 첨부가 fonts.googleapis.com 을 불러온다.
     오프라인에서 서체가 깨질 뿐 아니라, index 의 "외부 링크 없음" 서술이 사실과
     달라진다(감리에서 한 줄이 틀리면 나머지 서술의 신뢰도까지 내려간다).
     → <link preconnect/stylesheet> 제거. CSS 폰트 스택에 시스템 폰트 폴백이
       이미 있어(Pretendard·Apple SD Gothic Neo·Noto Sans KR·sans-serif) 그대로 읽힌다.

  2) 판정근거 보고서가 JS 없이는 "근거 없음"으로 보인다 — 777건이 전부
     <script id="data"> JSON 안에 있고 카드가 런타임에 그려진다. JS 를 끄면 본문
     1,483자만 남고 카드 자리에 "조건에 맞는 문서가 없습니다" 가 뜬다. "안 보인다"
     보다 나쁘다 — 요청 3 의 핵심 산출물이 '근거가 없다'로 읽힌다.
     → 같은 JSON 에서 정적 표를 만들어 <noscript> 로 함께 싣는다. JS 가 살아 있으면
       기존 카드 UI 가 그대로, 꺼져 있으면 전건 표가 보인다.

사용:
  python scripts/harden_bundle_offline.py           # 적용(멱등)
  python scripts/harden_bundle_offline.py --check   # 검사만 — 위반 있으면 exit 1
"""

from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    ROOT / "doc" / "result" / "open",
    ROOT / "doc" / "result" / "KL_AI자료_2026-08",
]

# Google Fonts 를 부르는 <link> 3형태(preconnect 2 + stylesheet 1).
FONT_LINK = re.compile(
    r'[ \t]*<link[^>]*(?:fonts\.googleapis\.com|fonts\.gstatic\.com)[^>]*>\s*\n?',
    re.I,
)
EXTERNAL = re.compile(r'(?:href|src)\s*=\s*["\']https?://', re.I)

NOSCRIPT_MARK = "<!-- offline-fallback: static evidence table -->"

GRADE_LABEL = {"TS": "특급", "S1": "1급", "S2": "2급", "S3": "3급"}


def strip_font_links(text: str) -> tuple[str, int]:
    out, n = FONT_LINK.subn("", text)
    return out, n


def _cell(v) -> str:
    if v is True:
        return "○"
    if v is False:
        return "×"
    if v is None:
        return "—"
    return html.escape(str(v))


def build_static_table(rows: list[dict]) -> str:
    """카드 UI 와 같은 JSON 에서 전건 정적 표를 만든다(JS-off 판독용)."""
    cols = [
        ("id", "문서 ID"), ("dom", "도메인"), ("g", "정답 등급"),
        ("r", "룰 등급"), ("m", "모델 등급"), ("f", "최종 등급"),
        ("ag", "룰·모델 합의"), ("src", "라벨 출처"), ("tier", "계층"),
        ("dp", "결정 경로"),
    ]
    head = "".join(f"<th>{html.escape(t)}</th>" for _, t in cols)
    body = []
    for r in rows:
        tds = "".join(f"<td>{_cell(r.get(k))}</td>" for k, _ in cols)
        body.append(f"<tr>{tds}</tr>")
    dist: dict[str, int] = {}
    for r in rows:
        dist[str(r.get("g"))] = dist.get(str(r.get("g")), 0) + 1
    dist_txt = " · ".join(
        f"{g}({GRADE_LABEL.get(g, g)}) {dist[g]}건" for g in ("TS", "S1", "S2", "S3") if g in dist
    )
    return f"""{NOSCRIPT_MARK}
<noscript>
  <div style="border:2px solid #0a0a0a;padding:14px 16px;margin:18px 0;background:#fafafa">
    <b>스크립트 없이 열람 중입니다.</b> 아래는 위 카드 UI 와 <b>동일한 데이터</b>를 정적 표로
    옮긴 것입니다 — 전 {len(rows)}건 전수이며 생략이 없습니다. 등급 분포: {html.escape(dist_txt)}.
    각 건의 근거 문장·신뢰도 분포까지 보시려면 스크립트를 허용해 주십시오.
  </div>
  <table style="width:100%;border-collapse:collapse;font-size:11.5px">
    <thead><tr style="background:#f4f4f5">{head}</tr></thead>
    <tbody>
{chr(10).join(body)}
    </tbody>
  </table>
</noscript>
"""


def extract_rows(text: str) -> list[dict] | None:
    """카드 데이터를 담은 JSON 배열을 꺼낸다.

    생성기 세대에 따라 두 형태가 있다 — `<script id="data">[…]</script>` 와
    `<script>const DATA = […];</script>`. 한쪽만 보다가 조용히 못 찾는 일이
    실제로 있었으므로(그리고 --check 가 그걸 통과시켰으므로) 둘 다 본다.
    """
    for pat in (
        r'<script[^>]*id=["\']data["\'][^>]*>(.*?)</script>',
        r'<script[^>]*>\s*const\s+DATA\s*=\s*(\[.*?\])\s*;?\s*(?:</script>|const |function |window\.)',
    ):
        m = re.search(pat, text, re.S)
        if not m:
            continue
        try:
            rows = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(rows, list) and rows and isinstance(rows[0], dict):
            return rows
    return None


def looks_js_only(text: str) -> bool:
    """스크립트를 걷어내면 알맹이가 사라지는 문서인가(= JS-off 에서 빈 화면)."""
    stripped = re.sub(r'<script.*?</script>|<style.*?</style>', '', text, flags=re.S)
    visible = re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', stripped)).strip()
    # 스크립트가 문서의 대부분이고, 남는 본문이 한 화면 분량도 안 되면 위험.
    return len(text) > 200_000 and len(visible) < 4_000


def harden_evidence_report(path: Path, text: str) -> tuple[str, bool]:
    """판정근거 보고서에 <noscript> 정적 표를 삽입(이미 있으면 그대로)."""
    if NOSCRIPT_MARK in text:
        return text, False
    rows = extract_rows(text)
    if rows is None:
        return text, False
    block = build_static_table(rows)
    # 카드 그리드 컨테이너 바로 뒤에 넣는다 — 없으면 </body> 앞.
    anchor = re.search(r'<[a-z]+[^>]*id=["\']grid["\'][^>]*>(?:\s*</[a-z]+>)?', text)
    if anchor:
        return text[: anchor.end()] + "\n" + block + text[anchor.end() :], True
    idx = text.rfind("</body>")
    if idx < 0:
        return text, False
    return text[:idx] + block + text[idx:], True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="검사만 — 위반 시 exit 1")
    args = ap.parse_args()

    violations: list[str] = []
    changed = 0
    for base in TARGETS:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.html")):
            orig = io.open(p, encoding="utf-8", errors="ignore").read()
            text, nfont = strip_font_links(orig)
            text, added = harden_evidence_report(p, text)
            rel = p.relative_to(ROOT).as_posix()
            if args.check:
                if nfont:
                    violations.append(f"외부 웹폰트 {nfont}건: {rel}")
                # [중요] added=False 는 "이미 되어 있음"과 "데이터를 못 찾음" 둘 다다.
                # 후자를 통과시키면 검사가 무의미하므로 JS-only 문서는 별도로 못 박는다.
                if looks_js_only(orig) and NOSCRIPT_MARK not in orig:
                    violations.append(f"JS-off 시 빈 화면(대체 표 없음): {rel}")
                for m in EXTERNAL.finditer(orig):
                    violations.append(f"외부 리소스 참조: {rel} ({m.group(0)}…)")
                    break
                continue
            if text != orig:
                io.open(p, "w", encoding="utf-8", newline="").write(text)
                changed += 1
                bits = []
                if nfont:
                    bits.append(f"웹폰트 {nfont}건 제거")
                if added:
                    bits.append("JS-off 정적 표 삽입")
                print(f"  {rel} — {' · '.join(bits)}")

    if args.check:
        if violations:
            print(f"[FAIL] 오프라인 하드닝 위반 {len(violations)}건")
            for v in violations[:20]:
                print("  -", v)
            return 1
        print("[OK] 외부 리소스 0 · JS-off 판독 가능")
        return 0
    print(f"\n하드닝 완료 — {changed}개 파일 수정")
    return 0


if __name__ == "__main__":
    sys.exit(main())
