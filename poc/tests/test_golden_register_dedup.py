"""POST /golden/jobs/register — 같은 파일을 다시 올려도 묶음이 늘지 않는다.

왜(2026-08-25 사용자 지적, 223 실측). 검수 목록 8행이 실제로는 파일 3개였다 —
demo_slate_v1.jsonl 4행 · regate_gold_20260702_013914.jsonl 2행 ·
golden_review/ff5a822c/candidates.jsonl 2행. register_build 가 호출마다
uuid4() 를 새로 뽑고 중복 검사가 없었다.

목록이 지저분한 것으로 끝나지 않는다. **검수 진행분은 잡 단위 원장**
(locked_<job_id>.jsonl · rejected_<job_id>.jsonl)에 쌓인다. 쌍둥이 행을 열면
이미 서명한 건이 '남은 건수'로 다시 나와 같은 문서를 두 번 검수하게 된다.
실제로 223 에서는 목록 맨 위(최신) 행이 진행분 0인 빈 잡이었고 작업분은 아래 행에 있었다.
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import settings
from koipa.services.golden_build_service import GoldenBuildService, _ledger_paths

client = TestClient(app)
API = "/api/v1"
_AUTH = {"X-API-Key": "test-key", "X-Actor-Role": "admin"}


def _slate(tmp_path, name: str = "slate.jsonl", n: int = 3):
    p = tmp_path / name
    p.write_text(
        "\n".join(
            json.dumps({"doc_id": f"d{i}", "text": f"본문 {i}", "label": "S2"})
            for i in range(n)
        ),
        encoding="utf-8",
    )
    return p


def test_same_file_twice_returns_same_job(tmp_path):
    """같은 경로를 두 번 등록하면 두 번째는 첫 잡을 그대로 돌려준다."""
    p = _slate(tmp_path)
    svc = GoldenBuildService()
    first = svc.register_build(str(p), actor_user_id="reviewer1")
    second = svc.register_build(str(p), actor_user_id="reviewer1")
    assert first is not None
    assert second == first


def test_different_files_stay_separate(tmp_path):
    """다른 파일은 당연히 다른 잡 — 중복 가드가 과하게 뭉치지 않는지."""
    svc = GoldenBuildService()
    a = svc.register_build(str(_slate(tmp_path, "a.jsonl")), actor_user_id="r")
    b = svc.register_build(str(_slate(tmp_path, "b.jsonl")), actor_user_id="r")
    assert a is not None and b is not None and a != b


def test_reused_job_refreshes_counts(tmp_path):
    """재사용 시 건수·등급분포는 지금 파일 내용으로 갱신된다(옛 값이 목록에 남지 않게)."""
    p = _slate(tmp_path, n=3)
    svc = GoldenBuildService()
    jid = svc.register_build(str(p), actor_user_id="r")
    _slate(tmp_path, p.name, n=7)          # 같은 경로에 내용을 갈아 끼운다
    again = svc.register_build(str(p), actor_user_id="r")
    assert again == jid
    assert svc.jobs.get(jid)["gold_count"] == 7


def test_prefers_the_job_that_has_review_progress(tmp_path):
    """쌍둥이가 이미 있으면 **진행분이 있는 잡**을 고른다 — 최신 등록이 빈 잡일 수 있다.

    223 실측이 그랬다. 최근순으로 고르면 작업분이 있는 잡을 두고 빈 잡으로 보내게 된다.
    """
    p = _slate(tmp_path)
    svc = GoldenBuildService()
    old = uuid.uuid4()
    new = uuid.uuid4()
    for jid, when in ((old, "2026-08-01T00:00:00+00:00"), (new, "2026-08-24T00:00:00+00:00")):
        svc.jobs.create(jid, payload={"kind": "golden_register", "actor": "r", "submitted_at": when})
        svc.jobs.update(jid, status="done", gold_path=str(p), gold_count=3)
    # 진행분은 **오래된** 쪽에 있다
    locked, _rejected = _ledger_paths(str(p), old)
    locked.write_text(json.dumps({"doc_id": "d0", "label": "S2"}) + "\n", encoding="utf-8")

    assert svc.find_registered_job(str(p)) == old


def test_api_marks_reuse(tmp_path, monkeypatch):
    """API 응답이 '새로 만들었다/이어서 한다'를 구분해 준다 — 화면이 그대로 말한다."""
    monkeypatch.setattr(settings, "api_key", "test-key")
    p = _slate(tmp_path)
    body = {"build_path": str(p), "actor": {"user_id": "reviewer1", "role": "admin"}}
    r1 = client.post(f"{API}/golden/jobs/register", json=body, headers=_AUTH)
    r2 = client.post(f"{API}/golden/jobs/register", json=body, headers=_AUTH)
    assert r1.status_code == 200 and r2.status_code == 200, (r1.text, r2.text)
    assert r1.json()["reused"] is False
    assert r2.json()["reused"] is True
    assert r2.json()["golden_job_id"] == r1.json()["golden_job_id"]


def test_job_list_has_one_row_per_file(tmp_path, monkeypatch):
    """목록에 같은 원본 파일이 두 행으로 뜨지 않는다 — 사용자가 본 그 증상."""
    monkeypatch.setattr(settings, "api_key", "test-key")
    p = _slate(tmp_path, "dup.jsonl")
    body = {"build_path": str(p), "actor": {"user_id": "reviewer1", "role": "admin"}}
    for _ in range(3):
        client.post(f"{API}/golden/jobs/register", json=body, headers=_AUTH)
    rows = client.get(f"{API}/golden/jobs?limit=100", headers=_AUTH).json()["jobs"]
    mine = [j for j in rows if (j.get("source_path") or "").endswith("dup.jsonl")]
    assert len(mine) == 1, mine
