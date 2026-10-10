#!/usr/bin/env python
"""기존 1,001건 모의문서에서 검수용 1,000건 고정 패키지를 만든다.

원본은 절대 수정하지 않는다. 품질 감사에서 발견된 상황문 메아리 네 건만 의미를
바꾸지 않는 범위에서 고치고, 1,001번째 레코드는 내용/등급과 무관한 안정 정렬 규칙으로
제외한다. 결과물은 검수자용 본문과 내부 manifest를 분리한 상태로 남는다.

사용:
    python poc/scripts/build_curated_mock1000_review_batch.py \
        --output poc/datasets/expert_review_mock1000_curated_20261010
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

POC = Path(__file__).resolve().parents[1]
SRC = POC / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from koipa.services.synthetic_import_quality import (  # noqa: E402
    audit_records,
    load_metadata_index,
    load_records,
    merge_metadata,
)


DEFAULT_INPUT = POC / "datasets" / "expert_review_mock1000_20261002"
DEFAULT_OUTPUT = POC / "datasets" / "expert_review_mock1000_curated_20261010"
TARGET_COUNT = 1000

# 2026-10-10 import audit에서 확인한 네 건. 고정 등급 상황문을 그대로 되풀이한
# 낱말만 바꾸며, 나머지 업무 사실·문서 형식·잠정 라벨은 바꾸지 않는다.
REWRITES: dict[str, tuple[str, str, str]] = {
    "MK-0139": (
        "생산본부 내 팀장급이 분담하여 처리하기로 하였으며",
        "생산본부 내 각 업무 책임자가 나누어 처리하기로 하였으며",
        "prompt_echo:팀장급",
    ),
    "MK-0715": (
        "원가 구조상 저희가 4% 선까지만 가능할 것 같습니다.",
        "현재 비용 조건상 저희가 4% 선까지만 가능할 것 같습니다.",
        "prompt_echo:원가 구조",
    ),
    "MK-0867": (
        "저희 쪽에서 대응 원가 구조를 다시 짰습니다.",
        "저희 쪽에서 대응 비용 산정안을 다시 정리했습니다.",
        "prompt_echo:원가 구조",
    ),
    "MK-0905": (
        "원가 구조 자체가 바뀌었다고 보시면 될 것 같습니다.",
        "비용 항목의 구성 자체가 달라졌다고 보시면 될 것 같습니다.",
        "prompt_echo:원가 구조",
    ),
}


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dump_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    docs_path = input_dir / "reviewer_documents.jsonl"
    internal_path = input_dir / "internal_manifest.jsonl"
    docs = _load_jsonl(docs_path)
    internal = _load_jsonl(internal_path)
    by_id = {str(row.get("review_id")): row for row in docs}
    meta_by_id = {str(row.get("review_id")): row for row in internal}
    if len(docs) != len(by_id) or len(internal) != len(meta_by_id):
        raise ValueError("원본 review_id에 중복이 있다")
    if set(by_id) != set(meta_by_id):
        raise ValueError("원본 reviewer_documents와 internal_manifest의 review_id가 일치하지 않는다")

    ids = sorted(by_id, key=lambda value: int(value.removeprefix("MK-")))
    expected_ids = [f"MK-{index:04d}" for index in range(1, len(ids) + 1)]
    if ids != expected_ids or len(ids) != TARGET_COUNT + 1:
        raise ValueError(f"이 도구는 연속된 MK-0001..MK-1001 원본만 처리한다 (got {len(ids)} rows)")

    selected_ids = ids[:TARGET_COUNT]
    excluded_ids = ids[TARGET_COUNT:]
    curated_docs: list[dict[str, Any]] = []
    curated_internal: list[dict[str, Any]] = []
    replacements: list[dict[str, str]] = []
    for review_id in selected_ids:
        doc = dict(by_id[review_id])
        meta = dict(meta_by_id[review_id])
        if review_id in REWRITES:
            before, after, reason = REWRITES[review_id]
            text = str(doc.get("text") or "")
            if text.count(before) != 1:
                raise ValueError(f"{review_id}: expected source phrase exactly once")
            doc["text"] = text.replace(before, after)
            meta["char_count"] = len(doc["text"])
            meta["source"] = "quality_prompt_echo_rewrite_20261010"
            meta["label_basis"] = f"{meta.get('label_basis', '')}; 품질 감사 {reason} 표현 교체"
            replacements.append({"review_id": review_id, "reason": reason, "before": before, "after": after})
        curated_docs.append(doc)
        curated_internal.append(meta)

    output_dir.mkdir(parents=True, exist_ok=False)
    out_docs = output_dir / "reviewer_documents.jsonl"
    out_internal = output_dir / "internal_manifest.jsonl"
    _dump_jsonl(out_docs, curated_docs)
    _dump_jsonl(out_internal, curated_internal)

    records, errors = load_records([out_docs])
    metadata, metadata_errors = load_metadata_index(out_internal)
    report = audit_records(merge_metadata(records, metadata))
    report["read_errors"] = [*errors, *metadata_errors]
    if report["summary"]["documents"] != TARGET_COUNT:
        raise AssertionError("curated output count drifted")
    if not report["summary"]["release_ready"]:
        raise AssertionError(f"curated output did not pass quality audit: {report['issue_counts']}")
    quality_path = output_dir / "QUALITY_AUDIT.json"
    quality_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": "curated-mock1000-v1",
        "source": {
            "directory": str(input_dir),
            "reviewer_documents_sha256": _sha256(docs_path),
            "internal_manifest_sha256": _sha256(internal_path),
            "documents": len(docs),
        },
        "selection": {
            "target_documents": TARGET_COUNT,
            "rule": "stable review_id ascending prefix; independent of grade, content, and audit result",
            "excluded_review_ids": excluded_ids,
        },
        "replacements": replacements,
        "output": {
            "reviewer_documents_sha256": _sha256(out_docs),
            "internal_manifest_sha256": _sha256(out_internal),
            "quality_report_sha256": _sha256(quality_path),
            "quality_release_ready": report["summary"]["release_ready"],
        },
    }
    (output_dir / "CURATION_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "README.md").write_text(
        "# 전문가 검수용 모의문서 1,000건\n\n"
        "이 패키지는 원본 1,001건을 수정하지 않고 만든 별도 검수 배치다. 검수자에게는 "
        "`reviewer_documents.jsonl`만 제공하며, `internal_manifest.jsonl`과 `CURATION_MANIFEST.json`은 "
        "관리자 전용이다.\n\n"
        "- 배치 수: 1,000건(20명 × 50건)\n"
        "- 초과 1건: `MK-1001`은 review_id 안정 정렬 기준 마지막 행으로 제외했다. 이 선택은 등급·본문·품질과 무관하다.\n"
        "- 교체 4건: 고정 상황문 메아리만 문맥 보존 편집했다. 상세는 `CURATION_MANIFEST.json` 참조.\n"
        "- 품질 게이트: `QUALITY_AUDIT.json`의 `release_ready=true` 확인.\n\n"
        "합성 문서는 생성기 일관성·회귀시험 및 전문가 검수용이다. 독립 검토와 잠금 홀드아웃 평가 전에는 "
        "실문서 정확도의 근거로 사용하지 않는다.\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    manifest = build(args.input, args.output)
    print(json.dumps({"output": str(args.output), **manifest["selection"], **manifest["output"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
