#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""등급이 갈리는 **최소 차이**를 전수로 센다 — 경계 쌍의 분모.

■ 왜 필요한가

"합성 문서에 등급을 정하는 내용이 없다"(2026-09-12 제거실험: 두 문자열을 지우니
57.2%→32.2%, 라벨 섞기 기준선 25%)는 것이 오늘의 핵심 결함이다. 고치려면 **등급이
무엇 때문에 갈리는지**를 문서에 담아야 하고, 그러려면 먼저 갈리는 자리를 알아야 한다.

등급식 `grade_from_svm` 은 순수 함수라 S·V·M 각 0..2 의 27개 조합을 전수로 돌릴 수 있다.
한 축만 1단계 움직여 등급이 바뀌는 조합이 **경계 쌍 유형**이다. 이 목록이 곧
경계 쌍 생성의 분모이고, 생성기가 무엇을 만들어야 하는지의 사양이다.

⚠ **경계 쌍 문서를 새로 만들지 말 것.** 이미 있다 —
   `datasets/v8/train.jsonl` 에 1,092쌍(varied_factor secrecy 896 · value 896 · management 392),
   dev 273쌍. 2026-09-13 에 이것을 모르고 생성기를 다시 짰다. 이 도구가 세는 것은
   그 쌍들이 덮어야 할 **유형의 분모**이지 문서가 아니다.

■ 읽는 법

  · 축      어느 요소를 움직였나 (S 비공지성 · V 경제가치 · M 관리성)
  · 전이    등급이 어떻게 바뀌나
  · 쏠림    어떤 등급이 조합 공간에서 희소한가 — 그 등급이 학습·평가에서 왜 어려운지의 구조적 이유

사용:
    python scripts/enumerate_grade_boundary_pairs.py
    python scripts/enumerate_grade_boundary_pairs.py --json
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))
sys.path.insert(0, str(_POC / "scripts"))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402

AXES = ("S", "V", "M")
AXIS_NAME = {"S": "비공지성", "V": "경제가치", "M": "관리성"}
LEVELS = (0, 1, 2)


def grade_cells() -> dict[tuple[int, int, int], str]:
    return {(s, v, m): grade_from_svm(s, v, m)
            for s in LEVELS for v in LEVELS for m in LEVELS}


def boundary_pairs(cells: dict[tuple[int, int, int], str]) -> list[dict]:
    """한 축만 1단계 올려 등급이 바뀌는 쌍을 전수로 모은다."""
    out = []
    deltas = {"S": (1, 0, 0), "V": (0, 1, 0), "M": (0, 0, 1)}
    for low, low_grade in sorted(cells.items()):
        for axis in AXES:
            d = deltas[axis]
            high = (low[0] + d[0], low[1] + d[1], low[2] + d[2])
            if max(high) > 2:
                continue
            high_grade = cells[high]
            if high_grade == low_grade:
                continue
            out.append({
                "axis": axis,
                "axis_name": AXIS_NAME[axis],
                "low_svm": list(low),
                "low_grade": low_grade,
                "high_svm": list(high),
                "high_grade": high_grade,
                "transition": f"{low_grade}->{high_grade}",
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="집계를 JSON 으로만 출력")
    a = ap.parse_args()

    cells = grade_cells()
    pairs = boundary_pairs(cells)
    grade_space = collections.Counter(cells.values())
    by_axis = collections.Counter(p["axis"] for p in pairs)
    by_transition = collections.Counter(p["transition"] for p in pairs)

    payload = {
        "formula": "grade_from_svm (정본 곱셈식 v2.2)",
        "combination_space": len(cells),
        "grade_space_distribution": dict(grade_space),
        "boundary_pair_types": len(pairs),
        "by_axis": dict(by_axis),
        "by_transition": dict(by_transition),
        "pairs": pairs,
    }
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=1))
        return 0

    w = sys.stdout.write
    w("=" * 84 + "\n")
    w(" 등급이 갈리는 최소 차이 — 경계 쌍 유형 전수\n")
    w("=" * 84 + "\n")
    w("  등급식      %s\n" % payload["formula"])
    w("  조합 공간   S·V·M 각 0..2 → %d 개\n" % len(cells))
    w("\n  조합 공간의 등급 분포 (희소한 등급이 구조적으로 어렵다)\n")
    for g in ("TS", "S1", "S2", "S3"):
        n = grade_space.get(g, 0)
        w("     %-3s %2d/%d  %5.1f%%%s\n"
          % (g, n, len(cells), n / len(cells) * 100,
             "   ← 조합이 하나뿐이다" if n == 1 else ""))
    w("\n  한 축 1단계 차이로 등급이 갈리는 쌍: **%d 개**\n" % len(pairs))
    w("     축별   %s\n" % "  ".join("%s %d" % (k, by_axis.get(k, 0)) for k in AXES))
    w("     전이별 %s\n" % "  ".join("%s %d" % (k, v) for k, v in sorted(by_transition.items())))
    w("\n" + "-" * 84 + "\n")
    for p in sorted(pairs, key=lambda x: (x["axis"], x["low_grade"], x["high_grade"])):
        lo, hi = p["low_svm"], p["high_svm"]
        w("  %s축(%s)  S%dV%dM%d = %-2s  →  S%dV%dM%d = %-2s\n"
          % (p["axis"], p["axis_name"], lo[0], lo[1], lo[2], p["low_grade"],
             hi[0], hi[1], hi[2], p["high_grade"]))
    w("-" * 84 + "\n")
    w("\n  쓰는 법: 이 %d 개가 경계 쌍 생성의 **사양이자 분모**다. 같은 문서종류·주제·길이에서\n"
      "  한 축만 바꿔 쌍을 만들면, 모델이 표면 단서가 아니라 그 차이를 보는지 직접 잴 수 있다.\n"
      % len(pairs))
    w("  ⛔ 이것은 등급식이 말하는 경계이지 사람이 확정한 정답이 아니다.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
