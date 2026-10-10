#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""전문가 검수용 모의문서 전달본의 표현 품질을 검사한다.

이 도구는 분류 정확도나 사람 검수 결과를 주장하지 않는다. 전문가가 실제 문서처럼
읽을 수 있는지에 필요한 최소 조건(제목·문서유형 다양성·본문 길이·필수 문서형식의
구성 항목)을 수치로 고정하고, 실패한 전달본을 현재 검수 배치로 승격하지 못하게 하는
사전 게이트다.

기본 기준은 다음 최종 전달본 계약을 검증한다.
  - 정확히 1,000건, 20개 이상의 문서유형
  - 제목에 "모의문서" 같은 생성 표식이 없음
  - 전체 배치에 TS/S1/S2/S3이 모두 존재
  - 본문 700자 이상
  - 요청된 지원서·고용계약서·매뉴얼·계약서가 실제 그 형식의 핵심 항목을 포함

사용:
  python scripts/audit_review_batch_presentation.py \
      --root datasets/proxy_gold/single_document_candidates \
      --batch expert_review_product_1000_20261010 \
      --report reports/expert_review_product_1000_presentation_audit.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio


force_utf8_stdio()

POC = Path(__file__).resolve().parents[1]
GRADES = ("TS", "S1", "S2", "S3")
# KL 요청에서 구체적으로 명시한 문서 형식은 단순 metadata 라벨만으로는 부족하다.
# 아래 항목 중 세 개 이상이 본문에 있어야 형식의 최소 골격을 갖춘 것으로 본다.
FORMAT_SIGNATURES: dict[str, tuple[str, ...]] = {
    "응시자 지원서": ("학력", "경력", "지원동기", "자기소개", "지원자"),
    "고용계약서": ("근로계약", "근로자", "사용자", "임금", "근로시간", "휴일"),
    "매뉴얼": ("절차", "단계", "준수사항", "담당자", "적용범위"),
    "각종 계약서": ("계약당사자", "계약기간", "대금", "비밀유지", "해지", "계약조건"),
}


def _text_path(root: Path, meta: dict[str, Any]) -> Path | None:
    revision = str(meta.get("content_revision_path") or "").strip()
    if revision:
        candidate = (root / revision).resolve()
        if candidate.is_relative_to(root.resolve()) and candidate.is_file():
            return candidate
    doc_id = str(meta.get("doc_id") or "").strip()
    matches = sorted(root.glob(f"{doc_id}_*.md")) if doc_id else []
    return matches[0] if len(matches) == 1 else None


def _has_markdown_table(text: str) -> bool:
    lines = text.splitlines()
    for index in range(len(lines) - 1):
        first, second = lines[index].strip(), lines[index + 1].strip()
        if first.count("|") >= 2 and second.count("|") >= 2:
            cells = second.strip("|").split("|")
            if cells and all(cell.strip().strip(":").replace("-", "") == "" and "-" in cell for cell in cells):
                return True
    return False


def _percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int((len(ordered) - 1) * q))]


def audit(root: Path, batch: str, *, expected_count: int, min_types: int, min_chars: int) -> dict[str, Any]:
    rows: list[tuple[dict[str, Any], str]] = []
    errors: list[dict[str, str]] = []
    for path in sorted(root.glob("*.metadata.json")):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"file": path.name, "error": type(exc).__name__})
            continue
        if str(meta.get("review_batch") or "").strip() != batch:
            continue
        source = _text_path(root, meta)
        if source is None:
            errors.append({"file": path.name, "error": "body_not_unique_or_missing"})
            continue
        rows.append((meta, source.read_text(encoding="utf-8")))

    lengths = [len(text) for _, text in rows]
    by_type = Counter(str(meta.get("doc_format") or "").strip() or "미분류" for meta, _ in rows)
    grade_counts = Counter(str(meta.get("intended_label") or "") for meta, _ in rows)
    title_violations = [
        str(meta.get("doc_id") or "")
        for meta, _ in rows
        if not str(meta.get("title") or "").strip() or "모의문서" in str(meta.get("title") or "")
    ]
    table_docs = [str(meta.get("doc_id") or "") for meta, text in rows if _has_markdown_table(text)]
    too_short = [str(meta.get("doc_id") or "") for meta, text in rows if len(text) < min_chars]
    format_failures: dict[str, list[dict[str, Any]]] = {}
    for doc_format, signatures in FORMAT_SIGNATURES.items():
        failures: list[dict[str, Any]] = []
        matching_rows = [(meta, text) for meta, text in rows if str(meta.get("doc_format") or "").strip() == doc_format]
        if not matching_rows:
            failures.append({"doc_id": None, "reason": "required_format_missing", "matched_terms": []})
        for meta, text in matching_rows:
            matched = [term for term in signatures if term in text]
            if len(matched) < 3:
                failures.append({
                    "doc_id": str(meta.get("doc_id") or ""),
                    "reason": "insufficient_format_signatures",
                    "matched_terms": matched,
                })
        if failures:
            format_failures[doc_format] = failures
    lengths_by_grade = {
        grade: [len(text) for meta, text in rows if str(meta.get("intended_label") or "") == grade]
        for grade in GRADES
    }
    max_median_drift = max(
        (abs(statistics.median(values) - statistics.median(lengths)) / max(statistics.median(lengths), 1)
         for values in lengths_by_grade.values() if values),
        default=0.0,
    )
    checks = {
        "readable_metadata": not errors,
        "exact_document_count": len(rows) == expected_count,
        "document_type_diversity": len(by_type) >= min_types,
        "non_generic_titles": not title_violations,
        "global_grade_coverage": all(grade_counts.get(grade, 0) > 0 for grade in GRADES),
        "minimum_body_length": not too_short,
        "required_format_characteristics": not format_failures,
        "grade_length_balance": max_median_drift <= 0.20,
    }
    return {
        "schema_version": "review-batch-presentation-audit-v1",
        "scope": "presentation readiness of a synthetic reviewer batch; not accuracy, calibration, or real-document evidence",
        "batch": batch,
        "release_ready": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "expected_count": expected_count,
            "min_document_types": min_types,
            "min_characters": min_chars,
            "max_grade_median_length_drift": 0.20,
        },
        "observed": {
            "documents": len(rows),
            "document_types": len(by_type),
            "table_documents": len(table_docs),
            "length": {"min": min(lengths, default=0), "p50": _percentile(lengths, .50), "p90": _percentile(lengths, .90), "max": max(lengths, default=0)},
            "max_grade_median_length_drift": round(max_median_drift, 4),
            "grade_counts": dict(sorted(grade_counts.items())),
            "document_type_counts": dict(sorted(by_type.items())),
        },
        "failures": {
            "metadata": errors[:100],
            "generic_or_missing_title_doc_ids": title_violations[:100],
            "too_short_doc_ids": too_short[:100],
            "required_format_characteristics": format_failures,
            "table_doc_ids_sample": table_docs[:100],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=POC / "datasets" / "proxy_gold" / "single_document_candidates")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--expected-count", type=int, default=1000)
    parser.add_argument("--min-types", type=int, default=20)
    parser.add_argument("--min-chars", type=int, default=700)
    args = parser.parse_args()
    report = audit(args.root.resolve(), args.batch.strip(), expected_count=args.expected_count, min_types=args.min_types, min_chars=args.min_chars)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"batch": report["batch"], "release_ready": report["release_ready"], "checks": report["checks"], "observed": report["observed"]}, ensure_ascii=False, indent=2))
    return 0 if report["release_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
