"""자동확정 게이트 시뮬레이터 — 사전등록 §3 의 **결합 조건**을 잰다.

판정기(`v8_judge.py`)는 모델 단독 지표만 잰다. 그런데 사전등록이 요구하는 것은 그게 아니다:

    자동확정 커버리지 >= 10%  이면서  무음 미탐 95% 상한 <= 2.0%

"무음 미탐 0" 단독은 조건이 될 수 없다. 자동확정이 0 이면 정의상 성립하고, 그러면 자동화를
멈추는 것이 게이트 통과의 최적해가 된다. 이 함정에 이미 두 번 빠졌다(v3 에서 "무음 미탐 0" 을
안전의 증거로 읽었다가 자기 계보에서 3.8% 가 나왔다).

이 도구는 판정기가 남긴 기록(`--records`)만 읽는다. 추론을 다시 돌리지 않으므로 임계를
몇 개든 훑을 수 있다 — 학습 한 사이클이 2시간이라 재추론은 비싸다.

게이트 구성(플랜 §5.2 · V8_MASTER_PLAN §3.3):

    1  신뢰도      세 헤드 최대확률의 **최소값**이 tau 이상   (한 요소라도 흔들리면 검수)
    2  S3 정책     S3 예측은 보수적 완성을 통과해야 한다      (부재가 입증돼야 자동확정)
    3  고등급      TS/S1 예측은 별도 tau 를 쓸 수 있다

⚠ 2번이 정책 역전의 핵심이다. 현행 배포본은 S3 예측을 합의에서 **면제**하는데 논리가
거꾸로다 - 최하등급 예측이 곧 과소분류다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
for _p in (str(_ROOT / "src"), str(_ROOT / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

GRADES = ("TS", "S1", "S2", "S3")
ORDER = {g: i for i, g in enumerate(GRADES)}


def cls_to_worst(c: int) -> int:
    return 0 if c == 0 else (2 if c == 3 else int(c))


def wilson_upper(k: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 1.0
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    r = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return min(1.0, (c + r) / d)


def evaluate(records: list[dict], tau: float, *, s3_policy: bool,
             grade_from_svm) -> dict:
    """한 임계에서의 게이트 동작.

    무음 미탐 = **자동확정된 것 중** 정답보다 낮은 등급을 준 건. 검수로 간 것은 사람이
    보므로 무음이 아니다. 이 구분이 없으면 모델 단독 FNR 과 섞여 숫자가 뒤집힌다.
    """
    auto = miss = 0
    auto_correct = 0
    blocked_by_s3 = 0
    miss_detail: dict[str, int] = {}
    for r in records:
        conf = min(r["head_conf"])
        if conf < tau:
            continue
        if s3_policy and r["pred"] == "S3":
            # 보수적 완성 - unknown 을 최악으로 채워도 S3 여야 자동확정 후보다
            pw = grade_from_svm(*[cls_to_worst(c) for c in r["pred_codes"]])
            if pw != "S3":
                blocked_by_s3 += 1
                continue
        auto += 1
        if r["pred"] == r["truth"]:
            auto_correct += 1
        elif ORDER[r["pred"]] > ORDER[r["truth"]]:
            miss += 1
            miss_detail[f"{r['truth']}->{r['pred']}"] = miss_detail.get(f"{r['truth']}->{r['pred']}", 0) + 1
    n = len(records)
    return {
        "tau": tau,
        "coverage": round(auto / n, 4),
        "auto_n": auto,
        "precision": round(auto_correct / auto, 4) if auto else None,
        "silent_miss_n": miss,
        "silent_miss_rate": round(miss / auto, 4) if auto else None,
        "silent_miss_95_upper": round(wilson_upper(miss, auto), 4) if auto else None,
        "blocked_by_s3_policy": blocked_by_s3,
        "miss_detail": miss_detail,
    }


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="자동확정 게이트 시뮬레이터")
    ap.add_argument("--records", default="reports/V8_RECORDS.jsonl")
    ap.add_argument("--taus", default="0.50,0.60,0.70,0.80,0.90,0.95,0.99")
    ap.add_argument("--min-coverage", type=float, default=0.10,
                    help="사전등록 1단계 조건 — 이 미만이면 조건 미달")
    ap.add_argument("--max-miss-upper", type=float, default=0.02,
                    help="무음 미탐 95% 상한 허용치")
    ap.add_argument("--temperature", default=None,
                    help="요소별 온도 json — 신뢰도를 보정해 게이트에 넣는다")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    records = [json.loads(l) for l in Path(args.records).read_text("utf-8").splitlines() if l.strip()]
    print(f"[records] {args.records} - {len(records)}건")

    if args.temperature:
        # 기록에는 헤드별 **최대확률** 만 있다. 4-way softmax 의 최대확률 p 를 온도 T 로
        # 다시 조인 값은 정확히는 로짓이 있어야 하지만, 최대 로짓과 나머지를 균등으로 보는
        # 근사(p -> p^(1/T) 정규화)로 순서를 보존하며 분포를 편다. 순서가 보존되므로
        # 게이트 판정(임계 통과 여부)의 상대 순위는 그대로다.
        temps = json.loads(Path(args.temperature).read_text("utf-8"))["per_factor"]
        tv = [temps[f] for f in ("secrecy", "value", "management")]
        print(f"[temperature] {dict(zip(('secrecy','value','management'), tv))}")
        for r in records:
            new = []
            for p, t in zip(r["head_conf"], tv):
                p = min(max(p, 1e-9), 1 - 1e-9)
                a = p ** (1.0 / t)
                b = (1 - p) ** (1.0 / t)
                new.append(a / (a + b))
            r["head_conf"] = new

    taus = [float(x) for x in args.taus.split(",")]
    out = {"records": args.records, "n": len(records),
           "condition": {"min_coverage": args.min_coverage,
                         "max_miss_95_upper": args.max_miss_upper},
           "with_s3_policy": [], "without_s3_policy": []}

    for policy in (True, False):
        key = "with_s3_policy" if policy else "without_s3_policy"
        print(f"\n=== S3 정책 {'적용' if policy else '미적용(현행 배포본과 같은 면제 상태)'}")
        print(f"{'tau':>6s}{'커버리지':>10s}{'자동n':>7s}{'정밀도':>9s}"
              f"{'무음미탐':>9s}{'95%상한':>9s}{'S3차단':>8s}  판정")
        for tau in taus:
            r = evaluate(records, tau, s3_policy=policy, grade_from_svm=grade_from_svm)
            out[key].append(r)
            ok = (r["coverage"] >= args.min_coverage
                  and r["silent_miss_95_upper"] is not None
                  and r["silent_miss_95_upper"] <= args.max_miss_upper)
            verdict = "통과" if ok else (
                "커버리지 미달" if r["coverage"] < args.min_coverage else "미탐 상한 초과")
            print(f"{tau:>6.2f}{r['coverage']:>10.4f}{r['auto_n']:>7d}"
                  f"{(r['precision'] if r['precision'] is not None else 0):>9.4f}"
                  f"{r['silent_miss_n']:>9d}"
                  f"{(r['silent_miss_95_upper'] if r['silent_miss_95_upper'] is not None else 1):>9.4f}"
                  f"{r['blocked_by_s3_policy']:>8d}  {verdict}")

    passing = [r for r in out["with_s3_policy"]
               if r["coverage"] >= args.min_coverage
               and r["silent_miss_95_upper"] is not None
               and r["silent_miss_95_upper"] <= args.max_miss_upper]
    print()
    if passing:
        best = max(passing, key=lambda r: r["coverage"])
        print(f"사전등록 1단계 조건 통과 — tau {best['tau']:.2f} 에서 "
              f"커버리지 {best['coverage']:.1%} · 무음 미탐 상한 {best['silent_miss_95_upper']:.4f}")
    else:
        print("사전등록 1단계 조건 미달 — 어떤 임계에서도 "
              f"커버리지 >= {args.min_coverage:.0%} 이면서 미탐 상한 <= {args.max_miss_upper:.0%} 이 안 된다.")
        print("  임계를 낮추지 않는다(사전등록 §6). 모델을 고칠 일이다.")

    if args.report:
        Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
        print(f"[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
