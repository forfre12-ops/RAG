"""A2(거부 조건) 설계안들을 **같은 데이터로** 비교한다 — 추론을 다시 돌리지 않는다.

왜. 첫 설계는 "v8 이 더 높게 보면 검수로" 였는데 스모크 12건에서 자동확정 6건을 전부
검수로 보내면서 실제 과소분류는 0건을 잡았다(유효율 0%). v8 은 체계적으로 과분류하므로
`factor_higher` 대부분이 v5 의 미탐이 아니라 v8 의 잡음이다.

거부 조건은 **비용(검수 증가)과 이득(미탐 차단)의 교환**이다. 설계를 여러 개 놓고 같은
표본에서 재야 고를 수 있다. 이 스크립트는 섀도 전수 결과(V8_SHADOW_SWEEP.json)를 읽어
후보 설계들을 채점한다.

설계 축은 셋이다:

    확신     v8 이 확신할 때만 받는다 (min_conf >= c)
    경계     이견이 **영업비밀 여부**를 가르는 경우만 받는다 (비밀 <-> 비-비밀)
    폭       한 등급 차이는 무시하고 두 등급 이상만 받는다

⚠ 라벨이 기계 라벨이라 "잡았다/헛수고" 는 정확도 주장이 아니라 **그 라벨 기준의 상대
   비교**다. 설계 간 순위를 보는 용도이지 절대 성능이 아니다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
SECRET = ("TS", "S1")


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def _under(row: dict) -> bool:
    """v5 가 기계 라벨보다 낮게 봤는가(= 과소분류)."""
    lab = row.get("label")
    return bool(lab) and ORDER.get(row["v5"], 9) > ORDER.get(lab, 9)


def _crosses_secret_boundary(row: dict) -> bool:
    """이견이 '영업비밀인가 아닌가' 를 가르는가. 그것이 실제로 갈리는 결정이다."""
    return (row["v5"] not in SECRET) and (row["v8"] in SECRET)


def _gap(row: dict) -> int:
    return ORDER.get(row["v5"], 9) - ORDER.get(row["v8"], 9)


DESIGNS = {
    "D0 이견이면 검수": lambda r: r["direction"] == "factor_higher",
    "D1 확신 0.90 이상": lambda r: r["direction"] == "factor_higher" and r["min_conf"] >= 0.90,
    "D2 확신 0.99 이상": lambda r: r["direction"] == "factor_higher" and r["min_conf"] >= 0.99,
    "D3 비밀경계만": lambda r: _crosses_secret_boundary(r),
    "D4 비밀경계+확신0.90": lambda r: _crosses_secret_boundary(r) and r["min_conf"] >= 0.90,
    "D5 두 등급 이상": lambda r: r["direction"] == "factor_higher" and _gap(r) >= 2,
    "D6 비밀경계+두등급": lambda r: _crosses_secret_boundary(r) and _gap(r) >= 2,
}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="A2 설계 비교")
    ap.add_argument("--sweep", default="reports/V8_SHADOW_SWEEP.json")
    ap.add_argument("--report", default="reports/V8_A2_DESIGNS.json")
    args = ap.parse_args(argv)

    data = json.loads(Path(args.sweep).read_text("utf-8"))
    rows = data["rows"]
    auto = [r for r in rows if r["v5_status"] != "needs_review"]
    base_under = [r for r in auto if _under(r)]
    print(f"[data] {len(rows)}건 · v5 자동확정 {len(auto)}건 · "
          f"그 안의 과소분류 {len(base_under)}건(기계 라벨 기준)\n")
    print(f"{'설계':24s}{'검수이동':>8s}{'잡음':>6s}{'헛수고':>7s}{'유효율':>8s}"
          f"{'남는자동':>9s}{'남는미탐':>9s}{'미탐상한':>10s}")

    out: dict = {"n": len(rows), "v5_auto": len(auto), "base_under": len(base_under),
                 "designs": {}}
    for name, rule in DESIGNS.items():
        moved = [r for r in auto if rule(r)]
        caught = [r for r in moved if _under(r)]
        wasted = len(moved) - len(caught)
        remain = [r for r in auto if r not in moved]
        left = [r for r in remain if _under(r)]
        eff = (len(caught) / len(moved)) if moved else 0.0
        up = wilson_upper(len(left), len(remain))
        out["designs"][name] = {
            "moved": len(moved), "caught": len(caught), "wasted": wasted,
            "efficiency": round(eff, 4), "remaining_auto": len(remain),
            "remaining_under": len(left), "under_upper95": round(up, 5),
        }
        print(f"{name:24s}{len(moved):>8d}{len(caught):>6d}{wasted:>7d}{eff:>8.1%}"
              f"{len(remain):>9d}{len(left):>9d}{up:>10.4f}")

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n읽는 법: 검수이동이 비용이고 잡음이 이득이다. 유효율이 낮으면 그 설계는")
    print("검수자를 헛되이 부른다. 남는미탐이 0 이 아니면 그 설계로는 아직 새는 곳이 있다.")
    print("⚠ 기계 라벨 기준이므로 설계 간 순위 비교용이지 절대 성능이 아니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
