"""Real-data eval: how does the model grade GENUINE public court rulings?

Why this exists
---------------
The only large pool of real (non-synthetic) documents with an authoritative
label is published court rulings: a published ruling is, by definition, public
(S3). This script takes the oss_corpus 판례 documents that were NOT in the
v4_clean training set (uncontaminated), and measures how the deployed model
grades them.

Two readings of "correct" exist and this test separates them:
  - publication-status grading: a published ruling -> S3. Anything else is a
    false positive (over-classification) that would block a public document.
  - content-sensitivity grading: a ruling that discusses a trade secret may be
    pushed to S1/S2. The model was TRAINED this way (real 판례 entered training
    as TS/S1/S2 by content, not S3).

The predicted-grade distribution on these clean real docs tells you which
philosophy the deployed model actually implements — measured on real data,
no synthesis, no LLM judge, no human labeling.

Deterministic: fixed RNG seed.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from eval_p1_model_gold import predict_direct  # noqa: E402
from lloydk.config import settings as _settings  # noqa: E402

# 배포 모델 단일 진실원 = .env CLASSIFIER_MODEL_DIR. 버전 문자열 하드코딩이 드리프트 원인이라 제거.
_DEPLOYED_MODEL = _settings.classifier_model_dir or "artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9"

LABELS = ("TS", "S1", "S2", "S3")
PREC_SOURCES = {"판례", "판례(1000+)", "판례(2000+)", "판례(3000+)"}


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _train_keys(train_path: Path) -> set[str]:
    keys: set[str] = set()
    for line in train_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        t = _norm(json.loads(line).get("text", ""))
        if t:
            keys.add(t[:300])
    return keys


def _bootstrap_ci(flags: list[int], n_boot: int, rng: random.Random) -> dict:
    """95% CI for the mean of 0/1 flags (e.g. over-classification rate)."""
    n = len(flags)
    if n == 0:
        return {"point": None, "lo": None, "hi": None, "n": 0}
    point = sum(flags) / n
    means = []
    for _ in range(n_boot):
        s = sum(flags[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    return {
        "point": round(point, 4),
        "lo": round(means[int(0.025 * n_boot)], 4),
        "hi": round(means[min(int(0.975 * n_boot), n_boot - 1)], 4),
        "n": n,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=_DEPLOYED_MODEL)
    ap.add_argument("--oss-dir", default="datasets/oss_corpus")
    ap.add_argument("--train", default="datasets/labeled_p1_retrain_v4_clean/train.jsonl")
    ap.add_argument("--report", default="reports/p1_real_public_fpr")
    ap.add_argument("--max-docs", type=int, default=600)
    ap.add_argument("--min-len", type=int, default=500)
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20260603)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    train_keys = _train_keys(Path(args.train))

    files = sorted(Path(args.oss_dir).glob("*.json"))
    rng.shuffle(files)
    rows: list[dict] = []
    skipped_contam = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("source") not in PREC_SOURCES:  # real published rulings only
            continue
        body = d.get("body") or ""
        if len(body) < args.min_len:
            continue
        text = f"{d.get('title','')}\n\n{body[:3000]}"
        nkey = _norm(text)[:300]
        bkey = _norm(body)[:300]
        if nkey in train_keys or bkey in train_keys:
            skipped_contam += 1
            continue
        rows.append({"text": text, "domain": d.get("domain", ""), "_file": f.name})
        if len(rows) >= args.max_docs:
            break

    if not rows:
        print("[ERR] no clean real public docs found", file=sys.stderr)
        return 2

    preds = predict_direct(Path(args.model_dir), rows)
    pred_labels = [p["label"] for p in preds]
    dist = Counter(pred_labels)

    # publication-status view: truth = S3, so anything != S3 is over-classification.
    over_flags = [1 if p != "S3" else 0 for p in pred_labels]
    high_flags = [1 if p in {"TS", "S1"} else 0 for p in pred_labels]  # escalated to *high* secrecy

    payload = {
        "model_dir": args.model_dir,
        "n_clean_real_docs": len(rows),
        "skipped_as_contaminated": skipped_contam,
        "predicted_distribution": dict(dist),
        "over_classification_rate": _bootstrap_ci(over_flags, args.n_boot, rng),
        "escalated_to_high_TS_S1": _bootstrap_ci(high_flags, args.n_boot, rng),
        "seed": args.seed,
        "n_boot": args.n_boot,
        "examples_over": [
            {"file": r["_file"], "pred": p["label"], "conf": round(p.get("confidence", 0), 3),
             "preview": r["text"][:160].replace("\n", " ")}
            for r, p in zip(rows, preds) if p["label"] != "S3"
        ][:15],
    }

    oc = payload["over_classification_rate"]
    hi = payload["escalated_to_high_TS_S1"]
    md = [
        "# Real Public Rulings — Over-Classification (false-positive) Test",
        "",
        f"- model: `{args.model_dir}`",
        f"- material: {len(rows)} genuine published court rulings, NOT in training "
        f"({skipped_contam} dropped as contaminated)",
        "- label is authoritative by definition: published ruling = public = S3",
        f"- bootstrap: {args.n_boot} resamples (seed {args.seed})",
        "",
        "## Result",
        "",
        f"Predicted grade distribution on real public docs: "
        f"{', '.join(f'{k}={dist.get(k,0)}' for k in LABELS)}",
        "",
        f"- **Over-classification rate (predicted != S3):** "
        f"{oc['point']:.3f}  [{oc['lo']:.3f}, {oc['hi']:.3f}]",
        f"- **Escalated to HIGH secrecy (TS or S1):** "
        f"{hi['point']:.3f}  [{hi['lo']:.3f}, {hi['hi']:.3f}]",
        "",
        "## How to read this",
        "- A published court ruling is already public; grading it TS/S1/S2 would, in a DLP "
        "deployment, wrongly restrict a public document.",
        "- A HIGH rate here means the model grades by **content sensitivity**, not **publication "
        "status** — which is exactly how it was trained (real 판례 entered training as TS/S1/S2).",
        "- This is measured on real, uncontaminated documents with an authoritative label: it is "
        "the cleanest real-data signal currently available without deployment data or a guideline.",
    ]

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
