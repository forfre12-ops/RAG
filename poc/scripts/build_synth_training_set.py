# -*- coding: utf-8 -*-
"""[W6] 검수 승인 합성 샘플(DB) → 학습셋 jsonl. noop_fallback/llm_nonjson 배제.

generate → queue → review → **train** 루프의 마지막 칸. 지재원 관리자가 검수 큐
(tb_sample_documents)에서 승인한 합성 문서를, 본문 출처가 정상인 것만 골라 학습 행으로
방출한다(합성 placeholder·비-JSON 노이즈의 학습 유입 차단 — P0#1이 보존한 label_source 소비).

스냅샷 시맨틱: 승인 전체에서 매번 재빌드(결정적). 출력 jsonl은 build_labeled_dataset.py
--extra 로 그대로 합류 가능한 {doc_id,text,label,source,domain} 스키마.

사용:
  python scripts/build_synth_training_set.py --out datasets/synth_approved/train.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="검수 승인 합성 샘플 → 학습셋 jsonl(노이즈 마커 배제)"
    )
    ap.add_argument(
        "--out",
        default="datasets/synth_approved/train.jsonl",
        help="출력 jsonl 경로(디렉토리 자동 생성)",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        help="최대 행 수(기본=전량). 스모크 확인용.",
    )
    args = ap.parse_args(argv)

    # 표준 스크립트 관례: src 를 import 경로에 추가(standalone 실행 지원).
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from koipa.services.synthesis_service import SynthesisService

    result = SynthesisService().build_training_rows(limit=args.limit)
    rows = result["rows"]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with io.open(out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    # ascii-safe 요약(cp949 콘솔 안전). 무음 드롭 금지 — 사유별 카운트 노출.
    summary = {
        "out": str(out),
        "approved_total": result["approved_total"],
        "included": result["included"],
        "excluded_noise": result["excluded_noise"],
        "excluded_empty": result["excluded_empty"],
    }
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
