from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from koipa.four_path_shadow import (
    ComparisonDependencies,
    compare_row,
    shadow_request_doc_id,
    summarize,
    write_jsonl,
)
from koipa.schemas.classify import EvaluationFactors, FactorDetail


class _Grade:
    value = "S2"


class _Service:
    def __init__(self):
        self.requests = []

    def classify(self, request):
        self.requests.append(request)
        return SimpleNamespace(
            label=_Grade(),
            confidence=0.81,
            scores={"S2": 0.81, "S3": 0.19},
            status="needs_review",
            model_version="test-model",
            rule_grade="S2",
            model_grade="S2",
            decision_path="agreement",
            # [2026-10-02] 실제 EvaluationFactors를 쓴다 — SimpleNamespace 흉내는 _to_plain()의
            # model_dump() 경로(실제 운영 경로)를 안 타고 __dict__ 폴백만 타서 거짓 통과할 수 있다.
            evaluation_factors=EvaluationFactors(
                secrecy=FactorDetail(state="observed", value=2, evidence=["근거"]),
                value=FactorDetail(state="observed", value=2, evidence=["근거"]),
                management=FactorDetail(state="observed", value=1, evidence=["근거"]),
                scores={"SECRECY": 2.0},
            ),
            rule_evaluation_factors=None,
            factors_source="rule_evidenced",
            evidence=[SimpleNamespace(start=3, end=8, text="민감한 원문", weight=0.9, tag="seed")],
            warnings=["low-confidence: confidence=0.49 < 0.50 — review recommended"],
            automation_assessment=SimpleNamespace(model_dump=lambda mode="json": {"selected_label": "S2"}),
        )


def _deps(*, factor=None, regulation=None, policy=None):
    return ComparisonDependencies(
        factor_shadow=factor or (lambda text, metadata, label: {"state": "not_configured"}),
        regulation_lookup=regulation or (lambda doc_id: {"state": "not_configured"}),
        policy_evaluate=policy or (lambda row, policy: {"state": "not_configured"}),
    )


def test_shadow_request_id_is_deterministic_and_never_a_real_uuid():
    first = shadow_request_doc_id("real-doc", "본문")
    assert first == shadow_request_doc_id("real-doc", "본문")
    assert first.startswith("shadow-eval-")
    with pytest.raises(ValueError):
        uuid.UUID(first)


def test_four_path_record_preserves_input_and_never_emits_evidence_text():
    source = {
        "doc_id": "8e1c8336-0015-4bce-b9d1-8c5f7e3e74e0",
        "text": "문서 본문",
        "metadata": {"source_type": "internal"},
    }
    before = copy.deepcopy(source)
    service = _Service()
    record = compare_row(
        source,
        line_number=7,
        service=service,
        dependencies=_deps(
            factor=lambda text, metadata, label: {"state": "ok", "factor_grade": "S1"},
            regulation=lambda doc_id: {
                "state": "ok",
                "items": [{"clause_id": "clause-1", "sentences": ["규정 원문"]}],
            },
            policy=lambda row, policy: {"state": "ok", "would_require_review": True,
                                        "missing_evidence": ["access_scope"]},
        ),
    )

    assert source == before
    assert service.requests[0].doc_id != source["doc_id"]
    assert service.requests[0].doc_id.startswith("shadow-eval-")
    assert record["record_number"] == 7
    assert record["model_candidate"] == "S2"
    assert record["causal_gate"] == "low-confidence"
    assert record["triggered_gates"] == ["low-confidence"]
    assert record["missing_evidence"] == ["access_scope"]
    assert record["automation_allowed"] is False and record["finalized"] is False
    span = record["evidence_spans"][0]
    assert span["text_chars"] == len("민감한 원문") and "text" not in span
    citation = record["regulation_evidence"]["items"][0]
    assert citation["sentence_count"] == 1 and "sentences" not in citation


def test_invalid_optional_paths_are_kept_as_states_not_dropped():
    record = compare_row(
        {"doc_id": "doc-1", "content": "본문"},
        line_number=1,
        service=_Service(),
        dependencies=_deps(
            factor=lambda text, metadata, label: {"state": "unavailable", "reason": "weights_missing"},
            regulation=lambda doc_id: {"state": "unavailable", "reason": "document_not_indexed"},
            policy=lambda row, policy: {"state": "missing_input", "missing": ["policy_packet"]},
        ),
    )
    assert record["factor_shadow"]["state"] == "unavailable"
    assert record["regulation_evidence"]["reason"] == "document_not_indexed"
    assert record["policy_shadow_proposal"]["state"] == "missing_input"
    assert record["final_serving_status"]["state"] == "ok"


def test_summary_and_jsonl_are_non_overwriting(tmp_path: Path):
    records = [{
        "final_serving_status": {"state": "ok", "status": "staging"},
        "factor_shadow": {"state": "not_configured"},
        "regulation_evidence": {"state": "not_configured"},
        "policy_shadow_proposal": {"state": "not_configured"},
        "causal_gate": None,
    }]
    summary = summarize(records)
    assert summary["record_count"] == 1
    assert summary["automation_allowed_count"] == summary["finalized_count"] == 0
    out = tmp_path / "four-path.jsonl"
    write_jsonl(out, records)
    assert json.loads(out.read_text(encoding="utf-8").strip()) == records[0]
    with pytest.raises(FileExistsError):
        write_jsonl(out, records)
