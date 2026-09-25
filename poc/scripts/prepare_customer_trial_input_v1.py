"""Exclusive offline preflight packs from public inputs, never answer files."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.customer_guide_reference import POLICY_SHA256
from koipa.customer_trial_input_v1 import SCHEMA, prepare_trial_inputs
from koipa.policy_facts import FactContractError, require, text_digest


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)+"\n"


def _jsonl(rows):
    return "".join(json.dumps(v, ensure_ascii=False, sort_keys=True, allow_nan=False)+"\n" for v in rows)


def _source_rows(path, expected_sha256):
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected_sha256, "trial_source_file_hash_mismatch")
    lines = raw.decode("utf-8-sig").splitlines()
    require(bool(lines) and all(line.strip() for line in lines), "trial_source_empty_or_blank_row")
    return [strict_loads(line) for line in lines]


def payload(preflight):
    contract = {k: v for k, v in preflight.items() if k not in {"source_inputs", "views"}}
    tokens = [v["tokens_with_special"] for v in preflight["views"]]
    counts = Counter(r for v in preflight["views"] for r in v["reason_codes"])
    summary = {**FLAGS, "status": preflight["status"], "profile": preflight["profile"],
        "documents": preflight["documents"], "input_ready_only": preflight["input_ready_only"],
        "needs_review": preflight["needs_review"], "tokens_with_special_min": min(tokens),
        "tokens_with_special_max": max(tokens), "hold_reason_counts": dict(sorted(counts.items())),
        "body_only_grade_scoring_allowed": False, "model_inference_performed": False, "model_forward_executed": False,
        "model_artifact_verified": False, "model_accuracy_measured": False,
        "note": "Input-ready is not release permission. Supplied facts are not extracted body evidence."}
    return {"contract.json": _json(contract), "summary.json": _json(summary),
            "inputs/public_inputs.jsonl": _jsonl(preflight["source_inputs"]),
            "inputs/presented_inputs.jsonl": _jsonl(preflight["views"])}


def prepare(out, *, inputs, expected_input_sha256, tokenizer, expected_tokenizer_sha256,
            expected_policy_sha256=POLICY_SHA256, profile="body_context"):
    out = Path(out)
    require(not out.exists(), "trial_output_exists")
    rows = _source_rows(inputs, expected_input_sha256)
    preflight = prepare_trial_inputs(rows, tokenizer_path=tokenizer,
        expected_tokenizer_sha256=expected_tokenizer_sha256,
        expected_policy_sha256=expected_policy_sha256, profile=profile)
    members = payload(preflight)
    out.mkdir(parents=True, exist_ok=False)
    manifest = {**FLAGS, "schema_version": SCHEMA, "dataset_role": "input_preflight_only",
        "source_input_file_sha256": expected_input_sha256,
        "tokenizer_sha256": expected_tokenizer_sha256, "policy_sha256": expected_policy_sha256,
        "profile": profile, "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "files": {k: text_digest(v) for k, v in members.items()}}
    for name, content in {**members, "manifest.json": _json(manifest)}.items():
        destination = out/name
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    return verify(out, inputs=inputs, expected_input_sha256=expected_input_sha256,
        tokenizer=tokenizer, expected_tokenizer_sha256=expected_tokenizer_sha256,
        expected_policy_sha256=expected_policy_sha256)


def verify(root, *, inputs, expected_input_sha256, tokenizer, expected_tokenizer_sha256,
           expected_policy_sha256=POLICY_SHA256):
    root = Path(root).resolve()
    manifest_bytes = (root/"manifest.json").read_bytes()
    manifest = strict_loads(manifest_bytes.decode("utf-8"))
    require(type(manifest) is dict and set(manifest) == set(FLAGS) | {"schema_version", "dataset_role",
        "source_input_file_sha256", "tokenizer_sha256", "policy_sha256", "profile", "script_sha256", "files"},
        "trial_manifest_fields_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS), "trial_permission_invalid")
    require(manifest["schema_version"] == SCHEMA and manifest["dataset_role"] == "input_preflight_only",
            "trial_manifest_schema_invalid")
    require(manifest["source_input_file_sha256"] == expected_input_sha256 and
        manifest["tokenizer_sha256"] == expected_tokenizer_sha256 and manifest["policy_sha256"] == expected_policy_sha256,
        "trial_manifest_identity_mismatch")
    require(manifest["script_sha256"] == hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "trial_script_drift")
    rows = _source_rows(inputs, expected_input_sha256)
    expected = payload(prepare_trial_inputs(rows, tokenizer_path=tokenizer,
        expected_tokenizer_sha256=expected_tokenizer_sha256,
        expected_policy_sha256=expected_policy_sha256, profile=manifest["profile"]))
    require(type(manifest["files"]) is dict and set(manifest["files"]) == set(expected), "trial_manifest_files_invalid")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(expected) | {"manifest.json"},
            "trial_unlisted_file")
    # Iterate hard-coded expected paths, not paths declared by an untrusted manifest.
    for name, content in expected.items():
        member = (root/name).resolve()
        require(member.is_relative_to(root), "trial_member_path_escape")
        data = member.read_bytes()
        require(hashlib.sha256(data).hexdigest() == manifest["files"][name], "trial_member_hash_mismatch")
        require(data == content.encode("utf-8"), "trial_replay_mismatch")
    require((root/"manifest.json").read_bytes() == manifest_bytes, "trial_manifest_changed")
    return strict_loads(expected["summary.json"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for command in ("prepare", "verify"):
        child = subs.add_parser(command)
        child.add_argument("--out" if command == "prepare" else "--pack", type=Path, required=True)
        child.add_argument("--inputs", type=Path, required=True)
        child.add_argument("--expected-input-sha256", required=True)
        child.add_argument("--tokenizer", type=Path, required=True)
        child.add_argument("--expected-tokenizer-sha256", required=True)
        child.add_argument("--expected-policy-sha256", default=POLICY_SHA256)
        if command == "prepare":
            child.add_argument("--profile", choices=("body_only", "body_context"), default="body_context")
    args = vars(parser.parse_args(argv))
    command = args.pop("command")
    root = args.pop("out" if command == "prepare" else "pack")
    try:
        result = (prepare if command == "prepare" else verify)(root, **args)
        print(_json(result), end="")
    except (FactContractError, OSError, UnicodeError, ValueError) as exc:
        # Stable code only; do not leak malformed source contents or file contents.
        code = str(exc) if isinstance(exc, FactContractError) else "trial_io_or_json_invalid"
        print(_json({"status": "failed", "code": code}), end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
