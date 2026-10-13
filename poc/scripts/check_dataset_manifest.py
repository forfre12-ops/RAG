"""Validate dataset_v1.0.yaml against local dataset files.

This catches stale counts without rewriting the human-maintained lineage notes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import yaml

# 판례-프록시 디오염 술어(단일 소스). scripts/ 를 경로에 넣어 재사용 — 술어 중복/drift 방지.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from depollute_court_proxy_gold import is_court_proxy_overlabel  # noqa: E402


def _load_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


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
    # bijective: 부분집합 필터를 쓰지 않는다 — 매니페스트가 누락한 키(예: label_source에
    # curated_scenario)나 오타 키까지 잡아 조용한 lineage drift를 막는다.
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

    # gold 불변식: 공개 판례 프록시 과라벨이 남아있으면 안 된다 — 디오염 재현성 트립와이어.
    # (ungoverned 수기/기계 in-place 정정이나 새 프록시 유입을 잡는다. 재생성: depollute_court_proxy_gold.py)
    proxy_pending = [r for r in _load_rows(cls_path) if is_court_proxy_overlabel(r)]
    if proxy_pending:
        errors.append(
            f"classification_gold_real.court_proxy_overlabel: {len(proxy_pending)} un-depolluted "
            "rows (run scripts/depollute_court_proxy_gold.py)"
        )

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
