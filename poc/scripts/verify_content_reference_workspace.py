"""Read-only preservation audit; writes one new report, never edits inputs.

This hashes local files without importing the model or connecting to services.
It is not an attestation of the server's active model.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from content_reference_contract import require
from evaluation_inputs import read_rows, sha256
from prepare_content_reference import POC, write_json


def check_files(root: Path, expected: dict[str, str]) -> dict:
    changed, missing = [], []
    for name, digest in expected.items():
        path = (root / name).resolve()
        require(path.is_relative_to(root.resolve()), "Preservation manifest target outside workspace")
        if not path.is_file():
            missing.append(name)
        elif sha256(path) != digest:
            changed.append(name)
    return {"checked": len(expected), "changed": changed, "missing": missing,
            "unchanged": len(expected) - len(changed) - len(missing)}


def verify(before_path: Path, ablation_path: Path, root: Path = POC) -> dict:
    before = json.loads(before_path.read_text(encoding="utf-8"))
    report = json.loads(ablation_path.read_text(encoding="utf-8"))
    inputs = check_files(root, {r["path"]: r["sha256"] for r in before["datasets"]})
    current = {p.relative_to(root).as_posix() for p in root.glob("datasets/**/*.jsonl")}
    current |= {p.relative_to(root).as_posix() for p in root.glob("evidence/*.jsonl")}
    inputs["added_paths"] = sorted(current - {r["path"] for r in before["datasets"]})
    sources = check_files(root, {r["path"]: r["sha256"] for r in before["sources"]})
    local_model = check_files(root, report["manifest"]["input_and_code_sha256"])
    calibration = root / "reports/CLASSIFICATION_PILOT_20260914/calibration20/reviewer/answer_template.jsonl"
    old_answers = read_rows(calibration)
    old_policy_path = root / "reports/CLASSIFICATION_PILOT_20260914/policy_decision_table_draft.jsonl"
    old_policy = read_rows(old_policy_path)
    untouched = all(not r["changed"] and not r["missing"] for r in (inputs, sources, local_model)) and not inputs["added_paths"]
    return {
        "schema_version": "content-reference-preservation-v1", "checked_at": datetime.now(timezone.utc).isoformat(),
        "status": "PRESERVED" if untouched else "CHANGED_REQUIRES_REVIEW",
        "git_head": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
        "baseline_sha256": sha256(before_path), "ablation_report_sha256": sha256(ablation_path),
        "original_jsonl": inputs, "baseline_source_files": sources, "prior_model_input_code_files": local_model,
        "existing_calibration20": {"rows": len(old_answers), "filled_grades": sum(r.get("grade") is not None for r in old_answers),
                                   "signer_ids": sum(bool(r.get("signer_id")) for r in old_answers), "sha256": sha256(calibration)},
        "existing_policy64": {"rows": len(old_policy), "approved_rows": sum(r.get("approval_status") == "approved" for r in old_policy),
                              "sha256": sha256(old_policy_path)},
        "active_server_model_verified": False, "model_loaded": False,
        "limitations": ["Known snapshots only; existing calibration20/64 report counts are current state, not a new full historical hash baseline.",
                        "No database, API, active-model query, inference or training was performed."],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", required=True)
    ap.add_argument("--ablation-report", default="reports/SVM_ABLATION_20260914/full02/report.json")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = POC / args.out
    require(not out.exists(), "Report exists; choose a new path")
    result = verify(POC / args.before, POC / args.ablation_report)
    write_json(out, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PRESERVED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
