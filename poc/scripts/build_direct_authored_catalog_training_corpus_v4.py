"""Build Proxy training corpus v4 with broader document-style exposure.

No LLM or model is called.  v3.9 is preserved as the source population so the
scenario/factor-profile catalog contract remains intact.  This version appends
new directly-authored review-style sections to every synthetic training record,
so the classifier sees document language closer to the independent v2.2
development suite without using v2.2 records for training.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from koipa.proxy_corpus import validate_proxy_record


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_9.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v4_3.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v4_3.manifest.json"

GRADE_EXTENSIONS = {
    "TS": (
        "추가 검토 기록",
        (
            "이 문서는 단일 수치보다 조합의 재현 가능성을 중점으로 본다. 공개된 기준만으로는 "
            "같은 결과를 다시 만들 수 없고, 실패 이력과 전환 순서와 예외 복구 기준이 함께 있어야 "
            "현장 판단이 가능하다. 열람 범위는 지정 담당자로 제한하고, 내려받기와 반출은 승인 "
            "기록을 남긴 뒤 종료 시점에 권한을 회수한다."
        ),
        (
            "외부에 알려질 경우 단순한 운영 참고가 아니라 납기, 원가, 복구 시간, 고객 대응 "
            "우위에 직접 영향을 줄 수 있다. 검토자는 수치가 좋아졌다는 사실만 보지 않고, 그 "
            "수치를 만들어낸 내부 조합이 공개 자료로 대체 가능한지 별도로 확인한다."
        ),
    ),
    "S1": (
        "추가 경계 검토",
        (
            "현장 조건과 예외 처리 순서는 외부 공개본에 포함되지 않는다. 다만 관리 체계는 "
            "프로젝트 저장소와 업무 공간 중심으로 운영되어, 수신자별 반출 승인과 정기 권한 "
            "회수까지 완전하게 굳어진 상태는 아니다."
        ),
        (
            "적용 순서와 보정 기준을 활용하면 오류 재발을 줄이고 투입 시간을 줄일 수 있다. "
            "검토자는 해당 정보가 경쟁 우위에 실질적으로 연결되는지 확인하되, 관리 수준이 "
            "가장 엄격한 보호 체계에 도달했는지는 별도 근거로 판단한다."
        ),
    ),
    "S2": (
        "추가 중간 민감도 검토",
        (
            "일부 내부 일정과 담당자별 처리 순서는 공개되지 않았지만, 핵심 원리와 일반 절차는 "
            "공개 지침이나 통상 업무 지식으로 설명 가능하다. 자료 접근은 부서와 협력 범위로 "
            "제한하고 공유 이력은 남기지만, 반출 승인과 보존 규칙은 일부 항목에만 적용된다."
        ),
        (
            "운영 참고 가치는 있으나 특정 기술 우위나 장기 경쟁력을 단독으로 만들 정도의 "
            "고유 조합은 확인되지 않는다. 따라서 자동으로 가장 높은 보호 대상으로 보지 않고, "
            "내부성이 있는 낮은 단계의 검토 대상으로 남긴다."
        ),
    ),
    "S3": (
        "추가 공개성 검토",
        (
            "근거가 되는 기준과 설명은 공개 지침과 이미 배포된 안내자료에서 확인된다. 접근 제한, "
            "수신자 제한, 반출 승인, 내부 전용 저장소 같은 관리 조건을 적용하지 않으며, 필요한 "
            "경우 공개 출처를 그대로 제시할 수 있다."
        ),
        (
            "문서는 일반 절차와 공개 기준을 정리한 수준이다. 비공개 조합이나 독자적인 사업 판단 "
            "기준을 포함하지 않으므로, 운영 교육이나 안내자료로 사용해도 특별한 비밀 관리 근거가 "
            "생기지 않는다."
        ),
    ),
}

FORM_NOTES = (
    "검토자는 사실, 판단, 후속 조치를 분리해서 남긴다.",
    "수치표는 원본을 보존하고 본문에는 판단에 필요한 요약만 둔다.",
    "보류 항목은 원인 미확정, 입력 누락, 영향 범위 불명확으로 나눈다.",
    "전달 범위가 바뀌면 같은 내용이라도 관리 상태를 다시 확인한다.",
    "종료 기준은 개선 수치뿐 아니라 접근 범위 확인과 재검토 일정 등록까지 포함한다.",
)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _extension(grade: str, ordinal: int) -> str:
    title, secrecy_management, value = GRADE_EXTENSIONS[grade]
    note = FORM_NOTES[ordinal % len(FORM_NOTES)]
    before = 18 + (ordinal * 5) % 47
    after = 4 + (ordinal * 3) % 19
    span = 3 + ordinal % 8
    reviewers = 2 + ordinal % 4
    return f"""

## {title}

이번 보강 문단은 문서 양식 변화에 따른 판정 흔들림을 줄이기 위해 추가한 검토 기록이다. 관찰 기간은 {span}일이고, 비교 대상 이벤트는 적용 전 {before}건에서 적용 후 {after}건으로 바뀌었다. 검토 담당자는 {reviewers}명으로 분리되어 같은 기준표를 사용했으며, 결론 문장보다 원자료의 범위와 관리 상태를 먼저 확인한다. {note}

{secrecy_management}

{value}

자료 보존은 작성 차수, 승인 차수, 검토 차수로 나누어 관리한다. 자동 판정이 어려운 경우에는 보류 사유와 필요한 추가 자료를 남기고, 다음 검토에서 같은 문서를 다시 확인할 수 있도록 가족 식별자와 원문 해시를 유지한다.
""".rstrip()


def _rewrite_record(row: dict[str, object], ordinal: int) -> dict[str, object]:
    grade = str(row["label"])
    if grade not in GRADE_EXTENSIONS:
        raise ValueError(f"unknown label: {grade}")
    text = str(row["text"]).rstrip() + _extension(grade, ordinal) + "\n"
    new_row = dict(row)
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3_9-", "direct-catalog-v4_3-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3_9-family-", "direct-catalog-v4_3-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_catalog_training_v4_style_broadening"
    new_row["generation_lineage"] = [
        *list(new_row.get("generation_lineage") or []),
        "transform:codex:document-style-broadening-v4",
    ]
    new_row["text"] = text
    new_row["requested_profile_min_chars"] = 3000
    new_row["requested_profile_max_chars"] = 3800
    evidence = dict(new_row.get("evidence_card") or {})
    evidence["text_sha256"] = _sha256_text(text.strip())
    new_row["evidence_card"] = evidence
    audit = dict(new_row.get("consensus_evidence") or {})
    audit["gate_status"] = "direct_authored_training_candidate"
    new_row["consensus_evidence"] = audit
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
    failures = {
        str(row["doc_id"]): list(validate_proxy_record(row, stage="eligible", intended_use="training").errors)
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="training").ok
    }
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    lengths = [len(str(row["text"])) for row in rows]
    manifest = {
        "schema": "direct-authored-catalog-training-v4_3",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256_bytes(SOURCE.read_bytes()),
        "source_manifest_sha256": _sha256_bytes(SOURCE_MANIFEST.read_bytes()),
        "records": len(rows),
        "records_sha256": _sha256_bytes(payload),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "text_length": {
            "min": min(lengths),
            "max": max(lengths),
            "mean": round(sum(lengths) / len(lengths), 2),
        },
        "training_only": True,
        "no_llm_generation": True,
        "evaluation_boundary": (
            "v2_2 development used only to identify style-generalization failure; "
            "v2_2 final remains unopened"
        ),
        "change_summary": (
            "Appended grade-specific review-style sections to v3.9 while preserving "
            "scenario_id and factor_profile_id catalog quotas; refreshed requested "
            "length range to match the longer documents while preserving the original "
            "shape-to-length-profile mapping for the training-pool contract."
        ),
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
