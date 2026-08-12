"""Proxy-candidate judge runner safety and audit contracts."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from koipa.modules.m3_labeling.judge import JudgeResult
from koipa.proxy_corpus import validate_proxy_record
from scripts import judge_proxy_candidates as judge_cli
from scripts.judge_proxy_candidates import (
    ProxyJudgeContractError,
    _static_rule_pipeline,
    attest_generation_input,
    create_unique_run_dir,
    judge_proxy_candidates,
    validate_candidates,
    validate_model_contract,
)


def test_static_rule_pipeline_is_db_independent(monkeypatch):
    def fail_if_called():
        raise AssertionError("operational DB rule loader must not be called")

    monkeypatch.setattr(
        "koipa.modules.m3_labeling.pipeline.build_rule_engine_from_db",
        fail_if_called,
    )
    pipeline, seed_sha256 = _static_rule_pipeline()
    assert pipeline.engine.seeds
    assert len(seed_sha256) == 64


def _candidate(
    doc_id: str,
    marker: str,
    *,
    intended: str = "S2",
    generator_provider: str = "ollama-gen",
    generator_model: str = "qwen3:14b",
    expected_factor_scores: dict[str, int] | None = None,
) -> dict:
    scores = expected_factor_scores or {
        "secrecy": 1,
        "value": 1,
        "management": 1,
    }
    paragraphs = [
        f"가상 프로젝트 {doc_id}의 변경 검토는 2026년 8월 8일 시작됐고 기준 처리량은 시간당 320건이다. 운영팀은 비용, 일정, 품질 변화를 서로 다른 표에서 비교했다.",
        "첫 관측 구간은 14시간 동안 유지했으며 오류 17건과 재시도 29건을 기록했다. 담당자는 원자료와 집계표의 시각을 맞춰 누락된 사건이 없는지 확인했다.",
        "두 번째 대안은 작업 순서를 바꾸고 검증 시간을 35분 늘리는 방식이다. 처리량은 8.4퍼센트 개선됐지만 월간 사용료가 760만원 늘어나는 것으로 계산됐다.",
        "원인 후보는 입력 편차, 장비 교대, 네트워크 지연으로 나눴다. 각 항목은 독립된 재현 절차와 중단 기준을 가지며 한 조건이 실패해도 다른 관측을 계속할 수 있다.",
        "외부에 전체 결합조건이 알려지면 반복 시험 비용을 줄이고 협상 기준을 역산할 가능성이 있다. 공개 설명에는 결론만 사용하고 상세 수치와 실패 경로는 제외한다.",
        "담당 부서는 구성원 계정으로 공유 범위를 제한하고 변경 이력을 보존한다. 별도 반출 심사는 없지만 종료일로부터 5일 안에 임시 권한을 회수한다.",
        "다음 검증은 9월 21일까지 세 환경에서 진행하며 편차가 2.7퍼센트 이내인지 확인한다. 결과표에는 책임자, 측정 장비, 원자료 위치, 예외 사유를 함께 연결한다.",
        "최종 권고는 제한된 시범 적용 후 복구 가능성과 비용 상한을 다시 심의하는 것이다. 품질 하한이나 일정 조건 중 하나라도 충족하지 못하면 기존 방식으로 돌아간다.",
    ]
    return {
        "doc_id": doc_id,
        "text": marker + "\n" + "\n\n".join(paragraphs),
        "label": intended,
        "label_source": "proxy_scenario_spec",
        "review_status": "proxy_gold_candidate",
        "source": "synthetic",
        "document_origin": "synthetic",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
        "document_family_id": f"family-{doc_id}",
        "document_type": "내부 검토 보고서",
        "domain": "tech",
        "scenario_id": f"scenario-{doc_id}",
        "factor_profile_id": "s2-s1-v1-m1",
        "expected_factor_scores": scores,
        "evidence_card": {
            "nonpublicity": "미공개 조건",
            "competitive_value": "재현 비용 절감",
            "access_controls": "지정 인원 열람",
        },
        "generation_lineage": [
            "scenario_catalog:test",
            f"generator:{generator_provider}:{generator_model}",
        ],
        "generated_at": "2026-08-08T00:00:00+00:00",
    }


_QUALITY_CHECKS = (
    "structure_appropriate",
    "timeline_consistent",
    "quantitative_consistent",
    "non_repetitive",
)


def _quality_result_fields(
    sample_count: int,
    *,
    samples: list[dict] | None = None,
) -> dict:
    if samples is None:
        samples = [
            {
                "sample_index": index,
                "checks": {check: True for check in _QUALITY_CHECKS},
                "issues": [],
            }
            for index in range(1, sample_count + 1)
        ]
    votes = {
        check: dict(
            Counter(
                sample["checks"][check]
                for sample in samples
                if type(sample.get("checks", {}).get(check)) is bool
            )
        )
        for check in _QUALITY_CHECKS
    }
    coverage = {check: sum(votes[check].values()) for check in _QUALITY_CHECKS}
    return {
        "document_quality_required": True,
        "quality_votes": votes,
        "quality_coverage": coverage,
        "quality_samples": samples,
    }


class _FakeRulePipeline:
    def label(self, text: str) -> SimpleNamespace:
        grade = "S2" if text.startswith("AGREE\n") else "S3"
        matched = SimpleNamespace(start=None, end=None)
        raw = SimpleNamespace(matched_keywords=[matched], management_evidenced=True)
        evidence = SimpleNamespace(
            start=0,
            end=5,
            text="가상 프로젝트",
            weight=1.2,
            tag="NON_PUBLICITY",
        )
        return SimpleNamespace(
            grade=grade,
            confidence=0.8,
            rule_result=raw,
            evidence=[evidence],
        )


class _FakeConsensusJudge:
    def __init__(self, *, interrupt_on: int | None = None) -> None:
        self.primary = SimpleNamespace(
            provider=SimpleNamespace(name="local_openai", model="gemma3:12b")
        )
        self.shadow = SimpleNamespace(
            provider=SimpleNamespace(name="local_openai", model="qwen3:14b")
        )
        self.interrupt_on = interrupt_on
        self.calls = 0

    def judge(self, text: str) -> JudgeResult:
        self.calls += 1
        if self.interrupt_on == self.calls:
            raise KeyboardInterrupt
        grade = "S2" if text.startswith("AGREE\n") else "S1"
        return JudgeResult(
            grade=grade,
            self_consistency=1.0,
            votes={grade: 2},
            mean_conf=0.9,
            shadow_grade=grade,
            airgap=False,
            primary_provider="local_openai",
            rationale="요소별 근거가 일치함",
            usage=[],
            factor_scores={"secrecy": 1, "value": 1, "management": 1},
            factor_votes={
                "secrecy": {1: 2},
                "value": {1: 2},
                "management": {1: 2},
            },
            factor_coverage={"secrecy": 2, "value": 2, "management": 2},
            sample_count=2,
            **_quality_result_fields(2),
        )


class _ParseFailJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:  # noqa: ARG002
        return JudgeResult(
            grade="S3",
            self_consistency=0.0,
            votes={"PARSE_FAIL": 2},
            mean_conf=0.0,
            shadow_grade=None,
            airgap=False,
            primary_provider="local_openai",
            rationale="",
            usage=[],
            factor_votes={"secrecy": {}, "value": {}, "management": {}},
            factor_coverage={"secrecy": 0, "value": 0, "management": 0},
            sample_count=2,
        )


class _FactorMismatchJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:  # noqa: ARG002
        return JudgeResult(
            grade="S2",
            self_consistency=1.0,
            votes={"S2": 3},
            mean_conf=0.9,
            shadow_grade="S2",
            airgap=False,
            primary_provider="local_openai",
            rationale="등급은 같지만 요소 점수가 시나리오와 다름",
            factor_scores={"secrecy": 2, "value": 1, "management": 1},
            factor_votes={
                "secrecy": {2: 3},
                "value": {1: 3},
                "management": {1: 3},
            },
            factor_coverage={"secrecy": 3, "value": 3, "management": 3},
            sample_count=3,
            **_quality_result_fields(3),
        )


class _TSJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:  # noqa: ARG002
        return JudgeResult(
            grade="TS",
            self_consistency=1.0,
            votes={"TS": 3},
            mean_conf=0.95,
            shadow_grade="TS",
            airgap=False,
            primary_provider="local_openai",
            rationale="비공지성·경제적 가치·관리성이 모두 명확함",
            factor_scores={"secrecy": 2, "value": 2, "management": 2},
            factor_votes={
                "secrecy": {2: 3},
                "value": {2: 3},
                "management": {2: 3},
            },
            factor_coverage={"secrecy": 3, "value": 3, "management": 3},
            sample_count=3,
            **_quality_result_fields(3),
        )


class _MissingFactorVoteJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:  # noqa: ARG002
        result = super().judge(text)
        return JudgeResult(
            **{
                **result.__dict__,
                "factor_votes": {
                    "secrecy": {1: 2},
                    "value": {1: 2},
                    "management": {1: 1},
                },
                "factor_coverage": {"secrecy": 2, "value": 2, "management": 1},
            }
        )


class _SplitFactorVoteJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:  # noqa: ARG002
        return JudgeResult(
            grade="S2",
            self_consistency=1.0,
            votes={"S2": 3},
            mean_conf=0.9,
            shadow_grade="S2",
            airgap=False,
            primary_provider="local_openai",
            rationale="등급 표는 일치하지만 요소 표가 갈림",
            factor_scores={"secrecy": 1, "value": 1, "management": 1},
            factor_votes={
                "secrecy": {1: 2, 2: 1},
                "value": {1: 3},
                "management": {1: 3},
            },
            factor_coverage={"secrecy": 3, "value": 3, "management": 3},
            sample_count=3,
        )


class _NumericContradictionJudge(_FakeConsensusJudge):
    def judge(self, text: str) -> JudgeResult:
        result = super().judge(text)
        spans = ["완료 80건", "성공률은 90%"]
        assert all(span in text for span in spans)
        samples = []
        for index in range(1, 3):
            checks = {check: True for check in _QUALITY_CHECKS}
            checks["quantitative_consistent"] = False
            samples.append(
                {
                    "sample_index": index,
                    "checks": checks,
                    "issues": [
                        {
                            "check": "quantitative_consistent",
                            "spans": spans,
                            "reason": "100건 중 80건 완료와 성공률 90%가 일치하지 않음",
                        }
                    ],
                }
            )
        return JudgeResult(
            **{
                **result.__dict__,
                **_quality_result_fields(2, samples=samples),
            }
        )


class _BrokenQualityVoteJudge(_FakeConsensusJudge):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    def judge(self, text: str) -> JudgeResult:
        result = super().judge(text)
        samples = _quality_result_fields(2)["quality_samples"]
        if self.mode == "missing":
            del samples[0]["checks"]["timeline_consistent"]
        elif self.mode == "malformed":
            samples[0]["checks"]["timeline_consistent"] = "true"
        elif self.mode == "split":
            samples[1]["checks"]["quantitative_consistent"] = False
            samples[1]["issues"] = [
                {
                    "check": "quantitative_consistent",
                    "spans": ["AGREE"],
                    "reason": "두 번째 표본은 수치 관계가 불명확하다고 판단함",
                }
            ]
        else:  # pragma: no cover - test helper contract
            raise AssertionError(self.mode)
        return JudgeResult(
            **{
                **result.__dict__,
                **_quality_result_fields(2, samples=samples),
            }
        )


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _judge_legacy(records, **kwargs):
    """Existing unit cases intentionally exercise the narrow in-memory bypass."""
    return judge_proxy_candidates(records, allow_unattested_legacy_input=True, **kwargs)


def _jsonl_bytes(rows: list[dict]) -> bytes:
    return b"".join(
        json.dumps(
            row, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        + b"\n"
        for row in rows
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_runtime_attestation(model: str, digest: str) -> dict[str, object]:
    core: dict[str, object] = {
        "schema_version": "ollama-model-attestation-v1",
        "status": "verified",
        "endpoint_kind": "ollama_openai_compatible",
        "endpoint_identity_sha256": "7" * 64,
        "requested_model": model,
        "canonical_model": model,
        "resolved_model": model,
        "live_model_digest": digest,
        "expected_model_digest": digest,
    }
    return {
        **core,
        "checked_at": "2026-08-08T00:00:00+00:00",
        "binding_sha256": hashlib.sha256(
            json.dumps(
                core,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }


def _write_generation_run(root: Path, records: list[dict]) -> Path:
    run_dir = root / "generation-source"
    run_dir.mkdir(parents=True)
    candidates_path = run_dir / "candidates.jsonl"
    rejected_path = run_dir / "rejected.jsonl"
    stats_path = run_dir / "stats.json"
    manifest_path = run_dir / "manifest.json"
    complete_path = run_dir / "COMPLETE.json"
    by_grade = dict(sorted(Counter(row["label"] for row in records).items()))
    by_scenario = dict(sorted(Counter(row["scenario_id"] for row in records).items()))
    by_profile = dict(
        sorted(Counter(row["factor_profile_id"] for row in records).items())
    )
    generation_namespace = "generation-source"
    generation_namespace_sha256 = hashlib.sha256(
        generation_namespace.encode("utf-8")
    ).hexdigest()
    model_digest = "sha256:" + "1" * 64
    model_attestation = _verified_runtime_attestation("qwen3:14b", model_digest)
    run_contract_material = {
        "schema_version": "proxy-generation-run-v3",
        "generation_namespace": generation_namespace,
        "catalog_version": "test-catalog-v1",
        "catalog_sha256": "b" * 64,
        "code_sha256": "c" * 64,
        "provider_identity_sha256": "d" * 64,
        "model_identity_sha256": "e" * 64,
        "model_runtime_attestation_sha256": model_attestation["binding_sha256"],
        "plan_sha256": "f" * 64,
        "selection_targets": by_grade,
        "selection_targets_by_scenario": by_scenario,
        "base_final_targets": by_grade,
        "base_final_targets_by_scenario": by_scenario,
        "max_quality_retries": 1,
    }
    run_contract = {
        **run_contract_material,
        "run_contract_sha256": hashlib.sha256(
            json.dumps(
                run_contract_material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
    }
    for index, record in enumerate(records):
        record.update(
            {
                "doc_id": (
                    f"proxy-{generation_namespace_sha256}-"
                    f"{record['scenario_id']}-{index:04d}"
                ),
                "generation_namespace": generation_namespace,
                "generation_namespace_sha256": generation_namespace_sha256,
                "generation_run_id": "generation-source",
                "generation_resume_key": f"resume-{index}",
                "generation_outcome": "candidate",
                "generation_contract": {
                    **run_contract,
                    "provider": "ollama-gen",
                    "model": "qwen3:14b",
                    "model_revision": model_digest,
                    "model_attestation": model_attestation,
                },
            }
        )
    candidates_path.write_bytes(_jsonl_bytes(records))
    rejected_path.write_bytes(b"")
    stats = {
        "run_id": "generation-source",
        "generation_namespace": generation_namespace,
        "planned": len(records),
        "completed": len(records),
        "unused_plan_items": 0,
        "candidates": len(records),
        "rejected": 0,
        "target_met": True,
        "selection_target_total": len(records),
        "selection_target_by_grade": by_grade,
        "selection_target_by_scenario": by_scenario,
        "base_final_target_by_grade": by_grade,
        "base_final_target_by_scenario": by_scenario,
        "candidate_by_grade": by_grade,
        "candidate_by_scenario": by_scenario,
        "candidate_by_factor_profile": by_profile,
    }
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        **run_contract,
        "run_id": "generation-source",
        "generation_namespace": generation_namespace,
        "status": "complete",
        "provider": {
            "runtime": "ollama-gen",
            "model": "qwen3:14b",
            "revision": model_digest,
            "endpoint_identity_sha256": model_attestation[
                "endpoint_identity_sha256"
            ],
            "model_attestation_binding_sha256": model_attestation[
                "binding_sha256"
            ],
        },
        "model_attestation": model_attestation,
        "latest_model_attestation": model_attestation,
        "stats": stats,
        "final_artifacts": {
            "candidates": "candidates.jsonl",
            "candidates_sha256": _sha256(candidates_path),
            "rejected": "rejected.jsonl",
            "rejected_sha256": _sha256(rejected_path),
            "stats": "stats.json",
            "stats_sha256": _sha256(stats_path),
        },
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    complete = {
        "schema_version": "proxy-generation-run-v3",
        "run_id": "generation-source",
        "generation_namespace": generation_namespace,
        "run_contract_sha256": run_contract["run_contract_sha256"],
        "model_runtime_attestation_sha256": model_attestation["binding_sha256"],
        "model_attestation": model_attestation,
        "manifest_sha256": _sha256(manifest_path),
        "candidates_sha256": _sha256(candidates_path),
        "rejected_sha256": _sha256(rejected_path),
        "stats_sha256": _sha256(stats_path),
        "target_met": True,
    }
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return candidates_path


def test_unattested_input_is_blocked_by_default(tmp_path):
    record = _candidate("d1", "AGREE")
    run_dir = create_unique_run_dir(tmp_path, "default-block")
    with pytest.raises(ProxyJudgeContractError, match="attested generation input_path"):
        judge_proxy_candidates(
            [record],
            run_dir=run_dir,
            judge=_FakeConsensusJudge(),
            judge_model="gemma3:12b",
            shadow_model="qwen3:14b",
            rule_pipeline=_FakeRulePipeline(),
        )
    assert not list(run_dir.iterdir())


def test_valid_generation_attestation_is_recorded_in_manifest_and_complete(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path / "upstream", records)
    attestation = attest_generation_input(input_path, records=records)
    assert attestation["status"] == "verified"
    assert attestation["generation_run_id"] == "generation-source"
    assert attestation["input_count"] == 1
    assert attestation["usage_contract"] == {
        "intended_use": "evaluation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "records": 1,
        "training_use_permitted": 0,
        "evaluation_use_permitted": 1,
    }
    run_dir = create_unique_run_dir(tmp_path, "attested-judge")
    stats = judge_proxy_candidates(
        records,
        run_dir=run_dir,
        judge=_FakeConsensusJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
        input_path=input_path,
    )
    assert stats["completed"] == 1
    assert stats["gold_by_factor_profile"] == {"s2-s1-v1-m1": 1}
    assert stats["gold_shortfall_by_factor_profile"] == {}
    assert stats["ready_for_exact_assembly"] is True
    gold = _read_jsonl(run_dir / "gold_candidate.jsonl")[0]
    assert gold["factor_profile_id"] == "s2-s1-v1-m1"
    assert gold["expected_factor_scores"] == {
        "secrecy": 1,
        "value": 1,
        "management": 1,
    }
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert manifest["upstream_generation"] == attestation
    assert complete["upstream_generation"] == attestation
    assert manifest["intended_use"] == "evaluation"
    assert manifest["catalog_split_role"] == "frozen_proxy_eval_only"
    assert complete["intended_use"] == "evaluation"
    assert complete["catalog_split_role"] == "frozen_proxy_eval_only"
    assert complete["upstream_generation"]["candidates_sha256"] == _sha256(input_path)


def test_generation_input_rejects_complete_or_row_model_attestation_tampering(
    tmp_path,
):
    complete_records = [_candidate("complete", "AGREE")]
    complete_input = _write_generation_run(
        tmp_path / "complete-tamper", complete_records
    )
    complete_path = complete_input.parent / "COMPLETE.json"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["model_attestation"]["binding_sha256"] = "0" * 64
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ProxyJudgeContractError, match="COMPLETE model attestation is invalid"
    ):
        attest_generation_input(complete_input, records=complete_records)

    row_records = [_candidate("row", "AGREE")]
    row_input = _write_generation_run(tmp_path / "row-tamper", row_records)
    rows = _read_jsonl(row_input)
    rows[0]["generation_contract"]["model_attestation"][
        "binding_sha256"
    ] = "0" * 64
    row_input.write_bytes(_jsonl_bytes(rows))
    manifest_path = row_input.parent / "manifest.json"
    complete_path = row_input.parent / "COMPLETE.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["final_artifacts"]["candidates_sha256"] = _sha256(row_input)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["candidates_sha256"] = _sha256(row_input)
    complete["manifest_sha256"] = _sha256(manifest_path)
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ProxyJudgeContractError, match="candidate model attestation invalid"
    ):
        attest_generation_input(row_input, records=rows)


def test_generation_v2_or_namespaceless_envelopes_are_not_migrated_silently(
    tmp_path,
):
    legacy_input = _write_generation_run(
        tmp_path / "legacy-schema", [_candidate("legacy", "AGREE")]
    )
    complete_path = legacy_input.parent / "COMPLETE.json"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["schema_version"] = "proxy-generation-run-v2"
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ProxyJudgeContractError, match="unsupported upstream generation COMPLETE schema"
    ):
        attest_generation_input(legacy_input)

    namespaceless_input = _write_generation_run(
        tmp_path / "namespaceless", [_candidate("namespaceless", "AGREE")]
    )
    run_dir = namespaceless_input.parent
    stats_path = run_dir / "stats.json"
    manifest_path = run_dir / "manifest.json"
    complete_path = run_dir / "COMPLETE.json"
    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats.pop("generation_namespace")
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stats"] = stats
    manifest["final_artifacts"]["stats_sha256"] = _sha256(stats_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["stats_sha256"] = _sha256(stats_path)
    complete["manifest_sha256"] = _sha256(manifest_path)
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(
        ProxyJudgeContractError, match="generation namespace envelope mismatch"
    ):
        attest_generation_input(namespaceless_input)

def test_judge_reports_profile_shortfall_after_uncertain_demotion(tmp_path):
    records = [
        _candidate("d1", "AGREE"),
        _candidate("d2", "DISAGREE"),
    ]
    input_path = _write_generation_run(tmp_path / "upstream-shortfall", records)
    run_dir = create_unique_run_dir(tmp_path, "profile-shortfall")
    stats = judge_proxy_candidates(
        records,
        run_dir=run_dir,
        judge=_FakeConsensusJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
        input_path=input_path,
    )

    assert stats["gold_candidate"] == 1
    assert stats["uncertain"] == 1
    assert stats["gold_shortfall_by_factor_profile"] == {"s2-s1-v1-m1": 1}
    assert stats["ready_for_exact_assembly"] is False


def test_generation_attestation_binds_catalog_split_and_usage_permissions(tmp_path):
    train_record = _candidate("train-1", "AGREE")
    train_record.update(
        {
            "catalog_split_role": "train_pool_only",
            "training_use_permitted": True,
            "evaluation_use_permitted": False,
        }
    )
    input_path = _write_generation_run(tmp_path / "training", [train_record])

    with pytest.raises(ProxyJudgeContractError, match="evaluation usage contract"):
        attest_generation_input(
            input_path,
            records=[train_record],
            intended_use="evaluation",
        )
    with pytest.raises(ProxyJudgeContractError, match="evaluation usage contract"):
        validate_candidates([train_record], intended_use="evaluation")

    attestation = attest_generation_input(
        input_path,
        records=[train_record],
        intended_use="training",
    )
    assert attestation["usage_contract"] == {
        "intended_use": "training",
        "catalog_split_role": "train_pool_only",
        "records": 1,
        "training_use_permitted": 1,
        "evaluation_use_permitted": 0,
    }


def test_cli_propagates_training_intended_use_end_to_end(tmp_path, monkeypatch):
    train_record = _candidate("train-cli", "AGREE")
    train_record.update(
        {
            "catalog_split_role": "train_pool_only",
            "training_use_permitted": True,
            "evaluation_use_permitted": False,
        }
    )
    input_path = _write_generation_run(tmp_path / "training-cli", [train_record])
    fake_judge = _FakeConsensusJudge()
    fake_judge.shadow = None
    monkeypatch.setattr(judge_cli, "_build_judge", lambda **_: fake_judge)
    monkeypatch.setattr(
        judge_cli,
        "verify_ollama_model",
        lambda **kwargs: _verified_runtime_attestation(
            str(kwargs["requested_model"]), str(kwargs["expected_manifest_sha256"])
        ),
    )
    output_root = tmp_path / "judged"

    assert (
        judge_cli.main(
            [
                "--input",
                str(input_path),
                "--out-root",
                str(output_root),
                "--run-id",
                "training-cli-judge",
                "--intended-use",
                "training",
                "--judge-model",
                "gemma3:12b",
                "--judge-model-manifest-sha256",
                "sha256:" + "2" * 64,
                "--no-shadow",
            ]
        )
        == 0
    )

    manifest = json.loads(
        (output_root / "training-cli-judge" / "run_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    assert manifest["intended_use"] == "training"
    assert manifest["catalog_split_role"] == "train_pool_only"
    assert manifest["stats"]["completed"] == 1
    assert manifest["primary_judge_runtime_attestation"]["status"] == "verified"


def test_missing_generation_envelope_is_blocked(tmp_path):
    input_path = tmp_path / "candidates.jsonl"
    input_path.write_bytes(_jsonl_bytes([_candidate("d1", "AGREE")]))
    with pytest.raises(ProxyJudgeContractError, match="missing or non-regular"):
        attest_generation_input(input_path)


def test_tampered_generation_candidate_is_blocked(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    input_path.write_bytes(input_path.read_bytes() + b"\n")
    with pytest.raises(ProxyJudgeContractError, match="candidates SHA-256 mismatch"):
        attest_generation_input(input_path, records=records)


def test_rehashed_candidate_must_still_bind_to_generation_run_contract(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    run_dir = input_path.parent
    manifest_path = run_dir / "manifest.json"
    complete_path = run_dir / "COMPLETE.json"

    tampered = _read_jsonl(input_path)
    tampered[0]["generation_run_id"] = "different-generation-run"
    input_path.write_bytes(_jsonl_bytes(tampered))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["final_artifacts"]["candidates_sha256"] = _sha256(input_path)
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["candidates_sha256"] = _sha256(input_path)
    complete["manifest_sha256"] = _sha256(manifest_path)
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ProxyJudgeContractError, match="generation_run_id mismatch"):
        attest_generation_input(input_path, records=tampered)


def test_rehashed_manifest_must_preserve_recomputed_generation_contract(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    manifest_path = input_path.parent / "manifest.json"
    complete_path = input_path.parent / "COMPLETE.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["catalog_version"] = "tampered-catalog"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["manifest_sha256"] = _sha256(manifest_path)
    complete_path.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with pytest.raises(ProxyJudgeContractError, match="run contract digest mismatch"):
        attest_generation_input(input_path, records=records)


def test_incomplete_generation_target_is_blocked(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    complete_path = input_path.parent / "COMPLETE.json"
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["target_met"] = False
    complete_path.write_text(json.dumps(complete), encoding="utf-8")
    with pytest.raises(ProxyJudgeContractError, match="target_met is not true"):
        attest_generation_input(input_path, records=records)


def test_noncanonical_or_wrong_in_memory_input_is_blocked(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    alternate = input_path.parent / "alternate.jsonl"
    alternate.write_bytes(input_path.read_bytes())
    with pytest.raises(ProxyJudgeContractError, match="canonical candidates.jsonl"):
        attest_generation_input(alternate, records=records)

    wrong_records = [_candidate("d2", "AGREE")]
    with pytest.raises(ProxyJudgeContractError, match="in-memory judge records"):
        attest_generation_input(input_path, records=wrong_records)


def test_rehashed_but_wrong_selected_count_is_blocked(tmp_path):
    records = [_candidate("d1", "AGREE")]
    input_path = _write_generation_run(tmp_path, records)
    run_dir = input_path.parent
    stats_path = run_dir / "stats.json"
    manifest_path = run_dir / "manifest.json"
    complete_path = run_dir / "COMPLETE.json"

    stats = json.loads(stats_path.read_text(encoding="utf-8"))
    stats["selection_target_total"] = 2
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["stats"] = stats
    manifest["final_artifacts"]["stats_sha256"] = _sha256(stats_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    complete = json.loads(complete_path.read_text(encoding="utf-8"))
    complete["stats_sha256"] = _sha256(stats_path)
    complete["manifest_sha256"] = _sha256(manifest_path)
    complete_path.write_text(json.dumps(complete, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(ProxyJudgeContractError, match="selected candidate count"):
        attest_generation_input(input_path, records=records)


def test_independence_and_unknown_model_contracts_are_blocked():
    candidate = _candidate("d1", "AGREE")
    with pytest.raises(ProxyJudgeContractError, match="independent"):
        validate_model_contract(
            [candidate], judge_model="Qwen/Qwen3-14B-AWQ", shadow_model=None
        )
    with pytest.raises(ProxyJudgeContractError, match="blocked model"):
        validate_model_contract([candidate], judge_model="noop", shadow_model=None)

    unknown = _candidate("d2", "AGREE", generator_model="unknown")
    with pytest.raises(ProxyJudgeContractError, match="blocked model"):
        validate_model_contract([unknown], judge_model="gemma3:12b", shadow_model=None)


def test_run_preserves_provenance_and_separates_final_buckets(tmp_path):
    records = [_candidate("d1", "AGREE"), _candidate("d2", "DISAGREE")]
    run_dir = create_unique_run_dir(tmp_path, "run-001")

    stats = _judge_legacy(
        records,
        run_dir=run_dir,
        judge=_FakeConsensusJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 1
    assert stats["uncertain"] == 1
    gold = _read_jsonl(run_dir / "gold_candidate.jsonl")[0]
    uncertain = _read_jsonl(run_dir / "uncertain.jsonl")[0]

    assert gold["doc_id"] == "d1" and gold["label"] == "S2"
    assert gold["intended_label"] == "S2"
    assert gold["input_label_source"] == "proxy_scenario_spec"
    assert gold["generation_lineage"] == records[0]["generation_lineage"]
    assert gold["document_family_id"] == records[0]["document_family_id"]
    assert gold["source_record_sha256"]
    evidence = gold["consensus_evidence"]
    assert evidence["rule_evidence_state"] == "present"
    assert evidence["primary_votes"] == {"S2": 2}
    assert evidence["primary_vote_state"] == "recorded"
    assert evidence["primary_self_consistency"] == 1.0
    assert evidence["gate_status"] == "gold_candidate"
    assert evidence["document_quality_gate_passed"] is True
    assert all(evidence["quality_check_passed"].values())

    assert uncertain["doc_id"] == "d2"
    assert uncertain["label"] is None
    assert uncertain["decision_bucket"] == "uncertain"
    assert uncertain["consensus_evidence"]["agreement"] is False

    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["claim_scope"] == "synthetic_proxy_candidate_only"
    assert manifest["human_reviewed"] is False
    assert len(_read_jsonl(run_dir / "decisions.journal.jsonl")) == 2
    assert not list(run_dir.glob(".*.tmp"))


def test_unique_run_directory_refuses_overwrite(tmp_path):
    first = create_unique_run_dir(tmp_path, "same-run")
    assert first.is_dir()
    with pytest.raises(ProxyJudgeContractError, match="refusing overwrite"):
        create_unique_run_dir(tmp_path, "same-run")


def test_runtime_noop_judge_is_blocked_before_output(tmp_path):
    record = _candidate("d1", "AGREE")
    judge = _FakeConsensusJudge()
    judge.primary.provider.name = "noop"
    run_dir = create_unique_run_dir(tmp_path, "noop-run")

    with pytest.raises(ProxyJudgeContractError, match="blocked provider"):
        _judge_legacy(
            [record],
            run_dir=run_dir,
            judge=judge,
            judge_model="gemma3:12b",
            shadow_model="qwen3:14b",
            rule_pipeline=_FakeRulePipeline(),
        )
    assert not list(run_dir.iterdir())


def test_all_parse_failures_are_uncertain_and_audited(tmp_path):
    run_dir = create_unique_run_dir(tmp_path, "parse-fail-run")
    stats = _judge_legacy(
        [_candidate("d1", "DISAGREE")],
        run_dir=run_dir,
        judge=_ParseFailJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 0
    assert stats["uncertain"] == 1
    assert stats["judge_parse_failures"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    evidence = row["consensus_evidence"]
    assert evidence["primary_vote_state"] == "parse_failed"
    assert evidence["primary_parse_fail_count"] == 2


def test_matching_grade_with_wrong_factor_scores_is_demoted_to_uncertain(tmp_path):
    run_dir = create_unique_run_dir(tmp_path, "factor-mismatch-run")
    stats = _judge_legacy(
        [_candidate("d1", "AGREE")],
        run_dir=run_dir,
        judge=_FactorMismatchJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )
    assert stats["gold_candidate"] == 0 and stats["uncertain"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    assert row["status"] == "needs_review_primary_factors_mismatch"
    assert (
        "primary_factors_mismatch"
        in (row["consensus_evidence"]["semantic_gate_failures"])
    )


def test_rule_disagreement_is_advisory_for_complete_ts_semantic_case(tmp_path):
    record = _candidate(
        "highcase",
        "AGREE",
        intended="TS",
        expected_factor_scores={"secrecy": 2, "value": 2, "management": 2},
    )
    record["text"] += "\n\n" + "\n\n".join(
        [
            "시험 책임자는 원자료의 결합 순서와 계산 계수를 승인된 연구원에게만 열람시키며, 외부 발표 자료에서는 재현에 필요한 상세 수치와 실패 조건을 모두 제외한다.",
            "경쟁사가 동일한 공정 조건을 독자적으로 다시 찾으려면 장비 교체와 장기간의 반복 시험이 필요하고, 선점 기간을 잃을 경우 예상 손실과 개발 비용이 크게 증가한다.",
            "저장소 접근 권한은 담당 부서 책임자의 사전 승인으로 부여되고 조회 기록과 파일 반출 로그를 매주 대조하며, 프로젝트 종료 직후 임시 계정을 회수한다.",
            "두 번째 검증에서는 원료 투입 간격과 온도 편차를 각각 다른 범위로 바꾸어 수율 변화를 확인하고, 측정 장비의 보정 이력까지 원자료와 함께 연결한다.",
            "협상 담당자는 상대 회사에 결과의 요약값만 제공하고 원가 절감 계산식과 예외 처리 절차는 공유하지 않으며, 회의 참석자의 열람 범위도 역할별로 제한한다.",
            "변경 승인을 받은 계산 결과는 별도 검증자가 원본 기록과 대조하고 차이가 발견되면 배포를 중단한 뒤 원인과 수정 이력을 책임자에게 보고한다.",
        ]
    )
    run_dir = create_unique_run_dir(tmp_path, "ts-rule-advisory-run")
    stats = _judge_legacy(
        [record],
        run_dir=run_dir,
        judge=_TSJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 1 and stats["uncertain"] == 0
    row = _read_jsonl(run_dir / "gold_candidate.jsonl")[0]
    evidence = row["consensus_evidence"]
    assert row["label"] == "TS"
    assert row["rule_grade"] == "S2" and row["llm_grade"] == "TS"
    assert row["agreement"] is False
    assert "advisory_rule_disagreement" in row["flags"]
    assert evidence["agreement"] is False
    assert evidence["rule_judge_agreement"] is False
    assert evidence["semantic_gate_passed"] is True
    assert evidence["proxy_contract_passed"] is True
    assert validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok


def test_numeric_contradiction_is_fail_closed_with_exact_issue_spans(tmp_path):
    record = _candidate("numeric-conflict", "AGREE")
    record["text"] += (
        "\n\n정량 검증 결과: 전체 100건 가운데 완료 80건으로 집계했으며, "
        "같은 모집단의 성공률은 90%라고 보고했다."
    )
    run_dir = create_unique_run_dir(tmp_path, "numeric-quality-fail")

    stats = _judge_legacy(
        [record],
        run_dir=run_dir,
        judge=_NumericContradictionJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 0 and stats["uncertain"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    assert row["status"] == (
        "needs_review_document_quality_failed:quantitative_consistent"
    )
    sample = row["consensus_evidence"]["primary_quality_samples"][0]
    spans = sample["issues"][0]["spans"]
    assert [span["quote"] for span in spans] == ["완료 80건", "성공률은 90%"]
    assert all(
        record["text"][span["start"] : span["end"]] == span["quote"] for span in spans
    )
    assert all(len(span["quote_sha256"]) == 64 for span in spans)
    assert row["consensus_evidence"]["document_quality_gate_passed"] is False


def test_repetition_issue_requires_two_distinct_verbatim_spans():
    text = "같은 사실을 반복한 첫 문장과 별도 결론 문장"
    checks = {check: True for check in _QUALITY_CHECKS}
    checks["non_repetitive"] = False
    samples = [
        {
            "sample_index": 1,
            "checks": checks,
            "issues": [
                {
                    "check": "non_repetitive",
                    "spans": ["같은 사실", "같은 사실"],
                    "reason": "동일 span을 중복 제출함",
                }
            ],
        }
    ]
    result = SimpleNamespace(**_quality_result_fields(1, samples=samples))

    _, failures = judge_cli._document_quality_audit(
        judge_result=result,
        text=text,
        sample_count=1,
    )

    assert "invalid_document_quality_issue" in failures
    assert "missing_document_quality_issue:non_repetitive" in failures


@pytest.mark.parametrize(
    ("mode", "failure"),
    [
        ("missing", "incomplete_document_quality:timeline_consistent"),
        ("malformed", "incomplete_document_quality:timeline_consistent"),
        ("split", "document_quality_disagreement:quantitative_consistent"),
    ],
)
def test_missing_malformed_or_split_quality_votes_are_uncertain(
    tmp_path, mode, failure
):
    run_dir = create_unique_run_dir(tmp_path, f"quality-{mode}")

    stats = _judge_legacy(
        [_candidate(f"quality-{mode}", "AGREE")],
        run_dir=run_dir,
        judge=_BrokenQualityVoteJudge(mode),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 0 and stats["uncertain"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    assert failure in row["consensus_evidence"]["semantic_gate_failures"]


def test_primary_grade_mismatch_remains_uncertain_even_when_rule_matches(tmp_path):
    run_dir = create_unique_run_dir(tmp_path, "judge-mismatch-run")
    stats = _judge_legacy(
        [_candidate("d1", "DISAGREE")],
        run_dir=run_dir,
        judge=_FakeConsensusJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 0 and stats["uncertain"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    assert "judge_label_mismatch" in row["consensus_evidence"]["semantic_gate_failures"]


@pytest.mark.parametrize(
    ("judge", "failure"),
    [
        (_MissingFactorVoteJudge(), "incomplete_factor_votes:management"),
        (_SplitFactorVoteJudge(), "factor_vote_disagreement:secrecy"),
    ],
)
def test_missing_or_inconsistent_factor_votes_are_uncertain(tmp_path, judge, failure):
    run_dir = create_unique_run_dir(tmp_path, failure.replace(":", "-"))
    stats = _judge_legacy(
        [_candidate("d1", "AGREE")],
        run_dir=run_dir,
        judge=judge,
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert stats["gold_candidate"] == 0 and stats["uncertain"] == 1
    row = _read_jsonl(run_dir / "uncertain.jsonl")[0]
    assert failure in row["consensus_evidence"]["semantic_gate_failures"]


def test_interruption_keeps_fsynced_journal_without_publishing_final_buckets(tmp_path):
    records = [_candidate("d1", "AGREE"), _candidate("d2", "AGREE")]
    run_dir = create_unique_run_dir(tmp_path, "interrupted-run")

    with pytest.raises(KeyboardInterrupt):
        _judge_legacy(
            records,
            run_dir=run_dir,
            judge=_FakeConsensusJudge(interrupt_on=2),
            judge_model="gemma3:12b",
            shadow_model="qwen3:14b",
            rule_pipeline=_FakeRulePipeline(),
        )

    assert len(_read_jsonl(run_dir / "decisions.journal.jsonl")) == 1
    progress = json.loads((run_dir / "progress.json").read_text(encoding="utf-8"))
    assert progress["completed"] == 1
    manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted"
    assert not (run_dir / "gold_candidate.jsonl").exists()
    assert not (run_dir / "uncertain.jsonl").exists()


def test_build_judge_is_self_contained_for_minimal_runtime_release():
    judge = judge_cli._build_judge(
        base_url="http://example.invalid:11434/v1",
        judge_model="gemma3:12b",
        shadow_model=None,
        k_min=2,
        k_max=3,
        temperature=0.6,
    )

    assert judge.primary.provider.model == "gemma3:12b"
    assert judge.primary.provider.enable_thinking is False
    assert judge.shadow is None
    assert judge.require_document_quality is True


def test_atomic_publish_chmods_temp_before_replace_and_final_after(
    tmp_path, monkeypatch
):
    events: list[tuple[str, Path, object]] = []
    real_chmod = judge_cli.os.chmod
    real_replace = judge_cli.os.replace

    def recording_chmod(path, mode):
        events.append(("chmod", Path(path), mode))
        real_chmod(path, mode)

    def recording_replace(source, target):
        events.append(("replace", Path(source), Path(target)))
        real_replace(source, target)

    monkeypatch.setattr(judge_cli.os, "chmod", recording_chmod)
    monkeypatch.setattr(judge_cli.os, "replace", recording_replace)
    run_dir = create_unique_run_dir(tmp_path, "atomic-permission-run")
    target = run_dir / "artifact.json"
    events.clear()

    judge_cli._atomic_write_json(target, {"version": 1})

    replace_index = next(
        index for index, event in enumerate(events) if event[0] == "replace"
    )
    source = events[replace_index][1]
    assert ("chmod", source, judge_cli.ARTIFACT_FILE_MODE) in events[:replace_index]
    assert (
        "chmod",
        target,
        judge_cli.ARTIFACT_FILE_MODE,
    ) in events[replace_index + 1 :]

    events.clear()
    judge_cli._atomic_write_json(target, {"version": 2}, replace=True)
    replace_index = next(
        index for index, event in enumerate(events) if event[0] == "replace"
    )
    source = events[replace_index][1]
    assert ("chmod", source, judge_cli.ARTIFACT_FILE_MODE) in events[:replace_index]
    assert (
        "chmod",
        target,
        judge_cli.ARTIFACT_FILE_MODE,
    ) in events[replace_index + 1 :]


def test_completed_judge_run_chmods_every_artifact_for_operator_group(
    tmp_path, monkeypatch
):
    real_chmod = judge_cli.os.chmod
    chmod_calls: list[tuple[Path, int]] = []

    def recording_chmod(path, mode):
        chmod_calls.append((Path(path), mode))
        real_chmod(path, mode)

    monkeypatch.setattr(judge_cli.os, "chmod", recording_chmod)
    run_dir = create_unique_run_dir(tmp_path, "group-readable-run")
    _judge_legacy(
        [_candidate("permissions", "AGREE")],
        run_dir=run_dir,
        judge=_FakeConsensusJudge(),
        judge_model="gemma3:12b",
        shadow_model="qwen3:14b",
        rule_pipeline=_FakeRulePipeline(),
    )

    assert (run_dir, judge_cli.ARTIFACT_DIRECTORY_MODE) in chmod_calls
    expected_files = {
        run_dir / "run_manifest.json",
        run_dir / "progress.json",
        run_dir / "decisions.journal.jsonl",
        run_dir / "gold_candidate.jsonl",
        run_dir / "uncertain.jsonl",
        run_dir / "stats.json",
        run_dir / "COMPLETE.json",
    }
    final_file_modes = {
        path: mode for path, mode in chmod_calls if path in expected_files
    }
    assert final_file_modes == {
        path: judge_cli.ARTIFACT_FILE_MODE for path in expected_files
    }
