"""Read-only conditional-reference audit; creates an exclusive report directory."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.customer_reference_audit_v1 import audit_reference, semantic_family_candidates
from koipa.policy_facts import require, text_digest

INPUT_FILES = ("authoring/documents.jsonl", "answers/answers.candidate.jsonl", "answers/evidence.jsonl")
SOURCES = ("src/koipa/customer_reference_audit_v1.py", "scripts/audit_customer_reference_v1.py")


def _rows(raw):
    rows = [strict_loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(bool(rows), "independent_zero_cases")
    return rows


def run(pack, out):
    pack, out = Path(pack).resolve(), Path(out).resolve()
    require(pack.is_dir(), "independent_pack_missing")
    require(not out.exists() and not out.is_relative_to(pack), "independent_output_not_exclusive")
    raw = {name: (pack / name).read_bytes() for name in INPUT_FILES}
    report = audit_reference(*[_rows(raw[name]) for name in INPUT_FILES])
    candidates = semantic_family_candidates(_rows(raw[INPUT_FILES[0]]))
    manifest_path = pack / "manifest.json"
    manifest_hash = hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest_path.is_file() else None
    report["provenance"] = {
        "input_files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in raw.items()},
        "pack_manifest_sha256": manifest_hash,
        "pack_manifest_validation_performed": False,
        "source_files_sha256": {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in SOURCES},
    }
    require(all((pack / name).read_bytes() == data for name, data in raw.items()), "independent_pack_mutated")
    out.mkdir(parents=True, exist_ok=False)
    outputs = {"audit.json": report, "semantic_candidates.json": candidates}
    payload_hashes = {}
    for name, value in outputs.items():
        content = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        with (out / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        payload_hashes[name] = text_digest(content)
    manifest = {**FLAGS, "schema_version": "customer-reference-independent-report-v1", "files": payload_hashes,
                "source_files_sha256": report["provenance"]["source_files_sha256"]}
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    require(all((pack / name).read_bytes() == data for name, data in raw.items()), "independent_pack_mutated")
    return {"cases": report["cases"], "passed": report["passed"], "grade_counts": report["grade_counts"],
            "semantic_candidate_groups": candidates["candidate_groups"], "out": str(out)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.pack, args.out), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
