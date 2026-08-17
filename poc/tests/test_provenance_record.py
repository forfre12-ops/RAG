"""실문서 출처를 **나중에 기록**하는 경로.

왜(실측 2026-08-17, KL 223). 실문서 74건 중 62건이 출처는 있고 **사용 권한 근거가 없는**
상태였다(적재 스크립트가 metadata top-level 에만 썼다). 그것을 채울 경로가 화면에도 API 에도
없어서, 그 62건은 영원히 미완으로 남는 구조였다.

⚠ 결정(decision) API 에 얹지 않고 별도 엔드포인트로 둔다. 결정은 action 마다 status 를
  정하는 표를 갖고 있어(approve→approved_proxy, change→grade_fixed_unlocked …)
  "등급은 그대로 두고 출처만 기록" 을 표현할 수 없다.

⚠ 원장은 append-only 이고 `_latest_decisions` 가 doc_id 별 **마지막 줄**을 현재 상태로 쓴다.
  출처 기록을 그냥 남기면 **등급 확정이 그 줄로 덮인다.** 그래서 event_kind="provenance" 로
  남기고 읽는 쪽이 걸러낸다. 이 성질을 시험으로 고정한다.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService

DOC = "0" * 16


def _svc(tmp_path: Path, *, origin: str = "public_real", legacy_source: str = "판례(2000+)"):
    (tmp_path / f"{DOC}_문서.md").write_text("본문 " * 300, encoding="utf-8")
    meta = {
        "doc_id": DOC, "document_origin": origin, "document_type": "판례 사본",
        "candidate_status": "under_review", "claim_scope": "검수 전 후보",
    }
    if legacy_source:
        meta["source_reference"] = legacy_source
    (tmp_path / f"{DOC}.metadata.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return ProxyGoldCandidateService(tmp_path)


def test_legacy_only_starts_as_partial(tmp_path):
    c = _svc(tmp_path).get_candidate(DOC)
    assert c["provenance"]["status"] == "partial"
    assert c["provenance"]["source_reference"] == "판례(2000+)"


def test_record_completes_it(tmp_path):
    svc = _svc(tmp_path)
    svc.record_provenance(doc_id=DOC, source_reference="대법원 판례공보 2001-3",
                          authorization_basis="공개 판례", actor_id="kim.cs")
    p = svc.get_candidate(DOC)["provenance"]
    assert p["status"] == "recorded"
    assert p["origin"] == "console_record"
    assert p["recorded_by"] == "kim.cs"
    assert svc.summary()["actual_provenance_recorded"] == 1


def test_recording_does_not_overwrite_the_grade(tmp_path):
    """가장 중요한 성질 — 출처 기록이 등급 확정을 지우면 안 된다."""
    svc = _svc(tmp_path)
    svc.decide(doc_id=DOC, action="change", grade="S3", actor_id="kim.cs", reason="확정")
    assert svc.summary()["fixed"] == 1

    svc.record_provenance(doc_id=DOC, source_reference="출처", authorization_basis="근거",
                          actor_id="kim.cs")
    c = svc.get_candidate(DOC)
    assert c["status"] == "grade_fixed_unlocked", "출처 기록이 상태를 덮었다"
    assert c["final_grade"] == "S3", "출처 기록이 등급을 지웠다"
    assert svc.summary()["fixed"] == 1, "확정 수가 줄었다"


def test_ledger_marks_the_event_kind(tmp_path):
    svc = _svc(tmp_path)
    svc.record_provenance(doc_id=DOC, source_reference="출처", authorization_basis="근거",
                          actor_id="kim.cs")
    rows = [json.loads(x) for x in
            (tmp_path / "candidate_decisions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["event_kind"] == "provenance"
    assert "final_grade" not in rows[-1], "결정 필드가 섞이면 나중에 결정으로 읽힌다"
    assert rows[-1]["provenance_before"] != rows[-1]["provenance_after"], "변경 전후가 안 남았다"


@pytest.mark.parametrize(("src", "basis"), [("", "근거"), ("출처", ""), ("   ", "근거")])
def test_both_fields_required(tmp_path, src, basis):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        svc.record_provenance(doc_id=DOC, source_reference=src, authorization_basis=basis,
                              actor_id="x")


def test_synthetic_candidate_is_rejected(tmp_path):
    """출처·권한 근거는 실문서에만 뜻이 있다."""
    svc = _svc(tmp_path, origin="synthetic", legacy_source="")
    with pytest.raises(ValueError, match="actual documents"):
        svc.record_provenance(doc_id=DOC, source_reference="a", authorization_basis="b",
                              actor_id="x")


def test_unknown_document_returns_none(tmp_path):
    assert _svc(tmp_path).record_provenance(
        doc_id="nope", source_reference="a", authorization_basis="b", actor_id="x") is None


def test_endpoint_is_exposed_and_guarded():
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    c = TestClient(app)
    path = f"/api/v1/golden/candidates/{DOC}/provenance"
    assert path.replace(DOC, "{doc_id}") in app.openapi()["paths"]
    r = c.post(path, json={"source_reference": "a", "authorization_basis": "b"})
    assert r.status_code == 401, f"무인증인데 {r.status_code}"
