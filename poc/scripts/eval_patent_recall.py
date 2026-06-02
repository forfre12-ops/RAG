"""Step 2 eval: REAL high-grade recall on held-out patents.

Patents are real disclosed technical secrets. This measures how often the model
recognizes genuine high-grade technical content as high (TS/S1) — the first
real "do we catch real secrets?" signal (the content axis, judged by content,
provenance held out). Bootstrap 95% CI.

NB: still a *capability* proxy with selection bias (patented secrets != a
client's internal secrets) — doc/32 §5. Not a deployment-recall measurement.
"""

from __future__ import annotations

import argparse
import json
import random
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

ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def _ci(flags: list[int], n_boot: int, rng: random.Random) -> dict:
    n = len(flags)
    if not n:
        return {"point": None, "lo": None, "hi": None, "n": 0}
    pt = sum(flags) / n
    ms = sorted(sum(flags[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot))
    return {"point": round(pt, 4), "lo": round(ms[int(0.025 * n_boot)], 4),
            "hi": round(ms[min(int(0.975 * n_boot), n_boot - 1)], 4), "n": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--holdout", default="datasets/patent_proxy/holdout_eval.jsonl")
    ap.add_argument("--report", default="reports/p1_patent_recall")
    ap.add_argument("--n-boot", type=int, default=5000)
    ap.add_argument("--seed", type=int, default=20260603)
    args = ap.parse_args()

    p = Path(args.holdout)
    if not p.exists():
        print(f"[patent-recall] {p} 없음 — build_step2_dataset.py 먼저 실행.")
        return 2
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = [r for r in rows if r.get("label") in {"TS", "S1"}]
    preds = predict_direct(Path(args.model_dir), rows)
    pl = [pr["label"] for pr in preds]

    rng = random.Random(args.seed)
    # 진짜 고위험 특허를 (a) 정확 등급, (b) 고위험(TS/S1)으로, (c) 비-S3로 잡는 비율
    exact = [1 if pl[i] == rows[i]["label"] else 0 for i in range(len(rows))]
    as_high = [1 if pl[i] in {"TS", "S1"} else 0 for i in range(len(rows))]
    not_s3 = [1 if pl[i] != "S3" else 0 for i in range(len(rows))]

    payload = {
        "model_dir": args.model_dir, "n": len(rows),
        "support": dict(Counter(r["label"] for r in rows)),
        "pred_dist": dict(Counter(pl)),
        "exact_grade_recall": _ci(exact, args.n_boot, rng),
        "recall_as_high_TS_or_S1": _ci(as_high, args.n_boot, rng),
        "not_dropped_to_S3": _ci(not_s3, args.n_boot, rng),
    }
    h = payload["recall_as_high_TS_or_S1"]
    md = [
        "# Real Patent High-Grade Recall (content axis)",
        "",
        f"- model: `{args.model_dir}`  | held-out real patents: {len(rows)}  | bootstrap {args.n_boot}",
        f"- support: {payload['support']}  pred: {payload['pred_dist']}",
        "",
        f"- **Recall as HIGH (TS/S1):** {h['point']:.3f} [{h['lo']:.3f}, {h['hi']:.3f}]",
        f"- not dropped to S3: {payload['not_dropped_to_S3']['point']:.3f}",
        f"- exact-grade recall: {payload['exact_grade_recall']['point']:.3f}",
        "",
        "Caveat: capability proxy with selection bias (patented != a client's secrets); "
        "not deployment recall. doc/32 §5.",
    ]
    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(md), encoding="utf-8")
    print("\n".join(md))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
