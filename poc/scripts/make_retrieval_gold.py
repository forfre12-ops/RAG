"""Create a deterministic doc-id based retrieval gold set."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def _load_records(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    raw = json.loads(text)
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return list(raw.values())
    return []


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="datasets/oss_eval_queries.json")
    ap.add_argument("--out", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--n", type=int, default=80)
    ap.add_argument("--per-grade-cap", type=int, default=25)
    args = ap.parse_args()

    records = [
        r for r in _load_records(Path(args.source))
        if (r.get("query") or r.get("text")) and (r.get("expected_doc_id") or r.get("relevant_doc_ids"))
    ]
    by_grade: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_grade[r.get("expected_grade") or "NA"].append(r)

    selected: list[dict] = []
    grades = sorted(by_grade)
    while len(selected) < args.n and any(by_grade.values()):
        advanced = False
        for grade in grades:
            bucket = by_grade[grade]
            already = sum(1 for r in selected if r.get("expected_grade") == grade)
            if not bucket or already >= args.per_grade_cap:
                continue
            r = bucket.pop(0)
            rel = r.get("relevant_doc_ids") or [r.get("expected_doc_id")]
            selected.append(
                {
                    "query_id": f"retrieval_gold_{len(selected) + 1:04d}",
                    "text": r.get("query") or r.get("text"),
                    "expected_grade": r.get("expected_grade", ""),
                    "expected_doc_id": r.get("expected_doc_id") or rel[0],
                    "relevant_doc_ids": [x for x in rel if x],
                    "source": r.get("source", Path(args.source).name),
                    "source_doc_title": r.get("source_doc_title", ""),
                    "label_source": "oss_eval_doc_id",
                }
            )
            advanced = True
            if len(selected) >= args.n:
                break
        if not advanced:
            break

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in selected) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(out), "lines": len(selected)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
