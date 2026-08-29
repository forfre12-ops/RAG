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


# ── ⑥ 검수 라우팅 게이트 퍼널 ────────────────────────────────────────────────
# 순서와 구성원은 코드가 정본이다(services/review_reasons.REVIEW_GATES).
# 한글 이름만 여기 적는다 — 테이블정의서가 논리명을 table_spec_meta.py 에 두는 것과 같은 방식.
# 코드에 게이트가 늘거나 이름이 바뀌면 아래 assert 가 먼저 깨진다.
GATE_LABELS = {
    "low-confidence": "저신뢰",
    "ingestion-degraded": "열화추출 격리",
    "cap-conflict": "출처 상한이 상향 신호를 덮음",
    "sparse-evidence": "근거 희소",
    "abbrev-only-escalation": "영문 약어만으로 승격",
    "body-below-threshold": "무음 빈본문",
    "metadata-access-conflict": "접근범위와 예측 충돌",
    "metadata-management-conflict": "관리성 부재와 예측 충돌",
    "icd-metadata-fnr-risk": "ICD 규약값 부적합",
    "s2-underclass-risk": "내부 신호가 있는데 S3 예측",
    "gate-fail-open": "게이트가 예외로 미적용",
    "agreement-gate": "룰·모델 불일치",
    "llm-secondopinion": "LLM 2차 의견이 더 높음",
    "kill-gate-brake": "배포 차단 신호 발동 중",
    "similarity-escalation": "사람 확정 문서와 유사",
}

# 실측(2026-08-29 · 경화 홀드아웃 42건 · 배포 프로파일 그대로).
# 재현: python scripts/measure_serving_records.py --eval datasets/gold_real/holdout_eval.hardened.jsonl \
#           --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b --out reports/<이름>.json
MEASURED = {"n": 42, "auto": 34, "review": 8,
            "reasons": {"low-confidence": 6, "agreement-gate": 2}}


def funnel_svg() -> str:
    from koipa.services.review_reasons import REVIEW_GATE_TAGS  # noqa: PLC0415
    tags = [t for t in REVIEW_GATE_TAGS if t != "extraction-gate"]
    missing = [t for t in tags if t not in GATE_LABELS]
    if missing:
        raise SystemExit("게이트가 늘었는데 이름이 없다 — GATE_LABELS 를 갱신할 것: %s" % missing)

    W = 940
    row_h, top = 27, 92
    H = top + row_h * len(tags) + 96
    left, right = 150, 300
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="검수 라우팅 게이트를 순서대로 통과하는 퍼널 도식. 앞 게이트에 걸리면 뒤 게이트는 평가되지 않는다">'
      % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.m{font:10.5px ui-monospace,monospace;fill:%s}.d{font:11px %s;fill:%s}'
      '.n{font:700 11px %s;fill:#fff}.hit{font:700 11px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, DIM, FONT, DIM, FONT, FONT, BAD))
    a('<text class="th" x="8" y="26">문서 한 건이 등급을 받은 뒤 &#8212; 게이트 15개를 순서대로 지난다</text>')
    a('<text class="d" x="8" y="46">앞 게이트가 걸리면 <tspan class="th">뒤 게이트는 평가되지 않는다</tspan>. '
      '그래서 한 문서의 검수 사유는 처음 걸린 게이트 하나다.</text>')
    a('<text class="d" x="8" y="66">아래 숫자는 경화 홀드아웃 %d건 실측(2026-08-29) &#8212; '
      '실제로 발동한 게이트는 <tspan class="hit">둘뿐</tspan>이다.</text>' % MEASURED["n"])
    a('<rect x="%d" y="%d" width="%d" height="20" fill="%s"/>' % (left, top - 26, W - left - right, MID))
    a('<text class="d" x="%d" y="%d">입력 %d건</text>' % (left + 8, top - 12, MEASURED["n"]))
    a('<text class="d" x="%d" y="%d">걸리면 &#8594; 검수</text>' % (W - right + 12, top - 12))

    for i, tag in enumerate(tags):
        y = top + i * row_h
        shrink = int(i * (W - left - right) * 0.012)
        w = W - left - right - shrink
        hit = MEASURED["reasons"].get(tag, 0)
        a('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="%s"/>'
          % (left + shrink // 2, y, w, row_h - 5, "#fafafa" if not hit else "#fff5f5",
             BAD if hit else LINE))
        a('<rect x="%d" y="%d" width="22" height="%d" fill="%s"/>' % (8, y, row_h - 5, INK))
        a('<text class="n" x="19" y="%d" text-anchor="middle">%d</text>' % (y + 15, i + 1))
        a('<text class="t" x="36" y="%d">%s</text>' % (y + 15, GATE_LABELS[tag]))
        a('<text class="m" x="%d" y="%d">%s</text>' % (left + shrink // 2 + 10, y + 15, tag))
        if hit:
            a('<text class="hit" x="%d" y="%d">&#8594; 검수 %d건</text>' % (W - right + 12, y + 15, hit))

    yb = top + row_h * len(tags) + 6
    a('<rect x="%d" y="%d" width="%d" height="26" fill="%s"/>' % (left, yb, W - left - right, INK))
    a('<text class="n" x="%d" y="%d">모든 게이트를 통과 &#8594; 자동확정(staging) %d건 · %.1f%%</text>'
      % (left + 10, yb + 18, MEASURED["auto"], 100.0 * MEASURED["auto"] / MEASURED["n"]))
    a('<text class="hit" x="%d" y="%d">검수 %d건</text>' % (W - right + 12, yb + 18, MEASURED["review"]))
    a('<text class="d" x="8" y="%d">게이트는 <tspan class="th">등급을 바꾸지 않는다</tspan> &#8212; '
      '자동확정할지 사람에게 보낼지만 정한다. 등급을 바꾸는 것은 그 앞의 보정이다.</text>' % (yb + 50))
    a('<text class="d" x="8" y="%d">같은 목록에 16번째 <tspan class="m">extraction-gate</tspan> 가 있으나 '
      '진단·시연 엔드포인트 전용이라 이 그림에서 뺐다.</text>' % (yb + 70))
    a('</svg>')
    return "".join(p)


# ── ⑤ 곱셈 교차 확인 16조합 격자 ─────────────────────────────────────────────
def grid_svg() -> str:
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: PLC0415
    grades = ["TS", "S1", "S2", "S3"]
    rank = {g: i for i, g in enumerate(grades)}
    combos = [(False, False), (False, True), (True, False), (True, True)]

    def cell(content, public, has_mgmt):
        strong = content in ("TS", "S1")
        s = 0 if (public or content == "S3") else (2 if strong else 1)
        v = 2 if strong else (0 if content == "S3" else 1)
        m = 2 if has_mgmt else (0 if content in ("S3", "S1") else 1)
        svm_g = grade_from_svm(s, v, m)
        final = min([svm_g, content], key=lambda g: rank[g])
        return (s, v, m), svm_g, final

    # 감사 스크립트의 전수열거와 같은 답이 나오는지 여기서 확인한다.
    changed = [(c, p_, m_) for c in grades for p_, m_ in combos if cell(c, p_, m_)[2] != c]
    if len(changed) != 1:
        raise SystemExit("등급을 바꾸는 조합이 %d개다 — 판정식이 바뀌었으면 문서도 함께 고칠 것" % len(changed))

    W, CW, CH = 940, 190, 74
    left, top = 190, 96
    H = top + CH * len(combos) + 96
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="키워드 등급과 두 신호의 16개 조합을 모두 적은 격자. 최종 등급이 바뀌는 칸은 하나뿐이다">'
      % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.m{font:11px ui-monospace,monospace;fill:%s}.d{font:11px %s;fill:%s}'
      '.hit{font:700 12px %s;fill:%s}.hd{font:700 12px %s;fill:#fff}</style>'
      % (FONT, INK, FONT, INK, DIM, FONT, DIM, FONT, BAD, FONT))
    a('<text class="th" x="8" y="26">곱셈(S&#215;V&#215;M) 교차 확인 &#8212; 조합 16개 전수</text>')
    a('<text class="d" x="8" y="46">S·V·M 은 <tspan class="th">키워드 등급과 두 신호에서 계산된다</tspan>. '
      '입력이 셋뿐이라 조합이 16개로 닫힌다.</text>')
    a('<text class="d" x="8" y="66">칸 안은 <tspan class="m">s·v·m &#8594; 곱셈 등급</tspan> 이고, '
      '최종 등급은 곱셈과 키워드 등급 중 <tspan class="th">더 높은 보안등급</tspan>이다.</text>')

    for j, g in enumerate(grades):
        x = left + j * CW
        a('<rect x="%d" y="%d" width="%d" height="26" fill="%s"/>' % (x, top - 30, CW - 6, INK))
        a('<text class="hd" x="%d" y="%d" text-anchor="middle">키워드 등급 %s</text>' % (x + (CW - 6) / 2, top - 12, g))
    for i, (public, has_mgmt) in enumerate(combos):
        y = top + i * CH
        label = "공개 신호 %s · 관리 키워드 %s" % ("있음" if public else "없음", "매치" if has_mgmt else "없음")
        a('<text class="t" x="%d" y="%d" text-anchor="end">%s</text>' % (left - 12, y + 30, label))
        for j, g in enumerate(grades):
            x = left + j * CW
            (s, v, m), svm_g, final = cell(g, public, has_mgmt)
            chg = final != g
            a('<rect x="%d" y="%d" width="%d" height="%d" fill="%s" stroke="%s" stroke-width="%s"/>'
              % (x, y, CW - 6, CH - 8, "#fff5f5" if chg else "#fafafa", BAD if chg else LINE, "2" if chg else "1"))
            a('<text class="m" x="%d" y="%d" text-anchor="middle">%d·%d·%d &#8594; %s</text>'
              % (x + (CW - 6) / 2, y + 26, s, v, m, svm_g))
            if chg:
                a('<text class="hit" x="%d" y="%d" text-anchor="middle">최종 %s &#8212; 등급 변경</text>'
                  % (x + (CW - 6) / 2, y + 48, final))
            else:
                a('<text class="d" x="%d" y="%d" text-anchor="middle">최종 %s &#8212; 그대로</text>'
                  % (x + (CW - 6) / 2, y + 48, final))

    yb = top + CH * len(combos) + 14
    a('<line x1="8" y1="%d" x2="%d" y2="%d" stroke="%s"/>' % (yb - 10, W - 8, yb - 10, LINE))
    a('<text class="hit" x="8" y="%d">등급을 바꾸는 조합은 16개 중 하나뿐이다 '
      '&#8212; 키워드 등급 S1 · 공개 신호 없음 · 관리 키워드 매치 &#8594; TS.</text>' % (yb + 8))
    a('<text class="d" x="8" y="%d">평가셋 4종 993건 실측에서 이 조합의 발동은 <tspan class="th">0건</tspan>이었다. '
      '재현: cd poc &amp;&amp; TESTING=1 python scripts/audit_rule_formula.py</text>' % (yb + 28))
    a('<text class="d" x="8" y="%d">곱셈이 더 낮은 등급을 가리켜도 채택하지 않는다 &#8212; '
      '올리기만 하고 내리지 않는다(미탐 방지).</text>' % (yb + 48))
    a('</svg>')
    return "".join(p)


# ── 주입 ─────────────────────────────────────────────────────────────────────
FIGS = {
    "seq": (seq_svg, ["doc/result/KL_회신_2026-08-28/KL_API_통신방안_검토회신.html",
                      "doc/result/KL_AI자료_2026-08/KL_API_통신방안_검토회신.html"]),
    "bar": (bar_svg, ["doc/result/KL_회신_2026-08-28/KL_질의사항_회신서.html",
                      "doc/result/KL_AI자료_2026-08/KL_질의사항_회신서.html"]),
    "funnel": (funnel_svg, ["doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_명세서.html",
                            "doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_쉬운설명서.html"]),
    "grid": (grid_svg, ["doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_명세서.html",
                        "doc/result/KL_회신_2026-08-28/KL_질의사항_회신서.html",
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
