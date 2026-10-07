# -*- coding: utf-8 -*-
"""poc/datasets/ 아래 전체 jsonl을 스캔해 document_origin(실문서/합성) 분포를 센다.

"어느 데이터가 가장 실문서에 가까운가"에 손으로 몇 개 폴더만 골라 답하지 않기 위한 도구.
전수 스캔 + 결과를 JSON으로 남겨 재현 가능하게 한다.

사용:
    PYTHONIOENCODING=utf-8 python scripts/audit_all_dataset_realism.py > reports/DATASET_REALISM_AUDIT.json
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASETS_DIR = ROOT / "datasets"

ORIGIN_FIELDS = ("document_origin", "doc_origin", "origin", "source_type")
REAL_VALUES = {"customer_real", "public_real", "real"}


def scan_file(path: Path) -> dict:
    total = 0
    origin_counter: Counter = Counter()
    label_source_counter: Counter = Counter()
    parse_errors = 0
    sample_ids = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                parse_errors += 1
                continue
            if not isinstance(row, dict):
                continue
            total += 1
            origin_val = None
            for f in ORIGIN_FIELDS:
                if f in row and row[f]:
                    origin_val = str(row[f])
                    break
            origin_counter[origin_val or "(no_origin_field)"] += 1
            ls = row.get("label_source")
            if ls:
                label_source_counter[str(ls)] += 1
            if len(sample_ids) < 3:
                sample_ids.append(row.get("doc_id") or row.get("id") or "(no_id)")
    return {
        "total_rows": total,
        "parse_errors": parse_errors,
        "origin_distribution": dict(origin_counter),
        "label_source_distribution": dict(label_source_counter),
        "sample_doc_ids": sample_ids,
    }


def main() -> int:
    folders = sorted(p for p in DATASETS_DIR.iterdir() if p.is_dir())
    report = {"root": str(DATASETS_DIR), "folder_count": len(folders), "folders": {}}

    for folder in folders:
        jsonl_files = sorted(folder.glob("*.jsonl"))
        if not jsonl_files:
            report["folders"][folder.name] = {"jsonl_files": 0, "status": "empty_or_non_jsonl"}
            continue
        folder_total = 0
        folder_origin: Counter = Counter()
        folder_label_source: Counter = Counter()
        files_detail = {}
        for jf in jsonl_files:
            try:
                r = scan_file(jf)
            except OSError as exc:
                files_detail[jf.name] = {"error": str(exc)}
                continue
            files_detail[jf.name] = r
            folder_total += r["total_rows"]
            folder_origin.update(r["origin_distribution"])
            folder_label_source.update(r["label_source_distribution"])

        real_rows = sum(v for k, v in folder_origin.items() if k in REAL_VALUES)
        real_ratio = round(real_rows / folder_total, 4) if folder_total else 0.0

        report["folders"][folder.name] = {
            "jsonl_files": len(jsonl_files),
            "total_rows": folder_total,
            "origin_distribution": dict(folder_origin),
            "label_source_distribution": dict(folder_label_source),
            "real_rows": real_rows,
            "real_ratio": real_ratio,
            "files": files_detail,
        }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
