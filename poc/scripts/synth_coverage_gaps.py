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

⚠ 빈 칸이라고 다 채울 것은 아니다. 도메인×등급 중에는 **현실에 없는 조합**이 있다
  (예: 공개 보도자료 도메인의 TS). 그런 칸을 억지로 채우면 모델에 없는 규칙을 가르친다.
  출력은 후보일 뿐이고, 무엇을 만들지는 사람이 고른다.
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

GRADES = ["TS", "S1", "S2", "S3"]
DEFAULT_SET = "datasets/labeled_p1_v5_clean"


def _load(root: Path) -> list[dict]:
    rows: list[dict] = []
    for name in ("train", "val", "test"):
        p = root / f"{name}.jsonl"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    return rows


def _domain(r: dict) -> str:
    return r.get("domain") or r.get("doc_type") or "(미상)"


def analyse(rows: list[dict], *, min_per_cell: int) -> dict:
    grid: collections.Counter = collections.Counter()
    real: collections.Counter = collections.Counter()
    for r in rows:
        lab = r.get("label")
        if lab not in GRADES:
            continue
        cell = (lab, _domain(r))
        grid[cell] += 1
        # 합성이 아닌 것 = 실문서 유래. 빈 칸이라도 실문서가 있으면 합성이 급하지 않다.
        if (r.get("source") or "") != "synthetic":
            real[cell] += 1

    domains = sorted({d for _g, d in grid})
    empty: list[dict] = []
    thin: list[dict] = []
    for g in GRADES:
        for d in domains:
            n = grid[(g, d)]
            item = {"grade": g, "domain": d, "n": n, "real": real[(g, d)]}
            if n == 0:
                empty.append(item)
            elif n < min_per_cell:
                thin.append(item)
    thin.sort(key=lambda x: x["n"])
    return {
        "documents": sum(grid.values()),
        "grades": {g: sum(v for (gg, _d), v in grid.items() if gg == g) for g in GRADES},
        "domains": domains,
        "cells_total": len(GRADES) * len(domains),
        "cells_filled": len(grid),
        "empty": empty,
        "thin": thin,
        "min_per_cell": min_per_cell,
        "grid": {f"{g}|{d}": grid[(g, d)] for g in GRADES for d in domains},
        "real_share": round(
            sum(real.values()) / sum(grid.values()), 3
        ) if grid else 0.0,
    }


def _render(out: dict) -> None:
    print("=" * 74)
    print(" 합성으로 채울 자리 — 등급 × 도메인 격자")
    print("=" * 74)
    print(f"  문서 {out['documents']:,}건 · 도메인 {len(out['domains'])}종 · "
          f"칸 {out['cells_filled']}/{out['cells_total']} 채워짐")
    print(f"  실문서 유래 비중 {out['real_share']:.1%}  (나머지는 합성)")
    print(f"  등급 분포 {out['grades']}")
    print()

    w = max((len(d) for d in out["domains"]), default=8) + 2
    print("  " + " " * w + "".join(f"{g:>8}" for g in GRADES))
    for d in out["domains"]:
        line = f"  {d:<{w}}"
        for g in GRADES:
            n = out["grid"][f"{g}|{d}"]
            mark = "  ." if n == 0 else f"{n:>4}"
            line += f"{mark:>8}"
        print(line)
    print()
    print(f"  빈 칸 {len(out['empty'])}개 · 얇은 칸(<{out['min_per_cell']}) {len(out['thin'])}개")
    if out["thin"]:
        print("\n  가장 얇은 칸 (합성 후보 — 현실에 있는 조합인지 사람이 고를 것)")
        for it in out["thin"][:12]:
            print(f"    {it['grade']:<3} {it['domain']:<16} {it['n']:>3}건 "
                  f"(실문서 {it['real']}건)")
    print()
    print("  ⚠ 빈 칸이라고 다 채울 것은 아니다 — 현실에 없는 조합(예: 보도자료 도메인의 TS)을")
    print("    억지로 채우면 모델에 없는 규칙을 가르친다. 출력은 후보일 뿐이다.")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="합성 커버리지 빈 칸 분석")
    ap.add_argument("--set", default=DEFAULT_SET, help="학습셋 디렉터리(train/val/test.jsonl)")
    ap.add_argument("--min", type=int, default=12, dest="min_per_cell",
                    help="이 수 미만이면 '얇은 칸'으로 본다")
    ap.add_argument("--json", help="결과를 이 경로에 JSON 으로 쓴다")
    a = ap.parse_args(argv)

    root = Path(a.set)
    rows = _load(root)
    if not rows:
        print(f"[gaps] 학습셋을 못 읽었다: {root}", file=sys.stderr)
        return 2

    out = analyse(rows, min_per_cell=a.min_per_cell)
    _render(out)
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  → {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
