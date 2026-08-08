"""Proxy-corpus provenance and quality contract tests."""

from __future__ import annotations

import hashlib

import pytest

from lloydk.hygiene import text_hash
from lloydk.proxy_corpus import (
    MATCHED_SYNTHETIC_TARGET_ORIGINS,
    PUBLIC_REAL,
    SYNTHETIC,
    assemble_proxy_gold,
    proxy_record_intended_use,
    validate_proxy_corpus,
    validate_proxy_record,
)


def _rich_text(seed: str = "가상 새한 프로젝트") -> str:
    paragraphs = [
        f"{seed}의 검토 목적은 신규 공정 전환의 재현성과 비용 영향을 함께 확인하는 것이다. 2026년 9월 3일 착수 후 기준선 수율 71.4퍼센트와 주기 48시간을 기록했고, 비교 대상 세 가지를 동일 조건으로 배치했다.",
        "첫 번째 관측에서는 온도 128도, 압력 0.84메가파스칼, 처리속도 분당 36개를 적용했다. 표본 240개 중 결함 19개가 발견됐으며 표면 편차와 냉각 지연이 동시에 나타난 구간을 별도로 표시했다.",
        "두 번째 대안은 공급 재료의 투입 순서를 바꾸고 안정화 시간을 17분 늘렸다. 결과 수율은 78.6퍼센트로 개선됐지만 에너지 사용량이 8.3퍼센트 증가해 월간 비용 추정치가 1천240만원 높아졌다.",
        "원인 검토팀은 센서 보정 오차, 작업자 교대 시점, 원료 보관 습도를 독립 변수로 분리했다. 각 변수마다 재시험 횟수와 중단 기준을 정했으며 관측값이 허용범위를 벗어나면 이전 조건으로 복귀하도록 했다.",
        "사업 영향 분석에서는 개발기간 14주 단축 가능성과 공급 계약 지연 위험을 함께 계산했다. 핵심 결합조건을 그대로 재현할 경우 경쟁자가 반복 시험 비용을 크게 줄일 수 있어 외부 자료에는 요약 결과만 싣기로 했다.",
        "문서 저장소는 승인된 책임자 계정으로 조회 범위를 제한하고 다운로드 이력과 반출 사유를 기록한다. 권한 검토는 매월 첫 근무일에 수행하며 프로젝트 종료 후 5일 이내에 임시 계정을 회수한다.",
        "다음 단계에서는 10월 12일까지 세 생산거점에서 교차 검증을 마치고 편차가 2.5퍼센트 이내인지 확인한다. 품질팀은 원자료를 보존하고 구매팀은 대체 공급 조건 두 가지를 비교해 운영위원회에 보고한다.",
        "최종 권고는 개선안을 제한된 파일럿에 적용하되 비용 상승과 냉각 지연을 별도 감시하는 것이다. 책임자는 측정 원본, 변경 승인, 예외 처리, 복구 결과를 하나의 추적표로 연결해 다음 회의에서 확인한다.",
        "재무 검토에서는 초기 설비 조정비 3천600만원, 분기 유지비 920만원, 예상 절감액 5천100만원을 구분했다. 환율과 원료가격 변동을 반영한 민감도 범위는 기준값 대비 마이너스 7퍼센트에서 플러스 11퍼센트다.",
        "현장 교육은 교대조별 4시간 과정으로 운영하고 첫 주에는 숙련 담당자가 모든 설정 변경을 확인한다. 작업표준 개정번호와 장비 로그 시각을 연결해 누가 어떤 근거로 값을 바꿨는지 사후 재구성할 수 있게 한다.",
        "검토위원은 성능 개선만으로 전환을 확정하지 않고 안전 여유, 공급 지속성, 복구 가능성을 함께 평가했다. 세 기준 가운데 하나라도 하한선에 미달하면 잔여 물량 420개에는 기존 공정을 유지한다.",
    ]
    return "\n\n".join(paragraphs)


def _public_record() -> dict:
    return {
        "doc_id": "public-1",
        "text": _rich_text("공개된 공공 연구보고서"),
        "label": "S3",
        "document_origin": "public_real",
        "proxy_role": "public_document",
        "document_family_id": "source-public-1",
        "document_type": "pilot-report",
        "source_id": "molit-aggregate-resource-surveys",
        "source_reference": "https://www.data.go.kr/data/15122643/fileData.do",
        "source_license": "KOGL-1",
        "source_sha256": "a" * 64,
        "license_evidence_sha256": "b" * 64,
        "retrieved_at": "2026-08-08T00:00:00+09:00",
        "training_use_permitted": True,
        "evaluation_use_permitted": True,
    }


def _simulation_record(
    *, label: str = "TS", doc_id: str = "sim-1", seed: str = "가상 새한 프로젝트"
) -> dict:
    scores = {
        "TS": {"secrecy": 2, "value": 2, "management": 2},
        "S1": {"secrecy": 2, "value": 2, "management": 0},
        "S2": {"secrecy": 1, "value": 1, "management": 1},
        "S3": {"secrecy": 0, "value": 0, "management": 0},
    }[label]
    return {
        "doc_id": doc_id,
        "text": _rich_text(seed),
        "label": label,
        "document_origin": "synthetic",
        "proxy_role": "confidential_simulation",
        "training_use_permitted": True,
        "evaluation_use_permitted": True,
        "document_family_id": "scenario-process-01",
        "document_type": "pilot-report",
        "scenario_id": "process-01",
        "expected_factor_scores": scores,
        "generation_lineage": ["scenario_catalog:v2", "generator:ollama:qwen3:14b"],
        "evidence_card": {
            "nonpublicity": "보유자를 통하지 않고는 결합조건을 알 수 없음",
            "competitive_value": "재현 비용과 개발기간을 줄일 수 있음",
            "access_controls": "시나리오에 정의된 관리 조건",
        },
    }


def _span(text: str, quote: str) -> dict:
    start = text.index(quote)
    return {
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "quote_sha256": hashlib.sha256(quote.encode()).hexdigest(),
    }


def _eligible_simulation(**kwargs: object) -> dict:
    row = _simulation_record(**kwargs)
    text = row["text"]
    quotes = [paragraph[:90] for paragraph in text.split("\n\n")[4:7]]
    row.update(
        {
            "decision_bucket": "gold_candidate",
            "consensus_evidence": {
                "agreement": True,
                "gate_status": "gold_candidate",
                "primary_valid_vote_count": 3,
                "primary_factor_scores": row["expected_factor_scores"],
            },
            "judging_lineage": ["primary_judge:local_openai:gemma3:12b"],
            "evidence_card": {
                "schema": "proxy-evidence-v1",
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "factors": {
                    "nonpublicity": {
                        "basis": "text",
                        "spans": [_span(text, quotes[0])],
                    },
                    "competitive_value": {
                        "basis": "text",
                        "spans": [_span(text, quotes[1])],
                    },
                    "access_controls": {
                        "basis": "text",
                        "spans": [_span(text, quotes[2])],
                    },
                },
            },
        }
    )
    return row


def _semantic_eligible_simulation(**kwargs: object) -> dict:
    row = _eligible_simulation(**kwargs)
    scores = row["expected_factor_scores"]
    label = row["label"]
    row["intended_label"] = label
    quality_checks = (
        "structure_appropriate",
        "timeline_consistent",
        "quantitative_consistent",
        "non_repetitive",
    )
    row["gate_version"] = "proxy_semantic_quality_v2"
    row["consensus_evidence"] = {
        "schema": "proxy-semantic-quality-adjudication-v2",
        "agreement": False,
        "rule_judge_agreement": False,
        "rule_advisory_only": True,
        "gate_status": "gold_candidate",
        "semantic_gate_passed": True,
        "semantic_gate_failures": [],
        "semantic_agreement": True,
        "intended_primary_agreement": True,
        "primary_grade": label,
        "primary_vote_count": 3,
        "primary_valid_vote_count": 3,
        "primary_parse_fail_count": 0,
        "primary_sample_count": 3,
        "primary_self_consistency": 1.0,
        "primary_self_consistency_valid": True,
        "min_self_consistency": 0.67,
        "primary_factor_scores": scores,
        "primary_factor_derived_grade": label,
        "expected_factor_derived_grade": label,
        "primary_factor_votes": {
            factor: {str(level): 3} for factor, level in scores.items()
        },
        "primary_factor_coverage": {factor: 3 for factor in scores},
        "factor_vote_complete": {factor: True for factor in scores},
        "factor_vote_expected_match": {factor: True for factor in scores},
        "primary_quality_required": True,
        "primary_quality_samples": [
            {
                "sample_index": index,
                "checks": {check: True for check in quality_checks},
                "issues": [],
            }
            for index in range(1, 4)
        ],
        "primary_quality_votes": {check: {"true": 3} for check in quality_checks},
        "primary_quality_coverage": {check: 3 for check in quality_checks},
        "quality_check_passed": {check: True for check in quality_checks},
        "document_quality_gate_passed": True,
        "document_quality_gate_failures": [],
    }
    return row


def test_public_real_s3_with_licence_is_eligible():
    assert validate_proxy_record(_public_record(), stage="eligible").ok


def test_semantic_proxy_gate_accepts_truthful_advisory_rule_disagreement():
    row = _semantic_eligible_simulation()

    check = validate_proxy_record(row, stage="eligible")

    assert check.ok
    assert row["consensus_evidence"]["agreement"] is False


def test_manual_audit_scaffold_cannot_become_eligible_without_signature():
    row = _semantic_eligible_simulation()
    row["requires_manual_audit"] = True

    assert "manual_audit:required_before_eligible" in validate_proxy_record(
        row, stage="eligible"
    ).errors

    row["manual_audit"] = {
        "approved": True,
        "auditor_id": "reviewer",
        "signed_at": "2026-08-08T00:00:00Z",
    }
    assert validate_proxy_record(row, stage="eligible").ok


def test_semantic_proxy_gate_rejects_rewritten_rule_agreement_or_vote_audit():
    rewritten = _semantic_eligible_simulation()
    rewritten["consensus_evidence"]["agreement"] = True
    assert (
        "adjudication:rule_agreement_audit_mismatch"
        in validate_proxy_record(rewritten, stage="eligible").errors
    )

    forged_votes = _semantic_eligible_simulation()
    forged_votes["consensus_evidence"]["primary_factor_votes"]["secrecy"] = {"1": 3}
    assert (
        "adjudication:invalid_primary_factor_vote_audit"
        in validate_proxy_record(forged_votes, stage="eligible").errors
    )


def test_semantic_proxy_gate_rejects_tampered_document_quality_audit():
    tampered = _semantic_eligible_simulation()
    tampered["consensus_evidence"]["primary_quality_samples"][0]["checks"][
        "quantitative_consistent"
    ] = "true"

    errors = validate_proxy_record(tampered, stage="eligible").errors

    assert "adjudication:incomplete_document_quality:quantitative_consistent" in errors
    assert "adjudication:invalid_document_quality_vote_audit" in errors


def test_high_grade_simulation_requires_context_and_evidence_card():
    assert validate_proxy_record(_simulation_record()).ok
    short = _simulation_record()
    short["text"] = "x" * 100
    short["evidence_card"] = {"nonpublicity": "only"}
    errors = validate_proxy_record(short).errors
    assert any(error.startswith("too_short:TS") for error in errors)
    assert any(error.startswith("missing:evidence_card:") for error in errors)


def test_repeated_placeholder_fails_measurable_quality_gate():
    record = _simulation_record()
    record["text"] = "가" * 1300
    errors = validate_proxy_record(record).errors
    assert any(error.startswith("quality:max_alnum_char_share") for error in errors)
    assert any(error.startswith("quality:unique_char4_ratio") for error in errors)


def test_public_real_cannot_be_used_as_high_grade_truth():
    record = _public_record()
    record["label"] = "S1"
    assert "public_real_requires_S3:S1" in validate_proxy_record(record).errors


def test_public_real_requires_explicit_training_permission():
    record = _public_record()
    record["training_use_permitted"] = False
    assert "training_use_not_permitted" in validate_proxy_record(record).errors


def test_evaluation_only_public_record_is_eligible_with_audit_warning():
    record = _public_record()
    record["training_use_permitted"] = False

    training = validate_proxy_record(record, intended_use="training")
    evaluation = validate_proxy_record(record, intended_use="evaluation")

    assert "training_use_not_permitted" in training.errors
    assert evaluation.ok
    assert evaluation.warnings == ("evaluation_only:not_training_permitted",)


def test_public_real_requires_explicit_evaluation_permission_for_evaluation():
    record = _public_record()
    record["evaluation_use_permitted"] = False

    check = validate_proxy_record(record, intended_use="evaluation")

    assert "evaluation_use_not_permitted" in check.errors


def test_synthetic_record_requires_permission_for_the_requested_use():
    record = _simulation_record()
    record["training_use_permitted"] = False
    record["evaluation_use_permitted"] = True

    training = validate_proxy_record(record, intended_use="training")
    evaluation = validate_proxy_record(record, intended_use="evaluation")

    assert "training_use_not_permitted" in training.errors
    assert evaluation.ok


@pytest.mark.parametrize(
    ("split_role", "training", "evaluation", "expected"),
    [
        ("train_pool_only", True, False, "training"),
        ("frozen_proxy_eval_only", False, True, "evaluation"),
    ],
)
def test_catalog_split_role_resolves_one_exact_intended_use(
    split_role: str, training: bool, evaluation: bool, expected: str
):
    record = {
        "catalog_split_role": split_role,
        "training_use_permitted": training,
        "evaluation_use_permitted": evaluation,
    }

    assert proxy_record_intended_use(record) == expected

    record["training_use_permitted"] = not training
    with pytest.raises(ValueError, match="catalog permission mismatch"):
        proxy_record_intended_use(record)


def test_corpus_report_counts_invalid_records():
    invalid = _public_record()
    invalid.pop("source_license")
    report = validate_proxy_corpus([_public_record(), invalid])
    assert report["total"] == 2 and report["valid"] == 1 and report["invalid"] == 1
    assert report["error_counts"]["missing:source_license"] == 1


def test_corpus_report_records_evaluation_permission_scope():
    record = _public_record()
    record["training_use_permitted"] = False

    report = validate_proxy_corpus([record], intended_use="evaluation")

    assert report["intended_use"] == "evaluation"
    assert report["valid"] == 1
    assert report["warning_counts"] == {"evaluation_only:not_training_permitted": 1}


def test_invalid_intended_use_fails_even_for_an_empty_corpus():
    with pytest.raises(ValueError, match="intended_use"):
        validate_proxy_corpus([], intended_use="publishing")


def test_assembly_reaches_exact_targets_and_excludes_blocked_families():
    public = _public_record()
    public["training_use_permitted"] = False
    s1 = _eligible_simulation(label="S1", doc_id="s1-1", seed="가상 다온 가격 프로젝트")
    s1["document_family_id"] = "s1-family"
    s2 = _eligible_simulation(label="S2", doc_id="s2-1", seed="가상 누리 운영 프로젝트")
    s2["document_family_id"] = "s2-family"
    blocked_ts = _eligible_simulation(
        doc_id="ts-blocked", seed="가상 미래 검증 프로젝트"
    )
    blocked_ts["document_family_id"] = "blocked-family"
    allowed_ts = _eligible_simulation(
        doc_id="ts-allowed", seed="가상 푸른 공정 프로젝트"
    )
    allowed_ts["document_family_id"] = "ts-family"

    result = assemble_proxy_gold(
        [public, s1, s2, blocked_ts, allowed_ts],
        targets={"TS": 1, "S1": 1, "S2": 1, "S3": 1},
        blocked_family_ids=["blocked-family"],
    )
    assert result.ready and len(result.selected) == 4
    assert {row["doc_id"] for row in result.selected} == {
        "public-1",
        "s1-1",
        "s2-1",
        "ts-allowed",
    }
    assert result.stats["blocked_family_records"] == 1


def test_assembly_is_not_ready_when_a_grade_is_short():
    result = assemble_proxy_gold([_public_record()], targets={"S3": 1, "TS": 1})
    assert not result.ready
    assert result.stats["missing_by_grade"] == {"TS": 1}


def test_assembly_normalizes_text_before_duplicate_check():
    first = _public_record()
    second = {**first, "doc_id": "public-2", "text": first["text"].replace(" ", "\n")}
    result = assemble_proxy_gold([first, second], targets={"S3": 2})
    assert not result.ready
    assert result.stats["dropped_duplicate_text"] == 1


def test_assembly_blocks_training_text_hash_even_when_spacing_differs():
    record = _public_record()
    result = assemble_proxy_gold(
        [record],
        targets={"S3": 1},
        blocked_text_hashes=[text_hash(record["text"].replace(" ", "\n"))],
    )
    assert not result.ready
    assert result.stats["blocked_text_records"] == 1


def test_assembly_blocks_holdout_doc_id_even_when_family_and_text_differ():
    record = _eligible_simulation(doc_id="shared-doc-id")
    result = assemble_proxy_gold(
        [record],
        targets={"TS": 1},
        blocked_doc_ids=["shared-doc-id"],
    )

    assert not result.ready
    assert result.stats["blocked_doc_id_records"] == 1
    assert result.stats["blocked_family_records"] == 0
    assert result.stats["blocked_text_records"] == 0


def test_primary_assembly_can_require_the_current_semantic_quality_gate():
    legacy = _eligible_simulation(doc_id="legacy-gate")
    current = _semantic_eligible_simulation(doc_id="current-gate")
    current.update(
        {
            "catalog_split_role": "frozen_proxy_eval_only",
            "training_use_permitted": False,
            "evaluation_use_permitted": True,
        }
    )

    legacy_result = assemble_proxy_gold(
        [legacy],
        targets={"TS": 1},
        require_catalog_usage_contract=True,
        required_synthetic_gate_version="proxy_semantic_quality_v2",
    )
    current_result = assemble_proxy_gold(
        [current],
        targets={"TS": 1},
        require_catalog_usage_contract=True,
        required_synthetic_gate_version="proxy_semantic_quality_v2",
    )

    assert not legacy_result.ready
    assert legacy_result.stats["invalid"] == 1
    assert current_result.ready
    assert current_result.stats["required_synthetic_gate_version"] == (
        "proxy_semantic_quality_v2"
    )


def test_primary_assembly_rejects_train_only_proxy_even_with_quality_v2():
    record = _semantic_eligible_simulation(doc_id="train-only")
    record.update(
        {
            "catalog_split_role": "train_pool_only",
            "training_use_permitted": True,
            "evaluation_use_permitted": False,
        }
    )

    result = assemble_proxy_gold(
        [record],
        targets={"TS": 1},
        intended_use="evaluation",
        require_catalog_usage_contract=True,
        required_synthetic_gate_version="proxy_semantic_quality_v2",
    )

    assert not result.ready
    assert result.stats["invalid"] == 1


def test_synthetic_s3_cannot_fill_public_real_cell():
    record = _eligible_simulation(label="S3", doc_id="synthetic-public")
    result = assemble_proxy_gold([record], targets={"S3": 1})
    assert not result.ready
    assert result.stats["wrong_origin_by_grade"] == {"S3": 1}


def test_matched_primary_profile_accepts_synthetic_s3_and_excludes_public_real():
    synthetic = _eligible_simulation(label="S3", doc_id="matched-s3")
    synthetic["document_family_id"] = "matched-s3-family"
    public = _public_record()
    public["training_use_permitted"] = False

    result = assemble_proxy_gold(
        [public, synthetic],
        targets={"S3": 1},
        expected_origins=MATCHED_SYNTHETIC_TARGET_ORIGINS,
    )

    assert result.ready
    assert [row["doc_id"] for row in result.selected] == ["matched-s3"]
    assert result.stats["expected_origin_by_grade"] == {"S3": "synthetic"}
    assert result.stats["origin_contract"] == "matched_synthetic_primary"
    assert result.stats["wrong_origin_by_grade"] == {"S3": 1}


def test_partial_synthetic_scenario_contract_keeps_public_s3_selection():
    synthetic = _eligible_simulation(
        label="TS", doc_id="high-grade", seed="고등급 가상 문서"
    )
    synthetic["document_family_id"] = "high-grade-family"
    synthetic["scenario_id"] = "ts-scenario"
    synthetic["factor_profile_id"] = "ts-s2-v2-m2"
    public = _public_record()
    public["training_use_permitted"] = False

    result = assemble_proxy_gold(
        [synthetic, public],
        targets={"TS": 1, "S3": 1},
        expected_origins={"TS": SYNTHETIC, "S3": PUBLIC_REAL},
        scenario_targets={"ts-scenario": 1},
        scenario_target_grades={"ts-scenario": "TS"},
        scenario_factor_profiles={"ts-scenario": "ts-s2-v2-m2"},
        min_families={"TS": 1, "S3": 1},
    )

    assert result.ready
    assert result.stats["selected_by_scenario"] == {"ts-scenario": 1}
    assert {row["doc_id"] for row in result.selected} == {"high-grade", "public-1"}


def test_assembly_rejects_incomplete_or_unknown_origin_contract():
    with pytest.raises(ValueError, match="expected origin for S3"):
        assemble_proxy_gold(
            [],
            targets={"S3": 1},
            expected_origins={},
        )


def test_actual_size_cell_requires_independent_families():
    rows = [
        _eligible_simulation(doc_id=f"ts-{index}", seed=f"가상 기술 검증 문서 {index}")
        for index in range(20)
    ]
    result = assemble_proxy_gold(rows, targets={"TS": 20})
    assert not result.ready
    assert result.stats["family_shortfall_by_grade"] == {"TS": 19}
