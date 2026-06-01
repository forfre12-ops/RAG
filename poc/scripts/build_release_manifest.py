"""Build a compact release artifact manifest with file hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_FILES = [
    "README.md",
    "RELEASE_CHECKLIST.md",
    "datasets/manifests/dataset_v1.0.yaml",
    "reports/operational_readiness.json",
    "reports/p1_v3_public_gold_direct.json",
    "reports/p1_v3_llm_gold_direct.json",
    "reports/p2_gold_kure_es_hybrid_v3.json",
    "reports/p1_boundary_report.json",
]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001
        return "unknown"


def build_manifest(files: list[str]) -> dict:
    artifacts = []
    for name in files:
        path = Path(name)
        if path.exists():
            artifacts.append(
                {
                    "path": name,
                    "exists": True,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
        else:
            artifacts.append({"path": name, "exists": False})
    missing = [a["path"] for a in artifacts if not a["exists"]]
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "status": "INCOMPLETE" if missing else "READY",
        "missing": missing,
        "artifacts": artifacts,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="reports/release_artifact_manifest.json")
    ap.add_argument("--files", nargs="*", default=DEFAULT_FILES)
    args = ap.parse_args()

    payload = build_manifest(args.files)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": payload["status"], "missing": payload["missing"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
