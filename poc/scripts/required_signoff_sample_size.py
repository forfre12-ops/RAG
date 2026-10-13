#!/usr/bin/env python
# -*- coding: utf-8 -*-
""""재현율 90%" 를 말하려면 사람이 몇 건을 서명해야 하는가 — 계산해서 못 박는다.

## 왜 이 계산이 필요한가 (2026-09-13)

우리는 "재현율 ≥90%(PMR-002)" 를 요건으로 받았고, 시나리오 실측에서는
`적대적 FNR 20.0% ≤ 5.0% · FAIL · N=2` 처럼 **N=2** 로 합격·불합격을 찍고 있었다.
2건으로는 어떤 비율도 주장할 수 없다. 0/2 를 맞혀도 참 미탐률의 95% 상한은 **77.6%** 다.

그래서 기준을 말로 정하지 않고 **표본 수로** 정한다:

    "미탐률 X% 이하" 를 신뢰수준 95% 로 주장하려면
    사람이 서명한 고등급 문서가 최소 몇 건 필요한가

이 수는 협상 대상이 아니다. 통계가 정한다.

## 쓰는 구간 추정법

Clopper-Pearson(정확 이항) 상한을 쓴다. Wilson 보다 보수적이고, 0 건 관측처럼
표본이 작고 사건이 드문 경우에 과소평가하지 않는다 — 미탐은 **놓치면 안 되는** 쪽이라
보수적인 편을 고른다.

    관측: 고등급 n 건 중 미탐 k 건
    주장: 참 미탐률 ≤ upper(n, k)  (신뢰수준 95%)

⚠ 이 수는 **그 표본이 대표하는 모집단에 대해서만** 성립한다. 합성 문서 100건을
   서명해도 그것은 합성 문서에 대한 주장이지 고객사 실문서에 대한 주장이 아니다.
   표본 수는 필요조건이지 충분조건이 아니다.

사용:

    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/required_signoff_sample_size.py
    ... --targets 0.05,0.10,0.20 --observed-misses 0,1,2
"""
from __future__ import annotations

import argparse
import sys

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

try:
    from scipy.stats import beta as _beta
except Exception:  # scipy 없으면 이분법으로 직접 푼다
    _beta = None


def _binom_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) — 작은 n 에서만 쓰므로 직접 더한다."""
    from math import comb

    return sum(comb(n, i) * (p**i) * ((1 - p) ** (n - i)) for i in range(k + 1))


def cp_upper(n: int, k: int, conf: float = 0.95) -> float:
    """Clopper-Pearson 상한. k 건 관측 시 참 비율의 상한."""
    if n <= 0:
        return 1.0
    if k >= n:
        return 1.0
    alpha = 1.0 - conf
    if _beta is not None:
        return float(_beta.ppf(1 - alpha, k + 1, n - k))
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = (lo + hi) / 2
        if _binom_cdf(k, n, mid) > alpha:
            lo = mid
        else:
            hi = mid
    return hi


def required_n(target: float, misses: int, conf: float = 0.95, cap: int = 20000) -> int | None:
    """미탐 `misses` 건을 관측하고도 '참 미탐률 <= target' 을 말할 수 있는 최소 n."""
    n = max(misses + 1, 1)
    while n <= cap:
        if cp_upper(n, misses, conf) <= target:
            return n
        n += 1
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--targets", default="0.05,0.10,0.20",
                    help="주장하려는 미탐률 상한들(쉼표). 0.10 = 재현율 90%%")
    ap.add_argument("--observed-misses", default="0,1,2,3",
                    help="그 표본에서 실제로 관측될 미탐 건수들(쉼표)")
    ap.add_argument("--conf", type=float, default=0.95)
    ap.add_argument("--have", type=int, default=39,
                    help="지금 보유한 사람 서명 건수(기본 39 = 2026-09-13 전수 실측)")
    args = ap.parse_args()

    targets = [float(x) for x in args.targets.split(",") if x.strip()]
    misses = [int(x) for x in args.observed_misses.split(",") if x.strip()]

    print("=" * 78)
    print(f"사람이 서명한 고등급 문서가 몇 건이어야 하는가 (신뢰수준 {args.conf:.0%})")
    print("=" * 78)
    print("\n  가로 = 그 표본에서 실제로 나올 미탐 건수 · 세로 = 주장하려는 미탐률 상한\n")
    head = "  주장          " + "".join(f"미탐 {m}건".rjust(12) for m in misses)
    print(head)
    print("  " + "-" * (len(head) - 2))
    for t in targets:
        row = f"  ≤{t*100:4.1f}% (재현율 {100-t*100:4.1f}%)"
        for m in misses:
            n = required_n(t, m, args.conf)
            row += (f"{n:,}건" if n else "불가").rjust(12)
        print(row)

    print("\n" + "=" * 78)
    print(f"지금 우리가 가진 것으로 말할 수 있는 최대치 (사람 서명 {args.have}건 전부가 고등급이라 가정)")
    print("=" * 78)
    for m in (0, 1, 2):
        u = cp_upper(args.have, m, args.conf)
        print(f"  미탐 {m}건 관측 -> 참 미탐률 ≤ {u*100:5.2f}%  (재현율 ≥ {100-u*100:5.2f}%)")
    print(f"\n  ⚠ 실제로는 서명 {args.have}건이 전부 고등급도 아니고 전부 실문서도 아니다.")
    print("     위 값은 **가장 후하게 쳐도 여기까지** 라는 뜻이다.")

    print("\n" + "=" * 78)
    print("N=2 로 찍던 합격·불합격의 실제 의미")
    print("=" * 78)
    for n in (2, 5, 10):
        u = cp_upper(n, 0, args.conf)
        print(f"  {n}건 중 미탐 0건 -> 참 미탐률 ≤ {u*100:5.1f}%  = 사실상 아무 말도 못 한다")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
