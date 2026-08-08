"""Immutable, resumable proxy-scenario generation run contracts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from types import SimpleNamespace

import pytest

import scripts.build_proxy_scenarios as scenario_builder
from lloydk.ollama_attestation import verify_ollama_model
from scripts.build_proxy_scenarios import (
    _CONTEXT_SAFETY_TOKENS,
    _OLLAMA_CONTEXT_WINDOW_TOKENS,
    _PROFILE_TOKEN_CONTRACT,
    ProxyGenerationRunError,
    _bounded_retry_draft,
    _classification_style_marker_errors,
    _create_empty_journal,
    _create_run_dir,
    _document_completion_errors,
    _document_access_errors,
    _fact_ledger_errors,
    _fact_ledger_block,
    _fact_ledger_for_item,
    _fact_ledger_prompt,
    _generation_prompt_artifact_errors,
    _allowed_prompt_numeric_tokens,
    _unapproved_numeric_claim_errors,
    _generate_plan_item,
    _nonpublic_document_access_errors,
    _profile_structure_requirements,
    _proxy_output_token_budget,
    _proxy_retry_draft_limit,
    _retry_revision_context,
    _retry_problem_summary,
    _s3_reconstruction_detail_errors,
    _zero_value_reconstruction_detail_errors,
    _atomic_write_bytes,
    completion_exit_code,
    describe_plan,
    run_generation,
)
from scripts.judge_proxy_candidates import attest_generation_input


_TEST_MODEL_DIGEST = "1" * 64


class _TagsResponse:
    status = 200

    def __init__(self, model: str, digest: str) -> None:
        self.body = json.dumps(
            {"models": [{"name": model, "model": model, "digest": digest}]}
        ).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.body[:limit]


@pytest.fixture(autouse=True)
def _local_ollama_inventory(monkeypatch):
    def attest(*, base_url, requested_model, expected_manifest_sha256):
        expected = str(expected_manifest_sha256).removeprefix("sha256:")
        return verify_ollama_model(
            base_url=base_url,
            requested_model=requested_model,
            expected_manifest_sha256=expected_manifest_sha256,
            urlopen=lambda *_args, **_kwargs: _TagsResponse(
                requested_model, expected
            ),
        )

    monkeypatch.setattr(scenario_builder, "verify_ollama_model", attest)


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode contract")
def test_generation_artifacts_are_group_readable_and_directories_traversable(
    tmp_path,
):
    run_dir = _create_run_dir(tmp_path, "shared-mode")
    journal = run_dir / "rows.journal.jsonl"
    artifact = run_dir / "manifest.json"
    _create_empty_journal(journal)
    _atomic_write_bytes(artifact, b"{}\n")

    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o2750
    assert stat.S_IMODE(journal.stat().st_mode) == 0o640
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o640


def _scenario() -> dict:
    return {
        "scenario_id": "matched-tech-s2",
        "document_family_id": "matched-tech-v2",
        "label": "S2",
        "domain": "tech",
        "industry": "manufacturing",
        "document_type": "내부 검토 보고서",
        "min_chars": 700,
        "max_chars": 1800,
        "scenario_context": "가상 프로젝트의 내부 운영 검토 상황",
        "disclosure_scope": "담당 조직 내부 공유",
        "harm_potential": "일정 및 협상 대응력 저하",
        "catalog_split_role": "train_pool_only",
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
        "expected_factor_scores": {"secrecy": 1, "value": 1, "management": 1},
        "evidence_card": {
            "nonpublicity": "내부 검토 중인 조건",
            "competitive_value": "일정과 비용 최적화",
            "access_controls": "담당 조직 공유",
        },
    }


def _plan(count: int = 3) -> list[tuple[dict, dict, dict, int]]:
    scenario = _scenario()
    family = {
        "family_profile_id": "pilot-report",
        "length_profile_id": "medium",
        "document_shape": "결과 보고서",
        "context": "배경, 조건표, 결과, 후속 조치 순서",
        "min_chars": 700,
        "max_chars": 1800,
    }
    return [
        (
            scenario,
            {
                "instance_profile_id": f"instance-{index}",
                "context": f"서로 독립적인 가상 사업 조건 {index}",
            },
            family,
            index,
        )
        for index in range(count)
    ]


def _two_scenario_plan() -> list[tuple[dict, dict, dict, int]]:
    first = _scenario()
    second = {
        **_scenario(),
        "scenario_id": "matched-business-s2",
        "document_family_id": "matched-business-v2",
    }
    family = {
        "family_profile_id": "pilot-report",
        "length_profile_id": "medium",
        "document_shape": "결과 보고서",
        "context": "배경, 조건표, 결과, 후속 조치 순서",
        "min_chars": 700,
        "max_chars": 1800,
    }
    return [
        (
            scenario,
            {
                "instance_profile_id": f"{scenario['scenario_id']}-{ordinal}",
                "context": f"독립 조건 {ordinal}",
            },
            family,
            ordinal,
        )
        for scenario in (first, second)
        for ordinal in range(2)
    ]


class _RecordingProvider:
    name = "recording_local"
    model = "qwen3:14b"
    model_revision = "sha-model-001"
    base_url = "http://localhost:11434/v1"

    def __init__(
        self, *, interrupt_on: int | None = None, malformed: bool = False
    ) -> None:
        self.interrupt_on = interrupt_on
        self.malformed = malformed
        self.calls = 0
        self.prompts: list[str] = []
        self.system_prompts: list[str] = []
        self.max_tokens: list[int] = []

    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        self.calls += 1
        self.prompts.append(prompt)
        self.system_prompts.append(str(kwargs.get("system") or ""))
        self.max_tokens.append(int(kwargs["max_tokens"]))
        if self.interrupt_on == self.calls:
            raise KeyboardInterrupt
        if self.malformed:
            return SimpleNamespace(text="not-json", usage=None)
        body = "\n\n".join(
            [
                f"1. 검토 배경\n가상 실행 번호 {self.calls}의 목적은 신규 조립 공정을 시범 라인에 적용하기 전에 설비 제약과 운영 영향을 확인하는 것이다. 기준 기간은 2026년 9월부터 11월까지이며 현장 책임자와 품질 담당자가 관찰 결과를 기록한다.",
                "2. 기준선 측정\n기존 방식은 시간당 84개를 처리했고 평균 대기시간은 17분이었다. 첫 주 표본 36건에서 치수 편차가 발견되어 센서 보정값과 작업 순서를 서로 다른 조건으로 나누어 비교했다.",
                "3. 변경 조건\n투입 간격을 42초로 조정하고 예열 구간을 63도로 유지했다. 작업자는 자재 배치번호, 장비 진동, 표면 상태를 각각 기록했으며 중간 점검에서 나온 이상 신호는 별도 원인표에 연결했다.",
                "4. 품질 결과\n두 번째 실험에서는 불완전 조립이 12건에서 4건으로 감소했다. 검사자는 외관, 강도, 정렬 오차를 독립적으로 확인했고 재검 표본에서도 같은 방향의 개선이 관찰되었다.",
                "5. 비용 분석\n월간 예상 절감액은 3,800만원이며 소모품 교체비는 620만원으로 계산했다. 다만 야간 운영 인력 2명과 예방 정비 6시간이 추가되므로 재무 검토에서는 보수적인 가정을 적용한다.",
                "6. 일정과 위험\n시범 운영은 10월 7일에 시작하고 3주 뒤 확대 여부를 판단한다. 공급 지연, 측정기 오류, 교육 미이수 가능성을 주요 위험으로 선정하고 각 상황에 대한 중단 기준과 복구 담당을 정했다.",
                "7. 후속 조치\n운영팀은 매주 수요일 지표를 갱신하고 품질팀은 원시 측정값의 누락 여부를 확인한다. 다음 회의에서는 생산성뿐 아니라 안전성, 유지보수성, 납기 영향까지 함께 검토한 뒤 승인 요청서를 작성한다.",
            ]
        )
        return SimpleNamespace(
            text=json.dumps(
                {
                    "title": f"가상 운영 검토 {self.calls}",
                    "body": body,
                    "document_type": "내부 검토 보고서",
                    "dept_hint": "사업운영",
                    "rationale_tags": ["internal", "planning"],
                },
                ensure_ascii=False,
            ),
            usage=None,
        )


class _LedgerSuffixProvider(_RecordingProvider):
    def __init__(self, suffix: str) -> None:
        super().__init__()
        self.suffix = suffix
        self.raw_title = ""
        self.raw_body = ""

    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        response = super().generate(prompt, **kwargs)
        payload = json.loads(response.text)
        payload["body"] = str(payload["body"]) + self.suffix
        self.raw_title = str(payload["title"])
        self.raw_body = str(payload["body"])
        response.text = json.dumps(payload, ensure_ascii=False)
        return response


class _ShortThenGoodProvider(_RecordingProvider):
    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        response = super().generate(prompt, **kwargs)
        if self.calls == 1:
            payload = json.loads(response.text)
            payload["body"] = "첫 출력은 의도적으로 짧다."
            response.text = json.dumps(payload, ensure_ascii=False)
        return response


class _LongThenGoodProvider(_RecordingProvider):
    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        response = super().generate(prompt, **kwargs)
        if self.calls == 1:
            payload = json.loads(response.text)
            payload["body"] = payload["body"] + "\n\n" + ("과도한 부록 문장. " * 180)
            response.text = json.dumps(payload, ensure_ascii=False)
        return response


class _AlwaysLongProvider(_RecordingProvider):
    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        response = super().generate(prompt, **kwargs)
        payload = json.loads(response.text)
        payload["body"] = payload["body"] + "\n\n" + ("과도한 부록 문장. " * 180)
        response.text = json.dumps(payload, ensure_ascii=False)
        return response


class _TruncatedThenGoodProvider(_RecordingProvider):
    def __init__(
        self, *, always_truncated: bool = False, truncated_calls: int = 1
    ) -> None:
        super().__init__()
        self.always_truncated = always_truncated
        self.truncated_calls = truncated_calls

    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        if self.always_truncated or self.calls < self.truncated_calls:
            self.calls += 1
            self.prompts.append(prompt)
            self.system_prompts.append(str(kwargs.get("system") or ""))
            limit = int(kwargs["max_tokens"])
            self.max_tokens.append(limit)
            return SimpleNamespace(
                text='{"title":"절단 응답","body":"' + ("가" * 900),
                usage=SimpleNamespace(input_tokens=1550, output_tokens=limit),
                meta={"finish_reason": "length"},
            )
        response = super().generate(prompt, **kwargs)
        response.usage = SimpleNamespace(input_tokens=1550, output_tokens=1200)
        response.meta = {"finish_reason": "stop"}
        return response


def _run(
    tmp_path,
    provider,
    *,
    run_id: str | None = None,
    resume_run=None,
    allow_partial: bool = False,
    plan=None,
    selection_targets=None,
    selection_targets_by_scenario=None,
    base_final_targets=None,
    base_final_targets_by_scenario=None,
    declared_model_revision: str | None = "sha256:" + _TEST_MODEL_DIGEST,
    max_quality_retries: int = 1,
    generation_namespace: str | None = None,
):
    return run_generation(
        plan or _plan(),
        provider=provider,
        requested_provider="local_openai",
        catalog_version="proxy-scenarios-v2",
        catalog_sha256="a" * 64,
        code_sha256="b" * 64,
        out_root=tmp_path,
        run_id=run_id,
        resume_run=resume_run,
        allow_partial=allow_partial,
        selection_targets=selection_targets,
        selection_targets_by_scenario=selection_targets_by_scenario,
        base_final_targets=base_final_targets,
        base_final_targets_by_scenario=base_final_targets_by_scenario,
        declared_model_revision=declared_model_revision,
        max_quality_retries=max_quality_retries,
        generation_namespace=generation_namespace,
    )


def _read_jsonl(path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_completed_run_is_immutable_and_records_hashes_and_counts(tmp_path):
    plan = _plan(2)
    run_dir, stats = _run(tmp_path, _RecordingProvider(), run_id="run-001", plan=plan)

    assert stats["planned"] == stats["completed"] == stats["candidates"] == 2
    assert stats["rejected"] == 0 and stats["target_met"] is True
    assert (run_dir / "COMPLETE.json").is_file()
    assert len(_read_jsonl(run_dir / "candidates.journal.jsonl")) == 2
    assert _read_jsonl(run_dir / "rejected.journal.jsonl") == []
    final_rows = _read_jsonl(run_dir / "candidates.jsonl")
    assert len(final_rows) == 2
    assert all(row["generation_resume_key"] for row in final_rows)
    assert all(row["catalog_split_role"] == "train_pool_only" for row in final_rows)
    assert all(row["training_use_permitted"] is True for row in final_rows)
    assert all(row["evaluation_use_permitted"] is False for row in final_rows)
    assert all(
        row["generation_contract"]["catalog_sha256"] == "a" * 64 for row in final_rows
    )
    row_attestation = final_rows[0]["generation_contract"]["model_attestation"]
    assert row_attestation["status"] == "verified"
    assert row_attestation["live_model_digest"] == f"sha256:{_TEST_MODEL_DIGEST}"
    assert "url" not in " ".join(row_attestation).lower()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "complete"
    assert manifest["catalog_sha256"] == "a" * 64
    assert manifest["code_sha256"] == "b" * 64
    assert manifest["provider"]["provider_identity_sha256"]
    assert manifest["provider"]["model_identity_sha256"]
    assert manifest["model_attestation"] == row_attestation
    assert (
        manifest["model_runtime_attestation_sha256"]
        == row_attestation["binding_sha256"]
    )
    assert manifest["plan"]["planned"] == 2
    complete = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert complete["model_attestation"] == row_attestation
    assert (
        complete["model_runtime_attestation_sha256"]
        == row_attestation["binding_sha256"]
    )

    descriptors, _ = describe_plan(plan, generation_namespace="run-001")
    assert [row["generation_resume_key"] for row in final_rows] == [
        row["resume_key"] for row in descriptors
    ]
    with pytest.raises(ProxyGenerationRunError, match="refusing overwrite"):
        _run(tmp_path, _RecordingProvider(), run_id="run-001", plan=plan)
    with pytest.raises(ProxyGenerationRunError, match="completed run is immutable"):
        _run(
            tmp_path,
            _RecordingProvider(),
            resume_run=run_dir,
            plan=plan,
        )


def test_distinct_generation_namespaces_make_topup_ids_and_resume_keys_disjoint(
    tmp_path,
):
    plan = _plan(2)
    initial_dir, _ = _run(
        tmp_path,
        _RecordingProvider(),
        run_id="initial-main",
        plan=plan,
    )
    topup_dir, _ = _run(
        tmp_path,
        _RecordingProvider(),
        run_id="topup-boundary-01",
        plan=plan,
    )

    initial = _read_jsonl(initial_dir / "candidates.jsonl")
    topup = _read_jsonl(topup_dir / "candidates.jsonl")
    assert {row["doc_id"] for row in initial}.isdisjoint(
        row["doc_id"] for row in topup
    )
    assert {row["generation_resume_key"] for row in initial}.isdisjoint(
        row["generation_resume_key"] for row in topup
    )
    assert {row["generation_namespace"] for row in initial} == {"initial-main"}
    assert {row["generation_namespace"] for row in topup} == {
        "topup-boundary-01"
    }
    # Top-ups intentionally retain the same semantic family identity so
    # family-based train/validation splitting cannot leak near-duplicates.
    assert {row["document_family_id"] for row in initial} == {
        row["document_family_id"] for row in topup
    }


def test_quality_failure_is_regenerated_and_history_is_retained(tmp_path):
    provider = _ShortThenGoodProvider()
    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="retry-quality",
        plan=_plan(1),
        max_quality_retries=1,
    )
    assert provider.calls == 2
    assert stats["candidates"] == 1 and stats["target_met"] is True
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    assert row["generation_attempt_count"] == 2
    assert provider.max_tokens == [6000, 4800]
    assert row["generation_quality_history"][0]["errors"]
    assert len(row["generation_quality_history"][0]["response_audit"]) == 1
    retry_prompt = provider.prompts[1]
    assert "[이전 초안 원문" in retry_prompt
    assert "첫 출력은 의도적으로 짧다" in retry_prompt
    assert "기초값으로 다시 계산" in retry_prompt
    assert "기존 문장을 바꾸어 반복" in retry_prompt
    assert "이름이 있는 절을 최소 6개" in retry_prompt
    assert "데이터 5행 이상" in retry_prompt
    assert "후속 조치는 최소 4개" in retry_prompt
    assert "too_short:S2" not in retry_prompt


def test_profile_overflow_is_regenerated_and_history_is_retained(tmp_path):
    provider = _LongThenGoodProvider()
    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="retry-overflow",
        plan=_plan(1),
        max_quality_retries=1,
    )

    assert provider.calls == 2
    assert stats["candidates"] == 1 and stats["target_met"] is True
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    assert any(
        str(error).startswith("quality:profile_too_long:")
        for error in row["generation_quality_history"][0]["errors"]
    )
    assert len(row["generation_quality_history"][0]["response_audit"]) == 1
    assert "요청한 본문 분량 초과" in provider.prompts[1]


def test_all_quality_failed_outer_attempts_keep_response_audit(tmp_path):
    run_dir, stats = _run(
        tmp_path,
        _AlwaysLongProvider(),
        run_id="quality-audit-exhausted",
        plan=_plan(1),
        max_quality_retries=2,
    )

    assert stats["target_met"] is False
    rejected = _read_jsonl(run_dir / "rejected.jsonl")[0]
    assert len(rejected["quality_history"]) == 3
    assert all(
        len(attempt["response_audit"]) == 1
        and attempt["response_audit"][0]["parse_ok"] is True
        for attempt in rejected["quality_history"]
    )
    assert len(rejected["candidate_snapshot"]["generation_response_audit"]) == 1


@pytest.mark.parametrize(
    ("profile_id", "max_chars", "old_cutoff"),
    [
        ("compact", 1599, 3500),
        ("standard", 2199, 3958),
        ("extended", 3200, 5760),
    ],
)
def test_profile_completion_budgets_exceed_observed_cutoffs_and_fit_context(
    profile_id, max_chars, old_cutoff
):
    family = {"length_profile_id": profile_id, "max_chars": max_chars}
    contract = _PROFILE_TOKEN_CONTRACT[profile_id]

    initial = _proxy_output_token_budget(family, quality_retry=False)
    retry = _proxy_output_token_budget(family, quality_retry=True)

    assert initial > old_cutoff
    assert retry > old_cutoff
    assert (
        initial + contract["initial_prompt_ceiling"] + _CONTEXT_SAFETY_TOKENS
        <= _OLLAMA_CONTEXT_WINDOW_TOKENS
    )
    assert (
        retry + contract["quality_retry_prompt_ceiling"] + _CONTEXT_SAFETY_TOKENS
        <= _OLLAMA_CONTEXT_WINDOW_TOKENS
    )


def test_profile_retry_draft_limits_are_bound_to_the_context_contract():
    doc = SimpleNamespace(title="가상 제목", body="가" * 3000)

    for profile_id in ("compact", "standard", "extended"):
        family = {"length_profile_id": profile_id, "max_chars": 3200}
        limit = _proxy_retry_draft_limit(family)
        draft = _bounded_retry_draft(doc, max_chars=limit)
        assert len(draft) <= limit

    extended = {"length_profile_id": "extended", "max_chars": 3200}
    assert _proxy_retry_draft_limit(extended) == 0
    assert _bounded_retry_draft(doc, max_chars=0) == ""


def test_truncated_json_retry_is_audited_and_can_recover(tmp_path):
    provider = _TruncatedThenGoodProvider()
    plan = _plan(1)
    plan[0][2].update(
        {"length_profile_id": "compact", "min_chars": 700, "max_chars": 1599}
    )

    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="truncated-then-good",
        plan=plan,
        max_quality_retries=0,
    )

    assert stats["target_met"] is True
    assert provider.max_tokens == [6000, 6000]
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    audit = row["generation_response_audit"]
    assert len(audit) == 2
    assert audit[0]["failure_reason"] == "output_token_limit_reached"
    assert audit[0]["finish_reason"] == "length"
    assert audit[0]["output_tokens"] == audit[0]["max_output_tokens"] == 6000
    assert audit[0]["input_tokens"] == 1550
    assert audit[0]["output_chars"] > 0
    assert len(audit[0]["output_sha256"]) == 64
    assert audit[1]["parse_ok"] is True


def test_exhausted_truncated_json_audit_is_preserved_in_rejection(tmp_path):
    provider = _TruncatedThenGoodProvider(always_truncated=True)
    plan = _plan(1)
    plan[0][2].update(
        {"length_profile_id": "compact", "min_chars": 700, "max_chars": 1599}
    )

    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="truncated-rejected",
        plan=plan,
        max_quality_retries=0,
    )

    assert stats["target_met"] is False
    rejected = _read_jsonl(run_dir / "rejected.jsonl")[0]
    response_audit = rejected["quality_history"][0]["response_audit"]
    assert len(response_audit) == 3  # initial call + two JSON repair attempts
    assert provider.max_tokens == [6000, 6000, 6000]
    assert all(
        row["failure_reason"] == "output_token_limit_reached"
        and row["token_limit_reached"] is True
        and row["output_chars"] > 0
        and len(row["output_sha256"]) == 64
        for row in response_audit
    )


def test_outer_parse_retry_uses_context_safe_revision_budget(tmp_path):
    provider = _TruncatedThenGoodProvider(truncated_calls=3)
    plan = _plan(1)
    plan[0][2].update(
        {"length_profile_id": "compact", "min_chars": 700, "max_chars": 1599}
    )

    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="outer-parse-retry",
        plan=plan,
        max_quality_retries=1,
    )

    assert stats["target_met"] is True
    assert provider.max_tokens == [6000, 6000, 6000, 4800]
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    assert row["generation_attempt_count"] == 2
    assert row["generation_quality_history"][0]["errors"] == ["parse_error"]


def test_retry_summary_reports_profile_length_overflow_without_grade_leakage():
    summary = _retry_problem_summary(
        ["quality:profile_too_long:2400>2199", "too_long:S1"]
    )

    assert summary == "요청한 본문 분량 초과"
    assert "S1" not in summary


def test_numeric_guard_retry_discards_the_failed_draft():
    context = _retry_revision_context(
        ["quality:unapproved_numeric_claim:105"],
        "이전 초안에는 온도 105℃가 있었다.",
    )

    assert "이전 초안에는" not in context
    assert "완전히 새로 작성" in context
    assert "아라비아 숫자" in context


@pytest.mark.parametrize(
    ("profile_id", "minimum_sections", "minimum_rows", "minimum_actions"),
    [
        ("compact", 5, 4, 3),
        ("standard", 6, 5, 4),
        ("extended", 8, 6, 5),
    ],
)
def test_length_profiles_have_explicit_structure_counts(
    profile_id, minimum_sections, minimum_rows, minimum_actions
):
    requirements = _profile_structure_requirements(
        {
            "length_profile_id": profile_id,
            "min_chars": 2200 if profile_id == "extended" else 1200,
            "max_chars": 3200 if profile_id == "extended" else 2199,
        }
    )
    assert f"최소 {minimum_sections}개" in requirements
    assert f"{minimum_rows}행 이상" in requirements
    assert f"최소 {minimum_actions}개" in requirements
    assert "책임 역할, 기한, 완료 판정 기준" in requirements
    assert "공개용" in requirements and "내부용" in requirements


def test_classification_style_title_or_heading_is_rejected_but_publicity_fact_is_not():
    title_doc = SimpleNamespace(title="1차 파일럿 결과 (공개판)", body="정상 본문")
    heading_doc = SimpleNamespace(
        title="1차 파일럿 결과",
        body="1. 열람등급: 전체\n\n검토 결과를 정리한다.",
    )
    factual_doc = SimpleNamespace(
        title="1차 파일럿 결과",
        body=(
            "게시 사실\n\n이 자료는 [가상기업A] 공식 기술자료실에 2026년 8월 1일 "
            "게시되었고 로그인이나 승인 없이 누구나 같은 내용을 내려받을 수 있다."
        ),
    )

    assert _classification_style_marker_errors(title_doc) == [
        "quality:classification_style_marker:title"
    ]
    assert _classification_style_marker_errors(heading_doc) == [
        "quality:classification_style_marker:body_heading"
    ]
    assert _classification_style_marker_errors(factual_doc) == []


def test_table_placeholder_and_missing_closing_section_are_rejected():
    blank = SimpleNamespace(
        body=(
            "| 항목 | 결과 |\n|---|---|\n| 기준값 | - |\n\n"
            "결론 및 후속 조치\n운영팀은 9월 3일까지 값을 다시 측정하고 품질팀은 "
            "원자료 일치 여부를 확인한 뒤 완료 여부를 판정한다."
        )
    )
    truncated = SimpleNamespace(
        body="검토 결과\n\n| 항목 | 결과 |\n|---|---|\n| 기준값 | 160℃ |"
    )
    complete = SimpleNamespace(
        body=(
            "| 항목 | 결과 |\n|---|---|\n| 기준값 | 160℃ |\n\n"
            "결론 및 후속 조치\n운영팀은 9월 3일까지 값을 다시 측정하고 품질팀은 "
            "원자료 일치 여부를 확인한 뒤 완료 여부를 판정한다."
        )
    )

    assert "quality:table_blank_or_dash_cell" in _document_completion_errors(blank)
    assert "quality:table_missing_closing_section" in _document_completion_errors(
        truncated
    )
    assert _document_completion_errors(complete) == []


def test_explicit_transition_difference_is_recomputed_without_threshold_false_positive():
    mismatch = SimpleNamespace(
        body="기존 150℃에서 변경 160℃로 조정했으며 변동 폭은 60℃이다."
    )
    correct = SimpleNamespace(
        body="기존 150℃에서 변경 160℃로 조정했으며 변동 폭은 10℃이다."
    )
    threshold = SimpleNamespace(
        body="기존 150℃에서 변경 160℃로 조정했고 60℃ 이상 변동 시 시험을 중단한다."
    )

    assert any(
        error.startswith("quality:derived_difference_mismatch:")
        for error in _document_completion_errors(mismatch)
    )
    assert _document_completion_errors(correct) == []
    assert _document_completion_errors(threshold) == []


def test_explicit_failure_threshold_table_must_match_measurement_and_status():
    below_mismatch = SimpleNamespace(
        body=(
            "| 항목 | 실패 경계 | 실측값 | 판정 |\n"
            "|---|---|---|---|\n"
            "| 두께 | 160㎛ 미만 | 168㎛ | 실패 |"
        )
    )
    above_mismatch = SimpleNamespace(
        body=(
            "| 항목 | 실패 조건 | 기존값 | 변경값 | 판정 |\n"
            "|---|---|---|---|---|\n"
            "| 온도 | 280℃ 이상 | 300℃ | 320℃ | 정상·성공 |"
        )
    )
    consistent = SimpleNamespace(
        body=(
            "| 항목 | 실패 경계 | 실측값 | 판정 |\n"
            "|---|---|---|---|\n"
            "| 두께 | 160㎛ 미만 | 168㎛ | 정상 |"
        )
    )

    assert any(
        error.startswith("quality:failure_threshold_status_mismatch:")
        for error in _document_completion_errors(below_mismatch)
    )
    assert any(
        error.startswith("quality:failure_threshold_status_mismatch:")
        for error in _document_completion_errors(above_mismatch)
    )
    assert not any(
        "failure_threshold_status_mismatch" in error
        for error in _document_completion_errors(consistent)
    )


def test_explicit_percentage_multiple_is_recomputed():
    mismatch = SimpleNamespace(
        body="불량률 20%는 정상 상한 5% 대비 2.5배라고 기록했다."
    )
    consistent = SimpleNamespace(
        body="불량률 20%는 정상 상한 5% 대비 4배라고 기록했다."
    )

    assert "quality:derived_ratio_mismatch:2.5" in _document_completion_errors(mismatch)
    assert not any(
        "derived_ratio_mismatch" in error
        for error in _document_completion_errors(consistent)
    )


def test_nonpublic_gate_rejects_only_explicit_publication_of_same_document():
    explicit_public = SimpleNamespace(
        body=(
            "본 문서 전체와 동일한 내용은 공식 홈페이지에서 로그인이나 승인 없이 "
            "누구나 열람하고 다운로드할 수 있다."
        )
    )
    ambiguous_internal = SimpleNamespace(
        body="내부 공용 폴더에서는 로그인 없이 담당 조직 구성원이 접근한다."
    )

    assert _nonpublic_document_access_errors(explicit_public, expected_label="S1") == [
        "quality:nonpublic_document_explicitly_public"
    ]
    assert (
        _nonpublic_document_access_errors(ambiguous_internal, expected_label="S1") == []
    )
    assert _nonpublic_document_access_errors(explicit_public, expected_label="S3") == []


def test_fact_ledger_is_code_computed_prompted_and_exactly_rechecked():
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None and ledger["profile"] == "internal_validation"
    assert ledger["absolute_difference"] == abs(
        int(ledger["after"]) - int(ledger["before"])
    )
    prompt = _fact_ledger_prompt(ledger)
    assert "코드 참조 사실" in prompt
    assert "<copy_exactly>" not in prompt
    assert "[사실 검산]" not in prompt
    assert "제목이나 본문에 복사하지 않는다" in prompt
    assert str(ledger["metric_name"]) not in prompt
    assert str(ledger["before"]) not in prompt
    assert "원장에 없는 합계·평균·비율·배수·차이·예측값도 추가하지 않는다" in prompt
    assert "정성적인 업무 맥락으로만 유지한다" in prompt
    timeline = ledger["timeline"]
    unit = ledger["unit"]
    body = (
        f"{ledger['metric_name']} 검산 결과다. 변경 전 {ledger['before']}{unit}, "
        f"변경 후 {ledger['after']}{unit}이며 절대 차이 "
        f"{ledger['absolute_difference']}{unit}, "
        f"{ledger['change_direction']}율 {ledger['change_rate_percent']}%다. "
        f"착수 {timeline['착수']}, 변경 {timeline['변경']}, 검증 {timeline['검증']}, "
        f"후속조치 {timeline['후속조치']} 순서로 진행한다. "
        f"정상 범위 {ledger['normal_lower']}~{ledger['normal_upper']}{unit}, "
        f"실측값 {ledger['observed']}{unit}, 판정은 {ledger['status']}다."
    )
    assert _fact_ledger_errors(SimpleNamespace(body=body), ledger) == []

    unrelated_checklist = body + " 별도 품질점검 판정: 통과."
    assert _fact_ledger_errors(SimpleNamespace(body=unrelated_checklist), ledger) == []

    unrelated_context_numbers = body + (
        " 변경 전 대비 차이 원인 후보는 3건이고, 변경 후 후속조치 담당 역할은 "
        "2개다. 별도 품질 측정 결과 분석 항목은 4건이다."
    )
    assert (
        _fact_ledger_errors(SimpleNamespace(body=unrelated_context_numbers), ledger)
        == []
    )

    tampered = body.replace(
        f"변경 후 {ledger['after']}{unit}",
        f"변경 후 {int(ledger['after']) + 1}{unit}",
    )
    assert "quality:fact_ledger_mismatch:after" in _fact_ledger_errors(
        SimpleNamespace(body=tampered), ledger
    )

    wrong_direction = "감소율" if ledger["change_direction"] == "증가" else "증가율"
    direction_conflict = body + (
        f" 별도 메모에는 {wrong_direction} {ledger['change_rate_percent']}%라고 적었다."
    )
    assert "quality:fact_ledger_conflict:rate" in _fact_ledger_errors(
        SimpleNamespace(body=direction_conflict), ledger
    )

    wrong_scoped_status = body + (
        f" 별도 기록의 실측값은 998{unit}, 판정은 "
        f"{'상한 초과' if ledger['status'] != '상한 초과' else '하한 미만'}다."
    )
    assert "quality:fact_ledger_conflict:status" in _fact_ledger_errors(
        SimpleNamespace(body=wrong_scoped_status), ledger
    )


def _ledger_item() -> tuple[dict, dict, dict, int]:
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    return item


def test_fact_ledger_materialization_reserves_bounds_and_records_provenance():
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    block = _fact_ledger_block(ledger)
    provider = _LedgerSuffixProvider("")

    candidate, rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(llm=provider),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )

    assert rejection is None and candidate is not None
    reserve = len(block) + 2
    assert candidate["generation_fact_ledger_reserved_chars"] == reserve
    assert candidate["prompt_max_chars"] == int(item[2]["max_chars"]) - reserve
    assert (
        f"body는 한국어 {candidate['prompt_min_chars']}자 이상 "
        f"{candidate['prompt_max_chars']}자 이내"
    ) in provider.prompts[0]
    assert int(item[2]["min_chars"]) <= len(candidate["text"]) <= int(
        item[2]["max_chars"]
    )
    assert candidate["text"].count(block) == 1

    raw_text = f"{provider.raw_title}\n\n{provider.raw_body}"
    provenance = candidate["generation_fact_ledger_materialization"]
    assert provenance == {
        "schema": "proxy-fact-ledger-materialization-v2",
        "policy": "reject_model_ledger_then_append",
        "mode": "code_appended_exact_block",
        "source": "deterministic_code",
        "appended": True,
        "raw_pre_materialization_text_sha256": hashlib.sha256(
            raw_text.encode("utf-8")
        ).hexdigest(),
        "raw_pre_materialization_text_chars": len(raw_text),
        "raw_pre_materialization_body_sha256": hashlib.sha256(
            provider.raw_body.encode("utf-8")
        ).hexdigest(),
        "raw_pre_materialization_body_chars": len(provider.raw_body),
        "raw_exact_block_count": 0,
        "raw_fact_heading_count": 0,
        "raw_control_tag_count": 0,
        "fact_ledger_sha256": hashlib.sha256(
            json.dumps(
                ledger,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "canonical_block_sha256": hashlib.sha256(block.encode("utf-8")).hexdigest(),
        "canonical_block_chars": len(block),
        "append_separator_chars": 2,
        "final_body_sha256": hashlib.sha256(
            (provider.raw_body + "\n\n" + block).encode("utf-8")
        ).hexdigest(),
        "final_body_chars": len(provider.raw_body + "\n\n" + block),
        "final_text_sha256": hashlib.sha256(candidate["text"].encode("utf-8")).hexdigest(),
        "final_text_chars": len(candidate["text"]),
        "final_exact_block_count": 1,
        "final_fact_heading_count": 1,
        "quality_errors": [],
    }


def test_numeric_guard_can_fall_back_to_a_marked_code_scaffold():
    item = _ledger_item()
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v2"

    candidate, rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(llm=_LedgerSuffixProvider("")),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )

    assert rejection is None and candidate is not None
    assert candidate["generation_mode"] == "deterministic_qualitative_scaffold_fallback"
    assert candidate["requires_manual_audit"] is True
    assert candidate["raw_model_generation_failures"]
    assert candidate["generation_fact_ledger_materialization"]["appended"] is True


def test_fact_ledger_materialization_rejects_even_one_model_emitted_exact_block():
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    block = _fact_ledger_block(ledger)
    provider = _LedgerSuffixProvider("\n\n" + block)

    candidate, rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(llm=provider),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )

    assert candidate is None and rejection is not None
    snapshot = rejection["candidate_snapshot"]
    assert snapshot["text"].count(block) == 1
    provenance = snapshot["generation_fact_ledger_materialization"]
    assert provenance["appended"] is False
    assert provenance["mode"] == "model_ledger_rejected"
    assert provenance["raw_exact_block_count"] == 1
    assert provenance["final_exact_block_count"] == 1
    assert provenance["append_separator_chars"] == 0
    assert "quality:fact_ledger_materialization:model_emitted_ledger_artifact" in (
        rejection["quality_history"][0]["errors"]
    )


def test_fact_ledger_materialization_enforces_exact_final_text_max_without_truncation():
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    block = _fact_ledger_block(ledger)
    probe = _RecordingProvider()
    payload = json.loads(probe.generate("probe", max_tokens=1).text)
    raw_title = str(payload["title"])
    raw_body = str(payload["body"])
    exact_final_max = len(f"{raw_title}\n\n{raw_body}\n\n{block}")
    item[2]["max_chars"] = exact_final_max

    exact_candidate, exact_rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(
            llm=_LedgerSuffixProvider("")
        ),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )
    assert exact_rejection is None and exact_candidate is not None
    assert len(exact_candidate["text"]) == exact_final_max

    overflow_candidate, overflow_rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(
            llm=_LedgerSuffixProvider("가")
        ),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )
    assert overflow_candidate is None and overflow_rejection is not None
    snapshot = overflow_rejection["candidate_snapshot"]
    assert len(snapshot["text"]) == exact_final_max + 1
    assert raw_body + "가" in snapshot["text"]
    assert f"quality:profile_too_long:{exact_final_max + 1}>{exact_final_max}" in (
        overflow_rejection["quality_history"][0]["errors"]
    )


def test_fact_ledger_materialization_preserves_and_rejects_wrong_raw_prose():
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    unit = ledger["unit"]
    wrong = (
        f"\n\n{ledger['metric_name']} 별도 기록에는 변경 전 999{unit}, "
        f"변경 후 998{unit}, 절대 차이 1{unit}로 적혀 있다."
    )
    provider = _LedgerSuffixProvider(wrong)

    candidate, rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(llm=provider),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )

    assert candidate is None and rejection is not None
    snapshot = rejection["candidate_snapshot"]
    assert wrong.strip() in snapshot["text"]
    assert snapshot["text"].count(_fact_ledger_block(ledger)) == 1
    assert snapshot["generation_fact_ledger_materialization"]["appended"] is True
    errors = rejection["quality_history"][0]["errors"]
    assert "quality:fact_ledger_conflict:before" in errors
    assert "quality:fact_ledger_conflict:after" in errors


@pytest.mark.parametrize(
    ("suffix_kind", "expected_error"),
    [
        (
            "partial",
            "quality:fact_ledger_materialization:model_emitted_ledger_artifact",
        ),
        ("duplicate", "quality:fact_ledger_materialization:model_emitted_ledger_artifact"),
    ],
)
def test_fact_ledger_materialization_rejects_partial_or_duplicate_attempts(
    suffix_kind: str, expected_error: str
):
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    block = _fact_ledger_block(ledger)
    suffix = (
        "\n\n" + "\n".join(block.splitlines()[:3])
        if suffix_kind == "partial"
        else "\n\n" + block + "\n\n" + block
    )

    candidate, rejection = _generate_plan_item(
        item,
        generator=scenario_builder.SyntheticDocGenerator(
            llm=_LedgerSuffixProvider(suffix)
        ),
        catalog_version="proxy-scenarios-test",
        max_quality_retries=0,
    )

    assert candidate is None and rejection is not None
    assert expected_error in rejection["quality_history"][0]["errors"]


def test_generation_prompt_artifacts_are_rejected_before_code_append():
    doc = SimpleNamespace(
        title="운영 검토",
        body=(
            "<copy_exactly>\n[판정 근거 문장]\n"
            "비공지성과 경제적 유용성 및 비밀관리성을 설명한다."
        ),
    )

    assert _generation_prompt_artifact_errors(doc) == [
        "quality:generation_prompt_artifact:control_tag",
        "quality:generation_prompt_artifact:instruction_heading",
        "quality:generation_prompt_artifact:rubric_term",
    ]


def test_numeric_guard_allows_only_explicit_prompt_values():
    scenario = {"scenario_context": "예산은 4,500만원이고 검토 기간은 6주다."}
    instance = {"context": "독립 조건 2개를 비교한다."}
    family = {"context": "표는 4행으로 작성한다."}
    allowed = _allowed_prompt_numeric_tokens(scenario, instance, family)

    assert allowed == {"2", "4", "6", "4500"}
    assert _unapproved_numeric_claim_errors(
        SimpleNamespace(title="검토", body="예산 4500만원과 6주 조건을 기록한다."),
        allowed_numeric_tokens=allowed,
    ) == []
    assert _unapproved_numeric_claim_errors(
        SimpleNamespace(title="검토", body="온도 105℃에서 수율 84%를 기록한다."),
        allowed_numeric_tokens=allowed,
    ) == [
        "quality:unapproved_numeric_claim:105",
        "quality:unapproved_numeric_claim:84",
    ]


def test_timeline_parser_ignores_metric_name_and_reverification_but_not_stage():
    metric = "검증 완료율"
    body = "9주차에 검증 완료율을 확인하고 9주차 재검증을 시행한다. 검증 7주차에 완료한다."

    assert scenario_builder._timeline_stage_mentions(
        body, "검증", ignored_metric_name=metric
    ) == [7]


def test_fact_ledger_retry_carries_only_raw_model_draft(tmp_path):
    item = _ledger_item()
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    block = _fact_ledger_block(ledger)
    provider = _ShortThenGoodProvider()

    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="ledger-raw-retry",
        plan=[item],
        max_quality_retries=1,
    )

    assert stats["target_met"] is True and provider.calls == 2
    retry_draft = provider.prompts[1].split("[이전 초안 원문", 1)[1]
    assert block not in retry_draft
    assert "첫 출력은 의도적으로 짧다." in retry_draft
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    assert row["generation_fact_ledger_materialization"]["appended"] is True


def test_fact_ledger_rejects_contradictory_second_anchor_mentions():
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None and ledger["profile"] == "internal_validation"
    timeline = ledger["timeline"]
    unit = ledger["unit"]
    correct = (
        f"{ledger['metric_name']} 검산 결과다. 변경 전 {ledger['before']}{unit}, "
        f"변경 후 {ledger['after']}{unit}이며 절대 차이 "
        f"{ledger['absolute_difference']}{unit}, "
        f"{ledger['change_direction']}율 {ledger['change_rate_percent']}%다. "
        f"착수 {timeline['착수']}, 변경 {timeline['변경']}, 검증 {timeline['검증']}, "
        f"후속조치 {timeline['후속조치']} 순서다. "
        f"정상 범위 {ledger['normal_lower']}~{ledger['normal_upper']}{unit}, "
        f"실측값 {ledger['observed']}{unit}, 판정은 {ledger['status']}다."
    )
    alternative_status = "상한 초과" if ledger["status"] != "상한 초과" else "하한 미만"
    contradictory = (
        f"변경 전 999{unit}, 변경 후 998{unit}, 절대 차이 1{unit}, "
        f"{ledger['change_direction']}율 0.1%로 "
        "별도 기록했다. 착수 20주차, 변경 21주차, 검증 22주차, 후속조치 "
        f"23주차로도 적었다. 정상 범위 1~2{unit}, 실측값 998{unit}, "
        f"판정은 {alternative_status}라고 중복 기재했다."
    )

    errors = _fact_ledger_errors(
        SimpleNamespace(body=correct + " " + contradictory), ledger
    )
    expected_conflicts = {
        "quality:fact_ledger_conflict:before",
        "quality:fact_ledger_conflict:after",
        "quality:fact_ledger_conflict:difference",
        "quality:fact_ledger_conflict:rate",
        "quality:fact_ledger_conflict:timeline_착수",
        "quality:fact_ledger_conflict:timeline_변경",
        "quality:fact_ledger_conflict:timeline_검증",
        "quality:fact_ledger_conflict:timeline_후속조치",
        "quality:fact_ledger_conflict:normal_range",
        "quality:fact_ledger_conflict:observed",
        "quality:fact_ledger_conflict:status",
    }
    assert expected_conflicts <= set(errors)


def test_fact_ledger_accepts_exact_natural_transition_and_reverse_timeline():
    ledger = {
        "schema": "proxy-fact-ledger-v1",
        "metric_name": "검증 완료율",
        "unit": "%",
        "before": 73,
        "after": 59,
        "absolute_difference": 14,
        "change_direction": "감소",
        "change_rate_percent": "19.2",
        "timeline": {
            "착수": "5주차",
            "변경": "6주차",
            "검증": "7주차",
            "후속조치": "8주차",
        },
        "normal_lower": 63,
        "normal_upper": 83,
        "observed": 59,
        "status": "하한 미만",
    }
    body = """검증 완료율은 착수 5주차(73%)에서 6주차 조건 변경 후 7주차 검증(59%)으로 감소했다.
| 지표 | 정상 범위 | 실측값 | 판정 |
| --- | --- | --- | --- |
| 검증 완료율 | 63~83% | 59% | 하한 미만 |
73%에서 59%로 14% 감소(감소율 19.2%)했으며, 8주차 후속 조치를 완료한다."""

    assert _fact_ledger_errors(SimpleNamespace(body=body), ledger) == []


def test_fact_ledger_accepts_unitless_metric_rows_but_rejects_wrong_reverse_week():
    ledger = {
        "schema": "proxy-fact-ledger-v1",
        "metric_name": "운영 안정성 점수",
        "unit": "점",
        "before": 92,
        "after": 80,
        "absolute_difference": 12,
        "change_direction": "감소",
        "change_rate_percent": "13.0",
        "timeline": {
            "착수": "4주차",
            "변경": "5주차",
            "검증": "6주차",
            "후속조치": "7주차",
        },
        "normal_lower": 82,
        "normal_upper": 102,
        "observed": 80,
        "status": "하한 미만",
    }
    body = """기존 운영 안정성 점수는 92점이었으나, 변경안 적용 후 80점으로 13.0% 감소했다.
변경 전 공정은 착수 4주차에 시작해 5주차에 조건을 변경하고 6주차에 검증했다.
| 지표 | 기존 | 변경안 | 차이 |
| --- | --- | --- | --- |
| 운영 안정성 점수 | 92 | 80 | -12 |
| 지표 | 정상 범위 | 관찰값 | 판정 |
| --- | --- | --- | --- |
| 운영 안정성 점수 | 82~102 | 80 | 하한 미만 |
7주차 후속조치로 책임 역할과 완료 기준을 확인한다."""
    assert _fact_ledger_errors(SimpleNamespace(body=body), ledger) == []

    wrong_week = body.replace("5주차에 조건을 변경", "9주차에 조건을 변경")
    errors = _fact_ledger_errors(SimpleNamespace(body=wrong_week), ledger)
    assert "quality:fact_ledger_mismatch:timeline_변경" in errors

    wrong_table = body.replace(
        "| 운영 안정성 점수 | 92 | 80 | -12 |",
        "| 운영 안정성 점수 | 99 | 78 | -21 |",
        1,
    )
    table_errors = _fact_ledger_errors(SimpleNamespace(body=wrong_table), ledger)
    assert {
        "quality:fact_ledger_conflict:before",
        "quality:fact_ledger_conflict:after",
        "quality:fact_ledger_conflict:difference",
    } <= set(table_errors)


def _combined_table_ledger() -> dict:
    return {
        "schema": "proxy-fact-ledger-v1",
        "metric_name": "검증 완료율",
        "unit": "%",
        "before": 73,
        "after": 59,
        "absolute_difference": 14,
        "change_direction": "감소",
        "change_rate_percent": "19.2",
        "timeline": {
            "착수": "5주차",
            "변경": "6주차",
            "검증": "7주차",
            "후속조치": "8주차",
        },
        "normal_lower": 63,
        "normal_upper": 83,
        "observed": 59,
        "status": "하한 미만",
    }


def test_fact_ledger_header_roles_recognize_bare_status_header():
    assert scenario_builder._ledger_header_roles("판정") == {"status"}


def test_fact_ledger_exact_combined_table_does_not_flatten_transition_values():
    ledger = _combined_table_ledger()
    table = """| 지표명 | 변경 전 | 변경 후 | 정상 범위 | 실측값 | 판정 |
| --- | --- | --- | --- | --- | --- |
| 검증 완료율 | 73 | 59 | 63~83 | 59 | 하한 미만 |"""
    passed, conflicts = scenario_builder._metric_table_row_evidence(table, ledger)

    assert conflicts == set()
    assert passed["normal_range"] is True
    assert passed["observed"] is True
    assert passed["status"] is True
    # Transition evidence is conservative without its complete difference column.
    assert passed["before"] is passed["after"] is False
    body = _fact_ledger_block(ledger) + "\n\n" + table
    assert _fact_ledger_errors(SimpleNamespace(body=body), ledger) == []


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("before", "74%"),
        ("after", "60%"),
        ("difference", "-13%"),
        ("normal_range", "64~83%"),
        ("observed", "60%"),
        ("status", "상한 초과"),
    ],
)
def test_fact_ledger_combined_table_attributes_wrong_cell_to_exact_field(
    field: str, replacement: str
):
    ledger = _combined_table_ledger()
    headers = [
        "지표명",
        "변경 전",
        "변경 후",
        "차이",
        "정상 범위",
        "실측값",
        "판정",
    ]
    fields = [
        "metric",
        "before",
        "after",
        "difference",
        "normal_range",
        "observed",
        "status",
    ]
    values = ["검증 완료율", "73%", "59%", "-14%", "63~83%", "59%", "하한 미만"]
    values[fields.index(field)] = replacement
    table = (
        "| " + " | ".join(headers) + " |\n"
        "| " + " | ".join("---" for _ in headers) + " |\n"
        "| " + " | ".join(values) + " |"
    )

    _, conflicts = scenario_builder._metric_table_row_evidence(table, ledger)
    assert conflicts == {field}


def test_fact_ledger_reordered_table_maps_cells_and_ignores_unknown_numeric_column():
    ledger = _combined_table_ledger()
    table = """| 판정 | 실측값 | 비고 수치 | 지표 | 정상 범위 | 차이 | 변경 후 | 변경 전 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 하한 미만 | 59 | 18% | 검증 완료율 | 63부터 83까지 | -14 | 59 | 73 |"""

    passed, conflicts = scenario_builder._metric_table_row_evidence(table, ledger)
    assert conflicts == set()
    assert all(passed.values())


@pytest.mark.parametrize(
    "table",
    [
        """| 지표 | 변경 전 | 변경 전 | 변경 후 | 차이 |
| --- | --- | --- | --- | --- |
| 검증 완료율 | 73 | 73 | 59 | -14 |""",
        """| 지표 | 변경 전/후 | 차이 |
| --- | --- | --- |
| 검증 완료율 | 73/59 | -14 |""",
        """| 지표 | 기존/변경 후 | 차이 |
| --- | --- | --- |
| 검증 완료율 | 73/59 | -14 |""",
        """| 지표 | 변경 전 | 변경 후 | 차이 |
| --- | --- | --- | --- |
| 검증 완료율 | 73 | 59 | -14 | 추가 셀 |""",
    ],
)
def test_fact_ledger_ambiguous_duplicate_or_width_mismatch_header_fails_closed(table: str):
    _, conflicts = scenario_builder._metric_table_row_evidence(
        table, _combined_table_ledger()
    )
    assert "table_schema" in conflicts


def test_fact_ledger_partial_table_never_grants_unitless_presence_but_wrong_value_conflicts():
    ledger = _combined_table_ledger()
    correct_partial = """| 지표 | 변경 전 |
| --- | --- |
| 검증 완료율 | 73 |"""
    wrong_partial = correct_partial.replace("| 73 |", "| 74 |")

    passed, conflicts = scenario_builder._metric_table_row_evidence(
        correct_partial, ledger
    )
    assert passed["before"] is False and conflicts == set()
    _, wrong_conflicts = scenario_builder._metric_table_row_evidence(
        wrong_partial, ledger
    )
    assert wrong_conflicts == {"before"}


def test_fact_ledger_correct_row_does_not_hide_wrong_second_metric_row_or_unit():
    ledger = _combined_table_ledger()
    table = """| 지표 | 변경 전 | 변경 후 | 차이 |
| --- | --- | --- | --- |
| 검증 완료율 | 73% | 59% | -14% |
| 검증 완료율 | 73점 | 58% | -15% |"""

    passed, conflicts = scenario_builder._metric_table_row_evidence(table, ledger)
    assert passed["before"] is passed["after"] is passed["difference"] is True
    assert conflicts == {"before", "after", "difference"}


def test_fact_ledger_does_not_read_timeline_week_as_after_measurement():
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    body = f"조건 변경 후 7주차에 별도 검증을 진행한다. 변경 후 {ledger['after']}{ledger['unit']}다."

    errors = _fact_ledger_errors(SimpleNamespace(body=body), ledger)
    assert "quality:fact_ledger_conflict:after" not in errors


def test_fact_ledger_conflicts_are_scoped_to_ledger_metric():
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None
    timeline = ledger["timeline"]
    unit = ledger["unit"]
    canonical = (
        f"{ledger['metric_name']} 검산 결과다. 변경 전 {ledger['before']}{unit}, "
        f"변경 후 {ledger['after']}{unit}, 절대 차이 "
        f"{ledger['absolute_difference']}{unit}, "
        f"{ledger['change_direction']}율 {ledger['change_rate_percent']}%다. "
        f"착수 {timeline['착수']}, 변경 {timeline['변경']}, 검증 {timeline['검증']}, "
        f"후속조치 {timeline['후속조치']}다. 정상 범위 "
        f"{ledger['normal_lower']}~{ledger['normal_upper']}{unit}, 실측값 "
        f"{ledger['observed']}{unit}, 판정은 {ledger['status']}다."
    )

    unrelated_metric = canonical + "\n압력 변동률이 18% 증가했다."
    assert _fact_ledger_errors(SimpleNamespace(body=unrelated_metric), ledger) == []

    wrong_rate = canonical + (
        f" 별도 메모에는 {ledger['metric_name']}의 "
        f"{ledger['change_direction']}율을 99%로 기록했다."
    )
    assert "quality:fact_ledger_conflict:rate" in _fact_ledger_errors(
        SimpleNamespace(body=wrong_rate), ledger
    )


def test_fact_ledger_does_not_attach_verification_week_to_prior_change():
    ledger = {
        "metric_name": "검증 완료율",
        "unit": "%",
        "before": 73,
        "after": 59,
        "absolute_difference": 14,
        "change_direction": "감소",
        "change_rate_percent": "19.2",
        "timeline": {
            "착수": "2주차",
            "변경": "3주차",
            "검증": "4주차",
            "후속조치": "5주차",
        },
    }
    body = (
        "검증 완료율은 변경 전 73%에서 변경 후 59%로 절대 차이 14%, "
        "감소율 19.2%였다. 검증은 착수 2주차에 시작되어, 3주차에 조건을 "
        "변경하고, 4주차에 검증하였다. 특히 3주차에 수행된 조건 변경은 "
        "4주차에 검증되었으며 5주차 후속조치로 마무리했다."
    )

    errors = _fact_ledger_errors(SimpleNamespace(body=body), ledger)
    assert "quality:fact_ledger_conflict:timeline_변경" not in errors
    assert errors == []


def test_s3_fact_ledger_omits_internal_thresholds_and_gate_blocks_them():
    item = _plan(1)[0]
    item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    item[0]["label"] = "S3"
    item[0]["expected_factor_scores"] = {
        "secrecy": 0,
        "value": 0,
        "management": 0,
    }
    ledger = _fact_ledger_for_item(item)
    assert ledger is not None and ledger["profile"] == "public_aggregate"
    assert "normal_lower" not in ledger and "status" not in ledger
    prompt = _fact_ledger_prompt(ledger)
    assert "추가하지 않는다" in prompt
    assert not re.search(r"(?:실패\s*경계|정상\s*범위)\D{0,8}\d", prompt)
    detail_doc = SimpleNamespace(
        body="공정 실패 경계는 160 미만이고 정상 범위는 160~180이다."
    )
    errors = _s3_reconstruction_detail_errors(detail_doc, expected_label="S3")
    assert "quality:s3_reconstruction_detail:failure_boundary" in errors
    assert "quality:s3_reconstruction_detail:normal_range" in errors
    assert (
        _s3_reconstruction_detail_errors(
            SimpleNamespace(body="실패 경계와 구체적인 공정 조건은 포함하지 않는다."),
            expected_label="S3",
        )
        == []
    )
    assert (
        _s3_reconstruction_detail_errors(
            SimpleNamespace(
                body="실패 경계는 포함하지 않으며 공개 지표의 변경 전 값은 80점이다."
            ),
            expected_label="S3",
        )
        == []
    )


def test_s3_reconstruction_gate_catches_reverse_order_and_parameter_aliases():
    reverse_boundary = SimpleNamespace(body="160㎛ 미만을 실패 경계로 정했다.")
    direct_parameters = SimpleNamespace(
        body="공정 재현에는 온도 180℃와 학습률 0.001을 적용한다."
    )

    assert "quality:s3_reconstruction_detail:failure_boundary" in (
        _s3_reconstruction_detail_errors(reverse_boundary, expected_label="S3")
    )
    assert "quality:s3_reconstruction_detail:reconstructable_parameter" in (
        _s3_reconstruction_detail_errors(direct_parameters, expected_label="S3")
    )


def test_fact_ledger_is_matched_by_archetype_and_instance_not_factor_profile():
    items = _plan(10)
    for item in items:
        item[0]["domain"] = "technology"
        item[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    ledgers = [_fact_ledger_for_item(item) for item in items]

    assert all(ledger is not None for ledger in ledgers)
    assert len({ledger["metric_name"] for ledger in ledgers}) >= 3
    assert {ledger["unit"] for ledger in ledgers} <= {"점", "건", "%"}
    assert all(
        0 <= int(ledger["after"]) <= 100 for ledger in ledgers if ledger["unit"] == "%"
    )

    matched = _plan(1)[0]
    matched[0]["fact_ledger_contract"] = "proxy-fact-ledger-v1"
    upper = _fact_ledger_for_item(matched)
    matched[0]["label"] = "S3"
    matched[0]["expected_factor_scores"] = {
        "secrecy": 0,
        "value": 0,
        "management": 0,
    }
    public = _fact_ledger_for_item(matched)
    neutral_fields = (
        "matched_baseline_key",
        "metric_name",
        "unit",
        "before",
        "after",
        "absolute_difference",
        "timeline",
    )
    assert all(upper[field] == public[field] for field in neutral_fields)
    assert upper["profile"] == "internal_validation"
    assert public["profile"] == "public_aggregate"


def test_factor_gates_use_secrecy_and_value_instead_of_final_grade():
    actually_public = SimpleNamespace(
        body=(
            "본 문서 전체는 공식 회사 홈페이지에서 누구나 로그인·등록·승인 없이 "
            "열람하고 다운로드할 수 있다. 공정 실패 경계는 160㎛이다."
        )
    )
    internal_s3 = SimpleNamespace(body="동일한 전체 문서는 외부에 게시하지 않았다.")

    assert _document_access_errors(actually_public, expected_secrecy=0) == []
    assert _document_access_errors(actually_public, expected_secrecy=1) == [
        "quality:nonpublic_document_explicitly_public"
    ]
    assert _document_access_errors(internal_s3, expected_secrecy=1) == []
    assert _document_access_errors(internal_s3, expected_secrecy=0) == [
        "quality:public_document_not_actually_published"
    ]
    assert _zero_value_reconstruction_detail_errors(
        actually_public, expected_value=1
    ) == []
    assert "quality:zero_value_reconstruction_detail:failure_boundary" in (
        _zero_value_reconstruction_detail_errors(actually_public, expected_value=0)
    )


def test_retry_draft_redacts_target_specific_labels_and_header_markers():
    draft = _bounded_retry_draft(
        SimpleNamespace(
            title="S2 검토서 (공개판)",
            body="대외비 표기를 잘못 붙인 첫 초안",
        )
    )

    assert "S2" not in draft
    assert "대외비" not in draft
    assert "공개판" not in draft
    assert "[분류 표기 제거]" in draft
    assert "[머리말 표기 제거]" in draft


class _MarkerThenGoodProvider(_RecordingProvider):
    def generate(self, prompt: str, **kwargs: object) -> SimpleNamespace:
        response = super().generate(prompt, **kwargs)
        if self.calls == 1:
            payload = json.loads(response.text)
            payload["title"] = "1차 파일럿 결과 (공개판)"
            response.text = json.dumps(payload, ensure_ascii=False)
        return response


def test_classification_style_marker_triggers_clean_retry(tmp_path):
    provider = _MarkerThenGoodProvider()
    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="retry-title-marker",
        plan=_plan(1),
        max_quality_retries=1,
    )

    assert stats["target_met"] is True and provider.calls == 2
    row = _read_jsonl(run_dir / "candidates.jsonl")[0]
    first_errors = row["generation_quality_history"][0]["errors"]
    assert "quality:classification_style_marker:title" in first_errors
    # The failed marker is redacted from the carried draft; only the generic
    # prohibition list remains in the prompt.
    assert "1차 파일럿 결과 [머리말 표기 제거]" in provider.prompts[1]


def test_oversample_plan_stops_after_selection_target_is_filled(tmp_path):
    provider = _RecordingProvider()
    _, stats = _run(
        tmp_path,
        provider,
        run_id="adaptive-fill",
        plan=_plan(3),
        selection_targets={"S2": 1},
    )
    assert provider.calls == 1
    assert stats["planned"] == 3
    assert stats["completed"] == stats["candidates"] == 1
    assert stats["unused_plan_items"] == 2
    assert stats["target_met"] is True


def test_scenario_targets_prevent_early_family_from_consuming_grade_quota(tmp_path):
    plan = _two_scenario_plan()
    provider = _RecordingProvider()
    run_dir, stats = _run(
        tmp_path,
        provider,
        run_id="scenario-balanced-selection",
        plan=plan,
        selection_targets={"S2": 2},
        selection_targets_by_scenario={
            "matched-tech-s2": 1,
            "matched-business-s2": 1,
        },
        base_final_targets={"S2": 2},
        base_final_targets_by_scenario={
            "matched-tech-s2": 1,
            "matched-business-s2": 1,
        },
    )

    assert provider.calls == 2
    assert stats["candidate_by_scenario"] == {
        "matched-business-s2": 1,
        "matched-tech-s2": 1,
    }
    assert stats["candidate_retention_mode"] == "base_target_only"
    rows = _read_jsonl(run_dir / "candidates.jsonl")
    assert {row["scenario_id"] for row in rows} == {
        "matched-tech-s2",
        "matched-business-s2",
    }


def test_prejudge_buffer_is_retained_separately_from_base_target(tmp_path):
    plan = _two_scenario_plan()
    run_dir, stats = _run(
        tmp_path,
        _RecordingProvider(),
        run_id="prejudge-buffer",
        plan=plan,
        selection_targets={"S2": 4},
        selection_targets_by_scenario={
            "matched-tech-s2": 2,
            "matched-business-s2": 2,
        },
        base_final_targets={"S2": 2},
        base_final_targets_by_scenario={
            "matched-tech-s2": 1,
            "matched-business-s2": 1,
        },
    )

    assert stats["target_met"] is True
    assert stats["candidates"] == stats["selection_target_total"] == 4
    assert stats["base_final_target_total"] == 2
    assert stats["candidate_buffer_target_total"] == 2
    assert stats["prejudge_candidate_target_total"] == 4
    assert stats["candidate_buffer_extra_total"] == 2
    assert stats["candidate_retention_mode"] == "pre_judge_buffer"
    assert stats["candidate_by_scenario"] == {
        "matched-business-s2": 2,
        "matched-tech-s2": 2,
    }
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selection_targets"] == {"S2": 4}
    assert manifest["base_final_targets"] == {"S2": 2}


def test_prejudge_buffered_run_passes_canonical_judge_attestation(tmp_path):
    run_dir, stats = _run(
        tmp_path,
        _RecordingProvider(),
        run_id="buffer-attestation",
        plan=_two_scenario_plan(),
        selection_targets={"S2": 4},
        selection_targets_by_scenario={
            "matched-tech-s2": 2,
            "matched-business-s2": 2,
        },
        base_final_targets={"S2": 2},
        base_final_targets_by_scenario={
            "matched-tech-s2": 1,
            "matched-business-s2": 1,
        },
        declared_model_revision="sha256:" + "1" * 64,
    )

    candidates = _read_jsonl(run_dir / "candidates.jsonl")
    attestation = attest_generation_input(
        run_dir / "candidates.jsonl",
        records=candidates,
        intended_use="training",
    )

    assert stats["base_final_target_total"] == 2
    assert stats["prejudge_candidate_target_total"] == 4
    assert attestation["input_count"] == 4
    assert attestation["selection_target_total"] == 4
    assert attestation["usage_contract"]["intended_use"] == "training"


def test_resume_uses_fsynced_journal_keys_without_duplicate_generation(tmp_path):
    plan = _plan(3)
    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            _RecordingProvider(interrupt_on=2),
            run_id="resume-me",
            plan=plan,
        )
    run_dir = tmp_path / "resume-me"
    assert len(_read_jsonl(run_dir / "candidates.journal.jsonl")) == 1
    assert not (run_dir / "candidates.jsonl").exists()
    assert not (run_dir / "COMPLETE.json").exists()
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "interrupted"

    resumed_provider = _RecordingProvider()
    resumed_dir, stats = _run(
        tmp_path,
        resumed_provider,
        resume_run=run_dir,
        plan=plan,
    )
    assert resumed_dir == run_dir
    assert resumed_provider.calls == 2
    assert stats["completed"] == stats["candidates"] == 3
    journal_rows = _read_jsonl(run_dir / "candidates.journal.jsonl")
    keys = [row["generation_resume_key"] for row in journal_rows]
    assert len(keys) == len(set(keys)) == 3
    assert len(_read_jsonl(run_dir / "candidates.jsonl")) == 3
    assert (run_dir / "COMPLETE.json").is_file()
    resumed_manifest = json.loads(
        (run_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(resumed_manifest["model_attestation_revalidations"]) == 1
    assert (
        resumed_manifest["model_attestation_revalidations"][0]["binding_sha256"]
        == resumed_manifest["model_attestation"]["binding_sha256"]
    )


def test_resume_refuses_changed_catalog_code_provider_model_or_plan(tmp_path):
    plan = _plan(2)
    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            _RecordingProvider(interrupt_on=1),
            run_id="strict-resume",
            plan=plan,
        )
    run_dir = tmp_path / "strict-resume"

    with pytest.raises(ProxyGenerationRunError, match="resume contract mismatch"):
        run_generation(
            plan,
            provider=_RecordingProvider(),
            requested_provider="local_openai",
            catalog_version="proxy-scenarios-v2",
            catalog_sha256="changed",
            code_sha256="b" * 64,
            declared_model_revision="sha256:" + _TEST_MODEL_DIGEST,
            out_root=tmp_path,
            resume_run=run_dir,
        )
    with pytest.raises(ProxyGenerationRunError, match="resume contract mismatch"):
        _run(
            tmp_path,
            _RecordingProvider(),
            resume_run=run_dir,
            plan=plan,
            generation_namespace="forged-topup-namespace",
        )


def test_resume_refuses_tampered_row_model_attestation(tmp_path):
    plan = _plan(2)
    with pytest.raises(KeyboardInterrupt):
        _run(
            tmp_path,
            _RecordingProvider(interrupt_on=2),
            run_id="tampered-attestation",
            plan=plan,
        )
    run_dir = tmp_path / "tampered-attestation"
    rows = _read_jsonl(run_dir / "candidates.journal.jsonl")
    rows[0]["generation_contract"]["model_attestation"][
        "endpoint_identity_sha256"
    ] = "0" * 64
    (run_dir / "candidates.journal.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    with pytest.raises(ProxyGenerationRunError, match="model attestation is invalid"):
        _run(
            tmp_path,
            _RecordingProvider(),
            resume_run=run_dir,
            plan=plan,
        )


def test_execution_requires_expected_live_digest_before_run_directory(tmp_path):
    with pytest.raises(ProxyGenerationRunError, match="expected model manifest"):
        run_generation(
            _plan(1),
            provider=_RecordingProvider(),
            requested_provider="local_openai",
            catalog_version="proxy-scenarios-v2",
            catalog_sha256="a" * 64,
            code_sha256="b" * 64,
            declared_model_revision=None,
            out_root=tmp_path,
            run_id="must-not-exist-no-digest",
        )
    assert not (tmp_path / "must-not-exist-no-digest").exists()


def test_dry_run_model_attestation_is_pending_and_provider_restricted():
    pending = scenario_builder.generation_model_attestation(
        _RecordingProvider(),
        requested_name="local_openai",
        expected_model_revision=f"sha256:{_TEST_MODEL_DIGEST}",
        live=False,
    )
    assert pending["status"] == "pending_live_verification"
    assert pending["checked_at"] is None
    with pytest.raises(ProxyGenerationRunError, match="requires provider"):
        scenario_builder.generation_model_attestation(
            _RecordingProvider(),
            requested_name="openai",
            expected_model_revision=f"sha256:{_TEST_MODEL_DIGEST}",
            live=False,
        )


def test_cli_dry_run_reports_live_verification_pending_without_inventory_call(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        scenario_builder, "build_provider", lambda _name: _RecordingProvider()
    )

    assert (
        scenario_builder.main(
            [
                "--catalog",
                "datasets/proxy_gold/scenario_catalog.v1.json",
                "--provider",
                "local_openai",
                "--representative-pilot",
                "--dry-run",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["model_attestation"]["status"] == "pending_live_verification"
    assert payload["model_attestation"]["checked_at"] is None


@pytest.mark.parametrize(
    ("requested", "runtime", "model"),
    [
        ("noop", "recording_local", "qwen3:14b"),
        ("local_openai", "unknown", "qwen3:14b"),
        ("local_openai", "recording_local", "fake-model"),
    ],
)
def test_noop_unknown_and_fake_provider_or_model_are_blocked_before_run_dir(
    tmp_path, requested, runtime, model
):
    provider = _RecordingProvider()
    provider.name = runtime
    provider.model = model
    with pytest.raises(ProxyGenerationRunError, match="noop/unknown/fake"):
        run_generation(
            _plan(1),
            provider=provider,
            requested_provider=requested,
            catalog_version="proxy-scenarios-v2",
            catalog_sha256="a" * 64,
            code_sha256="b" * 64,
            out_root=tmp_path,
            run_id="must-not-exist",
        )
    assert not (tmp_path / "must-not-exist").exists()


def test_target_shortfall_is_nonzero_unless_allow_partial(tmp_path):
    _, strict_stats = _run(
        tmp_path,
        _RecordingProvider(malformed=True),
        run_id="strict-partial",
        plan=_plan(1),
    )
    assert strict_stats["target_met"] is False
    assert strict_stats["candidates"] == 0 and strict_stats["rejected"] == 1
    assert completion_exit_code(strict_stats, allow_partial=False) == 2

    run_dir, allowed_stats = _run(
        tmp_path,
        _RecordingProvider(malformed=True),
        run_id="allowed-partial",
        allow_partial=True,
        plan=_plan(1),
    )
    assert allowed_stats["target_met"] is False
    assert completion_exit_code(allowed_stats, allow_partial=True) == 0
    marker = json.loads((run_dir / "COMPLETE.json").read_text(encoding="utf-8"))
    assert marker["target_met"] is False and marker["allow_partial"] is True
