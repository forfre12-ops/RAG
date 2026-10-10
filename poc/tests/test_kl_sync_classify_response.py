"""동기 분류도 KL의 설명 응답 계약을 잃지 않게 잠근다."""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from koipa.schemas.classify import ClassifyResponse


def _result(**overrides) -> ClassifyResponse:
    fields = {
        "inference_id": uuid4(),
        "doc_id": "kl-sync-doc",
        "label": "S2",
        "confidence": 0.81,
        "scores": {"S2": 0.81, "S3": 0.19},
        "model_version": "v-test",
        "elapsed_ms": 0,
        "rule_grade": "S1",
        "decision_path": "model-plus-rule-disagreement",
        "evidence": [],
    }
    fields.update(overrides)
    return ClassifyResponse(**fields)


def test_kl_sync_classify_projects_rule_and_regulation_explanation(monkeypatch):
    """KL 동기 호출도 대표 답·불일치 룰·RAG/LLM 설명을 같은 응답에 받는다."""
    from koipa.api import classify as mod

    result = _result()
    service = SimpleNamespace(classify=lambda _req: result)
    reference = [SimpleNamespace(article_no="제41조")]
    calls: list[object] = []

    monkeypatch.setattr(mod, "regulation_reference_for_kl_wire", lambda doc_id: calls.append(doc_id) or reference)
    monkeypatch.setattr(
        mod,
        "regulation_summary_for_kl_wire",
        lambda doc_id, items: calls.append((doc_id, items)) or "제41조가 적용됩니다.",
    )

    out = mod.classify(
        SimpleNamespace(state=SimpleNamespace(auth_role="kl_backend")),
        SimpleNamespace(),
        service,
    )

    assert out.label == "S2"
    assert out.rule_grade == "S1"
    assert out.regulation_reference == reference
    assert out.regulation_summary == "제41조가 적용됩니다."
    assert out.explanation_status == "available"
    assert out.fallback_reason is None
    assert calls == ["kl-sync-doc", ("kl-sync-doc", reference)]


def test_non_kl_sync_classify_does_not_invoke_regulation_enrichment(monkeypatch):
    """내부 분류 호출에는 KL 전용 RAG/LLM 비용을 붙이지 않는다."""
    from koipa.api import classify as mod

    result = _result()
    service = SimpleNamespace(classify=lambda _req: result)
    monkeypatch.setattr(
        mod,
        "regulation_reference_for_kl_wire",
        lambda _doc_id: (_ for _ in ()).throw(AssertionError("must not enrich non-KL request")),
    )

    out = mod.classify(
        SimpleNamespace(state=SimpleNamespace(auth_role="admin")),
        SimpleNamespace(),
        service,
    )

    assert out is result
    assert out.regulation_reference is None
    assert out.explanation_status == "not_requested"
