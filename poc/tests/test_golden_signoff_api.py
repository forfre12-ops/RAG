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


def test_publish_reaches_deploy_gate_consumer(tmp_path, monkeypatch):
    # 서명+publish 가 배포 게이트 소비 경로(locked_eval_jsonl)에 도달해 게이트가 실제로 읽는지 — 루프 실증.
    live = tmp_path / "locked_live.jsonl"
    monkeypatch.setattr(settings, "locked_eval_jsonl", str(live))
    import uuid as _uuid

    from lloydk.modules.m6_evaluation.locked_readiness import locked_eval_readiness

    job_id = _make_job(tmp_path / "run", [{"doc_id": "a", "text": "ts문서"}])
    result = GoldenBuildService().apply_signoff(
        _uuid.UUID(job_id),
        [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="r1",
        publish=True,
    )
    assert result["published"] is True and result["publish_note"] is None
    # 배포 게이트 consumer(locked_eval_readiness)가 방금 서명을 읽는다(루프 닫힘).
    rd = locked_eval_readiness(path=str(live))
    assert rd["per_grade"]["TS"] == 1


def test_publish_unset_path_notes_reason(tmp_path, monkeypatch):
    # publish=True 인데 LOCKED_EVAL_JSONL 미설정 → 조용한 no-op 대신 명시 신호(리뷰 ⑥).
    monkeypatch.setattr(settings, "locked_eval_jsonl", "")
    import uuid as _uuid

    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    result = GoldenBuildService().apply_signoff(
        _uuid.UUID(job_id),
        [GoldenSignoffDecision(doc_id="a", decision="approve")],
        reviewer_id="r1",
        publish=True,
    )
    assert result["published"] is False
    assert result["publish_note"] and "LOCKED_EVAL_JSONL" in result["publish_note"]


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


def test_signoff_html_escapes_script_breakout(tmp_path):
    # 문서 본문의 리터럴 </script> 가 data 스크립트 블록을 조기 종료시키지 못해야 함(저장형 XSS 차단).
    payload = "ts문서 </script><script>window.__pwned=1</script>"
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": payload}])
    r = client.get(f"{API}/golden/jobs/{job_id}/signoff.html", headers=_h(role="reviewer"))
    assert r.status_code == 200, r.text
    # 임베드된 JSON 안에 리터럴 </script> 가 있으면 안 됨(유니코드 이스케이프로 치환).
    assert "</script><script>window.__pwned" not in r.text
    assert "\\u003c/script\\u003e" in r.text  # breakout 페이로드가 이스케이프됨


def test_change_without_grade_rejected_422(tmp_path):
    # decision=change 인데 grade 미지정 → 스키마 validator 가 422 로 거부(조용한 no_signoff 방지).
    job_id = _make_job(tmp_path, [{"doc_id": "a", "text": "ts문서"}])
    r = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        headers=_h(role="reviewer"),
        json={
            "decisions": [{"doc_id": "a", "decision": "change"}],  # grade 없음
            "actor": {"user_id": "reviewer_kim", "role": "reviewer"},
        },
    )
    assert r.status_code == 422, r.text


def test_run_scoped_audit_accumulates_across_sessions(tmp_path):
    # 같은 잡을 여러 세션에 나눠 서명해도 run-스코프 감사 파일이 덮어써지지 않고 누적돼야 함.
    import uuid as _uuid

    job_id = _make_job(tmp_path, [
        {"doc_id": "a", "text": "ts문서"}, {"doc_id": "b", "text": "s1문서"},
    ])
    svc = GoldenBuildService()
    svc.apply_signoff(
        _uuid.UUID(job_id), [GoldenSignoffDecision(doc_id="a", decision="approve")], reviewer_id="r1",
    )
    svc.apply_signoff(
        _uuid.UUID(job_id), [GoldenSignoffDecision(doc_id="b", decision="approve")], reviewer_id="r1",
    )
    rows = [
        json.loads(ln)
        for ln in (tmp_path / f"locked_{job_id}.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert {r["doc_id"] for r in rows} == {"a", "b"}  # 세션2가 세션1을 덮어쓰지 않음


# ────────────────────────── build 등록 → signoff 연결 ──────────────────────────
def _write_build(tmp_path, rows) -> str:
    # pytest tmp_path 는 시스템 temp 하위 → _safe_path 허용(datasets/ · temp).
    p = tmp_path / "build_slate.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
    )
    return str(p)


def test_register_build_then_signoff_e2e(tmp_path):
    # 기존 build 파일 register(재라벨링 없음) → 그 잡 signoff.html 표시 → POST 승격까지 E2E.
    build = _write_build(tmp_path, [
        {"doc_id": "a", "label": "TS", "text": "ts 슬레이트 본문",
         "rule_grade": "TS", "llm_grade": "TS", "llm_confidence": 1.0, "domain": "tech"},
    ])
    reg = client.post(
        f"{API}/golden/jobs/register",
        headers=_h(role="admin"),
        json={"build_path": build, "actor": {"user_id": "admin1", "role": "admin"}},
    )
    assert reg.status_code == 200, reg.text
    job_id = reg.json()["golden_job_id"]
    # 등록된 잡의 signoff.html 이 슬레이트 후보(라벨 보존)를 보여줌
    h = client.get(f"{API}/golden/jobs/{job_id}/signoff.html", headers=_h(role="reviewer"))
    assert h.status_code == 200 and "ts 슬레이트 본문" in h.text
    # 화면 서명 → locked 승격
    s = client.post(
        f"{API}/golden/jobs/{job_id}/signoff",
        headers=_h(role="reviewer"),
        json={
            "decisions": [{"doc_id": "a", "decision": "approve"}],
            "actor": {"user_id": "reviewer_kim", "role": "reviewer"},
        },
    )
    assert s.status_code == 200 and s.json()["locked"] == 1


def test_register_build_rejects_outside_sandbox():
    # datasets/ · temp 밖 경로는 _safe_path 가 거부 → 404.
    r = client.post(
        f"{API}/golden/jobs/register",
        headers=_h(role="admin"),
        json={"build_path": "/etc/passwd", "actor": {"user_id": "admin1", "role": "admin"}},
    )
    assert r.status_code == 404, r.text


def test_register_build_requires_admin(tmp_path):
    build = _write_build(tmp_path, [{"doc_id": "a", "label": "TS", "text": "x"}])
    r = client.post(
        f"{API}/golden/jobs/register",
        headers=_h(role="reviewer"),  # reviewer 는 등록 불가(admin/kl_backend/system)
        json={"build_path": build, "actor": {"user_id": "rev", "role": "reviewer"}},
    )
    assert r.status_code == 403, r.text
