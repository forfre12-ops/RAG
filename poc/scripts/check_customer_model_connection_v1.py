"""Inventory a local checkpoint or explicitly smoke its two registered texts."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

from koipa.customer_benchmark import FLAGS
from koipa.customer_model_connection_v1 import SCHEMA, inventory_checkpoint, run_connection_smoke
from koipa.policy_facts import FactContractError, require, text_digest


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2)+"\n"


def _safe_output(out, model_dir):
    supplied = Path(out)
    require(not supplied.is_symlink(), "connection_output_alias_forbidden")
    target, model_root = supplied.resolve(), Path(model_dir).resolve()
    require(target != model_root and not target.is_relative_to(model_root), "connection_output_inside_checkpoint")
    require(not any((ancestor/"manifest.json").exists() for ancestor in target.parents), "connection_output_inside_frozen_pack")
    require(not supplied.exists(), "connection_output_exists")
    return target


def write_result(out, result, *, model_dir, expected_script_sha256):
    out = _safe_output(out, model_dir)
    require(all(type(result.get(k)) is bool and result[k] is False for k in FLAGS), "connection_permission_invalid")
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == expected_script_sha256, "connection_script_changed")
    before = inventory_checkpoint(model_dir)
    require(all(result[key] == before[key] for key in ("checkpoint_sha256", "model_sha256", "source_module_sha256")),
            "connection_source_changed_before_output")
    # Recheck the destination after the non-mutating checkpoint scan.
    out = _safe_output(out, model_dir)
    content = _json(result)
    manifest = {**FLAGS, "schema_version": SCHEMA, "dataset_role": "connection_check_not_benchmark",
        "source_script_sha256": expected_script_sha256,
        "checkpoint_sha256": result["checkpoint_sha256"], "model_sha256": result["model_sha256"],
        "model_forward_executed": result["model_forward_executed"],
        "files": {"result.json": text_digest(content)}}
    out.mkdir(parents=True, exist_ok=False)
    with (out/"result.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
    after = inventory_checkpoint(model_dir)
    require(before == after and hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == expected_script_sha256,
            "connection_source_changed_during_output")
    # A manifest is the completion marker, never present after a failed recheck.
    with (out/"manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(manifest))
    return {**FLAGS, "status": result["status"], "checkpoint_sha256": result["checkpoint_sha256"],
        "model_sha256": result["model_sha256"], "model_forward_executed": result["model_forward_executed"],
        "model_forward_calls": result["model_forward_calls"], "label_order": result["label_order"],
        "benchmark_documents_used": 0, "accuracy_measured": False, "output": str(out.resolve())}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    for name in ("inventory", "smoke"):
        child = subs.add_parser(name)
        child.add_argument("--model-dir", required=True, type=Path)
        child.add_argument("--out", required=True, type=Path)
        if name == "smoke":
            child.add_argument("--expected-checkpoint-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        _safe_output(args.out, args.model_dir)
        script_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        result = inventory_checkpoint(args.model_dir) if args.command == "inventory" else run_connection_smoke(
            args.model_dir, expected_checkpoint_sha256=args.expected_checkpoint_sha256)
        print(_json(write_result(args.out, result, model_dir=args.model_dir, expected_script_sha256=script_sha)), end="")
    except Exception as exc:
        code = str(exc) if isinstance(exc, FactContractError) else "connection_io_or_runtime_failed"
        print(_json({"status": "failed", "code": code, "benchmark_documents_used": 0,
            "model_forward_execution_state": "not_started" if args.command == "inventory" else "not_reported_due_failure"}), end="")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
