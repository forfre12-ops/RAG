"""요소 모델을 배포본과 '같은 게이트'에 태워 무음 미탐과 자동확정률을 비교한다.

왜 필요한가. 모델 단독 지표와 운영 지표는 다른 값이다. 배포본은 모델 단독 고등급 FNR 이
54.7% 인데 서빙 경로에서는 검수 라우팅이 흡수해 무음 미탐이 0 이었다(자동확정도 0 이었다).
요소 모델의 단독 고등급 미탐 20.0% 도 그대로 인용할 수 없다 - 게이트를 태워야 한다.

태우는 게이트는 배포본과 동일하다(classify_service):
    1. 신뢰도 임계  등급확률 < review_confidence_threshold(0.70) -> needs_review
    2. 합의 게이트  예측이 공개등급(S3)이 아니면 룰등급 == 모델등급 이어야 자동확정
                   (공개등급 예측은 conf 단독 신뢰 - 과소분류 위험이 없는 최하등급)

측정 항목:
    자동확정률       두 게이트를 다 통과한 비율
    자동확정 정밀도   자동확정된 것 중 등급이 맞은 비율
    **무음 미탐**    고등급(TS/S1)인데 낮게 가고 **자동확정까지** 된 건수 <- 이게 0 이어야 한다

⚠ 등급 확률은 세 헤드를 독립으로 보고 27조합을 합산해 얻는다(dump_factor_grade_probs).
헤드가 백본을 공유하므로 독립 가정은 근사다. 임계 0.70 은 4-way softmax 로 보정된
배포본 기준으로 잡힌 값이라, 축이 다른 확률에 그대로 적용하는 것은 근사다. 그 한계를
안고 읽어야 한다 - 이 수치는 '같은 게이트를 태웠을 때의 방향성'이지 배포 승인 근거가 아니다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

SEVERITY = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
PUBLIC = "S3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="서빙 게이트 시뮬레이션")
    parser.add_argument("--probs", required=True, help="dump_factor_grade_probs 산출 jsonl")
    parser.add_argument("--eval", required=True, help="본문을 읽을 원본 평가셋(룰등급 산출용)")
    parser.add_argument("--threshold", type=float, default=0.70)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(encoding="utf-8")

    from koipa.modules.m3_labeling.pipeline import LabelingPipeline

    rows = [json.loads(x) for x in Path(args.probs).read_text("utf-8").splitlines() if x.strip()]
    texts = [json.loads(x).get("text") or ""
             for x in Path(args.eval).read_text("utf-8").splitlines() if x.strip()]
    engine = LabelingPipeline()

    # 룰등급은 임계와 무관하게 **전건** 산출한다. 임계 스윕을 하려면 낮은 임계에서 새로
    # 통과하는 문서의 룰등급도 필요한데, 임계별로 다시 돌리면 800건 룰 실행을 반복하게 된다.
    print("[rule] 전건 룰등급 산출 중...")
    rule_grades = [getattr(getattr(engine.label(t), "rule_result", None), "grade", None)
                   for t in texts]

    def evaluate(threshold: float) -> tuple[dict, list]:
        auto = miss = correct = 0
        reasons = {"low-confidence": 0, "rule-disagreement": 0}
        recs = []
        for r in rows:
            gp = r["grade_probs"]
            pred = max(gp, key=lambda g: gp[g])
            conf = gp[pred]
            truth = r["truth_grade"]
            rg = rule_grades[r["idx"]]
            blocked = None
            if conf < threshold:
                blocked = "low-confidence"
            elif pred != PUBLIC and rg != pred:
                # 공개등급 예측은 합의 불요(배포본과 동일) - 과소분류 위험이 없는 최하등급.
                blocked = "rule-disagreement"
            if blocked:
                reasons[blocked] += 1
            else:
                auto += 1
                if pred == truth:
                    correct += 1
                if truth in ("TS", "S1") and SEVERITY[pred] > SEVERITY[truth]:
                    miss += 1
            recs.append({**r, "pred_grade": pred, "confidence": round(conf, 4),
                         "rule_grade": rg, "blocked": blocked})
        return {
            "threshold": round(threshold, 3),
            "auto_confirmed": auto,
            "auto_confirm_rate": round(auto / len(rows), 4),
            "auto_precision": round(correct / auto, 4) if auto else None,
            "silent_miss": miss,
            "block_reasons": reasons,
        }, recs

    grid = sorted({round(x / 100, 2) for x in range(20, 100, 5)} | {args.threshold})
    sweep = [evaluate(t)[0] for t in grid]
    print(f"\n{'임계':>6}{'자동확정':>9}{'자동확정률':>11}{'정밀도':>9}{'무음미탐':>9}")
    print("-" * 46)
    for s in sweep:
        p = f"{s['auto_precision']:.3f}" if s["auto_precision"] is not None else "  -  "
        flag = "  !!" if s["silent_miss"] else ""
        print(f"{s['threshold']:>6.2f}{s['auto_confirmed']:>9}{s['auto_confirm_rate']:>10.1%}"
              f"{p:>9}{s['silent_miss']:>9}{flag}")
    safe = [s for s in sweep if s["silent_miss"] == 0]
    best = min(safe, key=lambda s: s["threshold"]) if safe else None
    print("\n[기준] 무음 미탐 0 을 유지하는 가장 낮은 임계 = 자동화 최대")
    print(f"  {best}" if best else "  어떤 임계에서도 무음 미탐 0 이 안 된다")

    result, records = evaluate(args.threshold)
    auto, miss, reasons = result["auto_confirmed"], result["silent_miss"], result["block_reasons"]
    correct = round((result["auto_precision"] or 0) * auto)
    n = len(rows)

    report = {
        "n": n,
        "threshold": args.threshold,
        "auto_confirmed": auto,
        "auto_confirm_rate": round(auto / n, 4),
        "auto_precision": round(correct / auto, 4) if auto else None,
        "silent_miss": miss,
        "silent_miss_rate": round(miss / auto, 4) if auto else 0.0,
        "block_reasons": reasons,
        "sweep": sweep,
        "recommended": best,
        "criterion": "무음 미탐 0 을 유지하는 최소 임계. 정밀도나 자동확정률로 고르지 않는다 "
                     "- 본 사업 1차 목표가 미탐 최소화이기 때문이다.",
        "caveat": "등급확률은 세 헤드 독립 가정으로 27조합을 합산한 값이다(백본 공유라 근사). "
                  "임계 0.70 은 배포본 4-way softmax 기준이라 축이 다르다. 방향성 비교용이지 "
                  "배포 승인 근거가 아니다.",
    }
    print(f"N={n} · 임계 {args.threshold}")
    print(f"  자동확정   {auto} = {report['auto_confirm_rate']:.1%}")
    print(f"  자동확정 정밀도 {report['auto_precision']}")
    print(f"  무음 미탐  {miss}건")
    print(f"  차단 사유  {reasons}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        (out.parent / (out.stem + ".records.jsonl")).write_text(
            "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in records), encoding="utf-8")
        print(f"[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
