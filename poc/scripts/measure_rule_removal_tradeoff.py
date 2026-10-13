"""룰을 빼면 무엇을 얻고 무엇을 잃는가 — 자동확정율과 미탐을 같은 표에 놓는다.

왜 이 도구가 있는가(2026-09-10). KL 이 "S/V/M 룰이 자동확정율을 떨어뜨리니 빼고 다른
기능으로 대체하자"고 제안했다. 답하려면 **얻는 것과 잃는 것을 같은 분모로** 재야 하는데,
지금까지 그 둘은 따로 재고 있었다 — 자동확정율은 serving_records 가, 미탐은 score_model 이.

한 번 42건으로 재고 "룰을 빼면 +4.8pp" 라고 답할 뻔했다. 800건에서는 +29.6pp 였다.
**표본이 작으면 방향까지 틀린다.**

무엇을 세는가. `measure_serving_records.py` 가 남긴 레코드(.records.jsonl)를 읽어,
검수로 간 문서를 사유별로 가르고 **그중 실제로 낮게 본 것(미탐)** 을 센다.

    합의 게이트가 붙잡은 문서 N 건 · 그중 미탐 U 건
      → 룰을 빼면  자동확정율 +N/전체 ·  무음 미탐 +U 건

미탐 정의는 `예측 등급이 정답보다 낮다` 하나다. 계약 핵심목표가 "미탐 최소화"이므로
과대(예측이 더 높음)는 여기서 세지 않는다 — 그쪽은 검수 부담이지 사고가 아니다.

⚠ 평가셋의 정답이 곧 진실은 아니다. 이 저장소의 홀드아웃 4종은 길이가 등급을 누설하고
  (holdout-length-tells-grade), v3_final800 은 길이만으로 96% 적중한다. 그래서 이 도구는
  **셋을 여러 개 나란히** 찍는다. 한 셋의 수치를 단독으로 인용하지 말 것.

실행:
    python scripts/measure_serving_records.py --eval <셋> --out reports/x.json ...   # 먼저
    python scripts/measure_rule_removal_tradeoff.py reports/x.records.jsonl ...      # 그 다음
"""
from __future__ import annotations

try:  # 콘솔 출구 고정 — cp949 에서 em dash 하나에 죽지 않게
    from _cli_io import force_utf8_stdio  # noqa: E402
except ImportError:  # 패키지로 import 될 때(릴리스 번들의 import 폐쇄 검사)
    from scripts._cli_io import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

import argparse
import collections
import json
from pathlib import Path

# 낮은 값이 더 심각한 등급. 예측 순위 > 정답 순위 이면 낮게 본 것 = 미탐.
_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}

# 룰이 관여하는 검수 사유. 이것만 룰 제거의 영향을 받는다 — 나머지는 룰과 무관하다.
_RULE_REASONS = frozenset({"agreement-gate"})


def is_underclassified(row: dict) -> bool:
    truth, pred = row.get("truth"), row.get("predicted")
    if truth not in _ORDER or pred not in _ORDER:
        return False
    return _ORDER[pred] > _ORDER[truth]


def analyze(path: Path) -> dict:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    n = len(rows)
    if not n:
        return {"n": 0}
    auto = [r for r in rows if r.get("status") != "needs_review"]
    by_reason: dict[str, list[dict]] = collections.defaultdict(list)
    for r in rows:
        if r.get("status") == "needs_review":
            by_reason[str(r.get("causal_review_reason") or "미분류")].append(r)

    reasons = {}
    for reason, group in by_reason.items():
        misses = [r for r in group if is_underclassified(r)]
        reasons[reason] = {
            "held": len(group),
            "misses_held": len(misses),
            "miss_rate": len(misses) / len(group),
            "rule_related": reason in _RULE_REASONS,
            "shifts": dict(
                collections.Counter(f"{r['truth']}->{r['predicted']}" for r in misses).most_common(5)
            ),
        }
    rule_held = sum(v["held"] for v in reasons.values() if v["rule_related"])
    rule_misses = sum(v["misses_held"] for v in reasons.values() if v["rule_related"])
    return {
        "n": n,
        "auto_confirmed": len(auto),
        "auto_confirm_rate": len(auto) / n,
        "silent_misses_now": sum(1 for r in auto if is_underclassified(r)),
        "reasons": reasons,
        # 룰 제거의 결과 — 얻는 것과 잃는 것.
        "if_rule_removed": {
            "auto_confirm_rate": (len(auto) + rule_held) / n,
            "auto_confirm_gain_pp": rule_held / n,
            "new_silent_misses": rule_misses,
            "new_silent_miss_rate": rule_misses / n,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("records", nargs="+", help="measure_serving_records.py 가 남긴 *.records.jsonl")
    ap.add_argument("--out", default="reports/rule_removal_tradeoff.json")
    args = ap.parse_args()

    report: dict = {}
    for raw in args.records:
        path = Path(raw)
        if not path.exists():
            print(f"[건너뜀] 레코드 파일이 없다: {path}")
            continue
        res = analyze(path)
        if not res.get("n"):
            print(f"[건너뜀] 빈 레코드: {path}")
            continue
        report[path.stem] = res
        cf = res["if_rule_removed"]
        print(f"\n== {path.stem} — {res['n']}건")
        print(f"   지금        자동확정 {res['auto_confirmed']}건 ({res['auto_confirm_rate']:.1%})"
              f" · 무음 미탐 {res['silent_misses_now']}건")
        print(f"   {'검수 사유':<18}{'붙잡음':>8}{'그중 미탐':>10}{'미탐률':>9}")
        for reason, v in sorted(res["reasons"].items(), key=lambda x: -x[1]["held"]):
            mark = " ← 룰" if v["rule_related"] else ""
            print(f"   {reason:<18}{v['held']:>8}{v['misses_held']:>10}{v['miss_rate']:>8.1%}{mark}")
        print(f"   룰 제거 시  자동확정 {cf['auto_confirm_rate']:.1%}"
              f" (+{cf['auto_confirm_gain_pp']:.1%}p)"
              f" · 무음 미탐 +{cf['new_silent_misses']}건 ({cf['new_silent_miss_rate']:.1%})")
        for reason, v in res["reasons"].items():
            if v["rule_related"] and v["shifts"]:
                print(f"      새로 열리는 미탐: {v['shifts']}")

    if report:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n기록: {out}")
        print("\n⚠ 한 셋의 수치를 단독으로 인용하지 말 것 — 42건과 800건에서 결론이 갈렸다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
