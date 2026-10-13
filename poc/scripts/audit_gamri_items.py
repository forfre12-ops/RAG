#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""설계단계 감리 개선방향 항목을 **전수로** 센다 — 남은 일을 물을 때마다 다시 세지 않으려고.

■ 왜 필요한가

"남은 작업" 을 물으면 감리 지적이 분모의 한 축인데, 정본이 240쪽 PDF(git 밖)라
그동안 아무도 세지 못했다. 2026-09-13 까지 세 번 같은 질문이 나왔고 세 번 다
"세지 못했다" 로 답했다. 도구가 없다는 뜻이다.

■ 세는 법 (분모를 틀리지 않으려고 두 번 틀렸다)

  ① `(가)` 를 전부 세면 **87개** — "<<현황 및 문제점>> (나)항에서 제시한" 같은
     본문 참조까지 센 값이다. 과다.
  ② **줄 시작**의 마커만 세면 **48개**. 마커가 쪽마다 가·나·다… 순서로 이어지는지
     함께 검사해 누락이 없음을 보인다(15쪽 전부 정상).

  ⚠ 쪽 번호는 두 벌이다 — **인쇄 쪽 = PDF 뷰어 쪽 − 8**. 이 도구는 인쇄 쪽으로 말한다.

■ AI 부문

  인쇄 137·142·145·160 = Ⅱ.5 인공지능 본체의 개선방향
  인쇄 177·185·193     = Ⅲ 별첨1(AI 모델/학습데이터/골든셋)
  나머지 8쪽은 사업관리·품질·응용·DB·시스템구조 — **그 안에도 우리 몫이 섞여 있다**
  (미확정 요구사항 FUN-003-02·FUN-005-01 · DB 영역 71·79 의 AI 솔루션 테이블).

사용:
    python scripts/audit_gamri_items.py --pdf "<감리보고서.pdf>"
    python scripts/audit_gamri_items.py --pdf "..." --json
    python scripts/audit_gamri_items.py --pdf "..." --ai-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

# 개선방향이 실린 인쇄 쪽. PDF 전수 검색("개선방향" 포함 쪽)으로 확인한 목록이다.
PAGES = [25, 35, 47, 56, 71, 79, 102, 130, 137, 142, 145, 160, 177, 185, 193]
AI_PAGES = {137, 142, 145, 160, 177, 185, 193}
PAGE_OFFSET = 8  # 인쇄 쪽 + 8 = PDF 쪽(1-based)
MARKER_ORDER = "가나다라마바사아자차카타파하"

SECTION = {
    25: "사업관리 — 변경/일정관리",
    35: "사업관리 — 품질보증활동",
    47: "응용시스템",
    56: "응용시스템 — 테스트/아키텍처",
    71: "데이터베이스",
    79: "데이터베이스 — 데이터 유효성",
    102: "시스템구조 및 보안",
    130: "시스템구조 — 산출물/인터페이스/성능",
    137: "[AI] 사업관리 및 품질보증활동",
    142: "[AI] 응용시스템",
    145: "[AI] 데이터베이스",
    160: "[AI] 시스템구조 및 보안",
    177: "[AI] 별첨1 — 모델 및 알고리즘",
    185: "[AI] 별첨1 — 학습데이터 품질",
    193: "[AI] 별첨1 — 골든셋 검수",
}


def extract(pdf: Path) -> dict:
    import fitz  # noqa: PLC0415

    doc = fitz.open(str(pdf))
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()[:16]
    items, page_rows = [], []
    for printed in PAGES:
        idx = printed + PAGE_OFFSET - 1
        if idx >= doc.page_count:
            page_rows.append({"printed_page": printed, "error": "범위 밖"})
            continue
        text = doc[idx].get_text()
        head = text.find("개선방향")
        seg = text[head:] if head >= 0 else text
        found = list(re.finditer(r"^\s*\(([가-하])\)", seg, re.M))
        markers = [m.group(1) for m in found]
        ordered = markers == list(MARKER_ORDER[: len(markers)])
        for i, m in enumerate(found):
            start = m.end()
            end = found[i + 1].start() if i + 1 < len(found) else len(seg)
            body = re.sub(r"\s+", " ", seg[start:end]).strip()
            items.append({
                "printed_page": printed,
                "section": SECTION.get(printed, "?"),
                "is_ai": printed in AI_PAGES,
                "marker": m.group(1),
                "text": body,
            })
        page_rows.append({
            "printed_page": printed, "section": SECTION.get(printed, "?"),
            "is_ai": printed in AI_PAGES, "items": len(found),
            "markers": "".join(markers), "marker_order_ok": ordered,
        })
    return {
        "pdf": str(pdf), "pdf_sha256_16": sha, "pdf_pages": doc.page_count,
        "page_offset_note": "인쇄 쪽 = PDF 뷰어 쪽 − 8",
        "counting_rule": "줄 시작의 (가)~(하) 마커만 센다. 문장 중간의 '(나)항에서' 는 참조이지 항목이 아니다.",
        "total_items": len(items),
        "ai_items": sum(1 for x in items if x["is_ai"]),
        "pages": page_rows, "items": items,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="감리 개선방향 항목 전수 계수")
    ap.add_argument("--pdf", required=True, help="감리수행결과보고서 PDF 경로")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--ai-only", action="store_true", help="AI 부문 쪽만 출력")
    ap.add_argument("--full", action="store_true", help="항목 전문을 자르지 않는다")
    a = ap.parse_args(argv)

    pdf = Path(a.pdf)
    if not pdf.exists():
        raise SystemExit(f"PDF 가 없다: {pdf}")
    data = extract(pdf)

    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
        return 0

    w = sys.stdout.write
    w("=" * 92 + "\n")
    w(" 설계단계 감리 — 개선방향 항목 전수\n")
    w("=" * 92 + "\n")
    w(f"  정본      {pdf.name}\n")
    w(f"  지문      sha256[:16] = {data['pdf_sha256_16']} · {data['pdf_pages']}쪽\n")
    w(f"  세는 법   {data['counting_rule']}\n")
    w(f"  쪽 번호   {data['page_offset_note']}\n\n")
    bad = [p for p in data["pages"] if not p.get("marker_order_ok", True)]
    w(f"  **총 {data['total_items']} 항목** / {len(data['pages'])}개 쪽"
      f"   (AI 부문 {data['ai_items']} · 그 밖 {data['total_items'] - data['ai_items']})\n")
    w(f"  마커 순서 검사: {'전 쪽 정상 — 누락 없음' if not bad else '⚠어긋난 쪽 ' + str([p['printed_page'] for p in bad])}\n")
    w("-" * 92 + "\n")
    for p in data["pages"]:
        if a.ai_only and not p.get("is_ai"):
            continue
        w(f"  인쇄 {p['printed_page']:3d}쪽  {p['items']:2d}항목  {p['section']}\n")
    w("-" * 92 + "\n\n")
    for it in data["items"]:
        if a.ai_only and not it["is_ai"]:
            continue
        body = it["text"] if a.full else it["text"][:150]
        w(f"[{it['printed_page']:3d}·{it['marker']}] {it['section']}\n    {body}\n\n")
    w("⛔ 이 목록은 '감리가 무엇을 요구했나' 이지 '무엇이 조치됐나' 가 아니다.\n"
      "   조치 여부는 사람이 판정한다 — 이 도구는 분모만 준다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
