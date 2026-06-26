"""Evaluate a P1 model on the clean holdout, broken down by gold tier.

Loads the model once and reports F1/FNR for: ALL, legally_grounded, llm_judge.
Use to compare the contaminated model (trained on these docs) against a
decontaminated model (holdout removed from train) on the SAME holdout — the
gap is the train-on-test inflation.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from eval_p1_model_gold import compute_metrics, predict_direct  # noqa: E402

LEGAL = {"public_definitive", "koipa_case_based", "nkt_designated"}
LLMJ = {"llm_judge_primary", "llm_judge_consensus", "codex_review"}


def _tier(ls: str) -> str:
    if ls in LEGAL:
        return "legally_grounded"
    if ls in LLMJ:
        return "llm_judge"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--holdout", default="datasets/gold_real/holdout_eval.clean.jsonl")  # clean=train 누출 제거(dirty 109건 중 67건=61%가 train_subset 중복 → 암기 부풀림)
    ap.add_argument("--report", default="reports/p1_holdout_eval.json")
    args = ap.parse_args()

    rows = [
        json.loads(l)
        for l in Path(args.holdout).read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    rows = [r for r in rows if r.get("label") in {"TS", "S1", "S2", "S3"}]
    preds = predict_direct(Path(args.model_dir), rows)
    for r, p in zip(rows, preds):
        r["_pred"] = p["label"]

    def metrics_for(subset: list[dict]) -> dict:
        if not subset:
            return {"n": 0}
        m = compute_metrics([r["label"] for r in subset], [r["_pred"] for r in subset])
        return {
            "n": m["n"],
            "f1_macro": m["f1_macro"],
            "fnr_underclass": m["fnr_underclass"],
            "high_risk_to_s3": m["high_risk_to_s3"],
            "per_class_recall": {g: m["per_class"][g]["recall"] for g in ("TS", "S1", "S2", "S3")},
        }

    out = {
        "model_dir": args.model_dir,
        "holdout": args.holdout,
        "ALL": metrics_for(rows),
        "legally_grounded": metrics_for([r for r in rows if _tier(r.get("label_source", "")) == "legally_grounded"]),
        "llm_judge": metrics_for([r for r in rows if _tier(r.get("label_source", "")) == "llm_judge"]),
    }
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
