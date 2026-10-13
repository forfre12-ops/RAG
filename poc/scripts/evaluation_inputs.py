"""Strict, offline input binding for diagnostic classification comparisons."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as fh:
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("not an object")
            except ValueError as exc:
                raise ValueError(f"Invalid JSON object at {path.name}:{number}") from exc
            rows.append(row)
    if not rows:
        raise ValueError(f"Empty evaluation input: {path.name}")
    return rows


def text_of(row: dict) -> str:
    return next((row[k] for k in ("text", "content", "body")
                 if isinstance(row.get(k), str) and row[k].strip()), "")


def normalized_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", "", text).encode()).hexdigest()


def record_binding(records: list[dict], evaluation: list[dict]) -> list[str]:
    """Bind each measured truth to a unique eval document and exact body hash."""
    from measure_four_metrics import _label

    expected = {}
    for row in evaluation:
        text = text_of(row)
        key = str(row.get("doc_id") or row.get("id") or "")
        if not key or not text or key in expected:
            return ["평가 문서 ID·본문 누락 또는 ID 중복"]
        expected[key] = (_label(row), hashlib.sha256(text.encode()).hexdigest())
    seen = Counter(str(row.get("doc_id") or "") for row in records)
    if set(seen) != set(expected) or any(n != 1 for n in seen.values()):
        return ["측정 레코드와 평가셋의 문서 집합 불일치·중복"]
    for row in records:
        label, digest = expected[str(row["doc_id"])]
        if row.get("truth") != label or row.get("text_sha256") != digest:
            return ["측정 정답 또는 본문 해시가 평가셋과 불일치"]
    return []


def check_training(root: Path, manifest_path: Path | None, evaluation: list[dict]) -> dict:
    result = {"checked": False, "overlap": 0, "family_overlap": 0, "total_overlap": 0,
              "manifest_sha256": None, "model_id": None, "paths": [], "reasons": []}
    if manifest_path is None:
        result["reasons"].append("후보 모델 전체 학습 목록 미제공")
        return result
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Training manifest must be an object")
    result["manifest_sha256"] = sha256(manifest_path)
    result["model_id"] = manifest.get("model_id")
    files = manifest.get("files")
    if (manifest.get("schema_version") != "training-inputs-v1" or
            manifest.get("complete") is not True or not result["model_id"] or
            not isinstance(files, list) or not files):
        result["reasons"].append("모델 ID·전체 학습 목록 선언·파일 명세 미완비")
        return result
    hashes, families = set(), set()
    for entry in files:
        path = root / entry["path"]
        if sha256(path) != entry.get("sha256"):
            result["reasons"].append("학습 파일 해시 불일치")
            return result
        result["paths"].append(path)
        for row in read_rows(path):
            text = text_of(row)
            if not text or not row.get("family_id"):
                result["reasons"].append("학습 본문 또는 문서 계열 ID 미완비")
                return result
            hashes.add(normalized_hash(text))
            families.add(str(row["family_id"]))
    if any(not row.get("family_id") for row in evaluation):
        result["reasons"].append("평가 문서 계열 ID 미완비")
        return result
    result["overlap"] = sum(normalized_hash(text_of(row)) in hashes for row in evaluation)
    result["family_overlap"] = sum(str(row["family_id"]) in families for row in evaluation)
    result["total_overlap"] = sum(normalized_hash(text_of(row)) in hashes or
                                  str(row["family_id"]) in families for row in evaluation)
    result["checked"] = True
    return result
