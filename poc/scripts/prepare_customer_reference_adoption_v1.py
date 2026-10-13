"""Create a separate AI internal-reference review ledger, not a training release."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.customer_reference_adoption_v1 import (
    LEDGER_SCHEMA, SOURCE_MANIFEST_SHA256, build_adoption_ledger, source_hashes,
)
from koipa.policy_facts import FactContractError, require, text_digest

SOURCE_MEMBERS = ("authoring/documents.jsonl", "answers/answers.candidate.jsonl",
                  "answers/evidence.jsonl", "authoring/batch06_metadata.jsonl")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n"


def _read(path, snapshots):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "adoption_input_missing_or_linked")
    path = path.resolve()
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    require(str(path) not in snapshots or snapshots[str(path)] == sha, "adoption_input_changed")
    snapshots[str(path)] = sha
    return data


def _rows(data):
    lines = data.decode("utf-8").splitlines()
    require(bool(lines) and all(line.strip() for line in lines), "adoption_review_jsonl_empty_or_blank")
    return [strict_loads(line) for line in lines]


def _target(out, source_pack, review_files):
    supplied = Path(out)
    require(not supplied.is_symlink(), "adoption_output_alias_forbidden")
    target, source = supplied.resolve(), Path(source_pack).resolve()
    require(target != source and not target.is_relative_to(source), "adoption_output_inside_source_pack")
    require(not any((p/"manifest.json").exists() for p in target.parents), "adoption_output_inside_frozen_pack")
    require(target not in {Path(p).resolve() for p in review_files}, "adoption_output_overlaps_review")
    require(not target.exists(), "adoption_output_exists")
    return target


def _unchanged(snapshots):
    require(all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha
                for p, sha in snapshots.items()), "adoption_input_or_code_changed")


def prepare(out, *, source_pack, review_files, expected_review_sha256):
    require(type(review_files) in (list, tuple) and type(expected_review_sha256) in (list, tuple) and
            0 < len(review_files) == len(expected_review_sha256), "adoption_review_sources_required")
    paths = [Path(p).resolve() for p in review_files]
    require(len(set(paths)) == len(paths), "adoption_duplicate_review_file")
    target = _target(out, source_pack, paths)
    source_pack = Path(source_pack).resolve()
    snapshots = {}
    sources = source_hashes()
    script_sha = hashlib.sha256(_read(Path(__file__), snapshots)).hexdigest()
    for name, sha in sources.items():
        require(hashlib.sha256(_read(POC/name, snapshots)).hexdigest() == sha, "adoption_input_or_code_changed")
    raw_manifest = _read(source_pack/"manifest.json", snapshots)
    require(hashlib.sha256(raw_manifest).hexdigest() == SOURCE_MANIFEST_SHA256, "adoption_source_manifest_pin_invalid")
    manifest = strict_loads(raw_manifest.decode("utf-8"))
    require(type(manifest) is dict and all(type(manifest.get(k)) is bool and manifest[k] is False for k in FLAGS),
            "adoption_source_permissions_invalid")
    require(type(manifest.get("files")) is dict and set(SOURCE_MEMBERS) <= set(manifest["files"]), "adoption_source_members_missing")
    source_rows = []
    for name in SOURCE_MEMBERS:
        path = source_pack/name
        require(path.resolve().is_relative_to(source_pack), "adoption_source_member_escape")
        data = _read(path, snapshots)
        require(hashlib.sha256(data).hexdigest() == manifest["files"][name], "adoption_source_member_changed")
        source_rows.append(_rows(data))
    reviews, provenance = [], []
    for path, expected in zip(paths, expected_review_sha256, strict=True):
        data = _read(path, snapshots)
        require(hashlib.sha256(data).hexdigest() == expected, "adoption_review_file_pin_mismatch")
        rows = _rows(data)
        reviews.extend(rows)
        provenance.append({"path": str(path), "sha256": expected, "decisions": len(rows)})
    ledger = build_adoption_ledger(*source_rows[:3], reviews, source_manifest_sha256=SOURCE_MANIFEST_SHA256,
        hard_hold_benchmark_ids=[r["doc_id"] for r in source_rows[3]])
    require(ledger["source_files_sha256"] == sources, "adoption_input_or_code_changed")
    summary = {k: v for k, v in ledger.items() if k not in {"decisions", "hard_hold_benchmark_ids",
        "internal_reference_accepted_ids", "internal_reference_held_ids", "internal_reference_rejected_ids", "benchmark_eligible_candidate_ids"}}
    contents = {"ledger.json": _json(ledger), "summary.json": _json(summary)}
    result_manifest = {**FLAGS, "schema_version": LEDGER_SCHEMA, "dataset_role": "internal_ai_reference_decisions_not_training_release",
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256, "review_sources": provenance,
        "source_files_sha256": {**sources, Path(__file__).resolve().relative_to(POC).as_posix(): script_sha},
        "input_and_code_snapshots": snapshots, "ledger_sha256": ledger["ledger_sha256"],
        "benchmark_training_adopted": 0, "benchmark_evaluation_adopted": 0,
        "files": {name: text_digest(content) for name, content in contents.items()}}
    _unchanged(snapshots)
    target = _target(target, source_pack, paths)
    target.mkdir(parents=True, exist_ok=False)
    for name, content in contents.items():
        with (target/name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    _unchanged(snapshots)
    with (target/"manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(result_manifest))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-pack", type=Path, required=True)
    parser.add_argument("--review-file", type=Path, action="append", required=True)
    parser.add_argument("--expected-review-sha256", action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(_json(prepare(args.out, source_pack=args.source_pack, review_files=args.review_file,
                            expected_review_sha256=args.expected_review_sha256)), end="")
    except Exception as exc:
        print(_json({"status": "failed", "code": str(exc) if isinstance(exc, FactContractError) else "adoption_io_or_contract_failed",
                     "model_forward_executed": False, "benchmark_training_adopted": 0}), end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
