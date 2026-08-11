"""Build Proxy training corpus v3.7 from v3.6 with stronger high-grade evidence.

No LLM is called.  This script preserves the immutable v3.6 source corpus and
emits a new training-only corpus.  The change is intentionally narrow: S1/TS
records get an early, document-like evidence block that makes non-publicness,
business value, and access control visible in the body instead of leaving the
signal buried in later boilerplate.
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

from lloydk.proxy_corpus import validate_proxy_record


SOURCE = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_6.jsonl"
SOURCE_MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_6.manifest.json"
)
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_7.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_7.manifest.json"


S1_BLOCKS = (
    """## 판정 근거 보강

이 문서의 중심 근거는 외부 공개자료만으로 재구성하기 어려운 현장 조건의 결합이다. 수치 하나가 아니라 적용 순서, 예외 처리, 담당자별 확인 지점, 실패 시 되돌림 기준이 함께 기록되어 있다. 같은 장비나 같은 업무명을 알고 있어도 이 조합을 모르면 결과를 안정적으로 반복하기 어렵다.

공유 범위는 해당 업무 담당자와 검토자에게 제한된다. 회의에서 설명할 때도 전체 조건표를 그대로 전달하지 않고, 필요한 조치 항목과 검증 결과만 분리해 전달한다. 원본에는 시행착오 이력과 미공개 판단 기준이 남아 있으므로 외부 배포본이나 일반 교육자료로 전환하지 않는다.
""",
    """## 비공개 운영 근거

본문의 핵심은 일반 원칙이 아니라 실제 적용 과정에서 좁혀진 운영 기준이다. 입력 조건, 허용 편차, 예외 처리 순서, 재확인 시점이 서로 연결되어 있으며 일부 항목은 이전 실패 기록을 반영해 조정되었다. 이 연결 관계가 빠지면 문서의 결론만으로 같은 판단을 재현하기 어렵다.

자료 접근은 업무상 필요 인원으로 제한하고, 일부 값은 요약본에서 제외한다. 승인자는 공개 가능한 설명과 내부 판단 근거를 분리해 보관하도록 지시했다. 따라서 이 문서는 단순 참고자료가 아니라 제한 공유가 필요한 내부 판단 기록으로 취급한다.
""",
    """## 관리 필요성

검토 내용에는 공개 절차서에 없는 조합값과 예외 판단 순서가 포함되어 있다. 담당 부서는 적용 전 조건, 적용 중 관찰값, 적용 후 재확인 결과를 따로 기록했고, 차이가 발생한 구간은 후속 보정 목록으로 넘겼다. 이 흐름은 단기간에 외부에서 추정하기 어려운 시행착오를 담고 있다.

문서 열람은 프로젝트 공간 내 지정 폴더로 제한한다. 외부 협력사에는 결론과 작업 범위만 전달하고, 원인 분석표와 조건별 비교값은 제공하지 않는다. 복사본이 필요한 경우 사유와 수신자를 남긴 뒤 승인 이력에 연결한다.
""",
)


TS_BLOCKS = (
    """## 핵심 보호 근거

이 문서는 핵심 공정 또는 중요 사업 의사결정에 직접 연결되는 미공개 조건을 담고 있다. 단순한 결과 보고가 아니라 설계 변수, 제어 순서, 실패 이력, 복구 기준이 한 묶음으로 정리되어 있어 외부에 유출될 경우 경쟁자가 시행착오를 크게 줄일 수 있다.

원본은 반출 금지 대상으로 관리한다. 열람자는 지정 인원으로 제한하고, 협력사 전달본에서는 재현 가능한 조건표와 예외 판단 기준을 제거한다. 승인 없이 화면 캡처, 원문 복사, 외부 저장소 업로드를 금지하며 접근 이력과 변경 이력을 같이 남긴다.
""",
    """## 고위험 비공개 근거

본문의 판단은 공개 규격이나 일반 업무 절차만으로 도출되지 않는다. 조건 간 상호작용, 실패가 발생한 순서, 정상 범위로 되돌리는 절차가 함께 있어 실제 구현 또는 운영 우위를 좌우한다. 일부 항목은 핵심 설비, 알고리즘, 공급 조건, 고객 대응 전략과 직접 연결된다.

자료는 최소 권한 원칙으로 관리한다. 검토자는 원본을 내려받지 않고 지정 화면에서 확인하며, 외부 제출이 필요한 경우 비식별 요약본을 새로 만든다. 원본의 세부 수치, 조건 조합, 재현 절차는 승인자 확인 전까지 분리 저장하지 않는다.
""",
    """## 중대 영향 근거

해당 기록은 실패를 줄이기 위한 내부 조합과 검증 순서를 포함한다. 결론 문장만 보면 일반 검토서처럼 보일 수 있으나, 본문에는 재현 가능한 조건, 제한된 접근 이력, 대체하기 어려운 경험값이 함께 남아 있다. 이 정보가 외부로 나가면 제품 성능, 원가, 납기, 협상력 중 하나 이상에 직접적인 손실을 만들 수 있다.

관리자는 원본 보관 위치, 열람 권한, 전달 가능 범위를 분리해 승인한다. 전달 대상이 바뀌면 기존 문서를 그대로 보내지 않고, 공개 가능한 목적과 필요한 범위를 다시 확인한다. 미확인 사본은 폐기하고 폐기 이력까지 남긴다.
""",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _insert_after_heading(text: str, block: str) -> str:
    marker = "\n\n## 변경 안건과 검토 범위\n\n"
    if marker not in text:
        return f"{text.rstrip()}\n\n{block.strip()}\n"
    head, tail = text.split(marker, 1)
    return f"{head}{marker}{block.strip()}\n\n{tail}"


def _span(text: str, quote: str) -> dict[str, object]:
    start = text.index(quote)
    return {
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "quote_sha256": _sha256_text(quote),
    }


def _first_present(text: str, candidates: tuple[str, ...]) -> str:
    for quote in candidates:
        if quote in text:
            return quote
    raise ValueError(f"no evidence quote found in text: {candidates!r}")


def _high_grade_evidence_card(text: str, grade: str) -> dict[str, object]:
    if grade == "TS":
        nonpublicity = _first_present(
            text,
            (
                "핵심 공정 또는 중요 사업 의사결정에 직접 연결되는 미공개 조건",
                "본문의 판단은 공개 규격이나 일반 업무 절차만으로 도출되지 않는다",
                "재현 가능한 조건, 제한된 접근 이력, 대체하기 어려운 경험값",
            ),
        )
        competitive_value = _first_present(
            text,
            (
                "경쟁자가 시행착오를 크게 줄일 수 있다",
                "실제 구현 또는 운영 우위를 좌우한다",
                "제품 성능, 원가, 납기, 협상력 중 하나 이상에 직접적인 손실",
            ),
        )
        access_controls = _first_present(
            text,
            (
                "원본은 반출 금지 대상으로 관리한다",
                "자료는 최소 권한 원칙으로 관리한다",
                "관리자는 원본 보관 위치, 열람 권한, 전달 가능 범위를 분리해 승인한다",
            ),
        )
    else:
        nonpublicity = _first_present(
            text,
            (
                "외부 공개자료만으로 재구성하기 어려운 현장 조건의 결합",
                "일반 원칙이 아니라 실제 적용 과정에서 좁혀진 운영 기준",
                "공개 절차서에 없는 조합값과 예외 판단 순서",
            ),
        )
        competitive_value = _first_present(
            text,
            (
                "이 조합을 모르면 결과를 안정적으로 반복하기 어렵다",
                "같은 판단을 재현하기 어렵다",
                "단기간에 외부에서 추정하기 어려운 시행착오",
            ),
        )
        access_controls = _first_present(
            text,
            (
                "공유 범위는 해당 업무 담당자와 검토자에게 제한된다",
                "자료 접근은 업무상 필요 인원으로 제한하고",
                "문서 열람은 프로젝트 공간 내 지정 폴더로 제한한다",
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
    grade = str(row["label"])
    new_row = dict(row)
    new_row["doc_id"] = str(new_row["doc_id"]).replace("direct-catalog-v3-", "direct-catalog-v3_7-")
    new_row["document_family_id"] = str(new_row["document_family_id"]).replace(
        "direct-catalog-v3-family-", "direct-catalog-v3_7-family-"
    )
    new_row["authoring_method"] = "codex_direct_authored_high_grade_diverse_v3_7"
    new_row["generation_lineage"] = [
        "generator:codex:direct-authored-catalog-training-v3",
        "transform:codex:high-grade-evidence-frontload-v3_7",
    ]
    new_row["claim_scope"] = (
        "Direct-authored Proxy training only; not customer-real evidence, "
        "golden evaluation, or Locked Gold."
    )
    if grade == "S1":
        new_row["text"] = _insert_after_heading(str(new_row["text"]), S1_BLOCKS[ordinal % len(S1_BLOCKS)])
        new_row["evidence_card"] = _high_grade_evidence_card(str(new_row["text"]), grade)
    elif grade == "TS":
        new_row["text"] = _insert_after_heading(str(new_row["text"]), TS_BLOCKS[ordinal % len(TS_BLOCKS)])
        new_row["evidence_card"] = _high_grade_evidence_card(str(new_row["text"]), grade)
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
    source_payload = SOURCE.read_bytes()
    source_manifest_payload = SOURCE_MANIFEST.read_bytes()
    lengths = [len(str(row["text"])) for row in rows]
    manifest = {
        "schema": "direct-authored-catalog-training-v3_7",
        "source_corpus": str(SOURCE.relative_to(ROOT)),
        "source_records_sha256": _sha256(source_payload),
        "source_manifest_sha256": _sha256(source_manifest_payload),
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
        "evaluation_case_ledger": "disjoint_from_direct_authored_proxy_eval_v2_1",
        "change_summary": (
            "Front-loaded high-grade evidence blocks for S1/TS only; preserved "
            "catalog quotas, factor profiles, and public S3 separation."
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
