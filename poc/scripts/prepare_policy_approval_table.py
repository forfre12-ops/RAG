"""Generate 64 UNAPPROVED policy review rows, never an expected-answer oracle."""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402


def build_preview() -> list[dict]:
    rows = []
    for s, v, m in itertools.product((0, 1, 2, "unknown"), repeat=3):
        axes = [(0, 1, 2) if value == "unknown" else (value,) for value in (s, v, m)]
        options = {mode: sorted({grade_from_svm(*values, mode=mode)
                                for values in itertools.product(*axes)})
                   for mode in ("guide", "v22", "fnr")}
        rows.append({
            "rule_id": f"APPROVAL-S{s}-V{v}-M{m}", "s": s, "v": v, "m": m,
            "implementation_preview": options,
            "preview_is_not_test_oracle": True,
            "policy_version": "reference-v1-draft", "approval_status": "unapproved",
            "approved_grade": None, "approved_candidates": None,
            "approved_review_required": None, "required_evidence": None,
            "approval_reference": None, "approved_by": None,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = POC / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = build_preview()
    with out.open("x", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(rows), "approved_answers": 0, "out": str(out)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
