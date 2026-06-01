"""Build the mixed P1 retraining dataset without starting model training.

Inputs are intentionally explicit so a training run can be repeated later:
- labeled OSS train data
- matched rag_corpus_v2 documents
- gold_real high-risk records, optionally upsampled
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

LABELS = {"TS", "S1", "S2", "S3"}


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _rag_rows(corpus_dir: Path) -> tuple[list[dict], int]:
    rows: list[dict] = []
    skipped = 0
    for f in sorted(corpus_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("label_match", False):
            skipped += 1
            continue
        body = (d.get("body") or "").strip()
        label = d.get("target_grade")
        if not body or label not in LABELS:
            skipped += 1
            continue
        rows.append(
            {
                "text": body,
                "label": label,
                "domain": d.get("domain", ""),
                "source": "rag_corpus_v2",
            }
        )
    return rows, skipped


def _gold_rows(path: Path, repeat: int) -> list[dict]:
    rows = []
    for r in _jsonl(path):
        text = (r.get("text") or "").strip()
        label = r.get("label") or r.get("expected_grade")
        if not text or label not in LABELS:
            continue
        rows.append(
            {
                "text": text,
                "label": label,
                "source": r.get("label_source", "gold_real"),
                "doc_id": r.get("doc_id"),
            }
        )
    if repeat <= 1:
        return rows
    return rows * repeat


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oss-train", default="datasets/labeled_oss_v1/train.jsonl")
    ap.add_argument("--rag-corpus", default="datasets/rag_corpus_v2")
    ap.add_argument("--gold-real", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--gold-repeat", type=int, default=3)
    ap.add_argument("--out", default="datasets/labeled_p1_retrain_v3/train.jsonl")
    ap.add_argument("--manifest", default="datasets/labeled_p1_retrain_v3/manifest.json")
    args = ap.parse_args()

    oss = _jsonl(Path(args.oss_train))
    oss_norm = [
        {**r, "source": r.get("source", "labeled_oss_v1")}
        for r in oss
        if r.get("text") and r.get("label") in LABELS
    ]
    rag, rag_skipped = _rag_rows(Path(args.rag_corpus))
    gold = _gold_rows(Path(args.gold_real), repeat=max(1, args.gold_repeat))

    rows = oss_norm + rag + gold
    if not rows:
        print("[p1-dataset] no rows produced", file=sys.stderr)
        return 2

    out = Path(args.out)
    _write_jsonl(out, rows)

    manifest = {
        "output": str(out),
        "total_rows": len(rows),
        "grade_distribution": dict(Counter(r["label"] for r in rows)),
        "source_distribution": dict(Counter(r.get("source", "unknown") for r in rows)),
        "inputs": {
            "oss_train": {"path": args.oss_train, "rows": len(oss_norm)},
            "rag_corpus": {"path": args.rag_corpus, "rows": len(rag), "skipped": rag_skipped},
            "gold_real": {
                "path": args.gold_real,
                "rows_after_repeat": len(gold),
                "repeat": max(1, args.gold_repeat),
            },
        },
        "train_command": (
            "python scripts/p1_train_classifier.py --mode full "
            f"--train-path {out.as_posix()} "
            "--val-path datasets/labeled_oss_v1/val.jsonl "
            "--test-path datasets/labeled_oss_v1/test.jsonl "
            "--epochs 5 --max-seq-len 512 --no-mlflow "
            "--output-dir artifacts/classifier_p1_retrain_v3"
        ),
    }
    manifest_path = Path(args.manifest)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
