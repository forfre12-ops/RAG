"""Offline AI-Hub 71813 receipt, lineage, and immutability tests."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import intake_aihub_71813 as intake
from lloydk.proxy_corpus import validate_proxy_record


def _page_text(page: int) -> str:
    topics = [
        "지역 산업의 생산성과 공급망 회복력을 비교하기 위해 분기별 설비 가동률과 원자재 조달 기간을 조사하였다.",
        "조사팀은 표본 기관의 규모와 업종을 구분하고 동일한 질문지를 사용하여 응답 편향을 줄였다.",
        "분석 결과 연구개발 투자가 증가한 조직에서는 신규 서비스 출시 기간이 짧고 협업 부서 수도 많았다.",
        "현장 담당자는 자료 수집 기준과 누락값 처리 방법을 기록하고 검토 회의에서 변경 사유를 확인하였다.",
        "재정 계획에는 장비 교체 비용과 교육 운영비, 유지보수 예상액을 별도 항목으로 나누어 제시하였다.",
        "위험 평가는 일정 지연과 공급 중단, 품질 편차의 발생 가능성을 단계별로 산정하는 방식으로 수행하였다.",
        "기관은 월별 성과지표를 공개하고 목표치와 실적의 차이가 큰 사업에 대해 개선 일정을 마련하였다.",
        "자료의 신뢰성을 높이기 위해 두 개의 원천 통계를 대조하고 계산식과 기준연도를 부록에 설명하였다.",
        "시범 운영에서는 이용자의 처리 시간과 오류 신고 건수를 함께 측정하여 편의성과 안정성을 평가하였다.",
        "향후 계획은 담당 조직과 완료 시점, 필요한 예산을 명시하고 분기마다 이행 여부를 점검하도록 구성하였다.",
        "외부 환경 변화에 대응하기 위해 수요 전망을 세 가지 시나리오로 나누고 민감도 분석 결과를 제시하였다.",
        "최종 보고서는 조사 목적과 방법, 주요 결과, 정책적 시사점, 한계와 후속 과제를 순서대로 정리하였다.",
    ]
    rotated = topics[page % len(topics) :] + topics[: page % len(topics)]
    return "\n\n".join(
        f"{sentence} 이번 페이지의 관측번호는 {page * 100 + index}이며 검토일은 2026년 8월 {index + 1}일이다."
        for index, sentence in enumerate(rotated)
    )


def _write_source(root: Path, *, pages: int = 4, duplicate_last: bool = False) -> None:
    (root / "source").mkdir(parents=True)
    (root / "label").mkdir(parents=True)
    (root / "source" / "MI2_240808_TY2_0292.pdf").write_bytes(
        b"%PDF-1.7\nfixture"
    )
    for page in range(1, pages + 1):
        text = _page_text(1 if duplicate_last and page == pages else page)
        txt_name = f"MI2_240808_TY2_0292_{page}.txt"
        json_name = f"MI3_240808_TY2_0292_{page}.json"
        (root / "source" / txt_name).write_text(text, encoding="utf-8")
        metadata = {
            "raw_data_info": {
                "raw_data_name": "MI1_240808_TY2_0292.hwp",
                "doc_name": "지역 산업 생산성과 공급망 조사 보고서",
                "date": "240808",
                "doc_type": "보고서",
                "format": "hwp",
                "copyright": "구축수행기관",
                "publisher": "공공연구기관",
                "organ_type": "공공기관",
            },
            "source_data_info": {
                "source_data_name_pdf": "MI2_240808_TY2_0292.pdf",
                "source_data_name_txt": txt_name,
                "source_data_name_jpg": f"MI2_240808_TY2_0292_{page}.jpg",
                "document_resolution": [2480, 3508],
            },
            "learning_data_info": {
                "learning_data_name": json_name,
                "page_num": str(page),
                "visual_context": text,
                "type_id": "Type-01",
                "type_name": "텍스트+표",
                "annotation": [],
            },
        }
        (root / "label" / json_name).write_text(
            json.dumps(metadata, ensure_ascii=False), encoding="utf-8"
        )


def _write_receipt(
    root: Path,
    *,
    approval_granted: bool = True,
    evidence_hash: str | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    evidence = root / "approval-evidence.txt"
    evidence.write_text("recipient-specific AI-Hub approval proof", encoding="utf-8")
    observed_hash = hashlib.sha256(evidence.read_bytes()).hexdigest()
    receipt = {
        "schema": intake.RECEIPT_SCHEMA,
        "dataset_id": intake.DATASET_ID,
        "dataset_title": intake.DATASET_TITLE,
        "dataset_version": "1.1",
        "dataset_page_url": intake.DATASET_PAGE_URL,
        "terms_url": intake.TERMS_URL,
        "approved_recipient_legal_name": "주식회사 로이드케이",
        "approval_reference": "AIHUB-APPROVAL-20260808-001",
        "approval_granted": approval_granted,
        "approval_issued_at": "2026-08-08T09:00:00+09:00",
        "download_completed_at": "2026-08-08T10:00:00+09:00",
        "terms_accepted": True,
        "terms_accepted_at": "2026-08-08T08:55:00+09:00",
        "training_use_approved": True,
        "use_scope": "model_training_only",
        "redistribution_permitted": False,
        "third_party_access_permitted": False,
        "foreign_transfer_permitted": False,
        "evaluation_use_permitted": False,
        "golden_set_use_permitted": False,
        "dataset_sale_permitted": False,
        "attribution_required": True,
        "attribution_text": (
            "본 학습에는 과학기술정보통신부와 한국지능정보사회진흥원(NIA)의 "
            "AI-Hub 데이터가 사용되었습니다."
        ),
        "restrictions": sorted(intake.REQUIRED_RESTRICTIONS),
        "receipt_evidence": {
            "path": evidence.name,
            "sha256": evidence_hash or observed_hash,
        },
    }
    path = root / "approval-receipt.json"
    path.write_text(json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    return path


def test_receipt_fails_closed_without_explicit_approval(tmp_path: Path) -> None:
    receipt = _write_receipt(tmp_path / "receipt", approval_granted=False)
    with pytest.raises(intake.IntakeError, match="approval_granted"):
        intake.validate_approval_receipt(receipt)


def test_receipt_evidence_hash_must_match(tmp_path: Path) -> None:
    receipt = _write_receipt(tmp_path / "receipt", evidence_hash="0" * 64)
    with pytest.raises(intake.IntakeError, match="sha256 mismatch"):
        intake.validate_approval_receipt(receipt)


def test_receipt_evidence_cannot_escape_receipt_directory(tmp_path: Path) -> None:
    receipt = _write_receipt(tmp_path / "receipt")
    raw = json.loads(receipt.read_text(encoding="utf-8"))
    raw["receipt_evidence"]["path"] = "../outside.txt"
    receipt.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(intake.IntakeError, match="escapes"):
        intake.validate_approval_receipt(receipt)


def test_intake_preserves_hashes_lineage_and_train_only_scope(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    _write_source(source)
    receipt = _write_receipt(tmp_path / "receipt")

    run_dir, manifest = intake.run_intake(
        source_root=source,
        receipt_path=receipt,
        output_root=tmp_path / "runs",
        run_id="unit-run-001",
    )

    assert (run_dir / "COMPLETE.json").is_file()
    assert manifest["permission_scope"] == {
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
        "golden_set_use_permitted": False,
        "redistribution_permitted": False,
        "third_party_access_permitted": False,
        "foreign_transfer_permitted": False,
        "dataset_sale_permitted": False,
        "attribution_required": True,
        "attribution_text": (
            "본 학습에는 과학기술정보통신부와 한국지능정보사회진흥원(NIA)의 "
            "AI-Hub 데이터가 사용되었습니다."
        ),
        "restrictions": sorted(intake.REQUIRED_RESTRICTIONS),
    }
    records = [
        json.loads(line)
        for line in (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records
    assert all(intake.MIN_CHARS <= len(row["text"]) <= intake.MAX_CHARS for row in records)
    assert all(row["training_use_permitted"] is True for row in records)
    assert all(row["evaluation_use_permitted"] is False for row in records)
    assert all(row["golden_set_use_permitted"] is False for row in records)
    assert all(row["redistribution_permitted"] is False for row in records)
    assert all(row["document_family_sha256"] for row in records)
    assert all(row["source_document_sha256"] for row in records)
    assert all(row["page_lineage"] for row in records)
    assert all(row["approval_receipt_sha256"] for row in records)
    assert "주식회사 로이드케이" not in (run_dir / "records.jsonl").read_text(
        encoding="utf-8"
    )

    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["records_sha256"] == hashlib.sha256(
        (run_dir / "records.jsonl").read_bytes()
    ).hexdigest()
    assert complete["manifest_sha256"] == hashlib.sha256(
        (run_dir / "manifest.json").read_bytes()
    ).hexdigest()


def test_run_id_is_immutable(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    _write_source(source)
    receipt = _write_receipt(tmp_path / "receipt")
    kwargs = {
        "source_root": source,
        "receipt_path": receipt,
        "output_root": tmp_path / "runs",
        "run_id": "fixed-run",
    }
    intake.run_intake(**kwargs)
    with pytest.raises(intake.IntakeError, match="refusing to replace"):
        intake.run_intake(**kwargs)


def test_common_validator_rejects_aihub_permission_scope_tampering(
    tmp_path: Path,
) -> None:
    source = tmp_path / "extracted"
    _write_source(source)
    receipt = _write_receipt(tmp_path / "receipt")
    run_dir, _manifest = intake.run_intake(
        source_root=source,
        receipt_path=receipt,
        output_root=tmp_path / "runs",
        run_id="validator-run",
    )
    record = json.loads(
        (run_dir / "records.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert validate_proxy_record(record, intended_use="training").ok

    record["golden_set_use_permitted"] = True
    errors = validate_proxy_record(record, intended_use="training").errors
    assert "aihub_71813:requires_false:golden_set_use_permitted" in errors


def test_duplicate_page_text_is_dropped_before_chunking(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    _write_source(source, pages=5, duplicate_last=True)
    receipt = _write_receipt(tmp_path / "receipt")
    _run_dir, manifest = intake.run_intake(
        source_root=source,
        receipt_path=receipt,
        output_root=tmp_path / "runs",
        run_id="dedupe-run",
    )
    assert manifest["summary"]["counters"]["duplicate_pages_dropped"] == 1


def test_strong_pii_holds_family_without_emitting_text(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    _write_source(source)
    first_txt = source / "source" / "MI2_240808_TY2_0292_1.txt"
    first_txt.write_text(
        first_txt.read_text(encoding="utf-8") + "\n주민번호 900101-1234567",
        encoding="utf-8",
    )
    receipt = _write_receipt(tmp_path / "receipt")
    with pytest.raises(intake.IntakeError, match="strong PII pattern detected"):
        intake.run_intake(
            source_root=source,
            receipt_path=receipt,
            output_root=tmp_path / "runs",
            run_id="pii-run",
        )


def test_output_and_receipt_must_be_outside_source_tree(tmp_path: Path) -> None:
    source = tmp_path / "extracted"
    _write_source(source)
    receipt = _write_receipt(source / "receipt")
    with pytest.raises(intake.IntakeError, match="receipt must be stored outside"):
        intake.run_intake(
            source_root=source,
            receipt_path=receipt,
            output_root=tmp_path / "runs",
            run_id="unsafe-run",
        )
