"""Build Proxy training corpus v3.8 with S1 boundary strengthening.

No LLM is called.  v3.7 fixed TS recall direction, but development checks still
showed weak S1 recall.  This version leaves TS/S2/S3 text unchanged and adds a
short S1 boundary note that makes the "restricted but not nationally critical"
middle-high grade visible in the document body.
"""

from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from koipa.proxy_corpus import validate_proxy_record
from scripts.build_direct_authored_catalog_training_corpus_v3_7 import (
    _sha256,
    _sha256_text,
    _span,
)


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_7.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_7.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_8.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_8.manifest.json"


S1_BOUNDARY_BLOCKS = (
    """## 경계 판단 메모

이 기록은 공개 보도자료나 표준 매뉴얼만으로는 설명되지 않는 내부 적용 기준을 포함한다. 다만 국가중점기술 지정 자료나 핵심 설계 원본처럼 즉시 최고보호 대상으로 올릴 수준은 아니며, 현장 적용 조건과 예외 처리 순서가 결합되어 업무상 제한 공유가 필요한 범위로 본다.

검토자는 공개 가능한 결론, 내부 판단 근거, 재현 가능한 세부 조건을 분리했다. 원문 조건표와 실패 보정 이력은 담당 부서 안에서만 열람하고, 외부 설명에는 목적과 결과만 남긴다. 이 분리 기준이 지켜지지 않으면 같은 문서라도 상위 보호 검토 대상으로 다시 올린다.
""",
    """## 제한 공유 판단

문서에는 일반 원칙을 실제 운영에 맞게 좁힌 조건과 예외 처리 기준이 함께 들어 있다. 이 정보는 외부 공개자료의 단순 요약이 아니며, 같은 업무명을 아는 사람도 내부 이력 없이 곧바로 재현하기 어렵다. 반면 핵심 소스, 원천 알고리즘, 국가 지정 기술 목록 자체는 포함하지 않는다.

따라서 원본은 담당자, 검토자, 승인자 범위에서만 공유한다. 협력사 또는 다른 부서에는 결론과 적용 일정만 제공하고, 시행착오 표와 조건별 비교값은 별도 승인 없이는 전달하지 않는다. 접근 권한 변경은 승인 이력에 남긴다.
""",
    """## 상향·하향 배제 근거

이 문서는 단순한 공개 참고자료로 낮추기 어렵다. 적용 조건, 예외 순서, 검증 결과가 묶여 있어 실제 업무 품질과 비용에 영향을 준다. 동시에 최고보호 등급으로 볼 핵심 설계도, 원천기술 전체, 대체 불가능한 국가 지정 기술 원문은 포함하지 않는다.

관리 기준은 제한 열람, 요약본 분리, 원본 반출 금지의 세 가지다. 원문을 열람한 사람과 수정한 사람은 이력에 남기고, 외부 전달본에는 판단 근거 중 재현 가능한 값과 순서를 제거한다. 남은 의문은 보류 항목으로 관리한다.
""",
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _insert_after_heading(text: str, block: str) -> str:
    marker = "\n\n## 판정 근거 보강\n\n"
    if marker not in text:
        return f"{text.rstrip()}\n\n{block.strip()}\n"
    head, tail = text.split(marker, 1)
    return f"{head}{marker}{block.strip()}\n\n{tail}"


def _s1_evidence_card(text: str) -> dict[str, object]:
    nonpublicity = "공개 보도자료나 표준 매뉴얼만으로는 설명되지 않는 내부 적용 기준"
    if nonpublicity not in text:
        nonpublicity = "외부 공개자료의 단순 요약이 아니며"
    if nonpublicity not in text:
        nonpublicity = "단순한 공개 참고자료로 낮추기 어렵다"

    competitive_value = "실제 업무 품질과 비용에 영향을 준다"
    if competitive_value not in text:
        competitive_value = "곧바로 재현하기 어렵다"
    if competitive_value not in text:
        competitive_value = "업무상 제한 공유가 필요한 범위"

    access_controls = "원문 조건표와 실패 보정 이력은 담당 부서 안에서만 열람"
    if access_controls not in text:
        access_controls = "담당자, 검토자, 승인자 범위에서만 공유"
    if access_controls not in text:
        access_controls = "제한 열람, 요약본 분리, 원본 반출 금지"

    return {
        "schema": "proxy-evidence-v1",
        "text_sha256": _sha256_text(text.strip()),
        "factors": {
            "nonpublicity": {"basis": "text", "spans": [_span(text, nonpublicity)]},
            "competitive_value": {
                "basis": "text",
                "spans": [_span(text, competitive_value)],
            },
            "access_controls": {
                "basis": "text",
                "spans": [_span(text, access_controls)],
            },
        },
    }


def _rewrite_record(row: dict[str, object], ordinal: int) -> dict[str, object]:
    new_row = dict(row)
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3_7-", "direct-catalog-v3_8-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3_7-family-", "direct-catalog-v3_8-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_high_grade_diverse_v3_8"
    new_row["generation_lineage"] = [
        "generator:codex:direct-authored-catalog-training-v3",
        "transform:codex:high-grade-evidence-frontload-v3_7",
        "transform:codex:s1-boundary-strengthening-v3_8",
    ]
    if str(row["label"]) == "S1":
        text = _insert_after_heading(str(new_row["text"]), S1_BOUNDARY_BLOCKS[ordinal % len(S1_BOUNDARY_BLOCKS)])
        new_row["text"] = text
        new_row["evidence_card"] = _s1_evidence_card(text)
    return new_row


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    source_rows = _read_jsonl(SOURCE)
    rows = [_rewrite_record(row, idx) for idx, row in enumerate(source_rows)]
    failures = [
        str(row["doc_id"])
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="training").ok
    ]
    if failures:
        raise RuntimeError(f"invalid records: {failures[:20]}")
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    lengths = [len(str(row["text"])) for row in rows]
    manifest = {
        "schema": "direct-authored-catalog-training-v3_8",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256(SOURCE.read_bytes()),
        "source_manifest_sha256": _sha256(SOURCE_MANIFEST.read_bytes()),
        "records": len(rows),
        "records_sha256": _sha256(payload),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "text_length": {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 2),
        },
        "training_only": True,
        "no_llm_generation": True,
        "evaluation_case_ledger": "v2_1_development_used_for_direction_only; v2_1_final_retired_for_future_claims",
        "change_summary": "Added S1 boundary notes only; TS/S2/S3 text unchanged from v3.7.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
