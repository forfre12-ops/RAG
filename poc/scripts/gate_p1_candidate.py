"""[게이트] P1 분류 후보 모델을 배포본과 3축으로 비교해 PASS/FAIL 판정 — 단일 진입점.

배경: 후보 모델(예: v5_clean)을 배포 후보로 올릴지 판단은 3축을 봐야 한다. 그동안 이 평가는 ad-hoc
CPU 커맨드로 흩어져 있었다(overnight_v5_retrain.sh 내부). 이 스크립트가 그걸 재현가능한 커밋된
1커맨드로 통합한다. 사람 서명 eval 은 요구하지 않는다(지재원엔 실문서·검수자 0 — 자동 게이트만;
[[no-human-signoff-gate-for-deploy-model]]). 배포 스왑은 사람 결정으로 남는다.

3축:
  axis1  hardened holdout(누출0 검증셋): f1_macro·S1 recall·high_risk_to_s3(고→S3 위험미탐)
  axis2  공개문서 FPR(실 공개문서): over_classification_rate  (eval_real_public_fpr.py 재사용)
  axis3  적대 공격셋 golden_100: high→S3 위험미탐·under_graded

PASS 기준(배포본 대비 — 안전 무회귀가 최우선):
  axis1: high_risk_to_s3 ≤ base  AND  f1_macro ≥ base − 0.02(회귀 없음)
  axis2: over_classification_rate ≤ base
  axis3: high→S3 ≤ base  (최악 위험미탐 무회귀)
  overall PASS = 세 축 모두 PASS.  ⚠️ 게이트 통과는 '배포 자격'이지 '배포 실행'이 아니다.

사용:
  python scripts/gate_p1_candidate.py --candidate artifacts/classifier_p1_v5_clean/v-fe4b386b
  python scripts/gate_p1_candidate.py --candidate <dir> --baseline <dir> --skip-fpr   # 빠른 축1·3만
  CUDA_VISIBLE_DEVICES="" python scripts/gate_p1_candidate.py --candidate <dir>        # GPU 회피(CPU)
"""
from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
for p in (str(_HERE), str(_HERE.parent / "src")):
    if p not in sys.path:
        sys.path.insert(0, p)

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from eval_p1_model_gold import predict_direct  # noqa: E402  재사용 추론

LABELS = ["TS", "S1", "S2", "S3"]
_HIGH = frozenset({"TS", "S1", "S2"})
_SEV = {"TS": 3, "S1": 2, "S2": 1, "S3": 0}
# 배포본 = v5_clean/v-fe4b386b (2026-07-29 3축 PASS 승격). 직전 배포본·롤백 타깃 = v4_clean/v-dd3abab9.
# 향후 후보(v6+)는 기본으로 이 배포본(v5)에 대해 무회귀 판정. v5 승격 재현은 --baseline 로 v4 명시.
DEPLOYED = "artifacts/classifier_p1_v5_clean/v-fe4b386b"


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def _pred_grade(p: dict) -> str:
    return p.get("grade") or p.get("label") or p.get("pred") or p.get("predicted")


def _f1_macro(y_true: list[str], y_pred: list[str]) -> float:
    from sklearn.metrics import f1_score
    return round(float(f1_score(y_true, y_pred, labels=LABELS, average="macro", zero_division=0)), 4)


def _label_of(r: dict) -> str:
    return r.get("label") or r.get("expected_grade") or r.get("gold")


def eval_labelset(model_dir: str, path: Path) -> dict:
    """라벨 있는 평가셋(hardened holdout / golden_100)에 대해 후보를 추론·집계."""
    rows = _jsonl(path)
    for r in rows:
        r["text"] = r.get("text") or r.get("desc") or ""
    y_true = [_label_of(r) for r in rows]
    preds = predict_direct(Path(model_dir), rows)
    y_pred = [_pred_grade(p) for p in preds]
    pairs = [(t, p) for t, p in zip(y_true, y_pred) if t in LABELS]
    hi_to_s3 = sum(1 for t, p in pairs if t in _HIGH and p == "S3")
    under = sum(1 for t, p in pairs if _SEV.get(p, 0) < _SEV.get(t, 0))
    # per-class recall (S1 관심)
    rec = {}
    for g in LABELS:
        gt = [p for t, p in pairs if t == g]
        rec[g] = round(sum(1 for p in gt if p == g) / len(gt), 4) if gt else None
    return {
        "n": len(pairs),
        "f1_macro": _f1_macro([t for t, _ in pairs], [p for _, p in pairs]),
        "high_risk_to_s3": hi_to_s3,
        "under_graded": under,
        "per_class_recall": rec,
    }


def eval_fpr(model_dir: str, tag: str, max_docs: int) -> dict:
    """공개문서 FPR — 검증된 eval_real_public_fpr.py 를 서브프로세스로 재사용."""
    rep = f"reports/v5_gate/gate_fpr_{tag}"
    cmd = [sys.executable, "scripts/eval_real_public_fpr.py", "--model-dir", model_dir,
           "--report", rep, "--max-docs", str(max_docs)]
    subprocess.run(cmd, check=False, capture_output=True)
    j = Path(rep + ".json")
    if not j.exists():
        return {"over_classification_rate": None, "error": "no report"}
    d = json.loads(j.read_text(encoding="utf-8"))
    ocr = d.get("over_classification_rate")
    ocr = ocr.get("point") if isinstance(ocr, dict) else ocr
    esc = d.get("escalated_to_high_TS_S1")
    esc = esc.get("point") if isinstance(esc, dict) else esc
    return {"over_classification_rate": ocr, "escalated_to_high": esc, "n": d.get("n_clean_real_docs")}


def main() -> int:
    ap = argparse.ArgumentParser(description="P1 후보 모델 3축 게이트(vs 배포본).")
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--baseline", default=DEPLOYED)
    ap.add_argument("--holdout", default="datasets/gold_real/holdout_eval.hardened.jsonl")
    ap.add_argument("--adversarial", default="datasets/adversarial/golden_100.jsonl")
    ap.add_argument("--skip-fpr", action="store_true", help="axis2(FPR) 생략 — 축1·3만 빠르게")
    ap.add_argument("--fpr-max-docs", type=int, default=400)
    ap.add_argument("--report", default="reports/v5_gate/gate_verdict.json")
    ap.add_argument("--f1-regression-tol", type=float, default=0.02)
    args = ap.parse_args()

    Path("reports/v5_gate").mkdir(parents=True, exist_ok=True)
    hp, ap_ = Path(args.holdout), Path(args.adversarial)

    a1_c = eval_labelset(args.candidate, hp); a1_b = eval_labelset(args.baseline, hp)
    a3_c = eval_labelset(args.candidate, ap_); a3_b = eval_labelset(args.baseline, ap_)
    if args.skip_fpr:
        a2_c = a2_b = {"over_classification_rate": None, "skipped": True}
    else:
        a2_c = eval_fpr(args.candidate, "cand", args.fpr_max_docs)
        a2_b = eval_fpr(args.baseline, "base", args.fpr_max_docs)

    def le(x, y):  # x ≤ y (None 은 무시=통과처리 안 함)
        return x is not None and y is not None and x <= y

    axis1_pass = le(a1_c["high_risk_to_s3"], a1_b["high_risk_to_s3"]) and \
        (a1_c["f1_macro"] >= a1_b["f1_macro"] - args.f1_regression_tol)
    axis2_pass = True if args.skip_fpr else le(a2_c["over_classification_rate"], a2_b["over_classification_rate"])
    axis3_pass = le(a3_c["high_risk_to_s3"], a3_b["high_risk_to_s3"])
    overall = bool(axis1_pass and axis2_pass and axis3_pass)

    verdict = {
        "candidate": args.candidate, "baseline": args.baseline, "format": "candidate vs baseline",
        "axis1_hardened_holdout": {"candidate": a1_c, "baseline": a1_b, "PASS": bool(axis1_pass)},
        "axis2_public_fpr": {"candidate": a2_c, "baseline": a2_b, "PASS": bool(axis2_pass), "skipped": args.skip_fpr},
        "axis3_adversarial": {"candidate": a3_c, "baseline": a3_b, "PASS": bool(axis3_pass)},
        "OVERALL_PASS": overall,
        "note": "게이트 통과=배포 자격(자격이지 실행 아님). 배포 스왑은 사람이 CLASSIFIER_MODEL_DIR 교체+번들 재빌드로 결정. 안전(high_risk_to_s3) 무회귀가 최우선.",
    }
    Path(args.report).write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=== P1 후보 게이트 (candidate vs baseline) ===")
    print(f"axis1 hardened holdout : f1 {a1_c['f1_macro']} vs {a1_b['f1_macro']} | high→S3 {a1_c['high_risk_to_s3']} vs {a1_b['high_risk_to_s3']} | S1 {a1_c['per_class_recall'].get('S1')} vs {a1_b['per_class_recall'].get('S1')}  [{'PASS' if axis1_pass else 'FAIL'}]")
    if args.skip_fpr:
        print("axis2 public FPR       : (skipped)")
    else:
        print(f"axis2 public FPR       : over-class {a2_c['over_classification_rate']} vs {a2_b['over_classification_rate']}  [{'PASS' if axis2_pass else 'FAIL'}]")
    print(f"axis3 adversarial      : high→S3 {a3_c['high_risk_to_s3']} vs {a3_b['high_risk_to_s3']} | under-grade {a3_c['under_graded']} vs {a3_b['under_graded']}  [{'PASS' if axis3_pass else 'FAIL'}]")
    print(f"OVERALL: {'PASS' if overall else 'FAIL'}   → {args.report}")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())
