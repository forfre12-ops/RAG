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

import pytest
from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import settings
from koipa.services.golden_build_service import GoldenBuildService, _ledger_paths

# [2026-09-05] 잡 저장소가 Redis 라 앞선 실행의 잡이 남는다. 실행마다 다른 표식을 붙여
# 이 실행이 만든 행만 세게 한다. 꼬리(endswith)로 거르므로 표식은 **뒤에** 온다.
RUN = uuid.uuid4().hex[:8]


def _name(stem: str) -> str:
    """이 실행에서만 쓰는 파일 이름. 네 시험의 꼬리가 서로 겹치지 않게 한다."""
    return f"{stem}_{RUN}.jsonl"


client = TestClient(app)
API = "/api/v1"


@pytest.fixture(autouse=True)
def _own_job_store(monkeypatch):
    """이 시험들은 잡 목록 **전체**를 본다 — 남의 잡이 섞이면 답이 달라진다.

    [2026-09-05] 실측: 잡 저장소가 Redis 라 앞선 실행의 golden_register 잡 84개가
    남아 있었고, 전체 시험 두 번째 실행에서 이 파일 4건이 깨졌다. 기능이 아니라
    시험 격리의 문제였다. 시험마다 process-local 저장소를 새로 끼운다.
    """
    from koipa.services import job_store as _js  # noqa: PLC0415

    monkeypatch.setattr(_js, "_default", _js.InMemoryJobStore(), raising=False)
    yield
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
    name = _name("one")
    p = _slate(tmp_path, name)
    body = {"build_path": str(p), "actor": {"user_id": "reviewer1", "role": "admin"}}
    for _ in range(3):
        client.post(f"{API}/golden/jobs/register", json=body, headers=_AUTH)
    rows = client.get(f"{API}/golden/jobs?limit=100", headers=_AUTH).json()["jobs"]
    mine = [j for j in rows if (j.get("source_path") or "").endswith(name)]
    assert len(mine) == 1, mine


# ── 이미 쌓여 있던 중복 행 — 목록이 스스로 접는다 ────────────────────────────
# 등록 가드는 **새로** 생기는 것만 막는다. 가드 이전에 만들어진 행은 저장소에 그대로
# 남아 화면에서 계속 쌍둥이로 보인다(223 이 그 상태였다). 목록을 저장소 정리에
# 의존시키지 않는다 — 화면이 접는다.

def _register_twins(svc, p, times, *, decided_on=()):
    """가드를 우회해 옛날처럼 쌍둥이 잡을 직접 만든다(가드 이전 저장소 재현)."""
    made = []
    for i in range(times):
        jid = uuid.uuid4()
        svc.jobs.create(jid, payload={
            "kind": "golden_register", "actor": "r",
            "submitted_at": f"2026-08-{10 + i:02d}T00:00:00+00:00",
        })
        svc.jobs.update(jid, status="done", gold_path=str(p), gold_count=3)
        made.append(jid)
    for idx, n in decided_on:
        locked, _r = _ledger_paths(str(p), made[idx])
        locked.write_text(
            "\n".join(json.dumps({"doc_id": f"d{k}", "label": "S2"}) for k in range(n)),
            encoding="utf-8",
        )
    return made


def _rows_for(name, monkeypatch):
    monkeypatch.setattr(settings, "api_key", "test-key")
    rows = client.get(f"{API}/golden/jobs?limit=100", headers=_AUTH).json()
    return rows, [j for j in rows["jobs"] if (j.get("source_path") or "").endswith(name)]


def test_existing_duplicate_rows_are_folded(tmp_path, monkeypatch):
    """가드 이전에 쌓인 쌍둥이 3행이 한 행으로 접히고, 접었다는 사실이 응답에 남는다."""
    name = _name("old")
    p = _slate(tmp_path, name)
    _register_twins(GoldenBuildService(), p, 3)
    body, mine = _rows_for(name, monkeypatch)
    assert len(mine) == 1, mine
    assert mine[0]["folded_duplicates"] == 2      # 감추되 감췄다고 말한다
    assert body["folded_duplicates"] >= 2


def test_folded_row_is_the_one_holding_the_work(tmp_path, monkeypatch):
    """대표 행은 **진행분이 있는 쪽**이다 — 최신 행이 빈 잡이던 223 실측 때문."""
    name = _name("work")
    p = _slate(tmp_path, name)
    made = _register_twins(GoldenBuildService(), p, 3, decided_on=[(0, 2)])
    _body, mine = _rows_for(name, monkeypatch)
    assert len(mine) == 1
    assert mine[0]["job_id"] == str(made[0]), "가장 오래됐지만 결정이 쌓인 행이 남아야 한다"
    assert mine[0]["decided_count"] == 2


def test_two_rows_with_signatures_are_both_kept(tmp_path, monkeypatch):
    """서명이 두 곳에 갈라져 있으면 접지 않는다 — 감추면 사람 서명이 화면에서 사라진다."""
    name = _name("split")
    p = _slate(tmp_path, name)
    _register_twins(GoldenBuildService(), p, 3, decided_on=[(0, 2), (1, 1)])
    _body, mine = _rows_for(name, monkeypatch)
    assert len(mine) == 2, mine
    assert sorted(j["decided_count"] for j in mine) == [1, 2]
