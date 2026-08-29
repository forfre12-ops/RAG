#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""회신 묶음 도식을 만들어 문서에 다시 주입한다(인라인 SVG · 외부 링크 없음).

왜 생성기인가(2026-08-29). 이 묶음에서 하루에 고친 어긋난 자리 열하나 중 다수가
"코드가 바뀌었는데 문서가 안 따라온 것"이었다. 도식은 그 위험이 더 크다 —
`audit_doc_runtime.py` 는 SVG 안의 글자를 읽지 못하므로 그림이 틀려도 아무도 못 잡는다.
그래서 그림을 손으로 붙이지 않고 **여기서 만들어 표식 사이에 다시 넣는다.** 값이 바뀌면
이 파일의 상수만 고치고 다시 돌린다.

    cd poc && python scripts/build_reply_figures.py          # 다시 만들어 주입
    cd poc && python scripts/build_reply_figures.py --check  # 문서와 다르면 1 로 종료

그리는 것
    seq  등록 -> 분류 2단계 연동 절차. 등록을 건너뛴 경로를 붉은 점선으로 병기한다.
         (「API 통신 방안 검토 회신」 §2-1)
    bar  문서 종류별 파싱 : 판정 시간 분해. 스캔 문서에서 비율이 뒤집히는 것이
         OCR 요건화 요청의 근거다. (「질의사항 회신서」 §4-1)

값의 출처 — bar 의 초 단위는 §4-1 표와 같은 2026-08-28 측정(개발 PC · 추론 스레드 8)이다.
표를 고치면 여기 ROWS 도 함께 고쳐야 한다. 두 곳을 따로 두지 않으려면 측정 리포트를
읽어 오도록 바꿔야 하는데, 그 리포트가 아직 저장소에 없다(2026-08-29 확인).
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent

INK, DIM, LINE, MID = "#0a0a0a", "#71717a", "rgba(0,0,0,.18)", "#f4f4f5"
BAR_LIGHT = "#d4d4d8"
BAD = "#7f1d1d"

FONT = '"Pretendard","Noto Sans KR",sans-serif'


# ── ② 2단계 연동 시퀀스 ──────────────────────────────────────────────────────
def seq_svg() -> str:
    W, H = 940, 470
    lanes = [(120, "관리시스템 (KL 포털)"), (470, "분류 엔진 (Koipa)"), (820, "암호화 저장소")]
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="등록 후 분류를 요청하는 2단계 절차와, 등록을 건너뛴 경우의 경로를 함께 그린 시퀀스 도식">' % (W, H, W))
    a('<style>.t{font:12.5px %s;fill:%s}.th{font:700 13px %s;fill:%s}'
      '.m{font:11.5px ui-monospace,monospace;fill:%s}.d{font:11.5px %s;fill:%s}'
      '.bad{font:700 11.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, INK, FONT, DIM, FONT, BAD))
    a('<defs><marker id="ah" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>'
      '<marker id="ahb" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker></defs>' % (INK, BAD))
    for x, label in lanes:
        a('<rect x="%d" y="14" width="220" height="34" fill="%s" stroke="%s"/>' % (x - 110, MID, LINE))
        a('<text class="th" x="%d" y="36" text-anchor="middle">%s</text>' % (x, label))
        a('<line x1="%d" y1="48" x2="%d" y2="%d" stroke="%s" stroke-dasharray="3 4"/>' % (x, x, H - 60, LINE))

    def arrow(y, x1, x2, label, note=None, bad=False):
        stroke = BAD if bad else INK
        dash = ' stroke-dasharray="6 4"' if bad else ""
        a('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s"%s marker-end="url(#%s)"/>'
          % (x1, y, x2, y, stroke, dash, "ahb" if bad else "ah"))
        mx = (x1 + x2) / 2
        a('<text class="%s" x="%d" y="%d" text-anchor="middle">%s</text>'
          % ("bad" if bad else "t", mx, y - 8, label))
        if note:
            a('<text class="d" x="%d" y="%d" text-anchor="middle">%s</text>' % (mx, y + 15, note))

    def step(y, n, text):
        a('<rect x="8" y="%d" width="22" height="20" fill="%s"/>' % (y - 15, INK))
        a('<text class="th" x="19" y="%d" text-anchor="middle" fill="#fff">%s</text>' % (y - 1, n))
        a('<text class="d" x="36" y="%d">%s</text>' % (y - 1, text))

    step(90, "1", "문서 등록")
    arrow(90, 120, 470, "POST /api/v1/documents", "multipart · file + actor(필수)")
    arrow(122, 470, 820, "원본 저장", "AES-256-GCM 암호화")
    arrow(154, 470, 120, "doc_id (UUID) 발급")
    step(206, "2", "분류 요청")
    arrow(206, 120, 470, "POST /api/v1/classify/async", "{ doc_id, callback_url } · content 없이")
    arrow(238, 470, 120, "job_id 즉시 반환")
    step(290, "3", "완료 통보")
    arrow(290, 470, 120, "POST {callback_url}", "결과 21필드 · 재시도·DLQ 포함")

    a('<line x1="8" y1="330" x2="%d" y2="330" stroke="%s"/>' % (W - 8, LINE))
    a('<text class="bad" x="8" y="352">등록을 건너뛰면 &#8212; 응답은 200 인데 결과가 남지 않는다</text>')
    arrow(388, 120, 470, "POST /api/v1/classify (doc_id 미등록)", "HTTP 200 · 등급도 나온다", bad=True)
    a('<line x1="470" y1="388" x2="820" y2="388" stroke="%s" stroke-dasharray="6 4"/>' % BAD)
    a('<text class="bad" x="645" y="380" text-anchor="middle">저장 안 됨</text>')
    a('<text class="d" x="645" y="403" text-anchor="middle">document_exists 검사에서 영속화를 건너뛴다</text>')
    a('<text class="d" x="36" y="432">&#8226; 검수 대기 목록 · 판정 이력 · 감사 로그 어디에도 남지 않는다. '
      '경고 문자열 한 줄만 응답에 붙어 현장에서 발견하기 어렵다.</text>')
    a('<text class="d" x="36" y="452">&#8226; <tspan class="m">POST /api/v1/documents/analyze</tspan> '
      '(파일 하나로 끝나는 1단계 경로)도 같은 이유로 결과가 남지 않는다 &#8212; 시연·진단 전용이다.</text>')
    a('</svg>')
    return "".join(p)


# ── ④ 처리 시간 구간 분해 ────────────────────────────────────────────────────
# (이름, 글자 수, 파싱 초, 판정 초) — 회신서 §4-1 표와 같은 2026-08-28 측정
ROWS = [
    ("DOCX 100쪽", 179887, 0.5, 78.0),
    ("PDF 100쪽", 188241, 3.2, 78.0),
    ("HWP 32쪽", 58711, 1.4, 29.0),
    ("스캔 PDF 16쪽", 0, 28.5, 3.9),
]


def bar_svg() -> str:
    W, LEFT, RIGHT = 940, 150, 60
    track = W - LEFT - RIGHT
    row_h, top = 54, 58
    H = top + row_h * len(ROWS) + 74
    maxs = 82.0
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="문서 종류별 파싱 시간과 판정 시간을 나눠 그린 가로 막대. 텍스트 문서는 판정이 대부분이고 스캔 문서는 파싱이 대부분이다">'
      % (W, H, W))
    a('<style>.t{font:12.5px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.d{font:11.5px %s;fill:%s}.w{font:700 11.5px %s;fill:#fff}'
      '.bad{font:700 12.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, FONT, DIM, FONT, FONT, BAD))
    for sec in (0, 20, 40, 60, 80):
        x = LEFT + track * sec / maxs
        a('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s"/>'
          % (x, top - 20, x, top + row_h * len(ROWS) - 14, LINE))
        a('<text class="d" x="%.1f" y="%d" text-anchor="middle">%d초</text>' % (x, top - 26, sec))
    a('<rect x="%d" y="12" width="13" height="13" fill="%s"/>' % (LEFT, INK))
    a('<text class="d" x="%d" y="23">파싱(본문 추출)</text>' % (LEFT + 19))
    a('<rect x="%d" y="12" width="13" height="13" fill="%s"/>' % (LEFT + 130, BAR_LIGHT))
    a('<text class="d" x="%d" y="23">등급 판정</text>' % (LEFT + 149))
    for i, (name, chars, parse_s, cls_s) in enumerate(ROWS):
        y = top + i * row_h
        total = parse_s + cls_s
        wp, wc = track * parse_s / maxs, track * cls_s / maxs
        scan = name.startswith("스캔")
        a('<text class="%s" x="%d" y="%d" text-anchor="end">%s</text>'
          % ("bad" if scan else "th", LEFT - 14, y + 16, name))
        a('<text class="d" x="%d" y="%d" text-anchor="end">%s</text>'
          % (LEFT - 14, y + 32, (format(chars, ",") + "자") if chars else "글자 이미지"))
        a('<rect x="%d" y="%d" width="%.1f" height="26" fill="%s"/>' % (LEFT, y, wp, INK))
        a('<rect x="%.1f" y="%d" width="%.1f" height="26" fill="%s"/>' % (LEFT + wp, y, wc, BAR_LIGHT))
        if wp > 52:
            a('<text class="w" x="%.1f" y="%d" text-anchor="middle">%.1f초</text>' % (LEFT + wp / 2, y + 17, parse_s))
        else:
            a('<text class="d" x="%.1f" y="%d">%.1f</text>' % (LEFT + wp + 4, y - 3, parse_s))
        if wc > 52:
            a('<text class="d" x="%.1f" y="%d" text-anchor="middle">%.1f초</text>' % (LEFT + wp + wc / 2, y + 17, cls_s))
        else:
            a('<text class="d" x="%.1f" y="%d">%.1f초</text>' % (LEFT + wp + wc + 6, y + 17, cls_s))
        a('<text class="%s" x="%d" y="%d" text-anchor="end">파싱 %.0f%%</text>'
          % ("bad" if scan else "d", W - 8, y + 17, 100.0 * parse_s / total))
    yb = top + row_h * len(ROWS) + 8
    a('<line x1="8" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (yb, W - 8, yb, LINE))
    a('<text class="t" x="8" y="%d">텍스트 문서는 시간의 <tspan class="th">96~99%%가 등급 판정</tspan>이다 &#8212; '
      '비용을 정하는 것은 파일 크기가 아니라 본문 분량이다.</text>' % (yb + 22))
    a('<text class="bad" x="8" y="%d">스캔 문서는 뒤집힌다 &#8212; 파싱 88%%. 그리고 납품 번들에는 OCR 이 없어 '
      '이 문서는 느린 것이 아니라 처리되지 않는다.</text>' % (yb + 44))
    a('</svg>')
    return "".join(p)


# ── 주입 ─────────────────────────────────────────────────────────────────────
FIGS = {
    "seq": (seq_svg, ["doc/result/KL_회신_2026-08-28/KL_API_통신방안_검토회신.html",
                      "doc/result/KL_AI자료_2026-08/KL_API_통신방안_검토회신.html"]),
    "bar": (bar_svg, ["doc/result/KL_회신_2026-08-28/KL_질의사항_회신서.html",
                      "doc/result/KL_AI자료_2026-08/KL_질의사항_회신서.html"]),
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="다시 만든 그림이 문서와 같은지만 본다")
    args = ap.parse_args()

    diff = 0
    for name, (fn, targets) in FIGS.items():
        svg = fn()
        begin, end = "<!-- FIG:%s -->" % name, "<!-- /FIG:%s -->" % name
        for rel in targets:
            p = _REPO / rel
            if not p.exists():
                print("없음 %s" % rel)
                continue
            s = io.open(p, encoding="utf-8").read()
            if begin not in s or end not in s:
                print("표식 없음 %s — 먼저 %s ... %s 를 넣을 것" % (rel, begin, end))
                diff += 1
                continue
            new = re.sub(re.escape(begin) + ".*?" + re.escape(end), begin + svg + end, s, flags=re.S)
            if new == s:
                print("같음 %-58s %s" % (rel, name))
                continue
            diff += 1
            if args.check:
                print("다름 %-58s %s" % (rel, name))
            else:
                io.open(p, "w", encoding="utf-8", newline="").write(new)
                print("갱신 %-58s %s" % (rel, name))
    if args.check and diff:
        print("문서의 그림이 생성 결과와 다르다 — 인자 없이 다시 돌릴 것")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
