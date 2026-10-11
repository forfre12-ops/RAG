"""Immutable classifier-score snapshots for the expert-review candidate console.

The review corpus and its metadata are source records.  A model run is derived
evidence, so it is kept in a separate JSONL snapshot instead of being written
back into either record.  Readers only accept a complete, hash-bound score
record; a missing, malformed, or stale record is reported as unavailable.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


SCORE_SNAPSHOT_NAME = "candidate_classifier_scores.jsonl"
SCORE_SCHEMA_VERSION = "candidate-classifier-score-v1"
GRADE_CODES = ("TS", "S1", "S2", "S3")
_SUM_TOLERANCE = 0.001


def text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unavailable_score(reason: str = "score_snapshot_missing_or_stale") -> dict[str, str]:
    return {"status": "unavailable", "reason": reason}


def normalize_score_record(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return a narrow, validated public score record, or ``None``.

    This validation intentionally rejects partial distributions and rule-only
    fallbacks.  The reviewer must never see a made-up four-way distribution.
    """
    try:
        if raw.get("schema_version") != SCORE_SCHEMA_VERSION:
            return None
        doc_id = str(raw["doc_id"])
        digest = str(raw["document_sha256"])
        model_version = str(raw["model_version"])
        scored_at = str(raw["scored_at"])
        score_basis = str(raw.get("score_basis") or "")
        predicted_label = str(raw["predicted_label"])
        values = raw["scores"]
    except (KeyError, TypeError, ValueError):
        return None
    if not doc_id or len(digest) != 64 or not model_version or not scored_at:
        return None
    if predicted_label not in GRADE_CODES or score_basis != "serving_pipeline":
        return None
    if not isinstance(values, Mapping) or set(values) != set(GRADE_CODES):
        return None
    try:
        scores = {grade: float(values[grade]) for grade in GRADE_CODES}
    except (TypeError, ValueError):
        return None
    if any(not math.isfinite(value) or value < 0 or value > 1 for value in scores.values()):
        return None
    if not math.isclose(sum(scores.values()), 1.0, abs_tol=_SUM_TOLERANCE):
        return None
    if max(scores, key=scores.get) != predicted_label:
        return None
    return {
        "status": "available",
        "scores": scores,
        "predicted_label": predicted_label,
        "model_version": model_version,
        "scored_at": scored_at,
        "score_basis": score_basis,
        "calibrated": bool(raw.get("calibrated")),
    }


def load_score_index(root: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    """Load the latest valid score record per document from a materialized run."""
    path = root / SCORE_SNAPSHOT_NAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, tuple[str, dict[str, Any]]] = {}
    for line in lines:
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        normalized = normalize_score_record(raw)
        digest = raw.get("document_sha256")
        doc_id = raw.get("doc_id")
        if normalized is None or not isinstance(digest, str) or not isinstance(doc_id, str):
            continue
        result[doc_id] = (digest, normalized)
    return result


def score_for_document(
    index: Mapping[str, tuple[str, dict[str, Any]]], *, doc_id: str, document_sha256: str,
) -> dict[str, Any]:
    entry = index.get(doc_id)
    if entry is None:
        return unavailable_score()
    score_digest, score = entry
    if score_digest != document_sha256:
        return unavailable_score("score_snapshot_stale_for_document")
    return dict(score)


def write_score_snapshot(root: Path, records: Iterable[Mapping[str, Any]]) -> Path:
    """Atomically replace the current materialized score snapshot.

    The caller must calculate the whole requested batch before this function is
    invoked.  A failed batch therefore leaves the last complete snapshot intact.
    """
    root.mkdir(parents=True, exist_ok=True)
    materialized: list[str] = []
    seen: set[str] = set()
    for record in records:
        normalized = normalize_score_record(record)
        doc_id = str(record.get("doc_id") or "")
        if normalized is None or not doc_id or doc_id in seen:
            raise ValueError("invalid or duplicate classifier-score record")
        seen.add(doc_id)
        materialized.append(json.dumps(dict(record), ensure_ascii=False, sort_keys=True))
    if not materialized:
        raise ValueError("refusing to replace score snapshot with no records")
    target = root / SCORE_SNAPSHOT_NAME
    fd, temp_name = tempfile.mkstemp(prefix=f".{SCORE_SNAPSHOT_NAME}.", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(materialized) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target
