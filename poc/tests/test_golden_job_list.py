"""GET /golden/jobs — 골든 잡 목록.

목록이 없으면 콘솔은 마지막 job_id 를 메모리에만 들고 있어, 새로고침 한 번에 검수하던
후보로 돌아갈 길이 사라진다(2026-08-02 실환경 점검). JobStore.list_recent 는 이미 있었으나
레코드에 job_id 가 없어(키로만 존재) 목록으로 쓸 수 없던 것을 함께 고쳤다.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import settings
from koipa.golden_builder import LabelPair
from koipa.schemas.common import Actor
from koipa.schemas.golden import GoldenBuildRequest
from koipa.services.golden_build_service import GoldenBuildService
from koipa.services.job_store import InMemoryJobStore, get_default_store

client = TestClient(app)
API = "/api/v1"
_ACTOR = Actor(user_id="builder1", role="admin")
_AUTH = {"X-API-Key": "test-key", "X-Actor-Role": "admin"}


def _make_job(tmp_path, grade: str = "S2") -> str:
    req = GoldenBuildRequest(
        source_type="inline",
        docs=[{"doc_id": "a", "text": "본문 내용", "source": "판례"}],
        out_dir=str(tmp_path), actor=_ACTOR,
    )
    resp = GoldenBuildService().submit(
        req, label_fn=lambda _t: LabelPair(grade, 0.8, grade, 0.9, has_real_evidence=True)
    )
    return str(resp.golden_job_id)


def test_list_recent_carries_job_id():
    """레코드에 job_id 가 실려야 목록에서 개별 잡으로 진입할 수 있다."""
    store = InMemoryJobStore()
    jid = uuid.uuid4()
    store.create(jid, {"kind": "golden_build", "actor": "u1"})
    store.update(jid, status="done", gold_count=3)
    rows = store.list_recent(10)
    assert rows and rows[0]["job_id"] == str(jid)
    assert rows[0]["status"] == "done" and rows[0]["gold_count"] == 3


def test_job_list_returns_created_job(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "test-key")
    job_id = _make_job(tmp_path)
    r = client.get(f"{API}/golden/jobs", headers=_AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ordering"] == "best_effort"      # 정렬 신뢰도 고지(무음 오도 방지)
    ids = [j["job_id"] for j in body["jobs"]]
    assert job_id in ids
    row = next(j for j in body["jobs"] if j["job_id"] == job_id)
    assert row["kind"] == "golden_build"
    assert row["status"] == "done"
    # 목록에서 바로 검수/서명으로 진입할 수 있어야 한다
    assert row["review_url"] and job_id in row["review_url"]
    assert row["signoff_url"] and job_id in row["signoff_url"]


def test_job_list_excludes_non_golden_jobs(monkeypatch):
    """JobStore 에는 분류·학습 잡도 섞인다 — 골든 목록에 새면 안 된다."""
    monkeypatch.setattr(settings, "api_key", "test-key")
    other = uuid.uuid4()
    get_default_store().create(other, {"kind": "classify_batch", "actor": "u1"})
    r = client.get(f"{API}/golden/jobs", headers=_AUTH)
    assert r.status_code == 200
    assert str(other) not in [j["job_id"] for j in r.json()["jobs"]]


def test_job_list_requires_auth():
    assert client.get(f"{API}/golden/jobs").status_code in (401, 403)


def test_job_list_limit_is_clamped(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "test-key")
    _make_job(tmp_path)
    assert len(client.get(f"{API}/golden/jobs?limit=1", headers=_AUTH).json()["jobs"]) <= 1
    # 상한 초과·하한 미만도 거절이 아니라 클램프(운영 중 목록이 통째로 죽지 않게)
    assert client.get(f"{API}/golden/jobs?limit=9999", headers=_AUTH).status_code == 200
    assert client.get(f"{API}/golden/jobs?limit=0", headers=_AUTH).status_code == 200


def test_signed_urls_present_when_secret_set(tmp_path, monkeypatch):
    """비밀키가 켜져 있으면 목록의 링크에도 ?t= 토큰이 실려야 한다(링크만 복사해도 열리게)."""
    monkeypatch.setattr(settings, "api_key", "test-key")
    monkeypatch.setattr(settings, "golden_html_url_secret", "s" * 40)
    job_id = _make_job(tmp_path)
    body = client.get(f"{API}/golden/jobs", headers=_AUTH).json()
    row = next(j for j in body["jobs"] if j["job_id"] == job_id)
    assert "?t=" in row["review_url"] and "?t=" in row["signoff_url"]
