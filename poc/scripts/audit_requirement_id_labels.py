#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""요건 ID 뒤에 붙은 설명이 요건 원본과 맞는가 — 문서 전수로 센다.

왜(2026-09-12). `doc/17_진척_대시보드_외부공유.html` 의 용어 사전에서 **6개 중 5개**가
서로 밀려 있었다. ID 는 실재하는데 설명이 다른 요건의 것이었다.

    적힌 것                                    요건 원본
    FUN-003 문서 분류                           샘플문서 생성(LLM 합성)
    FUN-004 관리자 확정·재라벨                    등급분류 학습(BERT·RAG 보완 선택)
    FUN-005 등급체계 변경                        추론(등급체계 CRUD·임시저장→관리자 확정)
    FUN-022 가이드 문서 RAG                      전자문서 텍스트 파싱·정규화
    FUN-023 합성 데이터 생성                      가이드라인 라벨링 매핑
    FUN-024 평가·능동학습                        (맞음) 심층지표·FNR·혼동행렬·능동학습

**외부에 공유한 문서**다. ID 존재 여부만 보는 검사(RTM 대조)로는 안 잡힌다 — 여섯 개 다
실재하는 ID 이기 때문이다. 그래서 **ID 뒤에 어떤 말이 붙었는지**를 본다.

요건 원본 = `doc/한국지식재산보호원 AI 영업비밀_기능요구사항검토20260427_1_로이드케이체크.xlsx`
(FUN-001~024 · '업무'열로 KL/로이드케이 구분). 아래 표는 그 원본에서 옮긴 것이다.

⚠ 이 도구는 **의심 자리를 보여 줄 뿐** 자동으로 고치지 않는다. 같은 낱말이 다른 뜻으로
   쓰일 수 있다(예: FUN-004 설명에 'RAG' 가 나오는 것은 맞다 — 보완 옵션이 그 요건 안에 있다).

사용:
    python scripts/audit_requirement_id_labels.py
    python scripts/audit_requirement_id_labels.py --json reports/REQ_ID_LABELS.json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
_REPO = _POC.parent

# 요건 원본에서 옮긴 핵심어. 설명에 이 중 하나라도 있으면 맞는 것으로 본다.
EXPECTED: dict[str, tuple[str, tuple[str, ...]]] = {
    # ⚠ 같은 뜻을 다르게 적은 것을 결함으로 세지 않는다. 아래 낱말은 실제 산출물에서
    #   쓰인 표기를 보고 넓혔다("합성(샘플) 문서 생성" · "산출·리포트" 는 맞는 표기다).
    "FUN-003": ("샘플문서 생성(LLM 합성·자동라벨·검수 후 편입)",
                ("샘플문서", "샘플 문서", "합성문서", "합성 문서", "합성 데이터",
                 "합성데이터", "문서 생성", "문서생성", "합성(샘플)")),
    "FUN-004": ("등급분류 학습(BERT·RAG 규정보완 선택옵션·train/val/test)",
                ("학습", "파인튜닝", "fine-tun", "BERT", "분류모델", "분류 모델",
                 "인코더", "PLM")),
    "FUN-005": ("추론(등급체계 CRUD·인퍼런스·임시저장→관리자 확정)",
                ("추론", "인퍼런스", "분류 결과", "등급체계", "관리자 확정", "최종확정")),
    "FUN-022": ("전자문서 텍스트 파싱·정규화(HWP/DOCX/PDF)",
                ("파싱", "추출", "텍스트", "정규화", "청킹")),
    "FUN-023": ("가이드라인 라벨링 매핑(등급→라벨·키워드/패턴/태깅)",
                ("라벨링", "매핑", "키워드", "태깅", "가이드")),
    "FUN-024": ("심층지표(FNR 미탐·혼동행렬·오분류→능동학습)",
                ("지표", "미탐", "FNR", "혼동행렬", "Confusion", "능동학습", "평가",
                 "산출", "리포트")),
}

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_IDS = "FUN-0(?:03|04|05|22|23|24)"

# ⚠ "ID 가 나오는 자리"를 전부 보면 안 된다. 처음엔 그렇게 만들었다가 2,605곳 중 1,326곳이
#   의심으로 나왔는데, 대부분은 본문에서 요건을 **언급만** 한 문장이었다(정의가 아니다).
#   정의를 **주장하는 자리**는 구조가 정해져 있다 — 용어 사전(dt/dd)과 표의 첫 칸이다.
#   [[counting-tools-hand-picked-scope-2026-09-08]] 와 같은 교훈: 대상을 넓게 잡으면 셈이 틀린다.
_DEFINITION_PATTERNS = (
    re.compile(r"<dt[^>]*>\s*(%s)\s*</dt>\s*<dd[^>]*>(.{1,200}?)</dd>" % _IDS, re.S),
    re.compile(r"<t[dh][^>]*>\s*(%s)\s*</t[dh]>\s*<t[dh][^>]*>(.{1,200}?)</t[dh]>" % _IDS, re.S),
    re.compile(r"^\|\s*(%s)\s*\|([^|]{1,200})\|" % _IDS, re.M),
)


def scan(text: str) -> list[tuple[str, str, bool]]:
    out = []
    for pattern in _DEFINITION_PATTERNS:
        for m in pattern.finditer(text):
            fid = m.group(1)
            desc = _WS.sub(" ", _TAG.sub(" ", m.group(2))).strip()
            if not desc:
                continue
            _canon, keys = EXPECTED[fid]
            out.append((fid, desc[:60], any(k in desc for k in keys)))
    return out


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="요건 ID 설명 대조")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    listing = subprocess.run(  # noqa: S603
        ["git", "ls-files", "-z"], cwd=_REPO, capture_output=True, check=True).stdout
    files = [f for f in listing.decode("utf-8").split("\0")
             if f.endswith((".html", ".md", ".txt"))]

    suspects: list[dict] = []
    checked = 0
    for rel in files:
        path = _REPO / rel
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for fid, tail, ok in scan(text):
            checked += 1
            if not ok:
                suspects.append({"file": rel, "id": fid, "text": tail,
                                 "expected": EXPECTED[fid][0]})

    print("검사한 문서 %d개 · ID 뒤에 설명이 붙은 자리 %d곳 · 의심 %d곳"
          % (len(files), checked, len(suspects)))
    by_file: dict[str, list[dict]] = {}
    for s in suspects:
        by_file.setdefault(s["file"], []).append(s)
    for rel, rows in sorted(by_file.items()):
        print("\n%s" % rel)
        for s in rows:
            print("   %s  적힘: %s" % (s["id"], s["text"]))
            print("   %s  원본: %s" % (" " * len(s["id"]), s["expected"]))

    if a.json:
        target = _POC / a.json
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"files": len(files), "checked": checked, "suspects": suspects},
            ensure_ascii=False, indent=1), encoding="utf-8")
        print("\n기록: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
