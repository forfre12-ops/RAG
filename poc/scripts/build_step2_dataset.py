"""Step 2 (doc/32 §6): assemble the step-2 training set =
  relabeled train (court rulings -> S3, doc/32 step 1)
  + REAL patent technical content (-> TS/S1, from kipris_collect.py)
and carve a held-out PATENT eval slice — the first genuinely-real high-grade
recall test (patents are real disclosed technical secrets, not synthetic).

Run order:
  1) python scripts/kipris_collect.py --per-keyword 60      # needs KIPRIS_SERVICE_KEY
  2) python scripts/build_step2_dataset.py
  3) python scripts/p1_train_classifier.py --mode full \
       --train-path datasets/labeled_p1_retrain_v4_step2/train.jsonl \
       --val-path datasets/labeled_oss_v1/val.jsonl \
       --test-path datasets/labeled_oss_v1/test.jsonl \
       --fnr-cost-multiplier 2.0 --early-stop-metric fnr_high --epochs 5 \
       --output-dir artifacts/classifier_p1_retrain_v4_step2 --no-mlflow
  4) python scripts/eval_real_public_fpr.py --model-dir <new> --report reports/p1_real_public_fpr_step2
     python scripts/eval_trusted_ci.py   --model-dir <new> --report reports/p1_trusted_ci_step2
     python scripts/eval_patent_recall.py --model-dir <new>   # real high-grade recall

Expected: over-classification stays low (court->S3 held) AND high-grade recall
recovers on REAL patents (unlike the step-1 relabel-only ablation that collapsed).
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from pathlib import Path


def _read(p: Path) -> list[dict]:
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--relabel-train", default="datasets/labeled_p1_retrain_v4_relabel/train.jsonl")
    ap.add_argument("--patent", default="datasets/patent_proxy/raw.jsonl")
    ap.add_argument("--out-train", default="datasets/labeled_p1_retrain_v4_step2/train.jsonl")
    ap.add_argument("--out-patent-holdout", default="datasets/patent_proxy/holdout_eval.jsonl")
    ap.add_argument("--patent-holdout-frac", type=float, default=0.25)
    ap.add_argument("--patent-repeat", type=int, default=2, help="고위험 신호 보강용 반복(불균형 보정)")
    ap.add_argument("--seed", type=int, default=20260603)
    args = ap.parse_args()

    base = Path(args.patent)
    if not base.exists():
        print(f"[step2] {base} 없음 — 먼저 'python scripts/kipris_collect.py' 실행 "
              f"(KIPRIS_SERVICE_KEY 필요).")
        return 2

    rng = random.Random(args.seed)
    patents = [r for r in _read(base) if r.get("label") in {"TS", "S1"} and (r.get("text") or "").strip()]
    rng.shuffle(patents)

    # 특허를 등급 stratified로 train/holdout 분리 (홀드아웃은 학습 제외 = 진짜 recall 측정)
    by_g: dict[str, list[dict]] = {}
    for r in patents:
        by_g.setdefault(r["label"], []).append(r)
    holdout, train_pat = [], []
    for g, rows in by_g.items():
        k = max(1, int(len(rows) * args.patent_holdout_frac))
        holdout += rows[:k]
        train_pat += rows[k:]

    relabel = _read(Path(args.relabel_train))
    merged = relabel + train_pat * args.patent_repeat

    out = Path(args.out_train)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", encoding="utf-8")

    ho = Path(args.out_patent_holdout)
    ho.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in holdout) + "\n", encoding="utf-8")

    print(f"[step2] patents: total={len(patents)} train={len(train_pat)} (x{args.patent_repeat}) holdout={len(holdout)}")
    print(f"[step2] merged train: {len(merged)}  labels={dict(Counter(r['label'] for r in merged))}")
    print(f"[step2] wrote train={out}  patent_holdout={ho}")
    print(f"[step2] holdout labels={dict(Counter(r['label'] for r in holdout))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
