"""검수 순서가 미탐을 얼마나 빨리 잡는가 — 정렬 방식을 같은 큐에 대고 비교한다.

왜 이 도구가 있는가(2026-09-10). 검수 큐를 FIFO 에서 위험 기반으로 바꿨다. 근거는
"낮은 등급으로 본 것이 실은 고등급이면 치명적 미탐이므로 그것부터 보는 게 낫다"는
**추론**이었다. 추론으로 만든 기능은 추론으로 남겨 두지 않는다.

무엇을 재는가. 이미 태워 둔 서빙 레코드(`measure_serving_records.py` 산출)를 큐로 보고,
정렬을 바꿔 가며 **앞에서 k 건만 검수했을 때 잡히는 미탐 수**를 센다. 사람 검수 시간이
유한하다는 것이 이 측정의 전제다 — 20건만 볼 수 있으면 그 20건에서 몇 건을 잡는가.

    fifo        도착 순서(레코드 파일 순서). 종전 동작
    risk        예측 등급이 낮은 것 먼저 → 같으면 신뢰도 낮은 것 먼저 (배포한 정렬)
    confidence  신뢰도만 낮은 것 먼저 (등급을 안 보는 대조군)
    oracle      실제 미탐을 먼저 (도달 불가 상한 — 얼마나 남았는지 보는 자)

⚠ 이 측정은 **정렬만** 비교한다. 큐에 무엇이 들어오는지는 게이트가 정하고 그것은 다른 축이다.
⚠ 평가셋의 정답이 곧 진실은 아니다(홀드아웃 4종은 길이가 등급을 누설한다). 절대 수치보다
  정렬 간 **차이**를 볼 것 — 정답이 같은 자로 재므로 차이는 성립한다.

실행:
    python scripts/measure_serving_records.py --eval <셋> --out reports/x.json ...   # 먼저
    python scripts/measure_review_order_yield.py reports/x.records.jsonl            # 그 다음
"""
from __future__ import annotations

try:  # 콘솔 출구 고정 — cp949 에서 em dash 하나에 죽지 않게
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import 될 때(릴리스 번들의 import 폐쇄 검사)
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import json
from pathlib import Path

# 낮은 값이 더 심각한 등급. 위험 정렬은 **큰 값(낮은 등급)부터** 본다.
_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def is_miss(row: dict) -> bool:
    """예측이 정답보다 낮다 = 미탐. 계약 핵심목표가 막으려는 그것."""
    truth, pred = row.get("truth"), row.get("predicted")
    if truth not in _ORDER or pred not in _ORDER:
        return False
    return _ORDER[pred] > _ORDER[truth]


def _risk_key(row: dict):
    # level_order 는 TS=1…S3=4 이므로 낮은 등급이 먼저 오려면 내림차순이다. 파이썬
    # 오름차순 정렬에 태우려고 부호를 뒤집는다(서빙 SQL 의 .desc() 와 같은 뜻).
    return (-_ORDER.get(row.get("predicted"), 9), float(row.get("confidence") or 0.0))


def orderings(rows: list[dict]) -> dict[str, list[dict]]:
    return {
        "fifo": list(rows),
        "risk": sorted(rows, key=_risk_key),
        "confidence": sorted(rows, key=lambda r: float(r.get("confidence") or 0.0)),
        "oracle": sorted(rows, key=lambda r: (not is_miss(r),)),
    }


def yield_at(ordered: list[dict], ks: list[int]) -> dict[int, int]:
    """앞에서 k 건 검수했을 때 누적으로 잡힌 미탐 수."""
    found = 0
    out: dict[int, int] = {}
    for i, row in enumerate(ordered, start=1):
        if is_miss(row):
            found += 1
        if i in ks:
            out[i] = found
    for k in ks:
        out.setdefault(k, found)      # k 가 큐보다 크면 전량 검수한 값
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("records", nargs="+", help="measure_serving_records.py 가 남긴 *.records.jsonl")
    ap.add_argument("--k", default="20,50,100,200", help="검수 건수 지점")
    ap.add_argument("--out", default="reports/review_order_yield.json")
    args = ap.parse_args()
    ks = [int(x) for x in args.k.split(",") if x.strip()]

    report: dict = {}
    for raw in args.records:
        path = Path(raw)
        if not path.exists():
            print(f"[건너뜀] 레코드 파일이 없다: {path}")
            continue
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        queue = [r for r in rows if r.get("status") == "needs_review"]
        misses = sum(1 for r in queue if is_miss(r))
        if not queue:
            print(f"[건너뜀] 검수 대기가 없다: {path}")
            continue

        print(f"\n== {path.stem} — 검수 대기 {len(queue)}건 · 그중 미탐 {misses}건 "
              f"({misses/len(queue):.1%})")
        header = "".join(f"{('k=' + str(k)):>10}" for k in ks)
        print(f"   {'정렬':<12}{header}")
        per_order: dict[str, dict] = {}
        for name, ordered in orderings(queue).items():
            got = yield_at(ordered, ks)
            per_order[name] = {str(k): got[k] for k in ks}
            cells = "".join(f"{got[k]:>10}" for k in ks)
            tag = {"fifo": "  (종전)", "risk": "  (배포)", "oracle": "  (상한)"}.get(name, "")
            print(f"   {name:<12}{cells}{tag}")
        report[path.stem] = {
            "queue": len(queue), "misses": misses, "k": ks, "yield": per_order,
        }
        # 판정 문장을 사람이 아니라 도구가 낸다 — "좋아 보인다"로 남기지 않는다.
        for k in ks:
            f, r = per_order["fifo"][str(k)], per_order["risk"][str(k)]
            if r != f:
                verdict = "위험 정렬이 더 잡는다" if r > f else "위험 정렬이 **덜** 잡는다"
                print(f"      k={k}: fifo {f} → risk {r}  ({verdict})")

    if report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기록: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
