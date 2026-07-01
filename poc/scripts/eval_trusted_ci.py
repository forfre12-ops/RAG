"""Honest holdout eval: trusted (legally-grounded) tier + bootstrap CIs.

Why this exists
---------------
The deployed headline (FNR 0.056, F1 0.634) is measured on the FULL clean
holdout (109), but 61/109 of those labels were produced by an LLM judge
(reviewer_id=llm_judge_local_openai). On that majority we are not measuring
accuracy-vs-truth, we are measuring agreement-vs-another-model. Only the
legally_grounded tier (nkt_designated=TS / public_definitive=S3 /
koipa_case_based=S1,S2) carries real-world ground truth.

This script reports the SAME metrics as eval_p1_model_gold, split by tier,
with percentile bootstrap confidence intervals so the small-sample tiers
(legally_grounded high-risk denominator = 23) are not reported as if they
were point-precise.

Deterministic: fixed RNG seed, resampling is reproducible.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

try:  # Windows consoles default to cp949; report contains em-dash/CJK.
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from eval_p1_model_gold import compute_metrics, predict_direct  # noqa: E402
from lloydk.config import settings as _settings  # noqa: E402

# 배포 모델 단일 진실원 = .env CLASSIFIER_MODEL_DIR (버전 하드코딩 드리프트 방지).
_DEPLOYED_MODEL = _settings.classifier_model_dir or "artifacts/classifier_p1_retrain_v4_step3/v-f9b5cedb"

LEGAL = {"public_definitive", "koipa_case_based", "nkt_designated"}
LLMJ = {"llm_judge_primary", "llm_judge_consensus", "codex_review"}
LABELS = ("TS", "S1", "S2", "S3")


def tier(ls: str) -> str:
    if ls in LEGAL:
        return "legally_grounded"
    if ls in LLMJ:
        return "llm_judge"
    return "other"


def _metric_value(y_true: list[str], y_pred: list[str], key: str) -> float:
    m = compute_metrics(y_true, y_pred)
    if key == "f1_macro":
        return m["f1_macro"]
    if key == "fnr_underclass":
        return m["fnr_underclass"]
    if key.startswith("recall_"):
        g = key.split("_", 1)[1]
        return m["per_class"][g]["recall"]
    raise KeyError(key)


def bootstrap_ci(
    y_true: list[str], y_pred: list[str], key: str, n_boot: int, rng: random.Random
) -> dict:
    """Percentile bootstrap 95% CI for a metric over paired (truth, pred)."""
    n = len(y_true)
    if n == 0:
        return {"point": None, "lo": None, "hi": None, "n": 0}
    point = round(_metric_value(y_true, y_pred, key), 4)
    samples: list[float] = []
    idx = list(range(n))
    for _ in range(n_boot):
        pick = [rng.choice(idx) for _ in range(n)]
        bt = [y_true[i] for i in pick]
        bp = [y_pred[i] for i in pick]
        try:
            samples.append(_metric_value(bt, bp, key))
        except KeyError:
            samples.append(0.0)
    samples.sort()
    lo = samples[int(0.025 * n_boot)]
    hi = samples[min(int(0.975 * n_boot), n_boot - 1)]
    return {"point": point, "lo": round(lo, 4), "hi": round(hi, 4), "n": n}


def tier_block(rows: list[dict], n_boot: int, rng: random.Random) -> dict:
    yt = [r["label"] for r in rows]
    yp = [r["_pred"] for r in rows]
    m = compute_metrics(yt, yp) if rows else {"n": 0}
    # high-risk denominator (TS+S1) drives the FNR estimate; surface it.
    high_n = sum(1 for t in yt if t in {"TS", "S1"})
    block = {
        "n": len(rows),
        "high_risk_n": high_n,
        "support": {g: sum(1 for t in yt if t == g) for g in LABELS},
        "f1_macro": bootstrap_ci(yt, yp, "f1_macro", n_boot, rng),
        "fnr_underclass": bootstrap_ci(yt, yp, "fnr_underclass", n_boot, rng),
        "recall_TS": bootstrap_ci(yt, yp, "recall_TS", n_boot, rng),
        "recall_S1": bootstrap_ci(yt, yp, "recall_S1", n_boot, rng),
        "high_risk_to_s3": m.get("high_risk_to_s3", 0),
        "confusion_matrix": m.get("confusion_matrix", {}),
    }
    return block


def _fmt(ci: dict) -> str:
    if ci.get("point") is None:
        return "n/a"
    return f"{ci['point']:.3f} [{ci['lo']:.3f}, {ci['hi']:.3f}]"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=_DEPLOYED_MODEL)
    ap.add_argument("--holdout", default="datasets/gold_real/holdout_eval.clean.jsonl")  # clean=train 누출 제거(dirty 109건 중 67건=61%가 train_subset 중복 → 암기 부풀림)
    ap.add_argument("--report", default="reports/p1_trusted_ci")
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20260603)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rows = [
        json.loads(l)
        for l in Path(args.holdout).read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    rows = [r for r in rows if r.get("label") in LABELS]
    preds = predict_direct(Path(args.model_dir), rows)
    for r, p in zip(rows, preds):
        r["_pred"] = p["label"]

    tiers = {
        "ALL": rows,
        "legally_grounded": [r for r in rows if tier(r.get("label_source", "")) == "legally_grounded"],
        "llm_judge": [r for r in rows if tier(r.get("label_source", "")) == "llm_judge"],
    }
    payload = {
        "model_dir": args.model_dir,
        "holdout": args.holdout,
        "n_boot": args.n_boot,
        "seed": args.seed,
        "tiers": {name: tier_block(sub, args.n_boot, rng) for name, sub in tiers.items()},
    }

    md = [
        "# P1 Holdout — Trusted Tier + Bootstrap 95% CI",
        "",
        f"- model: `{args.model_dir}`",
        f"- holdout: `{args.holdout}`  | bootstrap: {args.n_boot} resamples (seed {args.seed})",
        "",
        "`legally_grounded` = real-world labels (nkt=TS / public=S3 / koipa=S1,S2).",
        "`llm_judge` = labels produced by an LLM judge (agreement-vs-model, not truth).",
        "",
        "| Tier | n | high-risk n | F1 macro [95% CI] | FNR underclass [95% CI] | Recall S1 [95% CI] | Recall TS [95% CI] |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for name in ("ALL", "legally_grounded", "llm_judge"):
        b = payload["tiers"][name]
        md.append(
            f"| {name} | {b['n']} | {b['high_risk_n']} | {_fmt(b['f1_macro'])} | "
            f"{_fmt(b['fnr_underclass'])} | {_fmt(b['recall_S1'])} | {_fmt(b['recall_TS'])} |"
        )
    md += [
        "",
        "## How to read this",
        "- The deployed headline FNR/F1 is the **ALL** row — but it is dominated by `llm_judge` labels.",
        "- The honest accuracy-vs-truth number is the **legally_grounded** row.",
        "- Wide CIs on `legally_grounded` reflect its small high-risk denominator; a point estimate there is not trustworthy on its own.",
    ]

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
