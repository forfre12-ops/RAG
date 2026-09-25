"""Pinned exposure ledger creation/audit/split; no original input is overwritten."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from koipa.customer_benchmark import strict_loads
from koipa.customer_eval_partition_v1 import audit_exposure, build_exposure_ledger, propose_exposure_safe_split
from koipa.policy_facts import require


def _read(path, expected):
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, "exposure_file_hash_mismatch")
    return raw


def _rows(raw):
    return [strict_loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("ledger", "audit", "split"))
    parser.add_argument("--documents", type=Path, required=True)
    parser.add_argument("--documents-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--source-sha256")
    parser.add_argument("--reason", choices=("diagnostic_fit_or_selection", "development_authoring", "model_selection"))
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--ledger-file-sha256")
    parser.add_argument("--ledger-sha256")
    parser.add_argument("--answers", type=Path)
    parser.add_argument("--answers-sha256")
    parser.add_argument("--semantic-links", type=Path)
    parser.add_argument("--semantic-links-sha256")
    args = parser.parse_args(argv)
    try:
        require(not args.out.exists(), "exposure_output_exists")
        output = args.out.resolve()
        for candidate in (args.documents, args.source, args.ledger, args.answers, args.semantic_links):
            if candidate is not None:
                for ancestor in candidate.resolve().parents:
                    require(not (ancestor / "manifest.json").is_file() or not output.is_relative_to(ancestor),
                            "exposure_output_inside_frozen_input")
        inputs = {}
        def load(path, expected):
            resolved = path.resolve()
            raw = _read(resolved, expected)
            require(resolved not in inputs or inputs[resolved] == raw, "exposure_aliased_input_changed")
            inputs.setdefault(resolved, raw)
            return raw
        docs = _rows(load(args.documents, args.documents_sha256))
        if args.command == "ledger":
            require(args.source is not None and args.reason is not None, "exposure_source_required")
            load(args.source, args.source_sha256)
            result = build_exposure_ledger(docs, source_ref=str(args.source), source_sha256=args.source_sha256, reason=args.reason)
        else:
            require(args.ledger is not None, "exposure_ledger_required")
            ledger = strict_loads(load(args.ledger, args.ledger_file_sha256).decode("utf-8"))
            links = []
            if args.semantic_links is not None:
                links = strict_loads(load(args.semantic_links, args.semantic_links_sha256).decode("utf-8"))
            kwargs = {"expected_ledger_sha256": args.ledger_sha256, "semantic_links": links}
            if args.command == "split":
                require(args.answers is not None, "exposure_answers_required")
                result = propose_exposure_safe_split(docs, _rows(load(args.answers, args.answers_sha256)), ledger, **kwargs)
            else:
                result = audit_exposure(docs, ledger, **kwargs)
        require(all(path.read_bytes() == raw for path, raw in inputs.items()), "exposure_input_changed_during_run")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
        # Detection, not locking: on failure the new diagnostic file may remain;
        # a caller must require exit 0 before adopting it.
        require(all(path.read_bytes() == raw for path, raw in inputs.items()), "exposure_input_changed_during_output")
        print(json.dumps({"status": result["status"], "training_allowed": False, "model_evaluation_allowed": False}))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_exposure_partition_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
