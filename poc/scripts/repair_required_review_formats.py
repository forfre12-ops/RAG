#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Repair only synthetic reviewer documents whose body contradicts a required type.

The 1,001-document source corpus stays the source of truth.  This tool selects
the same stable 1,000-document prefix as ``prepare_exact_expert_review_batch``
and replaces only required-format documents that fail the presentation audit.
It never changes labels, departments, information types, or the excluded
source record.  ``--apply`` is required for writes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

try:
    from audit_review_batch_presentation import FORMAT_SIGNATURES
    from prepare_exact_expert_review_batch import SOURCE, _atomic_write, _read_manifest, select_rows
except ImportError:  # package import in tests
    from scripts.audit_review_batch_presentation import FORMAT_SIGNATURES
    from scripts.prepare_exact_expert_review_batch import SOURCE, _atomic_write, _read_manifest, select_rows


force_utf8_stdio()

MIN_FORMAT_DOCUMENT_CHARS = 700


def _severity_context(row: dict[str, Any]) -> str:
    """Add a fictional business context without exposing the internal grade code."""
    contexts = {
        "TS": "아직 외부에 공개되지 않은 인수·핵심 기술 전환의 세부 조건이 포함될 수 있어 열람 범위를 지정 담당자로 한정한다.",
        "S1": "핵심 공정·가격·거래 조건과 연계될 수 있는 세부 사항은 업무상 필요한 담당자에게만 공유한다.",
        "S2": "조직 내부 운영과 인력·일정 관리에 쓰이는 내용이므로 외부 전송 전 소관 부서 확인을 거친다.",
        "S3": "일반 운영 안내 성격의 내용이지만, 최신 확정본 여부를 확인한 뒤 배포한다.",
    }
    return contexts[str(row.get("true_grade") or "S3")]


def _application(row: dict[str, Any]) -> str:
    doc_id = str(row["doc_id"])
    return f"""지원서\n\n지원자 식별번호: 지원자-{doc_id[-4:]}\n지원분야: {row['department']} · {row['info_type']} 관련 직무\n지원일: 2026년 10월 11일\n\n[학력]\n전공 기반 과목과 현장 프로젝트를 이수했으며, 지원 분야의 기초 분석과 문서화 방법을 교육 과정에서 익혔다. 학력 세부 증빙은 면접 대상자 확정 뒤 인사 담당자에게 별도로 제출한다.\n\n[경력 및 역량]\n지원자는 유관 업무에서 일정·자료·협업 이슈를 정리한 경험이 있으며, 부서별 요청을 구분해 기록하고 필요한 담당자에게 정확히 전달하는 역량을 보유하고 있다고 기재했다. 실무 과제에서는 수치와 근거를 분리해 보고하는 방식을 사용했다.\n\n[지원동기 및 자기소개]\n{row['department']} 부서의 {row['info_type']} 업무가 조직의 의사결정과 연결되는 점에 관심을 두고 지원했다. 입사 후에는 정해진 절차를 먼저 익히고, 확인되지 않은 내용을 단정하지 않으며, 검토 이력이 남는 방식으로 업무를 수행하고자 한다.\n\n[검토 메모]\n{_severity_context(row)} 지원자 본인과 인사 담당자 외에는 이 지원서를 열람하지 않으며, 전형 종료 뒤 보관·파기 기준을 적용한다.\n"""


def _employment_contract(row: dict[str, Any]) -> str:
    doc_id = str(row["doc_id"])
    return f"""고용계약서\n\n사용자: 주식회사 예시 ({row['department']} 부서)\n근로자: 근로자-{doc_id[-4:]}\n근로계약기간: 2026년 11월 1일 ~ 2027년 10월 31일\n담당업무: {row['info_type']} 관련 지원 및 문서 정리 업무\n\n제1조(근로시간) 근로시간은 주 40시간을 원칙으로 하며, 구체적인 시작·종료 시각과 휴일은 취업규칙 및 부서 근무표에 따른다. 연장근로가 필요한 경우에는 사전에 사용자와 근로자가 절차에 따라 협의한다.\n\n제2조(임금) 임금은 월 단위로 지급하며, 기본급·수당·지급일 등 세부 조건은 별도 임금 안내서에 명시한다. 업무 범위 또는 근무 형태가 변경될 경우 임금 조건도 서면으로 다시 확인한다.\n\n제3조(비밀유지) 근로자는 업무 중 알게 된 {row['info_type']} 및 관련 자료를 업무 목적 외로 이용하거나 제3자에게 제공하지 않는다. { _severity_context(row) }\n\n제4조(확인) 사용자와 근로자는 본 고용계약서의 내용을 읽고 이해했으며, 계약 변경은 당사자 서면 합의로만 한다.\n\n사용자 서명: ____________________    근로자 서명: ____________________\n"""


def _contract(row: dict[str, Any]) -> str:
    doc_id = str(row["doc_id"])
    return f"""업무 협력 계약서\n\n계약당사자: 주식회사 예시({row['department']} 부서, 이하 “발주자”)와 협력사-{doc_id[-4:]}(이하 “수행자”)\n계약목적: {row['info_type']} 관련 업무 지원 및 산출물 제공\n계약기간: 2026년 11월 1일 ~ 2027년 4월 30일\n\n제1조(업무 범위) 수행자는 발주자가 승인한 일정과 산출물 기준에 따라 업무를 수행하고, 변경 요청은 양 당사자가 서면으로 확인한 뒤 반영한다.\n\n제2조(대금) 대금은 검수 완료된 산출물을 기준으로 월별 정산하며, 금액·지급일·세금계산서 발행 기준은 별도 견적서와 발주서에 따른다.\n\n제3조(비밀유지) 계약 수행 중 취득한 자료와 협의 내용은 계약 목적 외로 사용하지 않는다. { _severity_context(row) }\n\n제4조(해지 및 반환) 중대한 계약 위반이 시정되지 않거나 비밀유지 의무를 위반한 경우 상대방은 계약을 해지할 수 있다. 계약 종료 시 수행자는 제공받은 자료와 복제본을 반환하거나 발주자의 지시에 따라 파기한다.\n\n본 계약의 계약조건은 양 당사자 서명일에 효력이 발생한다.\n\n발주자 서명: ____________________    수행자 서명: ____________________\n"""


def repaired_text(row: dict[str, Any]) -> str | None:
    doc_format = str(row.get("doc_format") or "").strip()
    if doc_format == "응시자 지원서":
        return _application(row) + (
            "\n[전형 확인]\n지원자는 제출 자료가 사실과 다를 경우 전형 결과가 변경될 수 있음을 확인한다. "
            "전형 담당자는 필요한 범위에서만 자료를 열람하고, 전형 종료 후 보관 기준을 적용한다. "
            "면접 일정과 추가 증빙 요청은 지원자에게 별도 안내하며, 접수 단계의 자료는 채용 목적 외로 사용하지 않는다.\n"
        )
    if doc_format == "고용계약서":
        return _employment_contract(row) + (
            "\n제5조(안전 및 교육) 사용자는 근로자에게 담당업무 수행에 필요한 사내 규정과 안전·보안 교육을 제공한다. "
            "근로자는 교육에서 정한 보호조치와 보고 절차를 준수하고, 자료 유출 또는 오기재 우려를 알게 되면 지체 없이 소관 부서에 알린다.\n"
        )
    if doc_format == "각종 계약서":
        return _contract(row) + (
            "\n제5조(검수 및 분쟁 처리) 발주자는 산출물을 받은 뒤 합리적인 기간 안에 검수 결과를 알리고, "
            "보완이 필요한 경우 서면으로 범위와 기한을 통지한다. 해석상 이견은 업무 책임자 협의로 우선 해결한다.\n"
        )
    return None


def _matches_required_signature(text: str, doc_format: str) -> bool:
    terms = FORMAT_SIGNATURES.get(doc_format)
    return terms is not None and sum(term in text for term in terms) >= 3


def repair(source: Path, *, apply: bool) -> list[str]:
    manifest_path = source / "scaleup_manifest.jsonl"
    rows = _read_manifest(manifest_path)
    selected, _ = select_rows(rows)
    selected_ids = {str(row["doc_id"]) for row in selected}
    changed: list[str] = []
    for row in rows:
        doc_id = str(row.get("doc_id") or "")
        if doc_id not in selected_ids:
            continue
        doc_format = str(row.get("doc_format") or "").strip()
        replacement = repaired_text(row)
        if replacement is None:
            continue
        path = source / "documents" / f"{doc_id}.txt"
        current = path.read_text(encoding="utf-8")
        if _matches_required_signature(current, doc_format) and len(current) >= MIN_FORMAT_DOCUMENT_CHARS:
            continue
        if not _matches_required_signature(replacement, doc_format):
            raise ValueError(f"internal template error for {doc_id}")
        changed.append(doc_id)
        if apply:
            _atomic_write(path, replacement)
            row["char_count"] = len(replacement)
    if apply and changed:
        _atomic_write(manifest_path, "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--apply", action="store_true", help="write only the nonconforming source documents")
    args = parser.parse_args(argv)
    changed = repair(args.source.resolve(), apply=args.apply)
    print(json.dumps({"changed_document_count": len(changed), "document_ids": changed}, ensure_ascii=False, indent=2))
    if not args.apply:
        print("--apply not supplied: no files changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
