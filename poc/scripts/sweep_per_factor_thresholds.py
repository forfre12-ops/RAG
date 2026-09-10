#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""요소별로 문턱을 따로 두면 어떻게 되는가 — 그리고 확신도 보정이 그것을 바꾸는가.

묻는 것(2026-09-10). 지금 factor_kappa 하나가 세 요소에 똑같이 걸린다. 그런데 요소별
하향 단언 확신도가 전혀 다르다(v8_caus 1,055건 실측):

    value        중앙 0.9913   문턱 0.99 에서 55.6% 확정
    secrecy      중앙 0.9094                10.0%
    management   중앙 0.8405                21.9%

하나의 문턱을 셋에 쓰는 것은 설계가 틀린 것이다. 요소마다 다른 값을 주면 어떻게 되는지 잰다.

그리고 그 확신도를 그대로 믿으면 안 된다. `v8_bal/temperature.json` 의 보정값이
secrecy 1.81 · value 1.7612 · management 1.615 다. **1 보다 크다는 것은 그 모델이
과신 상태였다는 뜻**이고, 보정은 확신도를 낮춘다. 그래서 두 번째 판에서 같은 온도를
v8_caus 확률에 얹어 본다.

온도 적용법: 저장한 것은 확률이지 로짓이 아니다. 그러나 softmax(log(p)/T) 는 원래
로짓에 온도를 건 것과 같다(softmax 가 상수 이동을 무시하므로). 그래서 그대로 쓸 수 있다.
⚠ 그 온도는 **v8_bal 을 위해 맞춘 값**이다. v8_caus 에 얹은 것은 크기 가늠이지 보정이
  아니다. 진짜 답은 v8_bal 자체를 돌린 값이다(tmp/factor_probs_v8_bal.jsonl).

사용:
    python scripts/sweep_per_factor_thresholds.py
    python scripts/sweep_per_factor_thresholds.py --probs tmp/factor_probs_v8_bal.jsonl --no-temp
"""
from __future__ import annotations

import argparse
import collections
import json
import math
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for _p in (str(_HERE), str(_HERE.parent / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

FACTORS = ("secrecy", "value", "management")
ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
# v8_bal/temperature.json 에서 그대로 옮긴 값
TEMP = {"secrecy": 1.81, "value": 1.7612, "management": 1.615}


def apply_temp(probs, temps):
    """softmax(log(p)/T) — 저장된 확률에 온도를 건다."""
    out = []
    for k, p in enumerate(probs):
        t = temps[k]
        z = [math.log(max(v, 1e-12)) / t for v in p]
        m = max(z)
        ez = [math.exp(v - m) for v in z]
        s = sum(ez)
        out.append([v / s for v in ez])
    return out


def evaluate(rows, kappas, tau, *, temps=None):
    """게이트를 다시 걸어 요소확정·자동확정·방향을 센다."""
    from koipa.modules.m5_inference.factor_model import apply_serving_gate, CLS_UNKNOWN

    settled = auto = same = higher = lower = 0
    for r in rows:
        probs = apply_temp(r["probs"], temps) if temps else r["probs"]
        codes = list(r["codes"])
        # apply_serving_gate 는 kappa 를 하나만 받는다. 요소별로 걸려면 여기서 먼저
        # 요소별 문턱을 적용해 unknown 으로 접고, 게이트에는 0 을 준다(추가 접힘 없음).
        from koipa.modules.m5_inference.factor_model import DOWNGRADE
        for k in range(3):
            if codes[k] in DOWNGRADE and probs[k][codes[k]] < kappas[k]:
                codes[k] = CLS_UNKNOWN
        fp = apply_serving_gate(tuple(codes), probs, metadata=None, tau=tau, kappa=0.0)
        if all(c != CLS_UNKNOWN for c in fp.codes):
            settled += 1
        if fp.auto_confirmable:
            auto += 1
            g, lab = ORDER.get(fp.serving_grade, 9), ORDER.get(r["label"], 9)
            if g == lab:
                same += 1
            elif g < lab:
                higher += 1
            else:
                lower += 1
    return settled, auto, same, higher, lower


def show(title, rows, settings, tau, temps=None):
    n = len(rows)
    print()
    print(title)
    print("  secrecy value  mgmt  | 요소확정  자동확정 | 일치   과분류   미탐")
    print("  " + "-" * 68)
    for kappas in settings:
        s, a, sm, hi, lo = evaluate(rows, kappas, tau, temps=temps)
        f = (lambda k: "%4d %5.1f%%" % (k, k / a * 100)) if a else (lambda k: "   -      -")
        print("  %5.2f  %5.2f  %5.2f | %6.1f%%  %6.1f%%  | %s %s %s"
              % (kappas[0], kappas[1], kappas[2], s / n * 100, a / n * 100,
                 f(sm), f(hi), f(lo)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probs", default="tmp/factor_probs_v8_caus.jsonl")
    ap.add_argument("--tau", type=float, default=0.99)
    ap.add_argument("--no-temp", action="store_true", help="온도 적용 판을 건너뛴다")
    a = ap.parse_args()

    rows = [json.loads(line) for line in Path(a.probs).open(encoding="utf-8") if line.strip()]
    n = len(rows)
    print("%s · %d건 · tau=%.2f" % (a.probs, n, a.tau))

    from koipa.modules.m5_inference.factor_model import DOWNGRADE
    print()
    print("요소별 하향 단언 확신도")
    for k, name in enumerate(FACTORS):
        conf = sorted(p["probs"][k][p["codes"][k]] for p in rows if p["codes"][k] in DOWNGRADE)
        if conf:
            print("  %-12s n=%5d  중앙 %.4f  상위10%% %.4f"
                  % (name, len(conf), conf[len(conf) // 2], conf[int(len(conf) * 0.9)]))

    settings = [
        (0.99, 0.99, 0.99),      # 현행 — 하나로 통일
        (0.95, 0.99, 0.95),
        (0.90, 0.99, 0.90),
        (0.90, 0.99, 0.99),      # 유용성·관리성은 엄격, 비공지성만 완화
        (0.99, 0.99, 0.90),
        (0.80, 0.99, 0.80),
    ]
    show("① 요소별 문턱 — 확신도 그대로 (보정 없음)", rows, settings, a.tau)

    if not a.no_temp:
        temps = [TEMP[f] for f in FACTORS]
        print()
        print("② 같은 판에 온도 보정을 얹으면 (v8_bal 의 온도 %s)"
              % {k: v for k, v in TEMP.items()})
        print("   ⚠ 이 온도는 v8_bal 용이다. 크기 가늠이지 보정이 아니다.")
        show("", rows, settings, a.tau, temps=temps)

    print()
    print("읽는 법 — 미탐(라벨보다 낮게 봄)이 1차 목표에 직결된다. 자동확정을 늘리면서")
    print("미탐 비율이 안 오르는 조합이 있으면 그것이 이득이다. 없으면 문턱은 답이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
