"""Strict inputs for leakage diagnostics only; never an ML eligibility grant."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from evaluation_inputs import read_rows as read_rows, sha256

GRADES = ("TS", "S1", "S2", "S3")
DEFAULT_POOL = Path(__file__).resolve().parents[1] / "datasets/proxy_gold/single_document_candidates"


def _check(ok, message):
    if not ok:
        raise ValueError(message)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_hash(row):
    return _hash(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


class _AuditSnapshot:
    """Bind parsing to the bytes actually read, then detect source changes."""

    def __init__(self):
        self.digests: dict[Path, str] = {}

    def text(self, path: Path) -> str:
        path = path.resolve()
        payload = path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        _check(path not in self.digests or self.digests[path] == digest, f"Audit input changed: {path.name}")
        self.digests[path] = digest
        return payload.decode("utf-8")

    @staticmethod
    def _object(pairs):
        result = {}
        for key, value in pairs:
            _check(key not in result, "Duplicate JSON key in audit input")
            result[key] = value
        return result

    def json(self, path: Path):
        return json.loads(self.text(path), object_pairs_hook=self._object)

    def rows(self, path: Path) -> list[dict]:
        result = []
        for number, line in enumerate(self.text(path).splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line, object_pairs_hook=self._object)
            _check(isinstance(row, dict), f"Invalid JSON object at {path.name}:{number}")
            result.append(row)
        _check(bool(result), f"Empty evaluation input: {path.name}")
        return result

    def verify(self):
        for path, digest in self.digests.items():
            _check(sha256(path) == digest, f"Audit input changed: {path.name}")


def verify_audit_snapshot(metadata: dict) -> None:
    """Check again after long-running diagnostics, without changing originals."""
    root = Path(metadata["pool"])
    for item in metadata["files"]:
        path = root / item["path"]
        _check(sha256(path) == item["sha256"], f"Audit input changed during measurement: {path.name}")


def _unique(rows, name):
    result = {}
    for row in rows:
        key = row.get("doc_id")
        _check(isinstance(key, str) and bool(key.strip()) and key not in result, f"Missing/duplicate ID: {name}")
        result[key] = row
    return result


def load_legacy_pool(pool: Path, *, _snapshot: _AuditSnapshot | None = None) -> tuple[list[dict], list[Path], int]:
    """Preserve content_revision_path priority, but never skip malformed inputs."""
    metadata = sorted(pool.glob("*.metadata.json"))
    snapshot = _snapshot or _AuditSnapshot()
    _check(bool(metadata), "No supported candidate files (expected paired JSONL or *.metadata.json)")
    rows, files, excluded = [], [], 0
    seen = set()
    for mp in metadata:
        meta = snapshot.json(mp)
        _check(isinstance(meta, dict), f"Invalid metadata object: {mp.name}")
        doc_id = str(meta.get("doc_id") or "")
        _check(bool(doc_id.strip()) and doc_id not in seen, "Missing/duplicate legacy ID")
        seen.add(doc_id)
        files.append(mp)
        match = re.search(r"-(TS|S1|S2|S3)-", doc_id)
        label = match.group(1) if match else meta.get("intended_label")
        if label is None or label == "":
            # Uploaded, not-yet-labelled documents are not malformed grade cases.
            # Count exclusions explicitly; do not read their body or infer a grade.
            excluded += 1
            continue
        _check(label in GRADES, f"Invalid grade: {mp.name}")
        if meta.get("intended_label"):
            _check(meta["intended_label"] == label, f"ID/metadata grade conflict: {mp.name}")
        revision = str(meta.get("content_revision_path") or "").strip()
        if revision:
            src = (pool / revision).resolve()
        else:
            candidates = [p for p in sorted(pool.glob(mp.name.replace(".metadata.json", "") + "*.md"))
                          if not p.name.endswith(".cleaned.md")]
            _check(len(candidates) == 1, f"Ambiguous/missing body: {mp.name}")
            src = candidates[0].resolve()
        _check(src.is_relative_to(pool.resolve()), "Body path escapes pool")
        text = snapshot.text(src)
        _check(bool(text.strip()), f"Empty body: {mp.name}")
        rows.append({**meta, "doc_id": doc_id, "text": text, "label": label,
                     "origin": str(meta.get("document_origin") or "unknown")})
        files.append(src)
    _unique(rows, "legacy")
    if _snapshot is None:
        snapshot.verify()
    return rows, files, excluded


def _paired(directory: Path, view: str, snapshot: _AuditSnapshot) -> tuple[list[dict], int, list[Path]]:
    files = [directory / "inputs.jsonl", directory / "answers.candidate.jsonl"]
    docs, answers = (_unique(snapshot.rows(p), p.name) for p in files)
    policies = {(r.get("policy_version"), r.get("policy_sha256")) for r in docs.values()}
    _check(len(policies) == 1, "Mixed policy versions in one input partition")
    _check(set(docs) == set(answers), "Input/answer ID coverage mismatch")
    rows, excluded = [], 0
    for key, doc in docs.items():
        answer = answers[key]
        text = doc.get("text")
        _check(isinstance(text, str) and bool(text.strip()), "Empty/invalid body")
        _check(_hash(text) == doc.get("text_sha256"), "Body hash mismatch")
        for field in ("text_sha256", "family_id", "policy_version", "policy_sha256"):
            _check(bool(doc.get(field)) and doc[field] == answer.get(field), f"Answer binding mismatch: {field}")
        _check(not any(k in doc for k in ("label", "expected_grade", "rationale", "facts", "views")),
               "Answer fields mixed into input")
        if "views" in answer:
            _check(_record_hash(doc) == answer.get("input_record_sha256"), "Input record binding mismatch")
            context = doc.get("context")
            _check(isinstance(context, dict) and _record_hash(context) == doc.get("context_sha256")
                   == answer.get("context_sha256"), "Context binding mismatch")
            if view == "body_plus_synthetic_context":
                text += "\n\n[명시적 가상 조건]\n" + json.dumps(
                    context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            target = answer["views"].get(view)
            _check(isinstance(target, dict) and target.get("input_sha256") == _hash(text), "View input binding mismatch")
        else:
            _check(view == "body_only", "This pack has no separate context input view")
            target = answer
        grade, status = target.get("expected_grade"), target.get("expected_status")
        if status in {"needs_evidence", "needs_policy_review"}:
            _check(grade is None, "Review truth must not have a grade")
            excluded += 1
            continue
        _check(status == "recommended" and grade in GRADES, "Invalid recommendation/status")
        _check(target.get("grade_metric_eligible", True) is True, "Grade eligibility contradicts recommendation")
        rows.append({**doc, "text": text, "label": grade, "origin": "policy_fixture"})
    return rows, excluded, files


def load_audit_pool(pool: Path | None = None, *, split: str = "development", view: str = "body_only") -> tuple[list[dict], dict]:
    pool = (pool or DEFAULT_POOL).resolve()
    _check(pool.is_dir(), "Pool directory does not exist")
    _check(split in {"development", "sealed_candidate", "all"}, "Unknown split")
    _check(view in {"body_only", "body_plus_synthetic_context"}, "Unknown input view")
    rows, files, excluded = [], [], 0
    snapshot = _AuditSnapshot()
    partitioned = any((pool / s).is_dir() for s in ("development", "sealed_candidate"))
    paired = partitioned or any((pool / name).is_file() for name in ("inputs.jsonl", "answers.candidate.jsonl"))
    _check(not (paired and list(pool.glob("*.metadata.json"))), "Ambiguous mixed pool formats")
    if paired:
        selected = ("development", "sealed_candidate") if split == "all" else (split,)
        directories = [pool / s for s in selected] if partitioned else [pool]
        for directory in directories:
            batch, review_count, batch_files = _paired(directory, view, snapshot)
            rows.extend(batch)
            files.extend(batch_files)
            excluded += review_count
        # Verify selected input/answer bytes, not unselected sealed bodies.
        manifest_path = next((p / "manifest.json" for p in (pool, pool.parent)
                              if (p / "manifest.json").is_file()), None)
        if manifest_path:
            manifest = snapshot.json(manifest_path)
            expected = {item["path"]: item["sha256"] for item in manifest["files"]}
            _check(len(expected) == len(manifest["files"]), "Duplicate manifest file entry")
            for path in files:
                key = path.relative_to(manifest_path.parent).as_posix()
                _check(expected.get(key) == snapshot.digests[path.resolve()], "Selected input/answer manifest hash mismatch")
            if manifest.get("policy_version"):
                _check(all(r["policy_version"] == manifest["policy_version"]
                           and r["policy_sha256"] == manifest["policy_sha256"] for r in rows),
                       "Manifest policy binding mismatch")
            files.append(manifest_path)
        input_format = "paired_jsonl"
    else:
        _check(split == "development" and view == "body_only", "Legacy pool has no explicit split/context views")
        rows, files, excluded = load_legacy_pool(pool, _snapshot=snapshot)
        input_format = "legacy_metadata"
    _unique(rows, "selected grade candidates")
    _check(bool(rows), f"No grade candidates: total={len(rows) + excluded}, excluded_review={excluded}; NOT a successful check")
    snapshot.verify()
    return rows, {"input_format": input_format, "pool": str(pool),
                  "split": split if partitioned else "direct_directory", "input_view": view,
                  "n_input_rows": len(rows) + excluded, "n_grade_candidates": len(rows),
                  "excluded_review_rows": excluded, "grade_distribution": dict(Counter(r["label"] for r in rows)),
                  "exclusion_reason": "unlabeled" if input_format == "legacy_metadata" else "review_required",
                  "files": [{"path": str(p.relative_to(pool)) if p.is_relative_to(pool) else str(p),
                             "sha256": snapshot.digests[p.resolve()]} for p in files],
                  "claim_scope": "shortcut_diagnostic_only", "model_quality_measured": False,
                  "customer_accuracy_claim_allowed": False, "training_eligibility_granted": False}
