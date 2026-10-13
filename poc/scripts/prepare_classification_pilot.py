"""Prepare 20 calibration cases. Never assign grades, sign, or promote records."""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import Counter
from pathlib import Path

from build_signoff_batch import POC, _h, evidence_bundle, write_batch, write_casebook
from evaluation_inputs import read_rows, sha256, text_of

SOURCE = "datasets/signoff_slate/locked_f90787e0-39c0-4b69-857b-ecf16f7e0f0a.jsonl"


def prepare(source: Path, out: Path, *, per_grade: int = 5, seed: int = 20260914) -> dict:
    rows = read_rows(source)
    rng = random.Random(seed)
    picked, seen = [], set()
    for grade in ("TS", "S1", "S2", "S3"):
        group = sorted((r for r in rows if r.get("label") == grade),
                       key=lambda r: _h(text_of(r)))
        rng.shuffle(group)
        count = 0
        for row in group:
            text = text_of(row)
            digest = _h(text)
            if not text or digest in seen:
                continue
            bundle = evidence_bundle(row)
            if bundle["document_origin"] != "synthetic":
                raise ValueError("This calibration builder only accepts synthetic source cases")
            seen.add(digest)
            picked.append({"text": text, "text_sha256": digest,
                "origin": "synthetic", "hidden_label": grade,
                "source_path": str(source), "original_doc_id": row.get("doc_id"),
                "evidence_bundle": bundle})
            count += 1
            if count == per_grade:
                break
        if count != per_grade:
            raise ValueError(f"Not enough unique calibration cases for {grade}: {count}")
    rng.shuffle(picked)  # Grade grouping/order itself must not reveal the old answer.
    manifest = {
        "schema_version": "classification-calibration-v1", "purpose": "calibration_not_evaluation",
        "source": str(source), "source_sha256": sha256(source), "seed": seed,
        "selected_n": len(picked), "old_label_sampling_counts": dict(Counter(r["hidden_label"] for r in picked)),
        "selection_note": "Existing labels used ONLY for calibration quotas; not verified answers or representative sampling.",
        "training_overlap_checked": False, "evaluation_allowed": False, "training_allowed": False,
        "human_signatures_created": 0, "grade_answers_created": 0,
        "policy_context": {"version": "reference-v1-draft", "approval_status": "unapproved",
            "reference": "poc/docs/CLASSIFICATION_POLICY_APPROVAL_DRAFT_2026-09-14.md"},
        "missing_system_evidence_cases": sum(bool(r["evidence_bundle"]["missing_system_fields"]) for r in picked),
        "selected_manifest_sha256": hashlib.sha256(
            "".join(sorted(r["text_sha256"] for r in picked)).encode()).hexdigest(),
    }
    write_batch(out, picked, manifest)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source", default=SOURCE)
    ap.add_argument("--out", required=True)
    ap.add_argument("--render-cases-only", action="store_true",
                    help="기존 팩에서 새 CASES.md만 생성. 답변·선정 원장은 변경하지 않음")
    args = ap.parse_args()
    if args.render_cases_only:
        reviewer_dir = POC / args.out / "reviewer"
        write_casebook(reviewer_dir, read_rows(reviewer_dir / "review_pack.jsonl"))
        print(str(reviewer_dir / "CASES.md"))
        return 0
    manifest = prepare(POC / args.source, POC / args.out)
    print(json.dumps({k: manifest[k] for k in (
        "selected_n", "old_label_sampling_counts", "grade_answers_created",
        "human_signatures_created", "missing_system_evidence_cases")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
