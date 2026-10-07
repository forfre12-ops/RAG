#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""저장해 둔 원시 확률로 kappa·tau 를 훑는다 — 추론 없이 표만 만든다.

묻는 것(2026-09-10): 요소 미확정 90%(secrecy)가 **데이터 탓인가 문턱 탓인가.**
kappa 를 낮추면 하향 단언이 unknown 으로 접히지 않으므로, 접힘이 문턱 탓이면
확정률이 곧바로 오른다. 오르지 않으면 모델이 정말 모르는 것이다.

⚠ 라벨은 생성 시 의도 등급(기계 라벨)이고 사람 확정 0건이다. 그래서 아래 '어긋남'은
  정확도가 아니라 **방향 분포**다. 미탐(라벨보다 낮게 봄)이 1차 목표에 직결된다.

입력 = scripts/dump_factor_probs.py 산출물
사용:
    python scripts/sweep_factor_thresholds.py --probs tmp/factor_probs_v8_caus.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
for _p in (str(_HERE), str(_POC / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="tmp/factor_probs_v8_caus.jsonl")
    ap.add_argument("--out", default="reports/FACTOR_THRESHOLD_SWEEP_2026-09-10.json")
    a = ap.parse_args()

    from koipa.modules.m5_inference.factor_model import CLS_UNKNOWN, apply_serving_gate

    rows = [json.loads(l) for l in Path(a.probs).open(encoding="utf-8") if l.strip()]
    n = len(rows)
    print("[data] %d건 · %s" % (n, a.probs))

    grid = [0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.99]
    results = []
    print()
    print("  kappa   tau |  요소확정   자동확정 | 자동확정분 라벨대비 (기계라벨)")
    print("              |                      |  일치    과분류(높게)  미탐(낮게)")
    print("  " + "-" * 76)
    for kappa in grid:
        for tau in grid:
            settled_n = auto_n = same = higher = lower = 0
            for r in rows:
                fp = apply_serving_gate(tuple(r["codes"]), r["probs"], metadata=None,
                                        tau=tau, kappa=kappa)
                if all(c != CLS_UNKNOWN for c in fp.codes):
                    settled_n += 1
                if fp.auto_confirmable:
                    auto_n += 1
                    g, lab = ORDER.get(fp.serving_grade, 9), ORDER.get(r["label"], 9)
                    if g == lab:
                        same += 1
                    elif g < lab:
                        higher += 1     # 라벨보다 높게 봄 = 과분류
                    else:
                        lower += 1      # 라벨보다 낮게 봄 = 미탐 방향
            rec = {"kappa": kappa, "tau": tau, "n": n, "settled": settled_n,
                   "auto": auto_n, "auto_same": same, "auto_higher": higher,
                   "auto_lower": lower}
            results.append(rec)
            pa = ("%5.1f%%" % (auto_n / n * 100))
            frac = lambda k: ("%4d %5.1f%%" % (k, k / auto_n * 100)) if auto_n else "   -      -"
            print("  %5.2f %5.2f | %5.1f%%   %s | %s  %s  %s"
                  % (kappa, tau, settled_n / n * 100, pa,
                     frac(same), frac(higher), frac(lower)))

    outp = Path(a.out)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({"probs_file": a.probs, "n": n, "grid": grid,
                                "results": results}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print("\n[done] %s" % outp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
