# -*- coding: utf-8 -*-
"""골든 후보 본문에서 **검수 스캐폴딩과 정답 노출**을 걷어내는 순수 함수들.

왜 따로 뺐나(2026-09-08). 이 로직은 2026-08 에 `build_kl_review_pool.py` 안에서 만들어졌고
223 의 검수 배치(kl-ff5a822c)를 그것으로 정리했다. 그런데 콘솔이 서빙하는 후보 풀은
정리되지 않아, 설계단계 감리가 **정답이 적힌 문서로** 분류기를 시험하게 됐다.

같은 로직이 두 곳에서 필요해졌는데 `build_kl_review_pool.py` 는 **임포트할 수 없다** —
모듈 수준에서 `sys.argv[1]` 을 읽고 파이프라인 전체를 실행해 파일까지 쓴다. 복사해 두면
두 벌이 조용히 갈라진다. 그래서 순수 함수만 여기로 옮기고 양쪽이 이것을 쓴다.

무엇을 걷어내나:

    ## 등급 제안 사유: TS      ← 정답이 그대로 적혀 있다(실측 964건 · 100% 일치)
    ## 확인 질문과 답변 기록     ← 검수 스캐폴딩
    ## 세부 검토 경과
    ## 후속 조치와 종료 조건
    ### 검수 전 확인 목록
    "…S1 이하로 재검토한다" 류의 등급코드가 든 **문장**

⚠ 문서를 통째로 버리지 않는다. 문장 단위로 떼어내 나머지 사실 서술은 판단 재료로 남긴다.
"""
from __future__ import annotations

import re

DROP_H2 = ("확인 질문과 답변 기록", "세부 검토 경과", "후속 조치와 종료 조건")
DROP_H3 = ("검수 전 확인 목록",)

GRADE_TOK = re.compile(r"\b(TS|S1|S2|S3)\b")


def strip_scaffolding(md: str) -> str:
    """검수용으로 붙인 절을 제거한다 — '등급 제안 사유' 가 그중 하나다."""
    out, skip = [], False
    for ln in md.splitlines():
        m2 = re.match(r"^##\s+(?!#)(.*)$", ln)
        if m2:
            t = m2.group(1).strip()
            skip = t.startswith("등급 제안 사유") or any(t.startswith(d) for d in DROP_H2)
            if skip:
                continue
        m3 = re.match(r"^###\s+(.*)$", ln)
        if m3 and any(m3.group(1).strip().startswith(d) for d in DROP_H3):
            skip = True
            continue
        if not skip:
            out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def drop_grade_sentences(text: str) -> str:
    """등급 문자열이 든 문장만 제거한다 — 문서를 통째로 버리지 않는다.

    본문에 'S3가 적절하다' 같은 문장이 남으면 검수자가 본문을 읽기 전에 정답을 본다.
    문장 단위로 떼어내면 나머지 사실 서술은 그대로 판단 재료로 남는다.
    """
    kept_paras = []
    for para in text.split("\n"):
        if not para.strip():
            kept_paras.append(para)
            continue
        if para.lstrip().startswith("#"):
            kept_paras.append(GRADE_TOK.sub("", para).rstrip())
            continue
        parts = re.split(r"(?<=다\.)\s+|(?<=[.!?])\s+", para)
        keep = [s for s in parts if s.strip() and not GRADE_TOK.search(s)]
        if keep:
            kept_paras.append(" ".join(keep))
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept_paras)).strip()


def clean_body(md: str) -> str:
    """스캐폴딩 제거 → 등급 문장 제거. 후보 본문 정리의 정본 순서다."""
    return drop_grade_sentences(strip_scaffolding(md))
