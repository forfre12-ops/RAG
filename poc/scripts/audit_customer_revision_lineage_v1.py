"""Pinned three-pack revision audit; completion manifest written only after checks."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.customer_revision_lineage_v1 import (
    PARENT_MANIFEST_SHA256, PRIOR_LEDGER_SHA256, SCHEMA, audit_revision, source_hashes,
)
from koipa.policy_facts import FactContractError, require, text_digest

PRIOR_INTEGRATION_MANIFEST_SHA256 = "e570416f32ada74f6c570a7af29d81d85044355b79702fbaa8b0b9e6395ab385"
COMMON = ("authoring/documents.jsonl", "answers/answers.candidate.jsonl", "answers/evidence.jsonl")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n"


def _read(path, snapshot):
    path = Path(path)
    require(path.is_file() and not path.is_symlink(), "revision_source_file_missing_or_linked")
    path = path.resolve()
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    require(str(path) not in snapshot or snapshot[str(path)] == sha, "revision_source_changed")
    snapshot[str(path)] = sha
    return data


def _pack(root, expected_manifest_sha256, members, snapshot):
    root = Path(root).resolve()
    manifest_bytes = _read(root/"manifest.json", snapshot)
    require(hashlib.sha256(manifest_bytes).hexdigest() == expected_manifest_sha256, "revision_manifest_pin_mismatch")
    manifest = strict_loads(manifest_bytes.decode("utf-8"))
    require(type(manifest) is dict and all(type(manifest.get(k)) is bool and manifest[k] is False for k in FLAGS),
            "revision_source_pack_permission_invalid")
    require(type(manifest.get("files")) is dict and set(members) <= set(manifest["files"]), "revision_source_member_missing")
    result = {}
    for name in members:
        member = root/name
        require(member.resolve().is_relative_to(root), "revision_source_member_escape")
        data = _read(member, snapshot)
        require(hashlib.sha256(data).hexdigest() == manifest["files"][name], "revision_source_member_hash_mismatch")
        text = data.decode("utf-8")
        if name.endswith(".jsonl"):
            lines = text.splitlines()
            require(bool(lines) and all(line.strip() for line in lines), "revision_jsonl_empty_or_blank")
            result[name] = [strict_loads(line) for line in lines]
        else:
            result[name] = strict_loads(text)
    return result


def _output_target(out, roots):
    supplied = Path(out)
    require(not supplied.is_symlink(), "revision_output_alias_forbidden")
    target = supplied.resolve()
    require(all(target != root and not target.is_relative_to(root) for root in roots), "revision_output_inside_source_pack")
    require(not any((p/"manifest.json").exists() for p in target.parents), "revision_output_inside_frozen_pack")
    require(not target.exists(), "revision_output_exists")
    return target


def _check_snapshot(snapshot):
    require(all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha
                for p, sha in snapshot.items()), "revision_source_changed")


def prepare(out, *, original_pack, revision_pack, expected_revision_manifest_sha256, prior_integration_pack,
            expected_original_manifest_sha256=PARENT_MANIFEST_SHA256,
            expected_integration_manifest_sha256=PRIOR_INTEGRATION_MANIFEST_SHA256):
    roots = [Path(p).resolve() for p in (original_pack, revision_pack, prior_integration_pack)]
    target = _output_target(out, roots)
    require(expected_original_manifest_sha256 == PARENT_MANIFEST_SHA256 and
            expected_integration_manifest_sha256 == PRIOR_INTEGRATION_MANIFEST_SHA256, "revision_origin_pin_invalid")
    snapshot = {}
    script_sha = hashlib.sha256(_read(Path(__file__), snapshot)).hexdigest()
    module_sources = source_hashes()
    for name, sha in module_sources.items():
        raw = _read(POC/"src"/"koipa"/name, snapshot)
        require(hashlib.sha256(raw).hexdigest() == sha, "revision_source_changed")
    original = _pack(roots[0], expected_original_manifest_sha256, (*COMMON, "authoring/batch06_metadata.jsonl"), snapshot)
    revised = _pack(roots[1], expected_revision_manifest_sha256, (*COMMON, "audit/lineage.jsonl"), snapshot)
    integration = _pack(roots[2], expected_integration_manifest_sha256,
                        ("exposure/development260.json", "exposure/semantic_links.json"), snapshot)
    result = audit_revision(original[COMMON[0]], original[COMMON[1]], revised[COMMON[0]], revised[COMMON[1]],
        revised["audit/lineage.jsonl"], original_evidence=original[COMMON[2]], revised_evidence=revised[COMMON[2]],
        panel_parent_ids=[r["doc_id"] for r in original["authoring/batch06_metadata.jsonl"]],
        ledger=integration["exposure/development260.json"], expected_ledger_sha256=PRIOR_LEDGER_SHA256,
        parent_manifest_sha256=expected_original_manifest_sha256, source_ref=str(roots[1]/"manifest.json"),
        source_sha256=expected_revision_manifest_sha256, semantic_links=integration["exposure/semantic_links.json"])
    require(result["source_files_sha256"] == module_sources, "revision_source_changed")
    summary = {k: v for k, v in result.items() if k not in {"exposure_ledger", "exposure_audit", "semantic_links_remapped",
        "parent_to_active_id", "retired_parent_ids", "changed_active_ids", "unchanged_panel_ids"}}
    files = {"result.json": _json(result), "summary.json": _json(summary)}
    manifest = {**FLAGS, "schema_version": SCHEMA, "dataset_role": "diagnostic_revision_lineage_not_adoption",
        "adoption_allowed": False, "authoritative_parent_replaced": False,
        "parent_manifest_sha256": expected_original_manifest_sha256, "revision_manifest_sha256": expected_revision_manifest_sha256,
        "prior_integration_manifest_sha256": expected_integration_manifest_sha256,
        "prior_ledger_sha256": PRIOR_LEDGER_SHA256, "updated_ledger_sha256": result["exposure_ledger"]["ledger_sha256"],
        "source_script_sha256": script_sha,
        "source_files_sha256": {**{"src/koipa/"+name: sha for name, sha in module_sources.items()},
                                Path(__file__).resolve().relative_to(POC).as_posix(): script_sha},
        "input_and_code_snapshots": snapshot, "files": {name: text_digest(text) for name, text in files.items()}}
    _check_snapshot(snapshot)
    target = _output_target(target, roots)
    target.mkdir(parents=True, exist_ok=False)
    for name, content in files.items():
        with (target/name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    _check_snapshot(snapshot)
    # Absence of manifest identifies a failed/incomplete output directory.
    with (target/"manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(manifest))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-pack", type=Path, required=True)
    parser.add_argument("--revision-pack", type=Path, required=True)
    parser.add_argument("--expected-revision-manifest-sha256", required=True)
    parser.add_argument("--prior-integration-pack", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = vars(parser.parse_args(argv))
    out = args.pop("out")
    try:
        print(_json(prepare(out, **args)), end="")
    except Exception as exc:
        print(_json({"status": "failed", "code": str(exc) if isinstance(exc, FactContractError) else "revision_io_or_contract_failed",
                     "model_forward_executed": False}), end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
