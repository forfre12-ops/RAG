"""생성 화면 고도화 회귀 시험.

숫자의 근거, 가짜 생성 성공 차단, 관리자 추가 규칙 저장을 각각 고정한다.
"""

from __future__ import annotations

import json

import pytest

from koipa.config import settings
from koipa.schemas.common import Actor, Grade
from koipa.schemas.synthesis import SynthGenerateRequest
from koipa.services.job_store import InMemoryJobStore
from koipa.services.synthesis_service import SynthesisDispatchUnavailable, SynthesisService


def _request(provider: str = "ollama") -> SynthGenerateRequest:
    return SynthGenerateRequest(
        target_grade=Grade.S2,
        domain="mixed",
        scenario_context="연구기획팀 기술 검토와 협력사 일정 조율",
        document_type_hint="기술 검토 회의록",
        count=2,
        llm_provider=provider,
        actor=Actor(user_id="console-admin", role="admin"),
    )


def test_generation_refuses_noop_before_creating_a_phantom_job() -> None:
    service = SynthesisService()
    service.jobs = InMemoryJobStore()

    with pytest.raises(SynthesisDispatchUnavailable, match="noop"):
        service.submit(_request("noop"))

    assert service.jobs.list_recent() == []


def test_generation_dispatches_context_to_the_actual_worker(monkeypatch) -> None:
    import koipa.services.synthesis_service as service_module
    import koipa.workers.tasks as tasks

    captured: dict = {}

    class _Task:
        @staticmethod
        def delay(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs

    monkeypatch.setattr(service_module, "_celery_dispatch_available", lambda: True)
    monkeypatch.setattr(
        "koipa.services.synth_provider_status.provider_status",
        lambda: [{"name": "ollama", "available": True, "reason": ""}],
    )
    monkeypatch.setattr(tasks, "synthesize_batch", _Task)
    service = SynthesisService()
    service.jobs = InMemoryJobStore()

    response = service.submit(_request())

    assert response.dispatched is True
    assert captured["kwargs"]["scenario_context"] == "연구기획팀 기술 검토와 협력사 일정 조율"
    assert captured["kwargs"]["document_type_hint"] == "기술 검토 회의록"


def test_custom_playbook_rules_are_persistent_and_applied(monkeypatch, tmp_path) -> None:
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text
    from koipa.services.synth_playbook_service import save_override, view

    path = tmp_path / "runtime" / "synth_playbook.json"
    monkeypatch.setattr(settings, "synth_playbook_path", str(path))
    saved = save_override(
        custom_rules=["회의록에는 결정 사항과 담당 부서를 함께 적는다."],
        custom_avoid_phrases=["관계 부서 협의"],
        updated_by="console-admin",
    )

    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["updated_by"] == "console-admin"
    assert saved["custom_rules"] == ["회의록에는 결정 사항과 담당 부서를 함께 적는다."]
    assert "회의록에는 결정 사항과 담당 부서를 함께 적는다." in playbook_text()
    payload = view()
    assert payload["custom_avoid_phrases"] == ["관계 부서 협의"]
    assert payload["updated_by"] == "console-admin"


def test_admin_can_update_playbook_through_the_api(monkeypatch, tmp_path) -> None:
    """팝업의 PUT 계약은 저장소·실제 생성 규칙까지 같은 내용을 본다."""
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    monkeypatch.setattr(settings, "synth_playbook_path", str(tmp_path / "synth_playbook.json"))
    headers = {
        "X-API-Key": settings.api_key or "test-key",
        "X-Actor-Id": "playbook-admin",
        "X-Actor-Role": "admin",
    }
    payload = {
        "custom_rules": ["검토 보고서는 결정 근거와 담당 부서를 함께 적는다."],
        "custom_avoid_phrases": ["당사"],
        "actor": {"user_id": "body-claim-is-bound", "role": "admin"},
    }
    with TestClient(app) as client:
        saved = client.put("/api/v1/synth/playbook", headers=headers, json=payload)
        fetched = client.get("/api/v1/synth/playbook", headers=headers)

    assert saved.status_code == 200, saved.text
    assert saved.json()["custom_rules"] == payload["custom_rules"]
    # API key 모드에는 개인 인증 subject가 없으므로, 기존 운영 API와 같이 포털이 전달한
    # actor.user_id를 감사자 표기로 보존한다. JWT에서는 bind_authenticated_actor가 sub로 덮는다.
    assert saved.json()["updated_by"] == "body-claim-is-bound"
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["custom_avoid_phrases"] == payload["custom_avoid_phrases"]


def test_coverage_explains_total_as_all_three_dataset_splits(tmp_path) -> None:
    from koipa.services.synth_coverage import coverage_report

    for split, amount in (("train", 2), ("val", 1), ("test", 3)):
        (tmp_path / f"{split}.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {"label": "S2", "domain": "mixed", "source": "synthetic" if i else "real"},
                    ensure_ascii=False,
                )
                for i in range(amount)
            ),
            encoding="utf-8",
        )

    result = coverage_report(dataset_dir=str(tmp_path))

    assert result["documents"] == 6
    assert result["split_counts"] == {"train": 2, "val": 1, "test": 3}
    assert result["source_counts"] == {"real": 3, "synthetic": 3}
