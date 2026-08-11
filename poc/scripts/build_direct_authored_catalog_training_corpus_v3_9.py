"""Build Proxy training corpus v3.9 with S2/S3 hard-negative strengthening.

No LLM is called.  v3.8 reached zero high-grade underclassification on the
development split, but overclassified S2/S3 aggressively.  This version keeps
high-grade text unchanged and adds realistic low/mid-grade boundary notes to
S2/S3 training-only records.
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

from lloydk.proxy_corpus import validate_proxy_record
from scripts.build_direct_authored_catalog_training_corpus_v3_7 import (
    _sha256,
    _sha256_text,
    _span,
)


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_8.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_8.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.manifest.json"


S2_BLOCKS = (
    """## 일반 내부자료 판단

이 문서는 내부 업무에 참고가 되지만 핵심 설계 원본이나 재현 가능한 비공개 조건표를 담고 있지는 않다. 일부 운영 수치와 일정이 보이더라도 공개된 절차, 표준 점검 방식, 일반 업무 경험으로 대부분 설명 가능하다. 담당자는 결론을 실행 참고로만 사용하고, 별도 최고보호 대상으로 올리지 않는다.

공유는 부서 내부와 관련 협력 범위에서 가능하다. 원본 반출을 무제한 허용하지는 않지만, 문서 자체가 경쟁 우위를 결정하는 조합이나 국가 지정 기술 내용을 포함하지 않으므로 제한 공유 내부자료로 관리한다.
""",
    """## 중간 민감도 검토

본문에는 업무 판단에 필요한 조건과 결과가 있지만, 조건 간 상호작용이나 실패 복구 절차가 독립적인 기술 자산으로 정리되어 있지는 않다. 같은 업무를 수행하는 사람이 공개 매뉴얼과 일반 경험을 함께 보면 주요 결론을 대체로 이해할 수 있다.

관리자는 열람 이력을 남기되 과도한 차단은 적용하지 않는다. 외부 전달 시에는 담당자명과 미확정 일정만 제거하고, 나머지 설명은 요약본으로 공유 가능하다. 상위 보호 검토는 새로운 비공개 조합값이 추가될 때 다시 수행한다.
""",
)


S3_BLOCKS = (
    """## 공개·저민감 판단

이 기록은 공개 기준, 일반 절차, 통상적인 점검 결과를 정리한 문서다. 특정 고객의 비공개 전략, 핵심 설계 수치, 시행착오로 얻은 조건 조합은 포함하지 않는다. 문서만으로 독자적인 기술 구현이나 영업상 우위를 재현하기 어렵다.

공유 제한은 개인정보와 작성 이력 보호 수준에 그친다. 공개자료와 같은 취지의 설명이므로 고등급 보호 대상으로 보지 않고, 운영 참고와 교육 자료로 재사용할 수 있다.
""",
    """## 낮은 민감도 근거

본문의 값과 절차는 이미 알려진 일반 기준을 적용한 결과다. 특정 현장만의 예외 판단이나 재현 가능한 세부 조합이 없고, 실패 이력도 개인이나 고객을 식별할 수 없는 범위에서 요약되어 있다. 따라서 외부 공개자료와 충돌하지 않는다.

보관은 일반 문서함 기준으로 충분하다. 수정 이력은 남기되 반출 금지나 지정 인원 열람 같은 상위 통제는 적용하지 않는다. 필요한 경우 공개 가능한 요약본으로 바로 전환할 수 있다.
""",
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _insert_after_heading(text: str, block: str) -> str:
    marker = "\n\n## 변경 안건과 검토 범위\n\n"
    if marker not in text:
        return f"{text.rstrip()}\n\n{block.strip()}\n"
    head, tail = text.split(marker, 1)
    return f"{head}{marker}{block.strip()}\n\n{tail}"


def _first_present(text: str, candidates: tuple[str, ...]) -> str:
    for quote in candidates:
        if quote in text:
            return quote
    raise ValueError(f"no evidence quote found in text: {candidates!r}")


def _evidence_card(text: str, grade: str) -> dict[str, object]:
    if grade == "S2":
        nonpublicity = _first_present(
            text,
            (
                "핵심 설계 원본이나 재현 가능한 비공개 조건표를 담고 있지는 않다",
                "조건 간 상호작용이나 실패 복구 절차가 독립적인 기술 자산으로 정리되어 있지는 않다",
            ),
        )
        competitive_value = _first_present(
            text,
            (
                "일반 업무 경험으로 대부분 설명 가능하다",
                "주요 결론을 대체로 이해할 수 있다",
            ),
        )
        access_controls = _first_present(
            text,
            (
                "부서 내부와 관련 협력 범위에서 가능하다",
                "열람 이력을 남기되 과도한 차단은 적용하지 않는다",
            ),
        )
    else:
        nonpublicity = _first_present(
            text,
            (
                "공개 기준, 일반 절차, 통상적인 점검 결과",
                "이미 알려진 일반 기준을 적용한 결과",
            ),
        )
        competitive_value = _first_present(
            text,
            (
                "독자적인 기술 구현이나 영업상 우위를 재현하기 어렵다",
                "외부 공개자료와 충돌하지 않는다",
            ),
        )
        access_controls = _first_present(
            text,
            (
                "공유 제한은 개인정보와 작성 이력 보호 수준에 그친다",
                "보관은 일반 문서함 기준으로 충분하다",
            ),
        )
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
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3_8-", "direct-catalog-v3_9-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3_8-family-", "direct-catalog-v3_9-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_high_grade_diverse_v3_9"
    new_row["generation_lineage"] = [
        "generator:codex:direct-authored-catalog-training-v3",
        "transform:codex:high-grade-evidence-frontload-v3_7",
        "transform:codex:s1-boundary-strengthening-v3_8",
        "transform:codex:s2-s3-hard-negative-strengthening-v3_9",
    ]
    grade = str(row["label"])
    if grade == "S2":
        text = _insert_after_heading(str(new_row["text"]), S2_BLOCKS[ordinal % len(S2_BLOCKS)])
        new_row["text"] = text
        new_row["evidence_card"] = _evidence_card(text, grade)
    elif grade == "S3":
        text = _insert_after_heading(str(new_row["text"]), S3_BLOCKS[ordinal % len(S3_BLOCKS)])
        new_row["text"] = text
        new_row["evidence_card"] = _evidence_card(text, grade)
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
        "schema": "direct-authored-catalog-training-v3_9",
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
        "change_summary": "Added S2/S3 hard-negative notes only; high-grade text unchanged from v3.8.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
