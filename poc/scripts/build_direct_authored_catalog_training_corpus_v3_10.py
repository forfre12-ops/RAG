"""Build Proxy training corpus v3.10 with S2 middle-grade anchoring.

No LLM is called.  v3.9 balanced high-grade and S3 on the development split,
but pushed S2 down to S3.  This version changes only S2 records, making the
"internal but not high-grade" evidence more explicit.
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


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_10.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_10.manifest.json"


S2_ANCHOR_BLOCKS = (
    """## 중간등급 보강 근거

이 문서는 완전 공개자료로 낮추기에는 부족하다. 본문에는 담당 부서가 실제 운영 중 확인한 조건, 적용 전후 비교, 보완 필요 항목이 포함되어 있어 일반 공개 문서만으로 동일한 판단을 바로 재현하기 어렵다. 다만 핵심 설계 원본, 원천 알고리즘, 국가 지정 기술, 대체 불가능한 조건 조합은 포함하지 않는다.

관리 기준은 내부 공유와 외부 요약본 분리다. 원본은 업무 담당자와 관련 검토자에게 공유하고, 외부 전달 시에는 담당자명, 미확정 일정, 세부 비교값을 줄인다. 이 수준은 공개자료보다 높지만 상위 보호 대상보다는 낮은 내부 관리 자료로 본다.
""",
    """## 내부 참고자료 판단

기록에는 공개 절차를 그대로 옮긴 내용만 있는 것이 아니라, 실제 적용 과정에서 확인한 차이와 담당자의 보완 판단이 들어 있다. 그래서 일반 공개자료와 같은 최저 공개 수준으로 낮추면 운영상 필요한 맥락이 사라진다. 반면 문서만으로 경쟁사가 핵심 기술이나 영업 전략을 곧바로 재현할 정도의 고위험 정보는 아니다.

공유 범위는 내부 실무자와 검토자로 제한하되, 반출 금지·지정 인원 열람 같은 최고 수준 통제는 적용하지 않는다. 협력사에는 결론과 일정 중심의 요약본을 전달하고, 원본 비교표는 내부 보관한다.
""",
)


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _insert_after_heading(text: str, block: str) -> str:
    marker = "\n\n## 일반 내부자료 판단\n\n"
    if marker not in text:
        return f"{text.rstrip()}\n\n{block.strip()}\n"
    head, tail = text.split(marker, 1)
    return f"{head}{marker}{block.strip()}\n\n{tail}"


def _first_present(text: str, candidates: tuple[str, ...]) -> str:
    for quote in candidates:
        if quote in text:
            return quote
    raise ValueError(f"no evidence quote found in text: {candidates!r}")


def _s2_evidence_card(text: str) -> dict[str, object]:
    nonpublicity = _first_present(
        text,
        (
            "완전 공개자료로 낮추기에는 부족하다",
            "실제 적용 과정에서 확인한 차이와 담당자의 보완 판단",
        ),
    )
    competitive_value = _first_present(
        text,
        (
            "일반 공개 문서만으로 동일한 판단을 바로 재현하기 어렵다",
            "운영상 필요한 맥락이 사라진다",
        ),
    )
    access_controls = _first_present(
        text,
        (
            "원본은 업무 담당자와 관련 검토자에게 공유",
            "공유 범위는 내부 실무자와 검토자로 제한",
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
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3_9-", "direct-catalog-v3_10-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3_9-family-", "direct-catalog-v3_10-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_high_grade_diverse_v3_10"
    new_row["generation_lineage"] = [
        "generator:codex:direct-authored-catalog-training-v3",
        "transform:codex:high-grade-evidence-frontload-v3_7",
        "transform:codex:s1-boundary-strengthening-v3_8",
        "transform:codex:s2-s3-hard-negative-strengthening-v3_9",
        "transform:codex:s2-middle-grade-anchoring-v3_10",
    ]
    if str(row["label"]) == "S2":
        text = _insert_after_heading(str(new_row["text"]), S2_ANCHOR_BLOCKS[ordinal % len(S2_ANCHOR_BLOCKS)])
        new_row["text"] = text
        new_row["evidence_card"] = _s2_evidence_card(text)
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
        "schema": "direct-authored-catalog-training-v3_10",
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
        "change_summary": "Added S2 middle-grade anchor notes only; high-grade and low-grade records unchanged from v3.9.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
