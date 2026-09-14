"""Validate the frozen pack and optionally measure externally supplied predictions.

No model is loaded. Output is a local candidate diagnostic, never acceptance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from content_reference_contract import load_pack, measure_predictions, require
from evaluation_inputs import read_rows, sha256
from prepare_content_reference import KNOWN_POOLS, POLICY, POC, known_pool_overlap, write_json


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pack", required=True)
    ap.add_argument("--policy", default=str(POLICY))
    ap.add_argument("--predictions", help="JSONL: doc_id, hashes, policy_version, predicted, model_grade, status")
    ap.add_argument("--split", choices=("all", "development", "sealed_candidate"), default="all")
    ap.add_argument("--out", required=True, help="NEW diagnostic JSON outside the pack")
    args = ap.parse_args()
    pack, out = (POC / args.pack).resolve(), (POC / args.out).resolve()
    require(not out.is_relative_to(pack), "Diagnostic outputs must not mutate the frozen pack")
    require(not out.exists(), "Diagnostic output exists; choose a new path")
    inputs, answers, splits, result = load_pack(pack, Path(args.policy))
    result["known_pool_overlap"] = known_pool_overlap(inputs, POC, KNOWN_POOLS)
    result["pack_manifest_sha256"] = sha256(pack / "manifest.json")
    result["model_executed"] = False
    if args.predictions:
        path = POC / args.predictions
        ids = set(splits[args.split]) if args.split != "all" else {d["doc_id"] for d in inputs}
        result["prediction_diagnostic"] = measure_predictions([d for d in inputs if d["doc_id"] in ids],
                                                              [a for a in answers if a["doc_id"] in ids], read_rows(path))
        result["prediction_file_sha256"] = sha256(path)
        result["prediction_split"] = args.split
    else:
        result["prediction_diagnostic"] = "NOT_RUN_NO_PREDICTIONS"
    write_json(out, result)
    print(json.dumps({"out": str(out), "status": result["status"], "n": result["n"], "model_executed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
