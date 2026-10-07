#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""합성으로 채울 자리를 센다 — 등급 × 도메인 격자의 빈 칸·얇은 칸.

왜(2026-09-05). 합성을 **양**으로 늘리는 것은 실측으로 막혀 있다: 합성-only 로 학습해
실문서를 재면 F1 0.26(실데이터 학습 0.736). 아무리 많이 만들어도 실문서 성능이 그만큼
오르지 않는다. 그래서 합성의 쓸모를 "빈 칸 채우기"로 좁힌다 — 실데이터로 얻을 수 없는
등급×도메인 조합만 지목해 만든다.

    python scripts/synth_coverage_gaps.py                       # 현행 학습셋 격자
    python scripts/synth_coverage_gaps.py --min 12              # 이 수 미만을 '얇음'으로
    python scripts/synth_coverage_gaps.py --json gaps.json      # 기계가 읽을 형태로

읽기 전용이다 — 어떤 데이터도 고치지 않는다.

[2026-09-06] 계산은 **koipa.services.synth_coverage 가 정본**이다. 종전에는 이 파일
안에만 있어서, 사람이 터미널에서 표를 읽고 조합을 외운 뒤 콘솔 폼에 손으로 다시 넣어야
했다. 필요한 정보가 이미 있는데 화면이 그것을 모르는 상태였다. 계산을 src 로 올려
`GET /synth/coverage` 와 이 스크립트가 **같은 것**을 쓴다.

⚠ 빈 칸이라고 다 채울 것은 아니다. 도메인×등급 중에는 **현실에 없는 조합**이 있다
  (예: 공개 보도자료 도메인의 TS). 그런 칸을 억지로 채우면 모델에 없는 규칙을 가르친다.
  출력은 후보일 뿐이고, 무엇을 만들지는 사람이 고른다.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 한국어 Windows 콘솔은 cp949 다 — 표의 특수문자 하나에 출력이 죽지 않게 출구를 고정한다.
for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

from koipa.services.synth_coverage import (  # noqa: E402
    DEFAULT_MIN_PER_CELL,
    analyse,
    load_training_rows,
    render_text,
)

DEFAULT_SET = "datasets/labeled_p1_v5_clean"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="합성 커버리지 빈 칸 분석")
    ap.add_argument("--set", default=DEFAULT_SET, help="학습셋 디렉터리(train/val/test.jsonl)")
    ap.add_argument("--min", type=int, default=DEFAULT_MIN_PER_CELL, dest="min_per_cell",
                    help="이 수 미만이면 '얇은 칸'으로 본다")
    ap.add_argument("--json", help="결과를 이 경로에 JSON 으로 쓴다")
    a = ap.parse_args(argv)

    root = Path(a.set)
    rows = load_training_rows(root)
    if not rows:
        print(f"[gaps] 학습셋을 못 읽었다: {root}", file=sys.stderr)
        return 2

    out = analyse(rows, min_per_cell=a.min_per_cell)
    for line in render_text(out):
        print(line)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
