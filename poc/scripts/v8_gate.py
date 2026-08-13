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
import math
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


def boundary_margin(dist: list[float]) -> float:
    """인접 등급 사이 접전 정도 — 1위와 2위 확률의 차.

    최대확률만 보면 "확신 있게 lv1" 로 보이는 문서가, 실제로는 lv1 0.52 · lv2 0.47 처럼
    경계에 얹혀 있을 수 있다. 정본 규칙에서 lv1/lv2 한 단계가 등급을 뒤집으므로
    ((2,2,1)=TS vs (1,2,1)=S2) 그 접전이 곧 미탐 위험이다.
    4차 미탐 5건이 전부 value lv2->lv1 이었다.
    """
    a = sorted(dist, reverse=True)
    return a[0] - a[1] if len(a) > 1 else 1.0


def _slice_stats(auto: list[dict]) -> tuple[int, int, float]:
    miss = [r for r in auto if ORDER[r["pred"]] > ORDER[r["truth"]]]
    return len(auto), len(miss), wilson_upper(len(miss), len(auto)) if auto else 1.0


def evaluate(records: list[dict], tau: float, *, s3_policy: bool,
             grade_from_svm, margin: float = 0.0) -> dict:
    """한 임계에서의 게이트 동작.

    무음 미탐 = **자동확정된 것 중** 정답보다 낮은 등급을 준 건. 검수로 간 것은 사람이
    보므로 무음이 아니다. 이 구분이 없으면 모델 단독 FNR 과 섞여 숫자가 뒤집힌다.
    """
    auto = miss = 0
    auto_correct = 0
    blocked_by_s3 = 0
    blocked_by_margin = 0
    miss_detail: dict[str, int] = {}
    accepted: list[dict] = []
    for r in records:
        conf = min(r["head_conf"])
        if conf < tau:
            continue
        if margin > 0 and r.get("head_dist"):
            # 세 요소 중 하나라도 경계에 얹혀 있으면 검수로 보낸다.
            if min(boundary_margin(d) for d in r["head_dist"]) < margin:
                blocked_by_margin += 1
                continue
        if s3_policy and r["pred"] == "S3":
            # 보수적 완성 - unknown 을 최악으로 채워도 S3 여야 자동확정 후보다
            pw = grade_from_svm(*[cls_to_worst(c) for c in r["pred_codes"]])
            if pw != "S3":
                blocked_by_s3 += 1
                continue
        auto += 1
        accepted.append(r)
        if r["pred"] == r["truth"]:
            auto_correct += 1
        elif ORDER[r["pred"]] > ORDER[r["truth"]]:
            miss += 1
            miss_detail[f"{r['truth']}->{r['pred']}"] = miss_detail.get(f"{r['truth']}->{r['pred']}", 0) + 1
    n = len(records)
    # 사전등록 §2 는 "형태별 최악 성능 — 홀드아웃 형태 **각각**에서 충족" 을 요구한다.
    # 합산만 보면 두 형태가 각각 미달인데 합쳐서 통과하는 일이 생긴다(6차에서 실제로
    # contract_terms 0.0356 · customer_list 0.0340 인데 합산 0.0177 로 통과했다).
    per_form: dict[str, dict] = {}
    for form in sorted({r.get("form_id", "?") for r in accepted}):
        a = [r for r in accepted if r.get("form_id") == form]
        an, mn, up = _slice_stats(a)
        per_form[form] = {"n": an, "silent_miss_n": mn,
                          "silent_miss_95_upper": round(up, 4),
                          "coverage_in_form": round(
                              an / max(1, sum(1 for r in records if r.get("form_id") == form)), 4)}
    worst = max((v["silent_miss_95_upper"] for v in per_form.values()), default=1.0)
    return {
        "tau": tau,
        "per_form": per_form,
        "worst_form_miss_95_upper": round(worst, 4),
        "min_form_n": min((v["n"] for v in per_form.values()), default=0),
        "coverage": round(auto / n, 4),
        "auto_n": auto,
        "precision": round(auto_correct / auto, 4) if auto else None,
        "silent_miss_n": miss,
        "silent_miss_rate": round(miss / auto, 4) if auto else None,
        "silent_miss_95_upper": round(wilson_upper(miss, auto), 4) if auto else None,
        "blocked_by_s3_policy": blocked_by_s3,
        "blocked_by_margin": blocked_by_margin,
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
    # 0/n 에서 Wilson 상한이 0.02 이하가 되려면 n>=189 다(0/150 -> 0.02497 로 초과).
    # 사전등록 표의 "2.0% -> 150건" 은 산술 오류였다.
    ap.add_argument("--min-form-n", type=int, default=189,
                    help="형태당 최소 자동확정 건수 — 이 미만이면 상한을 주장할 수 없다")
    ap.add_argument("--margin", type=float, default=0.0,
                    help="요소 확률 1-2위 차가 이 미만이면 경계로 보고 검수행")
    ap.add_argument("--temperature", default=None,
                    help="요소별 온도 json — 신뢰도를 보정해 게이트에 넣는다")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    records = [json.loads(l) for l in Path(args.records).read_text("utf-8").splitlines() if l.strip()]
    print(f"[records] {args.records} - {len(records)}건")

    if args.temperature:
        # 기록에 head_dist(4-way 전체 분포)가 있으므로 **정식으로** 온도를 적용한다.
        # 이전에는 최대확률만 있다고 보고 p -> p^(1/T) 근사를 썼는데, 기록을 확인하니
        # 전체 분포가 들어 있었다. 근사할 이유가 없었고 값도 달랐다.
        temps = json.loads(Path(args.temperature).read_text("utf-8"))["per_factor"]
        tv = [temps[f] for f in ("secrecy", "value", "management")]
        print(f"[temperature] {dict(zip(('secrecy','value','management'), tv))}")
        missing = 0
        for r in records:
            if not r.get("head_dist"):
                missing += 1
                continue
            new = []
            for dist, t in zip(r["head_dist"], tv):
                lg = [math.log(max(x, 1e-12)) / t for x in dist]
                m = max(lg)
                e = [math.exp(x - m) for x in lg]
                z = sum(e)
                new.append(max(x / z for x in e))
            r["head_conf"] = new
        if missing:
            raise SystemExit(f"head_dist 없는 기록 {missing}건 — 정식 보정 불가. 판정기를 다시 돌릴 것")

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
            r = evaluate(records, tau, s3_policy=policy, grade_from_svm=grade_from_svm,
                         margin=args.margin)
            out[key].append(r)
            # 판정은 **형태별 최악** 으로 한다. 합산 통과는 조건이 아니다.
            ok = (r["coverage"] >= args.min_coverage
                  and r["min_form_n"] >= args.min_form_n
                  and r["worst_form_miss_95_upper"] <= args.max_miss_upper)
            verdict = ("통과" if ok else
                       "커버리지 미달" if r["coverage"] < args.min_coverage else
                       f"형태당 표본 부족({r['min_form_n']}<{args.min_form_n})"
                       if r["min_form_n"] < args.min_form_n else "형태별 미탐 상한 초과")
            print(f"{tau:>6.2f}{r['coverage']:>10.4f}{r['auto_n']:>7d}"
                  f"{(r['precision'] if r['precision'] is not None else 0):>9.4f}"
                  f"{r['silent_miss_n']:>9d}"
                  f"{(r['silent_miss_95_upper'] if r['silent_miss_95_upper'] is not None else 1):>9.4f}"
                  f"{r['blocked_by_s3_policy']:>8d}  {verdict}")
            for form, v in r["per_form"].items():
                print(f"        {form:18s} n={v['n']:4d} 미탐 {v['silent_miss_n']:2d} "
                      f"상한 {v['silent_miss_95_upper']:.4f}")

    passing = [r for r in out["with_s3_policy"]
               if r["coverage"] >= args.min_coverage
               and r["min_form_n"] >= args.min_form_n
               and r["worst_form_miss_95_upper"] <= args.max_miss_upper]
    print()
    if passing:
        best = max(passing, key=lambda r: r["coverage"])
        print(f"사전등록 1단계 조건 통과 — tau {best['tau']:.3f} 에서 "
              f"커버리지 {best['coverage']:.1%} · **형태별 최악** 상한 "
              f"{best['worst_form_miss_95_upper']:.4f} · 형태당 최소 n={best['min_form_n']}")
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
