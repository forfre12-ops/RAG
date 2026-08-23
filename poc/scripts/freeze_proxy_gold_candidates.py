"""Create an immutable manifest for the current 1,000 Proxy Gold candidates."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
OUT_DIR = ROOT / "datasets" / "proxy_gold" / "frozen_candidates"
OUT = OUT_DIR / "proxy_gold_candidates_1000.v1.manifest.json"
EXPECTED = {"TS": 200, "S1": 250, "S2": 250, "S3": 300}


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def document_path(meta: dict[str, object]) -> Path:
    revision = meta.get("content_revision_path")
    if revision:
        candidate = SOURCE / str(revision)
        if candidate.exists():
            return candidate
    matches = sorted(SOURCE.glob(f"{meta['doc_id']}*.md"))
    if not matches:
        raise FileNotFoundError(str(meta["doc_id"]))
    return matches[0]


def grade_of(meta: dict[str, object]) -> str:
    label = meta.get("intended_label")
    if label in EXPECTED:
        return str(label)
    if meta.get("document_origin") == "public_real":
        return "S3"
    raise ValueError(f"missing proposed grade: {meta['doc_id']}")


def main() -> int:
    if OUT.exists():
        raise FileExistsError(f"freeze already exists: {OUT}")
    metas = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(SOURCE.glob("*.metadata.json"))]
    if len(metas) != 1000:
        raise RuntimeError(f"freeze requires 1,000 candidates; found {len(metas)}")
    rows = []
    counts = Counter()
    for meta in metas:
        grade = grade_of(meta)
        counts[grade] += 1
        meta_path = SOURCE / f"{meta['doc_id']}.metadata.json"
        doc_path = document_path(meta)
        rows.append({
            "doc_id": meta["doc_id"],
            "proposed_grade": grade,
            "document_origin": meta.get("document_origin"),
            "candidate_status": meta.get("candidate_status", "proposed"),
            "document_path": str(doc_path.relative_to(ROOT)).replace("\\", "/"),
            "document_sha256": sha256_bytes(doc_path.read_bytes()),
            "metadata_sha256": sha256_bytes(meta_path.read_bytes()),
            "source_file_sha256": meta.get("source_file_sha256"),
        })
    if dict(counts) != EXPECTED:
        raise RuntimeError(f"unexpected grade composition: {dict(counts)}")
    payload = {
        "artifact": "proxy_gold_candidates_freeze",
        "version": "v1",
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Proxy Gold candidate snapshot only; no human signoff, no Locked Gold claim, no operational-accuracy claim",
        "total_candidates": len(rows),
        "proposed_grade_distribution": dict(counts),
        "entries": sorted(rows, key=lambda row: str(row["doc_id"])),
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload["manifest_sha256"] = sha256_bytes(canonical)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    print(json.dumps({"total": len(rows), "distribution": dict(counts), "manifest_sha256": payload["manifest_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
