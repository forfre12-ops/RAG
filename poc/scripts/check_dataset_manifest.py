"""Validate dataset_v1.0.yaml against local dataset files.

This catches stale counts without rewriting the human-maintained lineage notes.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import yaml


def _jsonl_stats(path: Path) -> tuple[int, dict[str, int], dict[str, int]]:
    n = 0
    grades: Counter = Counter()
    sources: Counter = Counter()
    if not path.exists():
        return 0, {}, {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        n += 1
        grade = row.get("label") or row.get("expected_grade")
        if grade:
            grades[str(grade)] += 1
        source = row.get("label_source") or row.get("source")
        if source:
            sources[str(source)] += 1
    return n, dict(sorted(grades.items())), dict(sorted(sources.items()))


def _expect_equal(errors: list[str], name: str, actual, expected) -> None:
    if isinstance(actual, dict) and isinstance(expected, dict):
        actual = {key: actual.get(key, 0) for key in expected}
    if actual != expected:
        errors.append(f"{name}: expected {expected!r}, got {actual!r}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="datasets/manifests/dataset_v1.0.yaml")
    args = ap.parse_args()

    manifest_path = Path(args.manifest)
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []

    retrain = data["datasets"].get("labeled_p1_retrain_v3", {})
    retrain_path = Path(str(retrain.get("path", "")).removeprefix("poc/"))
    n, grades, _ = _jsonl_stats(retrain_path)
    _expect_equal(errors, "labeled_p1_retrain_v3.total_lines", n, retrain.get("total_lines"))
    _expect_equal(errors, "labeled_p1_retrain_v3.grade_distribution", grades, retrain.get("grade_distribution"))

    gold = data.get("gold", {})
    cls_gold = gold.get("classification_gold_real", {})
    cls_path = Path(str(cls_gold.get("path", "")).removeprefix("poc/"))
    n, grades, sources = _jsonl_stats(cls_path)
    _expect_equal(errors, "classification_gold_real.lines", n, cls_gold.get("lines"))
    _expect_equal(errors, "classification_gold_real.grade_distribution", grades, cls_gold.get("grade_distribution"))
    _expect_equal(errors, "classification_gold_real.label_source_distribution", sources, cls_gold.get("label_source_distribution"))

    ret_gold = gold.get("retrieval_gold", {})
    ret_path = Path(str(ret_gold.get("path", "")).removeprefix("poc/"))
    n, _, _ = _jsonl_stats(ret_path)
    _expect_equal(errors, "retrieval_gold.lines", n, ret_gold.get("lines"))

    if errors:
        print("[manifest] FAIL")
        for err in errors:
            print(f"- {err}")
        return 1
    print("[manifest] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
