"""Create a directly-authored, training-forbidden Proxy evaluation source.

No LLM is called.  This corpus is independently drafted from the training
corpus and exists solely to produce development-200 and final-locked-800
evaluation partitions.  It is not Locked Gold or customer-real evidence.
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from koipa.proxy_corpus import validate_proxy_record
from scripts.build_direct_authored_training_corpus import CASES
from scripts.build_direct_authored_training_pilot import (
    GRADE_POLICY,
    _editorial_audit,
    _evidence_card,
)


OUT = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v1.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v1.manifest.json"

EVAL_PURPOSES = (
    ("field-observation", "현장 관찰 확인서", "현장 조건과 관찰값의 연결 상태를 확인"),
    ("acceptance-criteria", "수용기준 점검서", "적용 결과가 수용 기준과 일치하는지 점검"),
    ("contract-change", "계약조건 영향 메모", "변경이 납기·책임·전달 범위에 미치는 영향을 정리"),
    ("data-retention", "자료보존 점검서", "원자료·요약본·증적의 보관과 회수 상태를 확인"),
    ("technology-transfer", "기술이전 사전검토서", "다른 조직에 전달할 작업 범위와 재현 조건을 검토"),
    ("claim-analysis", "이견 분석 기록", "상충하는 측정값과 이견의 원인을 분리해 확인"),
    ("capacity-review", "운영여력 검토서", "자원 제약 아래에서 적용 가능 범위와 중단 기준을 검토"),
    ("closure-review", "조치 종료 확인서", "조치 종료 전 미결 항목·인수인계·재점검 일정을 확인"),
)

FORM_NAMES = (
    "현장확인서",
    "기준점검표",
    "영향분석 메모",
    "자료관리 기록",
    "사전검토 의견서",
    "이견조정 회의록",
    "운영여력 보고서",
    "종료확인서",
)

EVAL_APPENDIX = """평가 대상 문서는 결과를 외운 모델이 아니라, 문서 안의 공개성·경제적 가치·관리 상태 근거를 읽는지를 확인하기 위해 작성한다. 따라서 일반 원칙, 업무상 편의 정보, 재현에 중요한 결합 정보, 접근통제 운영 사실을 한 문장으로 뭉개지 않고 구분해 기록한다.

확인자는 수치의 크기만으로 중요도를 판단하지 않는다. 수치가 어떤 조건에서 나온 것인지, 다른 조직이 같은 결과를 얻기 위해 무엇을 다시 해야 하는지, 문서에 적힌 절차가 실제 접근관리와 연결되는지를 함께 본다. 근거가 부족하면 높은 등급으로 단정하지 않고 보완 항목으로 남긴다.

이 문서는 학습 과정에 제공되지 않는다. 개발 단계에서의 조정과 최종 단계의 성적표가 서로 섞이지 않도록 문서 가족 단위로 분리하고, 결과를 본 뒤에 문구나 라벨을 수정하지 않는다. 평가가 끝난 뒤에도 이 원칙은 유지한다.

검토 의견은 사실·판단·후속 조치를 구분한다. 사실은 원자료나 관찰 기록으로 확인하고, 판단은 확인된 사실과 등급 기준의 연결로 설명하며, 후속 조치는 책임자와 기한을 남긴다. 세 요소 중 하나가 빠지면 판단을 재현하기 어려우므로 완료 상태로 처리하지 않는다."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _case(base: dict[str, str], purpose: tuple[str, str, str]) -> dict[str, str]:
    purpose_id, form, focus = purpose
    return {
        **base,
        "case_id": f"eval-{base['case_id']}-{purpose_id}",
        "document_type": f"{base['document_type']} {form}",
        "context": f"{base['context']}에서 {focus}하는 평가 상황",
        "detail": f"{base['detail']}과 {focus}에 필요한 근거의 연결",
        "public": base["public"],
        "roles": base["roles"],
        "focus": focus,
    }


def _body(case: dict[str, str], grade: str, ordinal: int) -> str:
    policy = GRADE_POLICY[grade]
    disclosure = str(policy["disclosure"])
    if grade == "S3":
        disclosure += f"{case['public']}에 근거한다."
    return f"""# {case['document_type']}

## 확인 대상

본 문서는 {case['context']}을 위한 독립 평가 기록이다. 이번 차수의 초점은 {case['focus']}이며, 작성자는 결론을 먼저 정하지 않고 관찰 사실·근거의 출처·예외 사항을 나누어 기록한다. 검토 차수는 {ordinal + 1}차이며, 문서 형식은 {FORM_NAMES[ordinal % len(FORM_NAMES)]}이다. 동일한 형식 자체가 특정 등급을 뜻하지 않도록 모든 등급에 공통으로 사용한다.

## 관찰 내용과 비교 기준

검토 대상은 {case['detail']}이다. 기존 관찰값은 {case['metric']}으로 정리되어 있으며, 검토자는 원자료의 작성 시점, 적용 조건, 수치의 단위, 변경 이력을 대조한다. 수치가 목표 범위에 들어와도 측정 조건이 바뀌었거나 비교 대상이 달라지면 결론을 유보한다. 역할은 {case['roles']}가 분담하며, 이견이 있으면 원자료 또는 확인 가능한 기록으로 되돌아간다.

## 판단 근거

{disclosure}

{policy['value']}

{policy['access']}

## 예외와 후속 확인

예외가 발견되면 영향 범위, 임시 조치, 복구 기준, 재확인 책임자를 분리해 적는다. 협력 조직에 설명이 필요할 때에는 업무 수행에 필요한 범위만 전달하고, 전달한 자료와 회수 여부를 기록한다. 적용 또는 보류의 결정은 수치 하나가 아니라 근거의 충분성, 관리 상태, 재현 가능성을 함께 확인한 뒤 내린다.

## 평가 독립성 확인

개발 단계에서 이 문서를 읽거나 임계값을 맞추는 용도로 사용하지 않는다. 평가가 끝난 뒤에도 원문·라벨·근거카드는 고정하며, 수정이 필요하면 기존 결과를 덮어쓰지 않고 새 버전으로 분리한다.

## 종합 점검

{EVAL_APPENDIX}
"""


def _record(
    case: dict[str, str],
    *,
    grade: str,
    ordinal: int,
    family_kind: str,
) -> dict[str, object]:
    text = _body(case, grade, ordinal)
    policy = GRADE_POLICY[grade]
    family_id = f"{case['case_id']}-{family_kind}"
    audit = _editorial_audit(policy, grade)
    audit["gate_status"] = "direct_authored_evaluation_candidate"
    return {
        "doc_id": f"{family_id}-{grade.lower()}-{ordinal}",
        "document_family_id": family_id,
        "scenario_id": f"{case['case_id']}-{grade.lower()}",
        "factor_profile_id": f"direct-eval-{grade.lower()}-svm",
        "family_profile_id": f"direct-eval-form-{ordinal % len(FORM_NAMES) + 1}",
        "length_profile_id": "direct-eval-2800-5200",
        "requested_profile_min_chars": 2800,
        "requested_profile_max_chars": 5200,
        "document_type": case["document_type"],
        "domain": "direct-authored-evaluation",
        "industry": "direct-authored-proxy",
        "text": text.strip() + "\n",
        "label": grade,
        "intended_label": grade,
        "expected_factor_scores": dict(policy["scores"]),
        "evidence_card": _evidence_card(text.strip() + "\n", policy, case),
        "document_origin": "synthetic",
        "source": "direct_authored_proxy",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
        "authoring_method": "codex_direct_authored_proxy_evaluation_v1",
        "generation_lineage": ["generator:codex:direct-authored-proxy-eval-v1"],
        "decision_bucket": "direct_authored_evaluation_candidate",
        "gate_version": "direct_authored_quality_v1",
        "primary_judge_model": "codex-editorial-audit-v1",
        "judging_lineage": ["primary_judge:codex-editorial-audit-v1"],
        "consensus_evidence": audit,
        "requires_manual_audit": False,
        "claim_scope": (
            "Direct-authored Proxy evaluation only; training forbidden; not "
            "customer-real accuracy evidence or Locked Gold."
        ),
    }


def build_records() -> list[dict[str, object]]:
    families = [_case(case, purpose) for case in CASES for purpose in EVAL_PURPOSES]
    assert len(families) == 200
    rows: list[dict[str, object]] = []
    for index, case in enumerate(families):
        family_kind = "four-grade" if index < 100 else "s3-expanded"
        for grade in ("TS", "S1", "S2"):
            rows.append(_record(case, grade=grade, ordinal=index, family_kind=family_kind))
        rows.append(_record(case, grade="S3", ordinal=index, family_kind=family_kind))
        if index >= 100:
            # The second S3 document stays in the same family so family-atomic
            # development/final splitting preserves the intended distribution.
            rows.append(
                _record(
                    case,
                    grade="S3",
                    ordinal=index + 1000,
                    family_kind=family_kind,
                )
            )

    # 100 supplemental one-grade families complete S1/S2 quotas without
    # placing TS documents in every family.
    for index, case in enumerate(families[:50]):
        rows.append(_record(case, grade="S1", ordinal=index + 2000, family_kind="s1-extra"))
        rows.append(_record(case, grade="S2", ordinal=index + 3000, family_kind="s2-extra"))

    assert Counter(str(row["label"]) for row in rows) == {
        "TS": 200,
        "S1": 250,
        "S2": 250,
        "S3": 300,
    }
    assert len({str(row["doc_id"]) for row in rows}) == len(rows)
    assert len({str(row["text"]) for row in rows}) == len(rows)
    return rows


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
    rows = build_records()
    failures = {
        str(row["doc_id"]): list(
            validate_proxy_record(row, stage="eligible", intended_use="evaluation").errors
        )
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok
    }
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-proxy-evaluation-v1",
        "records": len(rows),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "training_forbidden": True,
        "records_sha256": _sha256(payload),
        "claim_scope": "Proxy-only evaluation source; not Locked Gold or customer-real accuracy evidence.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
