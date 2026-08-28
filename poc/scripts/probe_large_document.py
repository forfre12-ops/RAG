#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""대용량 문서 종단 처리 시간 측정 — 파일 파싱부터 등급 판정까지.

왜 이 스크립트가 따로 필요한가.
  기존 `probe_ingest_capacity.py` 는 **같은 문단을 반복한 합성 텍스트**를
  `classify(content=...)` 에 직접 넣는다. 그래서 두 가지가 빠져 있다.
    · 파일 파싱(HWP/DOCX/PDF 추출) 시간이 측정에 없다
    · 같은 문장이 반복되면 실제 문서와 어휘 분포가 다르다 — 청크 수는 맞아도
      룰 매칭·근거 추출 비용이 달라질 수 있다
  발주처가 "100페이지 문서 처리 시간"을 물었을 때 답해야 하는 것은 **사용자가
  파일을 올린 순간부터 등급이 나올 때까지**다. 이 스크립트가 그 구간을 잰다.

무엇을 재는가.
    ① 파싱      파일 -> 본문 텍스트(추출기 경유)
    ② 전처리    정규화·PII 마스킹·청킹
    ③ 분류      모델 추론 + 룰 + 게이트
    합계는 세 구간의 합이다.

사용:
    cd poc
    python scripts/probe_large_document.py                      # 합성 문서(기본)
    python scripts/probe_large_document.py --file <경로>         # 실제 파일
    python scripts/probe_large_document.py --pages 100 --repeat 2

주의: 분류기 모델을 로드하므로 첫 실행에 시간이 걸린다(워밍업은 측정에서 뺀다).
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(_ROOT / "src"), str(_ROOT)]

os.environ.setdefault("TESTING", "1")

PAGE_CHARS = 1800  # 한글 A4 1쪽 ≈ 1,800자


def _synthetic_text(pages: int) -> str:
    """합성 본문 — 어휘가 한 문단에 갇히지 않도록 여러 문형을 섞는다.

    기존 프로브는 한 문단만 반복해 실제 문서와 어휘 분포가 크게 달랐다.
    """
    paras = [
        "본 사업의 총 사업비는 15,000천원이며 지원 한도는 1,000천원이다. "
        "담당 부서는 연구기획팀이고 사업 기간은 2026년 3월부터 12월까지다. ",
        "제조 공정은 전처리·성형·소결 3단계로 구성되며, 소결 온도는 1,250도에서 "
        "90분간 유지한다. 냉각 속도는 분당 12도를 넘지 않도록 관리한다. ",
        "가격 협상 결과 단가는 전년 대비 7.4% 인하로 합의했다. 계약 상대방과의 "
        "합의 내용은 별도 부속 합의서에 따른다. ",
        "임직원 인사평가 결과는 등급별로 산정하며, 평가자 교육을 매년 1회 시행한다. "
        "평가 자료는 인사팀장 승인 후 열람할 수 있다. ",
        "시스템 구성은 응용 서버 2대와 데이터베이스 1대로 이루어진다. 백업은 매일 "
        "02시에 수행하고 보관 기간은 90일이다. ",
    ]
    need = PAGE_CHARS * pages
    buf: list[str] = []
    total = 0
    i = 0
    while total < need:
        p = paras[i % len(paras)]
        buf.append(p)
        total += len(p)
        i += 1
    return "".join(buf)[:need]


def _fmt(sec: float) -> str:
    return f"{sec:6.1f}s" if sec < 60 else f"{sec/60:5.1f}분"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="실제 문서 파일 경로(주면 파싱 시간까지 측정)")
    ap.add_argument("--pages", type=int, nargs="*", default=[1, 10, 25, 50, 100],
                    help="합성 문서 쪽수 목록")
    ap.add_argument("--repeat", type=int, default=1, help="회차(중앙값 보고)")
    ap.add_argument("--stop-after", type=float, default=900.0,
                    help="이 시간을 넘기면 이후 규모는 재지 않는다")
    args = ap.parse_args(argv)

    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    try:
        import torch
        threads = torch.get_num_threads()
    except Exception:  # noqa: BLE001
        threads = -1

    print("=" * 74)
    print(" 대용량 문서 종단 처리 시간 — 파싱 + 전처리 + 분류")
    print("=" * 74)
    print(f"  torch threads={threads} · OMP={os.environ.get('OMP_NUM_THREADS', '-')} "
          f"· cpu_count={os.cpu_count()}")

    pre = PreprocessPipeline()
    svc = ClassifyService()

    # 워밍업 — 모델 로드 비용을 첫 케이스에 섞지 않는다.
    t0 = time.perf_counter()
    svc.classify(ClassifyRequest(doc_id="warmup", content="워밍업 문장이다."))
    print(f"  워밍업(모델 로드 포함) {time.perf_counter()-t0:.1f}s\n")

    cases: list[tuple[str, str, float]] = []  # (라벨, 본문, 파싱시간)

    if args.file:
        p = Path(args.file)
        if not p.exists():
            print(f"  [오류] 파일 없음: {p}")
            return 2
        from koipa.modules.m2_preprocess.extractor import extract  # noqa: PLC0415
        t0 = time.perf_counter()
        res = extract(str(p))
        parse_s = time.perf_counter() - t0
        text = getattr(res, "text", "") or ""
        pages = getattr(res, "pages", None) or max(1, len(text) // PAGE_CHARS)
        method = getattr(res, "method", "?")
        cases.append((f"{p.name} ({pages}쪽·{method})", text, parse_s))
    else:
        for n in args.pages:
            cases.append((f"합성 {n}쪽", _synthetic_text(n), 0.0))

    print(f"  {'대상':<22}{'글자':>9}{'청크':>6}{'파싱':>9}{'전처리':>9}{'분류':>9}{'합계':>9}")
    print("  " + "-" * 71)

    for label, text, parse_s in cases:
        runs: list[tuple[float, float, int]] = []
        for _ in range(max(1, args.repeat)):
            t0 = time.perf_counter()
            pr = pre.run_text_full(text)
            pre_s = time.perf_counter() - t0
            n_chunks = len(getattr(pr, "chunks", None) or [])
            t0 = time.perf_counter()
            svc.classify(ClassifyRequest(doc_id="probe-large", content=text))
            clf_s = time.perf_counter() - t0
            runs.append((pre_s, clf_s, n_chunks))
        pre_s = statistics.median(r[0] for r in runs)
        clf_s = statistics.median(r[1] for r in runs)
        n_chunks = runs[0][2]
        total = parse_s + pre_s + clf_s
        print(f"  {label:<22}{len(text):>9,}{n_chunks:>6}"
              f"{_fmt(parse_s):>9}{_fmt(pre_s):>9}{_fmt(clf_s):>9}{_fmt(total):>9}")
        if total > args.stop_after:
            print(f"    ({args.stop_after:.0f}s 초과 — 이후 규모 생략)")
            break

    print()
    print("  주의 — 이 수치는 측정한 이 장비 기준이다. 처리 시간은 할당 CPU 코어 수에")
    print("  거의 반비례하므로, 다른 사양으로 옮길 때는 코어 수로 환산할 것.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
