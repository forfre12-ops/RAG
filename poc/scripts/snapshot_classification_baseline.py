"""Capture local classification inputs without loading a model or contacting services.

Only allowlisted settings are recorded. This is not a deployed-model attestation:
an active model selected from the server database is deliberately not queried.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))
SETTING_KEYS = (
    "classifier_model_dir", "classifier_base_model", "classifier_temperature",
    "classifier_escalation_tau", "classifier_device", "grade_formula_mode",
    "agreement_gate_enabled", "factor_model_dir", "serving_prefer_active_model",
    "max_seq_len", "chunk_overlap", "model_secondopinion_llm_enabled",
)
SOURCE_PATHS = (
    "src/koipa/modules/m3_labeling/rule_engine.py",
    "src/koipa/modules/m3_labeling/policy_engine.py",
    "src/koipa/modules/m5_inference/pipeline.py",
    "src/koipa/services/classify_service.py",
    "src/koipa/golden_tiers.py", "src/koipa/golden_signoff.py",
    "src/koipa/eval_authority.py", "scripts/measure_four_metrics.py",
    "scripts/judge_model_candidate.py", "scripts/audit_eval_ground_truth.py",
    "scripts/build_signoff_batch.py", "scripts/build_decision_table.py",
)


def fingerprint(path: Path, root: Path = POC) -> dict:
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise RuntimeError(f"Input changed during snapshot: {path.name}")
    return {"path": path.relative_to(root).as_posix() if path.is_relative_to(root)
            else str(path), "bytes": after.st_size, "sha256": digest.hexdigest()}


def capture(root: Path = POC) -> dict:
    from koipa.config import settings

    repo = root.parent
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True,
                                       encoding="utf-8").strip()

    configured = {k: getattr(settings, k, None) for k in SETTING_KEYS}
    inputs = sorted(set(root.glob("datasets/**/*.jsonl")) |
                    set(root.glob("evidence/*.jsonl")))
    data = [fingerprint(p, root) for p in inputs]
    models = []
    for key in ("classifier_model_dir", "factor_model_dir"):
        value = configured.get(key)
        path = Path(value) if value else None
        if path is not None and not path.is_absolute():
            path = root / path
        files = sorted(p for p in path.rglob("*") if p.is_file()) if path and path.is_dir() else []
        models.append({"setting": key, "exists": bool(files),
                       "files": [fingerprint(p, root) for p in files]})
    return {
        "schema_version": "classification-baseline-v1",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git("rev-parse", "HEAD"),
        "git_status": git("status", "--short"),
        "settings": configured, "configured_model_artifacts": models,
        "active_server_model_verified": False,
        "limitations": ["No database, API, model inference or training was run.",
                        "Configured local model is not proof of the active server model.",
                        "Dataset inventory contains duplicate documents across files."],
        "sources": [fingerprint(root / p, root) for p in SOURCE_PATHS],
        "datasets": data, "dataset_files": len(data),
        "dataset_manifest_sha256": hashlib.sha256(
            json.dumps(data, sort_keys=True).encode()).hexdigest(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = POC / args.out
    if out.exists():
        ap.error("Snapshot exists; choose a new output path.")
    result = capture()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=2)
    print(json.dumps({"out": str(out), "dataset_files": result["dataset_files"],
                      "active_server_model_verified": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
