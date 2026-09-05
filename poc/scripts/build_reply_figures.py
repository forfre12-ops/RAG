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

def _response_field_count() -> int:
    """분류 응답 필드 수 — 스키마에서 센다.

    [2026-09-05] 종전에는 도식에 "21필드"라고 손으로 적혀 있었다. 같은 문서 본문은
    "20필드"라고 적고 코드도 20 이었다 — 그림만 하나 더 세고 있었다. 그림 속 글자는
    audit_doc_runtime.py 가 못 읽으므로 아무도 잡지 못한다. 그래서 여기서 센다.
    """
    sys.path.insert(0, str(_REPO / "poc" / "src"))
    from koipa.schemas.classify import ClassifyResponse  # noqa: PLC0415

    return len(ClassifyResponse.model_fields)

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
    arrow(290, 470, 120, "POST {callback_url}",
          "결과 %d필드 · 재시도·DLQ 포함" % _response_field_count())

    a('<line x1="8" y1="330" x2="%d" y2="330" stroke="%s"/>' % (W - 8, LINE))
    a('<text class="bad" x="8" y="352">등록을 건너뛴 경우 &#8212; 응답은 200 이나 분류 결과가 저장되지 않는다</text>')
    arrow(388, 120, 470, "POST /api/v1/classify (doc_id 미등록)", "HTTP 200 · 등급도 나온다", bad=True)
    a('<line x1="470" y1="388" x2="820" y2="388" stroke="%s" stroke-dasharray="6 4"/>' % BAD)
    a('<text class="bad" x="645" y="380" text-anchor="middle">결과 미저장</text>')
    a('<text class="d" x="645" y="403" text-anchor="middle">document_exists 검사에서 영속화를 건너뛴다</text>')
    a('<text class="d" x="36" y="432">&#8226; 분류 결과가 검수 대기 목록 · 판정 이력에 저장되지 않는다 '
      '(호출 자체는 감사 로그에 기록된다).</text>')
    a('<text class="d" x="36" y="452">&#8226; <tspan class="m">POST /api/v1/documents/analyze</tspan> '
      '(파일 하나로 끝나는 1단계 경로)도 같은 이유로 결과가 저장되지 않는다 &#8212; 시연·진단 전용 경로다.</text>')
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
    a('<text class="th" x="8" y="26">문서 한 건이 등급을 받은 뒤 &#8212; '
      '게이트 %d개를 순서대로 지난다</text>' % len(tags))
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
    a('<text class="d" x="8" y="%d">게이트는 운영 적재 경로 기준 %d개다 &#8212; '
      '진단·시연 전용 항목은 이 그림에서 뺐다.</text>' % (yb + 70, len(tags)))
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


# ── 보정 순서 — 코드에서 읽는다 ──────────────────────────────────────────────
# 순서가 곧 결과다(출처 상한이 먼저 내리고 보안표시 하한이 뒤에 올린다). 손으로 적으면
# 누가 순서를 바꿔도 그림만 옛것으로 남으므로 pipeline.py 의 등장 순서를 그대로 읽는다.
CORRECTIONS = [
    ("fnr_safe_override", "미탐 방지 상향", "룰 점수가 임계를 넘으면 분류기 등급을 올린다", "up"),
    ("source_prior_enabled", "출처 상한", "출처가 공개 문서면 S3 로 내린다 — 유일한 하향", "down"),
    ("metadata_floor_enabled", "보안표시 하한", "문서에 찍힌 보안표시가 예측보다 높으면 올린다", "up"),
]


def correction_order() -> list:
    """pipeline.py 에서 세 보정이 나오는 순서를 확인하고 CORRECTIONS 를 그 순서로 돌려준다."""
    src = io.open(_HERE.parent / "src" / "koipa" / "modules" / "m5_inference" / "pipeline.py",
                  encoding="utf-8").read().split("\n")
    pos = {}
    for key, _, _, _ in CORRECTIONS:
        for i, line in enumerate(src):
            if key in line and ("_record_gate_fail_open" in line or "getattr(" in line):
                pos.setdefault(key, i)
    missing = [k for k, *_ in CORRECTIONS if k not in pos]
    if missing:
        raise SystemExit("보정을 코드에서 못 찾았다 — CORRECTIONS 를 갱신할 것: %s" % missing)
    ordered = sorted(CORRECTIONS, key=lambda c: pos[c[0]])
    if [c[0] for c in ordered] != [c[0] for c in CORRECTIONS]:
        raise SystemExit("코드의 보정 순서가 바뀌었다 — 그림과 문서를 함께 고칠 것: %s"
                         % [c[0] for c in ordered])
    return ordered


def _profile(key, default=None):
    from koipa.config import _PROFILE_DEFAULTS, Settings  # noqa: PLC0415
    prof = _PROFILE_DEFAULTS.get("full-train", {})
    if key in prof:
        return prof[key]
    f = Settings.model_fields.get(key)
    return f.default if f is not None else default


# ── ① 서빙 워크플로우 세로 플로우 ────────────────────────────────────────────
def flow_svg() -> str:
    corr = correction_order()
    tau = _profile("classifier_escalation_tau")
    temp = _profile("classifier_temperature")
    conf = _profile("review_confidence_threshold")
    ts_th = _profile("fnr_rule_ts_threshold")
    s1_th = _profile("fnr_rule_s1_threshold")
    s2_th = _profile("fnr_rule_s2_threshold")
    from koipa.services.review_reasons import REVIEW_GATE_TAGS  # noqa: PLC0415
    n_gate = len([t for t in REVIEW_GATE_TAGS if t != "extraction-gate"])

    W = 940
    bx, bw = 150, 470          # 본문 상자
    rx = bx + bw + 26          # 오른쪽 설명
    steps = [
        ("1", "전처리", "텍스트 추출 → 정규화 → PII 마스킹 → 청크 분할", None, 44),
        ("2", "분류기 추론", "KF-DeBERTa 청크별 softmax · 온도 보정 %.2f · TS·S1 은 최댓값, "
                         "그 외 길이 가중 평균 · 위험도 순 선택 τ=%.2f" % (temp, tau), "등급 결정", 62),
        ("3", "룰 등급 산출", "키워드 argmax → 요소 정합 → 상위 등급 채택", "등급 결정 아님", 44),
        ("4", "등급 보정", "; ".join("%s(%s)" % (c[1], "올림" if c[3] == "up" else "내림") for c in corr)
                       + " · 룰 상향 임계 TS %.1f · S1 %.1f · S2 %.1f" % (ts_th, s1_th, s2_th),
         "등급 변경", 62),
        ("5", "검수 라우팅", "게이트 %d개를 순서대로 · 신뢰도 임계 %.2f" % (n_gate, conf), "라우팅", 44),
    ]
    gap = 26
    H = 96 + sum(h + gap for _, _, _, _, h in steps) + 96
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="서빙 경로 다섯 단계를 세로로 그린 도식. 등급을 산출하거나 바꾸는 단계는 둘뿐이다">'
      % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 13px %s;fill:%s}'
      '.d{font:11px %s;fill:%s}.n{font:700 12px %s;fill:#fff}'
      '.bg{font:700 10.5px %s;fill:#fff}.bgo{font:700 10.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, FONT, DIM, FONT, FONT, FONT, DIM))
    a('<text class="th" x="8" y="26">문서 한 건이 등급을 받기까지 &#8212; 서빙 경로 다섯 단계</text>')
    a('<text class="d" x="8" y="46">등급을 <tspan class="th">산출하거나 바꾸는 단계는 2·4 둘뿐</tspan>이다. '
      '3 은 등급을 정하지 않고, 5 는 자동확정 여부만 정한다.</text>')
    a('<rect x="%d" y="66" width="%d" height="24" fill="%s" stroke="%s"/>' % (bx, bw, MID, LINE))
    a('<text class="t" x="%d" y="83">입력 &#8212; 문서 또는 텍스트 + ICD 메타데이터</text>' % (bx + 12))

    y = 104
    for num, title, desc, badge, h in steps:
        a('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" marker-end="url(#fah)"/>'
          % (bx + 24, y - 14, bx + 24, y - 2, INK))
        a('<rect x="%d" y="%d" width="%d" height="%d" fill="#fafafa" stroke="%s"/>' % (bx, y, bw, h, LINE))
        a('<rect x="%d" y="%d" width="24" height="22" fill="%s"/>' % (bx, y, INK))
        a('<text class="n" x="%d" y="%d" text-anchor="middle">%s</text>' % (bx + 12, y + 16, num))
        a('<text class="th" x="%d" y="%d">%s</text>' % (bx + 34, y + 16, title))
        # 설명은 상자 폭에 맞춰 두 줄까지
        words, line, lines = desc.split(" "), "", []
        for w in words:
            if len(line) + len(w) > 46:
                lines.append(line); line = w
            else:
                line = (line + " " + w).strip()
        lines.append(line)
        for k, ln in enumerate(lines[:3]):
            a('<text class="d" x="%d" y="%d">%s</text>' % (bx + 12, y + 34 + k * 15, ln))
        if badge:
            solid = badge in ("등급 결정", "등급 변경")
            a('<rect x="%d" y="%d" width="86" height="20" fill="%s" stroke="%s"/>'
              % (rx, y, INK if solid else "#fff", INK if solid else LINE))
            a('<text class="%s" x="%d" y="%d" text-anchor="middle">%s</text>'
              % ("bg" if solid else "bgo", rx + 43, y + 14, badge))
        y += h + gap

    a('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" marker-end="url(#fah)"/>'
      % (bx + 24, y - 14, bx + 24, y - 2, INK))
    a('<rect x="%d" y="%d" width="%d" height="26" fill="%s"/>' % (bx, y, bw, INK))
    a('<text class="n" x="%d" y="%d">출력 &#8212; 등급 · 신뢰도 · 근거 · 상태(staging / needs_review)</text>' % (bx + 12, y + 18))
    a('<defs><marker id="fah" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker></defs>' % INK)
    a('<text class="d" x="8" y="%d">값은 배포 프로파일(full-train)에서 읽어 그린다 &#8212; '
      '설정이 바뀌면 이 그림도 함께 바뀐다.</text>' % (y + 52))
    a('</svg>')
    return "".join(p)


# ── ③ 보정 사다리 ────────────────────────────────────────────────────────────
def ladder_svg() -> str:
    corr = correction_order()
    W, H = 940, 320
    grades = ["TS", "S1", "S2", "S3"]
    lane_y = {g: 84 + i * 46 for i, g in enumerate(grades)}
    x0, x1 = 210, 800
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="보정이 적용되는 순서를 등급 사다리 위에 그린 도식. 출처 상한이 먼저 내리고 보안표시 하한이 뒤에 올린다">'
      % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.d{font:11px %s;fill:%s}.up{font:700 11.5px %s;fill:%s}'
      '.dn{font:700 11.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, FONT, DIM, FONT, INK, FONT, BAD))
    a('<defs><marker id="lu" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker>'
      '<marker id="ld" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker></defs>' % (INK, BAD))
    a('<text class="th" x="8" y="26">보정은 순서대로 적용된다 &#8212; 순서가 곧 결과다</text>')
    a('<text class="d" x="8" y="46">예: 공개 출처로 들어온 문서에 <tspan class="th">기밀 도장</tspan>이 찍혀 있으면 '
      'S3 로 내려갔다가 다시 올라온다. 둘이 충돌하면 <tspan class="th">문서에 찍힌 표시가 이긴다</tspan>.</text>')
    for g in grades:
        y = lane_y[g]
        a('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-dasharray="3 4"/>' % (x0 - 40, y, x1, y, LINE))
        a('<text class="th" x="%d" y="%d" text-anchor="end">%s</text>' % (x0 - 52, y + 5, g))
    # 예시 경로 — 모델 S1 → 출처 상한 S3 → 보안표시 하한 S1
    pts = [(x0, "S1", "모델 판정"), (x0 + 200, "S3", corr[1][1]), (x0 + 430, "S1", corr[2][1])]
    for i in range(len(pts) - 1):
        (xa, ga, _), (xb, gb, lb) = pts[i], pts[i + 1]
        down = grades.index(gb) > grades.index(ga)
        a('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="%s" stroke-width="2" marker-end="url(#%s)"/>'
          % (xa, lane_y[ga], xb, lane_y[gb], BAD if down else INK, "ld" if down else "lu"))
        mx, my = (xa + xb) / 2, (lane_y[ga] + lane_y[gb]) / 2
        a('<text class="%s" x="%d" y="%d" text-anchor="middle">%d. %s</text>'
          % ("dn" if down else "up", mx, my - 8, i + 2, lb))
        a('<text class="d" x="%d" y="%d" text-anchor="middle">%s</text>'
          % (mx, my + 8, "내림" if down else "올림"))
    for x, g, label in pts:
        a('<circle cx="%d" cy="%d" r="5" fill="%s"/>' % (x, lane_y[g], INK))
        a('<text class="d" x="%d" y="%d" text-anchor="middle">%s</text>' % (x, lane_y[g] - 12, label))
    a('<text class="up" x="%d" y="%d">1. %s &#8212; 이 예시에서는 발동하지 않는다</text>'
      % (x0, 276, corr[0][1]))
    a('<text class="d" x="%d" y="%d">%s</text>' % (x0, 296, corr[0][2]))
    a('<text class="dn" x="8" y="%d">내림은 하나뿐이고, 그것도 고등급 예측을 덮으면 자동확정하지 않는다 '
      '&#8212; cap-conflict 로 검수에 넘긴다.</text>' % 276)
    a('</svg>')
    return "".join(p)


# ── ⑦ 세 경로 합류 ───────────────────────────────────────────────────────────
def paths_svg() -> str:
    """세 경로가 하나의 학습셋으로 합류하는 그림. 허용 계층은 코드에서 읽는다."""
    from koipa import golden_tiers as gt  # noqa: PLC0415
    # 그림이 주장하는 것 — 평가정답과 격리분은 어느 경로로도 학습에 들어오지 않는다.
    for tier in (gt.TIER_LOCKED, gt.TIER_HELD):
        if tier in gt.TRAIN_TIERS:
            raise SystemExit("학습 허용 계층이 바뀌었다 — 그림의 주장이 깨진다: %s" % tier)
    nightly = bool(_profile("enable_nightly_retrain_schedule", False))
    dsdir = _profile("training_dataset_dir", "")

    W, H = 940, 466
    bx, bw = 8, 470
    tx, tw = 610, 300
    rows = [
        ("1", "합성 검수 승인", "tb_sample_documents (승인분)", "사람이 실행", False,
         "위생 게이트 3종 제외 &#183; 교정 등급이 라벨"),
        ("2", "골든 후보 분할", "gold_candidate", "사람이 실행", False,
         "등급 층화 75/25 &#183; 누수 행 선제거"),
        ("3", "운영 교정", "tb_corrections (미소비)",
         "자동" if nightly else "자동(조건부)", True,
         "홀드아웃과 겹치면 병합 제외 &#183; 반영분만 소비"),
    ]
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="사람의 판단이 학습셋으로 들어오는 세 경로가 하나의 학습셋으로 합류하는 도식. '
      '둘은 사람이 실행하고 하나만 조건부 자동이다">' % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.m{font:10.5px ui-monospace,monospace;fill:%s}.d{font:11px %s;fill:%s}'
      '.n{font:700 11px %s;fill:#fff}.bgo{font:700 10.5px %s;fill:%s}'
      '.no{font:700 11.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, DIM, FONT, DIM, FONT, FONT, DIM, FONT, BAD))
    a('<text class="th" x="8" y="26">사람의 판단이 학습셋에 닿는 길은 셋이다</text>')
    a('<text class="d" x="8" y="46">셋 중 <tspan class="th">둘은 사람이 명령을 실행해야</tspan> 진행된다. '
      '자동인 것은 3 하나뿐이고, 그마저 조건을 모두 만족해야 발화한다.</text>')

    y0, rh, gap = 74, 86, 20
    for i, (num, title, src, run, auto, note) in enumerate(rows):
        y = y0 + i * (rh + gap)
        a('<rect x="%d" y="%d" width="%d" height="%d" fill="#fafafa" stroke="%s"/>' % (bx, y, bw, rh, LINE))
        a('<rect x="%d" y="%d" width="24" height="22" fill="%s"/>' % (bx, y, INK))
        a('<text class="n" x="%d" y="%d" text-anchor="middle">%s</text>' % (bx + 12, y + 16, num))
        a('<text class="th" x="%d" y="%d">%s</text>' % (bx + 34, y + 16, title))
        a('<text class="m" x="%d" y="%d">%s</text>' % (bx + 12, y + 38, src))
        a('<text class="d" x="%d" y="%d">%s</text>' % (bx + 12, y + 58, note))
        a('<rect x="%d" y="%d" width="96" height="20" fill="%s" stroke="%s"/>'
          % (bx + bw - 106, y + 6, INK if auto else "#fff", INK if auto else LINE))
        a('<text class="%s" x="%d" y="%d" text-anchor="middle">%s</text>'
          % ("n" if auto else "bgo", bx + bw - 58, y + 20, run))
        my = y + rh // 2
        a('<path d="M%d,%d H%d V%d H%d" fill="none" stroke="%s" marker-end="url(#pah)"/>'
          % (bx + bw, my, 560, 215, tx - 6, INK))

    a('<rect x="%d" y="%d" width="%d" height="76" fill="%s"/>' % (tx, 177, tw, INK))
    a('<text class="n" x="%d" y="%d">학습셋</text>' % (tx + 14, 200))
    a('<text class="n" x="%d" y="%d">train.jsonl &#183; val.jsonl &#183; test.jsonl</text>' % (tx + 14, 222))
    a('<text class="n" x="%d" y="%d">%s</text>' % (tx + 14, 242, dsdir))

    yb = y0 + 3 * (rh + gap) + 8
    a('<rect x="%d" y="%d" width="%d" height="46" fill="#fff" stroke="%s"/>' % (bx, yb, W - 16, BAD))
    a('<text class="no" x="%d" y="%d">어느 경로로도 들어오지 않는 것 &#8212; '
      '<tspan class="m">%s</tspan>(평가정답) &#183; <tspan class="m">%s</tspan>(격리분)</text>'
      % (bx + 12, yb + 20, gt.TIER_LOCKED, gt.TIER_HELD))
    a('<text class="d" x="%d" y="%d">학습 허용 계층은 <tspan class="m">golden_tiers.TRAIN_TIERS</tspan> '
      '허용목록이 정한다 &#8212; 목록에 없는 계층은 기본이 제외다.</text>' % (bx + 12, yb + 38))
    a('<defs><marker id="pah" markerWidth="9" markerHeight="9" refX="8" refY="3" orient="auto">'
      '<path d="M0,0 L8,3 L0,6 z" fill="%s"/></marker></defs>' % INK)
    a('</svg>')
    return "".join(p)


# ── ⑧ 계층별 학습·평가 허용 격자 ─────────────────────────────────────────────
TIER_LABELS = {
    "locked_gold_eval": ("사람 서명이 끝난 평가정답", "학습에 넣으면 성능 수치가 무의미해진다"),
    "gold_candidate": ("자동 게이트 통과 &#183; 서명 대기", "라벨이 믿을 만해 학습에 쓴다"),
    "silver_train": ("그 밖의 기계 라벨", "학습 시드 전용"),
    "legal_floor": ("법적 기준으로 등급이 정해지는 것", "평가정답이 빌 때만 임시 평가로 쓴다"),
    "held_review": ("서명이 무효이거나 본문이 합성인 격리분", "어느 쪽으로도 쓰지 않는다"),
}


def tiers_svg() -> str:
    """계층 계약을 golden_tiers 에서 읽어 격자로 그린다."""
    from koipa import golden_tiers as gt  # noqa: PLC0415
    tiers = [v for k, v in sorted(vars(gt).items())
             if k.startswith("TIER_") and isinstance(v, str)]
    missing = [t for t in tiers if t not in TIER_LABELS]
    if missing:
        raise SystemExit("계층이 늘었는데 이름이 없다 — TIER_LABELS 를 갱신할 것: %s" % missing)
    order = ["locked_gold_eval", "gold_candidate", "silver_train", "legal_floor", "held_review"]
    order = [t for t in order if t in tiers] + [t for t in tiers if t not in order]

    W = 940
    rh, top = 40, 108
    H = top + rh * len(order) + 76
    cx1, cx2, cw = 660, 780, 100
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="골든셋 계층마다 학습과 평가에 쓸 수 있는지를 표시한 격자. '
      '평가정답 계층은 학습 금지, 격리 계층은 양쪽 다 금지다">' % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.m{font:10.5px ui-monospace,monospace;fill:%s}.d{font:11px %s;fill:%s}'
      '.y{font:700 12px %s;fill:#fff}.n{font:700 12px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, DIM, FONT, DIM, FONT, FONT, BAD))
    a('<text class="th" x="8" y="26">계층마다 쓸 수 있는 곳이 다르다 &#8212; 이 구분이 무너지면 '
      '시험 문제로 공부하고 그 시험을 본다</text>')
    a('<text class="d" x="8" y="46">허용은 <tspan class="m">golden_tiers.TRAIN_TIERS</tspan> 와 '
      '<tspan class="m">eval_records()</tspan> 가 정본이다. 이 그림은 그 둘을 불러 칠한다.</text>')
    a('<text class="d" x="8" y="66"><tspan class="th">양쪽 다 금지인 칸이 있다</tspan> &#8212; '
      '&ldquo;평가에서 뺐으니 학습에는 써도 되겠지&rdquo;가 성립하지 않는다는 뜻이다.</text>')
    a('<text class="th" x="%d" y="%d" text-anchor="middle">학습</text>' % (cx1 + cw // 2, top - 12))
    a('<text class="th" x="%d" y="%d" text-anchor="middle">평가</text>' % (cx2 + cw // 2, top - 12))

    for i, tier in enumerate(order):
        y = top + i * rh
        train = tier in gt.TRAIN_TIERS
        if tier == gt.TIER_LOCKED:
            ev = "허용"
        elif tier == gt.TIER_LEGAL_FLOOR:
            ev = "보조"
        else:
            ev = "금지"
        what, why = TIER_LABELS[tier]
        both_no = (not train) and ev == "금지"
        a('<rect x="8" y="%d" width="%d" height="%d" fill="%s" stroke="%s"/>'
          % (y, W - 16, rh - 4, "#fff5f5" if both_no else "#fafafa", BAD if both_no else LINE))
        a('<text class="m" x="20" y="%d">%s</text>' % (y + 16, tier))
        a('<text class="d" x="20" y="%d">%s &#183; %s</text>' % (y + 30, what, why))
        for cx, txt in ((cx1, "허용" if train else "금지"), (cx2, ev)):
            solid = txt == "허용"
            a('<rect x="%d" y="%d" width="%d" height="22" fill="%s" stroke="%s"/>'
              % (cx, y + 6, cw, INK if solid else "#fff",
                 INK if solid else (LINE if txt == "보조" else BAD)))
            a('<text class="%s" x="%d" y="%d" text-anchor="middle">%s</text>'
              % ("y" if solid else ("d" if txt == "보조" else "n"), cx + cw // 2, y + 22, txt))

    yb = top + rh * len(order) + 14
    a('<text class="d" x="8" y="%d">&ldquo;보조&rdquo;는 평가정답이 비었을 때만 쓰는 임시값이다 '
      '(<tspan class="m">eval_records(allow_floor_fallback=True)</tspan>) &#8212; '
      '호출부가 그 사실을 경고로 함께 낸다.</text>' % yb)
    a('<text class="d" x="8" y="%d">계층은 <tspan class="th">라벨이 얼마나 믿을 만한가</tspan>를 말하고, '
      '출처(<tspan class="m">public_real / customer_real / synthetic / unknown</tspan>)는 '
      '<tspan class="th">본문이 실제 문서인가</tspan>를 말한다 &#8212; 둘은 직교한다.</text>' % (yb + 20))
    a('</svg>')
    return "".join(p)


# ── ⑨ 배포 게이트 사다리 ─────────────────────────────────────────────────────
CHECK_LABELS = {
    "degenerate": "한 등급으로만 찍는가",
    "fnr_high_present": "고등급 미탐율을 잴 수 있는가",
    "fnr_high_regression": "미탐이 기준선보다 나빠졌는가",
    "f1_regression": "전반 성능이 크게 떨어졌는가",
    "baseline_present": "비교할 기준선이 있는가",
    "first_deploy_fnr_floor": "최초 배포 절대 상한을 넘는가",
    "anchor_high_grade_miss": "앵커에서 고등급을 놓쳤는가",
    "anchor_eval_skipped": "앵커 리포트가 있는가",
    "metamorphic_forward_regression": "문체만 바꿨는데 등급이 내려갔는가",
    "metamorphic_eval_skipped": "메타모픽 리포트가 있는가",
}


def gate_svg() -> str:
    """deploy_gate.py 에 실제로 있는 검사만 순서대로 그린다."""
    src = io.open(_HERE.parent / "src" / "koipa" / "modules" / "m6_evaluation" / "deploy_gate.py",
                  encoding="utf-8").read()
    names = []
    for m in re.finditer(r'GateCheck\(\s*"([a-z0-9_]+)"', src):
        if m.group(1) not in names:
            names.append(m.group(1))
    if not names:
        raise SystemExit("deploy_gate.py 에서 검사 이름을 못 읽었다 — 정규식을 고칠 것")
    missing = [n for n in names if n not in CHECK_LABELS]
    if missing:
        raise SystemExit("검사가 늘었는데 이름이 없다 — CHECK_LABELS 를 갱신할 것: %s" % missing)
    must = ("degenerate", "fnr_high_present", "fnr_high_regression", "f1_regression",
            "first_deploy_fnr_floor")
    gone = [n for n in must if n not in names]
    if gone:
        raise SystemExit("문서가 이름을 대는 검사가 코드에서 사라졌다 — 문서와 함께 고칠 것: %s" % gone)
    fnr_tol = _profile("retrain_fnr_high_tolerance", 0.02)
    floor = _profile("deploy_gate_first_deploy_fnr_high_max", None)

    always = [n for n in ("degenerate", "fnr_high_present") if n in names]
    with_base = [n for n in ("fnr_high_regression", "f1_regression") if n in names]
    no_base = [n for n in ("baseline_present", "first_deploy_fnr_floor") if n in names]
    tail = [n for n in names if n not in always + with_base + no_base]

    W = 940
    rh, top = 30, 130
    nrow = len(always) + max(len(with_base), len(no_base)) + len(tail)
    H = top + rh * nrow + 160
    p = []
    a = p.append
    a('<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx;height:auto" '
      'xmlns="http://www.w3.org/2000/svg" role="img" '
      'aria-label="재학습본을 서비스에 올릴지 정하는 배포 게이트 검사들을 순서대로 그린 도식. '
      '판정할 수 없으면 통과가 아니라 거부다">' % (W, H, W))
    a('<style>.t{font:12px %s;fill:%s}.th{font:700 12.5px %s;fill:%s}'
      '.m{font:10.5px ui-monospace,monospace;fill:%s}.d{font:11px %s;fill:%s}'
      '.n{font:700 11px %s;fill:#fff}.no{font:700 11.5px %s;fill:%s}</style>'
      % (FONT, INK, FONT, INK, DIM, FONT, DIM, FONT, FONT, BAD))
    a('<text class="th" x="8" y="26">학습이 끝나면 &#8212; 사람의 인상이 아니라 이 검사들이 승격을 정한다</text>')
    a('<text class="d" x="8" y="46">원칙은 <tspan class="th">fail-closed</tspan> 다. '
      '지표가 없거나 리포트가 깨져 <tspan class="th">판정할 수 없으면 통과가 아니라 거부</tspan>다 &#8212; '
      '미탐을 치명으로 보는 설계에서</text>')
    a('<text class="d" x="8" y="64">&ldquo;측정 못 함&rdquo;을 &ldquo;이상 없음&rdquo;으로 읽지 않기 위해서다.</text>')
    a('<rect x="8" y="82" width="%d" height="24" fill="%s"/>' % (W - 16, MID))
    a('<text class="d" x="20" y="98">입력 &#8212; 재학습 후보 1건. 자동 활성화는 기본 꺼짐'
      '(<tspan class="m">retrain_auto_activate=False</tspan>)</text>')

    def row(y, i, name, x=8, w=W - 16):
        a('<rect x="%d" y="%d" width="%d" height="%d" fill="#fafafa" stroke="%s"/>'
          % (x, y, w, rh - 5, LINE))
        a('<rect x="%d" y="%d" width="22" height="%d" fill="%s"/>' % (x, y, rh - 5, INK))
        a('<text class="n" x="%d" y="%d" text-anchor="middle">%s</text>' % (x + 11, y + 17, i))
        a('<text class="t" x="%d" y="%d">%s</text>' % (x + 32, y + 17, CHECK_LABELS[name]))
        a('<text class="m" x="%d" y="%d" text-anchor="end">%s</text>' % (x + w - 10, y + 17, name))

    y, i = top, 1
    for n in always:
        row(y, i, n)
        y += rh
        i += 1
    a('<text class="d" x="8" y="%d">기준선이 <tspan class="th">있으면</tspan> 왼쪽, '
      '<tspan class="th">없으면</tspan>(최초 배포) 오른쪽을 본다</text>' % (y + 16))
    y += 26
    half = (W - 26) // 2
    for k in range(max(len(with_base), len(no_base))):
        if k < len(with_base):
            row(y, i, with_base[k], 8, half)
        if k < len(no_base):
            row(y, i, no_base[k], 18 + half, half)
        y += rh
        i += 1
    for n in tail:
        row(y, i, n)
        y += rh
        i += 1

    a('<rect x="8" y="%d" width="%d" height="26" fill="%s"/>' % (y + 6, W - 16, INK))
    a('<text class="n" x="20" y="%d">전부 통과해야 승격 &#8212; 판정 결과는 '
      'tb_training_runs.final_metrics 의 deploy.gate 한 곳에 남는다</text>' % (y + 24))
    a('<text class="d" x="8" y="%d">허용폭은 설정에서 읽는다 &#8212; '
      '미탐 악화 허용 <tspan class="th">+%.2f</tspan>%s. 값이 바뀌면 이 그림도 함께 바뀐다.</text>'
      % (y + 56, fnr_tol,
         (' &#183; 최초 배포 절대 상한 <tspan class="th">%.2f</tspan>' % floor) if floor else ''))
    a('<text class="no" x="8" y="%d">미탐(고등급을 낮게 본 것)이 나빠지는 모델은 '
      '다른 지표가 좋아도 올라가지 않는다 &#8212; 이 시스템의 1순위 축이다.</text>' % (y + 76))
    a('</svg>')
    return "".join(p)


# 「학습 데이터 입력 경로 명세서」는 세 곳에 같은 본문으로 둔다 — 셋 다 갈아 끼운다.
_SPEC = ["doc/result/KL_회신_2026-08-28/첨부/학습_데이터_입력_경로_명세서.html",
         "doc/result/KL_AI자료_2026-08/첨부문서/학습_데이터_입력_경로_명세서.html",
         "doc/감리문서/학습_데이터_입력_경로_명세서.html"]


# ── 주입 ─────────────────────────────────────────────────────────────────────
FIGS = {
    "seq": (seq_svg, ["doc/result/KL_회신_2026-08-28/KL_API_통신방안_검토회신.html",
                      "doc/result/KL_AI자료_2026-08/KL_API_통신방안_검토회신.html"]),
    "bar": (bar_svg, ["doc/result/KL_회신_2026-08-28/KL_질의사항_회신서.html",
                      "doc/result/KL_AI자료_2026-08/KL_질의사항_회신서.html"]),
    "funnel": (funnel_svg, ["doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_명세서.html",
                            "doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_쉬운설명서.html"]),
    "flow": (flow_svg, ["doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_명세서.html",
                        "doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_쉬운설명서.html",
                        "doc/result/KL_회신_2026-08-28/KL_질의사항_회신서.html",
                        "doc/result/KL_AI자료_2026-08/KL_질의사항_회신서.html"]),
    "ladder": (ladder_svg, ["doc/result/KL_회신_2026-08-28/첨부/등급분류_알고리즘_쉬운설명서.html"]),
    "paths": (paths_svg, _SPEC),
    "tiers": (tiers_svg, _SPEC),
    "gate": (gate_svg, _SPEC),
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
