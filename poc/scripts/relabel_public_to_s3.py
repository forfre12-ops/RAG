"""Step 1 (doc/32 §6): relabel REAL published court rulings in the P1 train set
to S3 — restoring the provenance/non-publicity gate at the data level.

Why
---
The deployed model over-classifies 85% of genuine public court rulings as
confidential (reports/p1_real_public_fpr.*). Root cause: real 판례 documents
entered training labeled TS/S1/S2 by content. A published ruling is, by the
non-publicity requirement (부정경쟁방지법 §2.2), S3 regardless of content. This
script flips those training rows to S3 so the model learns the rule even when
provenance metadata is absent at inference.

Scope
-----
- Only TRAIN is relabeled. val/test (labeled_oss_v1) are 100% synthetic
  (source=synthetic, balanced) — they carry no real rulings, so they keep the
  secret-detection signal for early stopping. No inconsistency.
- Only rows whose text matches a REAL oss_corpus 판례 doc are touched. The
  hand-written koipa/nkt synthetic scenarios do NOT match, so they are left
  untouched (they are removed later, doc/32 step 3).

Deterministic, no model needed.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

LABELS = {"TS", "S1", "S2", "S3"}
PREC_SOURCES = {"판례", "판례(1000+)", "판례(2000+)", "판례(3000+)"}
KEYLEN = 150


def _norm(t: str) -> str:
    return re.sub(r"\s+", "", t or "")


def _ruling_keys(oss_dir: Path) -> set[str]:
    """Normalized text-prefix keys for every REAL published ruling."""
    keys: set[str] = set()
    for f in oss_dir.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if d.get("source") not in PREC_SOURCES:
            continue
        body = d.get("body") or ""
        title = d.get("title") or ""
        if body:
            keys.add(_norm(body)[:KEYLEN])
        if title and body:
            keys.add(_norm(f"{title}\n\n{body}")[:KEYLEN])
    return keys


def _row_matches(text: str, keys: set[str]) -> bool:
    if _norm(text)[:KEYLEN] in keys:
        return True
    if "\n\n" in text:  # gold rows are "제목\n\n본문"
        if _norm(text.split("\n\n", 1)[1])[:KEYLEN] in keys:
            return True
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-train", default="datasets/labeled_p1_retrain_v4_clean/train.jsonl")
    ap.add_argument("--oss-dir", default="datasets/oss_corpus")
    ap.add_argument("--out-train", default="datasets/labeled_p1_retrain_v4_relabel/train.jsonl")
    args = ap.parse_args()

    keys = _ruling_keys(Path(args.oss_dir))
    print(f"[relabel] real-ruling keys: {len(keys)}")

    rows = [
        json.loads(l)
        for l in Path(args.in_train).read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    before = Counter(r.get("label") for r in rows)
    flipped = Counter()  # original label of rows flipped to S3
    for r in rows:
        if r.get("label") in {"TS", "S1", "S2"} and _row_matches(r.get("text", ""), keys):
            flipped[r["label"]] += 1
            r["_orig_label"] = r["label"]
            r["label"] = "S3"
            r["label_source"] = "provenance_gate_s3"  # mark the correction
    after = Counter(r.get("label") for r in rows)

    out = Path(args.out_train)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )

    total_flipped = sum(flipped.values())
    print(f"[relabel] rows: {len(rows)}  flipped→S3: {total_flipped}  by orig label: {dict(flipped)}")
    print(f"[relabel] label dist before: {dict(before)}")
    print(f"[relabel] label dist after : {dict(after)}")
    print(f"[relabel] wrote: {out}")

    # manifest for provenance
    (out.parent / "manifest.json").write_text(
        json.dumps(
            {
                "purpose": "v4_clean train with real published rulings relabeled TS/S1/S2 -> S3 "
                "(provenance/non-publicity gate at data level). doc/32 step 1.",
                "source_train": args.in_train,
                "rows": len(rows),
                "flipped_to_s3": dict(flipped),
                "total_flipped": total_flipped,
                "label_before": dict(before),
                "label_after": dict(after),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
