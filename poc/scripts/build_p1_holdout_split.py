"""Carve a clean, eval-only holdout out of gold_real and rebuild a
decontaminated P1 training set.

Why this exists
---------------
labeled_p1_retrain_v3/train.jsonl includes *all* of gold_real (repeated x3),
yet gold_real is also the file reported as the operational F1 evidence
(llm_judge_gold_f1, public_case_nkt_f1). That is train-on-test: the reported
numbers are partly memorization, not generalization.

This script produces:
  - datasets/gold_real/holdout_eval.jsonl  -> stratified slice, EVAL ONLY,
        guaranteed absent from the rebuilt train set (by normalized text).
  - datasets/labeled_p1_retrain_v4_clean/train.jsonl -> oss + rag +
        (gold_real MINUS holdout) x3.  Same recipe as v3 minus the leak.

Split is deterministic (sorted order within each (label_source, label)
stratum, keyed on normalized text so identical texts never straddle).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from lloydk.golden_tiers import TIER_LOCKED, tier_of  # noqa: E402

LABELS = {"TS", "S1", "S2", "S3"}


def _norm(text: str) -> str:
    """Whitespace-insensitive key — identical content collapses to one key."""
    return re.sub(r"\s+", "", text or "")


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def _rag_rows(corpus_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(corpus_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if not d.get("label_match", False):
            continue
        body = (d.get("body") or "").strip()
        label = d.get("target_grade")
        if not body or label not in LABELS:
            continue
        rows.append({"text": body, "label": label, "domain": d.get("domain", ""), "source": "rag_corpus_v2"})
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold-real", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--oss-train", default="datasets/labeled_oss_v1/train.jsonl")
    ap.add_argument("--rag-corpus", default="datasets/rag_corpus_v2")
    ap.add_argument("--holdout-frac", type=float, default=0.30)
    ap.add_argument("--gold-repeat", type=int, default=3)
    ap.add_argument("--holdout-out", default="datasets/gold_real/holdout_eval.jsonl")
    ap.add_argument("--train-out", default="datasets/labeled_p1_retrain_v4_clean/train.jsonl")
    ap.add_argument("--manifest", default="datasets/labeled_p1_retrain_v4_clean/manifest.json")
    args = ap.parse_args()

    gold = [r for r in _jsonl(Path(args.gold_real)) if (r.get("label") in LABELS and (r.get("text") or "").strip())]

    # 1) group rows by normalized text (so identical content never straddles)
    by_key: dict[str, list[dict]] = defaultdict(list)
    for r in gold:
        by_key[_norm(r["text"])].append(r)

    # 2) stratify unique text-keys by (label_source, label) of their first row
    strata: dict[tuple, list[str]] = defaultdict(list)
    for key, rows in by_key.items():
        head = rows[0]
        strata[(head.get("label_source", "?"), head["label"])].append(key)

    holdout_keys: set[str] = set()
    for stratum, keys in strata.items():
        keys_sorted = sorted(keys)  # deterministic
        n_hold = int(round(len(keys_sorted) * args.holdout_frac))
        holdout_keys.update(keys_sorted[:n_hold])

    # 3) decontaminate: a holdout text that ALSO lives in oss_train or rag stays
    #    in train via those sources -> it cannot be a clean eval doc. Drop it.
    oss = [r for r in _jsonl(Path(args.oss_train)) if r.get("text") and r.get("label") in LABELS]
    rag = _rag_rows(Path(args.rag_corpus))
    external_keys = {_norm(r["text"]) for r in oss} | {_norm(r["text"]) for r in rag}
    leaked = holdout_keys & external_keys
    holdout_keys -= leaked

    # 4) materialize holdout (eval-only) and train pool (gold minus holdout)
    holdout_rows = [r for r in gold if _norm(r["text"]) in holdout_keys]
    train_gold = [
        {"text": r["text"], "label": r["label"], "source": r.get("label_source", "gold_real"), "doc_id": r.get("doc_id")}
        for r in gold
        if _norm(r["text"]) not in holdout_keys and tier_of(r) != TIER_LOCKED  # locked=평가정답, 학습 금지(train-on-test 차단)
    ]

    oss_norm = [{**r, "source": r.get("source", "labeled_oss_v1")} for r in oss]
    train_rows = oss_norm + rag + train_gold * max(1, args.gold_repeat)

    # 5) hard verification: zero holdout text may appear in the rebuilt train
    train_keys = {_norm(r["text"]) for r in train_rows}
    leak_check = sum(1 for r in holdout_rows if _norm(r["text"]) in train_keys)
    assert leak_check == 0, f"LEAK: {leak_check} holdout texts present in train"

    _write_jsonl(Path(args.holdout_out), holdout_rows)
    _write_jsonl(Path(args.train_out), train_rows)

    def tier(r: dict) -> str:
        ls = r.get("label_source", "?")
        # koipa는 2026-07-03 강등(판례 인용 조작 확인) — legally_grounded 아님(golden_tiers 참조).
        if ls in {"public_definitive", "nkt_designated"}:
            return "legally_grounded"
        if ls in {"koipa_case_based", "curated_scenario"}:
            return "curated_scenario"
        if ls in {"llm_judge_primary", "llm_judge_consensus", "codex_review"}:
            return "llm_judge"
        return ls

    manifest = {
        "purpose": "Decontaminated P1 train + clean eval-only holdout carved from gold_real.",
        "holdout_frac": args.holdout_frac,
        "gold_real_total": len(gold),
        "holdout": {
            "path": args.holdout_out,
            "rows": len(holdout_rows),
            "by_label": dict(Counter(r["label"] for r in holdout_rows)),
            "by_tier": dict(Counter(tier(r) for r in holdout_rows)),
            "by_label_source": dict(Counter(r.get("label_source") for r in holdout_rows)),
            "dropped_as_leaked_into_oss_or_rag": len(leaked),
        },
        "train": {
            "path": args.train_out,
            "rows": len(train_rows),
            "by_label": dict(Counter(r["label"] for r in train_rows)),
            "by_source": dict(Counter(r.get("source") for r in train_rows)),
            "gold_in_train_unique": len(train_gold),
            "gold_repeat": max(1, args.gold_repeat),
        },
        "leak_check_holdout_in_train": leak_check,
        "train_command": (
            "python scripts/p1_train_classifier.py --mode full "
            f"--train-path {Path(args.train_out).as_posix()} "
            "--val-path datasets/labeled_oss_v1/val.jsonl "
            "--test-path datasets/labeled_oss_v1/test.jsonl "
            "--epochs 5 --max-seq-len 512 --no-mlflow "
            "--output-dir artifacts/classifier_p1_retrain_v4_clean"
        ),
    }
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
