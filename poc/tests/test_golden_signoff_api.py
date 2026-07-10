"""골든셋 검수 · 화면 서명(signoff) — 서비스 + HTTP 엔드포인트.

빌드 잡의 gold 후보를 승인/등급변경/거부해 locked_gold_eval 로 승격하는 경로를 검증한다.
- 서비스: apply_signoff 가 결정을 promote_to_locked 로 흘려 locked/rejected/readiness 산출.
- 엔드포인트: RBAC, 인증 신원 바인딩(위조 차단), 머신 reviewer 403, 정본·라이브 무변경.

두 검수 루프 분리 유지: 이건 지재원 '골든셋 검수'(→locked_gold_eval)이지 회원사 운영검수 아님.
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings
from lloydk.golden_builder import LabelPair
from lloydk.schemas.common import Actor
from lloydk.schemas.golden import GoldenBuildRequest, GoldenSignoffDecision
from lloydk.services.golden_build_service import GoldenBuildService

client = TestClient(app)
API = "/api/v1"
_ACTOR = Actor(user_id="builder1", role="admin")


def _label_fn(text: str) -> LabelPair:
    """텍스트 키워드로 rule==llm 합의 등급 부여(gold_candidate 생성)."""
    t = text.lower()
    if "ts문서" in t:
        return LabelPair("TS", 0.9, "TS", 0.95, has_real_evidence=True)
    if "s1문서" in t:
        return LabelPair("S1", 0.9, "S1", 0.95, has_real_evidence=True)
    return LabelPair("S2", 0.8, "S2", 0.9, has_real_evidence=True)


def _make_job(tmp_path, docs) -> str:
    req = GoldenBuildRequest(
        source_type="inline", docs=docs, out_dir=str(tmp_path), actor=_ACTOR,
    )
    resp = GoldenBuildService().submit(req, label_fn=_label_fn)
    return str(resp.golden_job_id)


def _h(role="reviewer", key=None):
    return {"X-API-Key": key if key is not None else settings.api_key, "X-Actor-Role": role}


# ────────────────────────────── 서비스 단위 ──────────────────────────────
def test_apply_signoff_approve_change_reject(tmp_path):
    docs = [
        {"doc_id": "a", "text": "ts문서 내용"},
        {"doc_id": "b", "text": "s1문서 내용"},
        {"doc_id": "c", "text": "s2문서 내용"},
    ]
    job_id = _make_job(tmp_path, docs)
    svc = GoldenBuildService()
    import uuid as _uuid

    result = svc.apply_signoff(
        _uuid.UUID(job_id),
        [
            GoldenSignoffDecision(doc_id="a", decision="approve"),
            GoldenSignoffDecision(doc_id="b", decision="change", grade="TS"),
            GoldenSignoffDecision(doc_id="c", decision="reject"),
        ],
        reviewer_id="r1",
    )
    assert result is not None
    assert result["locked"] == 2                       # a(approve TS) + b(change→TS)
    assert result["locked_by_grade"] == {"TS": 2}
    assert result["rejected"] == 1                      # c(reject)
    assert result["rejected_reasons"].get("no_signoff") == 1
    assert result["published"] is False                # 기본 미리보기
    # readiness 구조(등급별 2 미만이라 ready=False)
    assert result["readiness"]["ready"] is False
    assert result["readiness"]["per_grade"]["TS"] == 2
    # run-스코프 감사 파일 기록됨(정본 아님)
    locked_rows = [
        json.loads(ln)
        for ln in (tmp_path / f"locked_{job_id}.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(locked_rows) == 2
    assert all(r["label_source"] == "human_review" and r["reviewer_id"] == "r1" for r in locked_rows)


def test_apply_signoff_machine_reviewer_locks_nothing(tmp_path):
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    import uuid as _uuid

    result = GoldenBuildService().apply_signoff(
        _uuid.UUID(job_id),
        [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="qwen_bot",                          # 머신 접두사 → is_human_reviewer=False
    )
    assert result["locked"] == 0
    assert result["rejected_reasons"].get("machine_reviewer") == 1


def test_apply_signoff_unknown_job_is_none():
    import uuid as _uuid

    assert GoldenBuildService().apply_signoff(
        _uuid.uuid4(), [], reviewer_id="r1"
    ) is None


def test_apply_signoff_publish_writes_live_path(tmp_path, monkeypatch):
    live = tmp_path / "locked_eval_live.jsonl"
    monkeypatch.setattr(settings, "locked_eval_jsonl", str(live))
    job_id = _make_job(tmp_path / "run", [{"doc_id": "a", "text": "ts문서"}])
    import uuid as _uuid

    result = GoldenBuildService().apply_signoff(
        _uuid.UUID(job_id),
        [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="r1",
        publish=True,
    )
    assert result["published"] is True
    assert live.exists()
    rows = [json.loads(ln) for ln in live.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(rows) == 1 and rows[0]["doc_id"] == "a" and rows[0]["label"] == "TS"


# ────────────────────────────── HTTP 엔드포인트 ──────────────────────────────
def test_signoff_endpoint_approve_locks(tmp_path):
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    r = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        headers=_h(role="reviewer"),
        json={
            "decisions": [{"doc_id": "a", "decision": "approve"}],
            "actor": {"user_id": "reviewer_kim", "role": "reviewer"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["locked"] == 1
    assert body["locked_by_grade"] == {"TS": 1}
    assert body["reviewer_id"] == "reviewer_kim"
    assert body["published"] is False


def test_signoff_endpoint_rejects_machine_reviewer(tmp_path):
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    r = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        headers=_h(role="reviewer"),
        json={
            "decisions": [{"doc_id": "a", "decision": "approve"}],
            "actor": {"user_id": "ai_assist", "role": "reviewer"},  # 머신 → 403
        },
    )
    assert r.status_code == 403, r.text


def test_signoff_endpoint_requires_role(tmp_path):
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    # 무인증 → 401/403
    r = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        json={"decisions": [], "actor": {"user_id": "x", "role": "reviewer"}},
    )
    assert r.status_code in (401, 403), r.text
    # system 역할은 서명 불가(admin/reviewer/kl_backend만)
    r2 = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        headers=_h(role="system"),
        json={
            "decisions": [{"doc_id": "a", "decision": "approve"}],
            "actor": {"user_id": "sys", "role": "system"},
        },
    )
    assert r2.status_code == 403, r2.text


def test_signoff_endpoint_unknown_job_404(tmp_path):
    import uuid as _uuid

    r = client.post(
        f"{API}/golden/jobs/{_uuid.uuid4()}/signoff",
        headers=_h(role="reviewer"),
        json={
            "decisions": [{"doc_id": "a", "decision": "approve"}],
            "actor": {"user_id": "reviewer_kim", "role": "reviewer"},
        },
    )
    assert r.status_code == 404, r.text


def test_signoff_html_renders(tmp_path):
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서 본문"}])
    r = client.get(f"{API}/golden/jobs/{job_id}/signoff.html", headers=_h(role="reviewer"))
    assert r.status_code == 200, r.text
    assert r.text.startswith("<!doctype html>")
    assert "서명 제출" in r.text and "ts문서 본문" in r.text
    assert f"/api/v1/golden/jobs/{job_id}/signoff" in r.text  # POST url 배선
