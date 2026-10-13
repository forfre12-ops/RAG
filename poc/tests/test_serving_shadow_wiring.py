"""FUN-004/FUN-023 shadow paths are reachable from real classification flow.

Both integrations are intentionally observational: the deployed grade and
review status must be identical to the baseline decision, while operators get
bounded metrics and internal-only structured results.
"""
from __future__ import annotations

import json
import uuid

from koipa.four_path_shadow import configured_policy_shadow
from koipa.policy_facts import text_digest
from koipa.policy_shadow import parse_shadow_policy, policy_digest
from koipa.schemas.common import Grade


def _policy_packet(text: str, document_id: str) -> tuple[dict, dict]:
    raw_policy = {
        "org_id": "member-0142",
        "policy_version": "v1",
        "effective_date": "2020-01-01",
        "grade_order": ["TS", "S1", "S2", "S3"],
        "default_grade": "S3",
        "rules": [{
            "id": "R-PUBLIC",
            "grade": "S3",
            "priority": 10,
            "when": {"public_disclosed": {"op": "eq", "value": True}},
            "requires_evidence": [],
        }],
    }
    policy = parse_shadow_policy(raw_policy)
    packet = {
        "schema_version": "policy-facts-v1-draft",
        "org_id": policy.org_id,
        "document_id": document_id,
        "document_sha256": text_digest(text),
        "policy_version": policy.version,
        "policy_sha256": policy_digest(policy),
        "sources": [],
        "facts": [],
        "estimates": [],
    }
    return raw_policy, packet


def test_policy_shadow_is_inert_until_an_operator_enables_it(monkeypatch):
    from koipa.config import settings

    monkeypatch.setattr(settings, "policy_shadow_enabled", False, raising=False)
    # Disabled must not inspect malformed caller data or introduce a policy result.
    result = configured_policy_shadow("본문", {"policy_packet": object()}, "doc-1")
    assert result == {"state": "not_configured", "reason": "policy_shadow_disabled"}


def test_policy_shadow_uses_server_policy_and_server_document_binding(tmp_path, monkeypatch):
    from koipa.config import settings

    text, document_id = "정책 섀도 확인용 본문", "doc-shadow-1"
    raw_policy, packet = _policy_packet(text, document_id)
    policy_path = tmp_path / "member-policy.json"
    policy_path.write_text(json.dumps(raw_policy), encoding="utf-8")
    monkeypatch.setattr(settings, "policy_shadow_enabled", True, raising=False)
    monkeypatch.setattr(settings, "policy_shadow_policy_path", str(policy_path), raising=False)

    result = configured_policy_shadow(
        text,
        {
            "policy_packet": packet,
            # A caller-supplied context must not select the effective org/time.
            "policy_context": {"org_id": "attacker-org", "as_of": "2099-01-01T00:00:00Z"},
        },
        document_id,
    )

    assert result["state"] == "ok"
    proposal = result["proposal"]
    assert proposal["mode"] == "shadow_only"
    assert proposal["org_id"] == "member-0142"
    assert proposal["document_id"] == document_id
    assert proposal["document_sha256"] == text_digest(text)
    assert proposal["automation_allowed"] is False
    assert proposal["finalized"] is False


def test_policy_shadow_rejects_a_packet_bound_to_other_document(tmp_path, monkeypatch):
    from koipa.config import settings

    text, document_id = "정책 섀도 확인용 본문", "doc-shadow-1"
    raw_policy, packet = _policy_packet(text, document_id)
    packet["document_sha256"] = "a" * 64
    policy_path = tmp_path / "member-policy.json"
    policy_path.write_text(json.dumps(raw_policy), encoding="utf-8")
    monkeypatch.setattr(settings, "policy_shadow_enabled", True, raising=False)
    monkeypatch.setattr(settings, "policy_shadow_policy_path", str(policy_path), raising=False)

    result = configured_policy_shadow(text, {"policy_packet": packet}, document_id)
    assert result == {"state": "invalid_input", "error_code": "packet_scope_mismatch"}


def test_classify_service_records_both_shadows_without_changing_verdict(monkeypatch):
    import koipa.four_path_shadow as four_path_shadow

    from koipa.api.prom_metrics import registry
    from koipa.modules.m5_inference.pipeline import InferenceResult
    from koipa.schemas import common as common_schemas
    from koipa.schemas.classify import ClassifyRequest
    from koipa.services import classify_service as service_module

    monkeypatch.setattr(common_schemas.GradeRegistry, "get_codes", lambda *args, **kwargs: ["TS", "S1", "S2", "S3"])
    monkeypatch.setattr(service_module, "_resolve_serving_model_dir", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        four_path_shadow,
        "configured_factor_shadow",
        lambda text, metadata, label: {
            "state": "ok", "factor_grade": "S1", "direction": "factor_higher", "min_confidence": 0.97,
        },
    )
    monkeypatch.setattr(
        four_path_shadow,
        "configured_policy_shadow",
        lambda text, metadata, doc_id: {
            "state": "ok", "proposal": {"mode": "shadow_only", "policy_version": "v1"},
            "would_require_review": True, "missing_evidence": ["access_scope"],
        },
    )

    service = service_module.ClassifyService()
    monkeypatch.setattr(service, "_agreement_gate", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "_kill_gate_brake", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service.inference,
        "run",
        lambda *args, **kwargs: InferenceResult(
            label=Grade.S2,
            confidence=0.99,
            scores={"S2": 0.99, "S3": 0.01},
            model_version="test-model",
            warnings=[],
            rule_grade="S2",
            model_grade="S2",
            evidence=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "_try_persist",
        lambda *args, **kwargs: (uuid.uuid4(), [], kwargs["status"]),
    )

    factor_labels = {"path": "factor", "state": "ok", "direction": "factor_higher"}
    policy_labels = {"path": "policy", "state": "ok", "direction": "not_applicable"}
    before_factor = registry.get_sample_value("koipa_classify_shadow_observation_total", factor_labels) or 0.0
    before_policy = registry.get_sample_value("koipa_classify_shadow_observation_total", policy_labels) or 0.0

    response = service.classify(ClassifyRequest(doc_id="not-a-uuid", content="서빙 섀도 본문"))

    assert response.label == Grade.S2
    assert response.status == "staging"
    assert response.factor_shadow and response.factor_shadow["direction"] == "factor_higher"
    assert response.policy_shadow and response.policy_shadow["proposal"]["mode"] == "shadow_only"
    assert "계량 전용 — 등급·상태 미변경" in " ".join(response.warnings)
    assert (registry.get_sample_value("koipa_classify_shadow_observation_total", factor_labels) or 0.0) >= before_factor + 1
    assert (registry.get_sample_value("koipa_classify_shadow_observation_total", policy_labels) or 0.0) >= before_policy + 1
