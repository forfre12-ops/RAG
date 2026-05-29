"""KOIPA AI 데모 콘솔 — 단위 검증.

검증 범위:
1. GET /demo/ — 200 + text/html + <title> 마커 + V2 디자인 토큰 활용 흔적
2. GET /demo/styles.css — 200 + text/css + 토큰 변수
3. GET /demo/app.js — 200 + js + ES module import 확인
4. GET /demo/samples.js — 200 + 자동 빌드 export 존재
5. GET /demo/legal — samples.js 안에 legal 데이터 존재 확인 (별 파일 분리 X)
6. GET /demo/incident.js — 200 + INCIDENT export
7. /healthz 에 데모 콘솔용 필드 노출 (deploy_profile·warmup_done 등 8개)
8. /demo/ 가 OpenAPI 스키마에 노출되지 않음 (StaticFiles 자동 제외)
9. 빌드된 샘플 12건이 의도 등급으로 분류되는지 (회귀 보장)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lloydk.api.app import app

STATIC = Path(__file__).resolve().parents[1] / "src" / "lloydk" / "api" / "static"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------
# 1~6. /demo 정적 파일
# --------------------------------------------------------------------

def test_demo_index_returns_html(client):
    r = client.get("/demo/")
    assert r.status_code == 200, r.text[:200]
    assert "text/html" in r.headers.get("content-type", "")
    # V2 디자인 토큰을 차용한 흔적
    assert "KOIPA AI" in r.text
    assert "로이드케이" in r.text
    # 신규 와우 컴포넌트가 마크업에 존재
    assert 'id="toggle-row"' in r.text  # 와우 A 키워드 토글
    assert 'class="race"' in r.text  # 와우 B 시간 경쟁
    assert 'id="legal-grid"' in r.text  # 와우 C 법령
    assert 'id="fnr-sequence"' in r.text  # 와우 D 사고 시뮬
    assert 'id="capability-stats"' in r.text  # 와우 E stats
    assert 'id="profile-grid"' in r.text  # §6 배포


def test_demo_styles_css(client):
    r = client.get("/demo/styles.css")
    assert r.status_code == 200
    assert "text/css" in r.headers.get("content-type", "")
    # V2 핵심 토큰 차용 확인
    assert "--bg-surface" in r.text
    assert "--border-strong" in r.text
    assert "--font-mono" in r.text
    assert "Geist" in r.text
    # 데모 신규 컴포넌트
    assert ".kw-toggle" in r.text
    assert ".race-fill" in r.text
    assert ".legal-card" in r.text
    assert "@media print" in r.text


def test_demo_app_js(client):
    r = client.get("/demo/app.js")
    assert r.status_code == 200
    # JS MIME — Windows OS 가 application/javascript 또는 text/javascript 둘 다 반환 가능
    ct = r.headers.get("content-type", "")
    assert "javascript" in ct or "text/plain" in ct
    # ES module import 확인
    assert "import" in r.text
    assert "DEMO_DATA" in r.text


def test_demo_samples_js(client):
    r = client.get("/demo/samples.js")
    assert r.status_code == 200
    assert "DEMO_DATA" in r.text
    assert "AUTO-GENERATED" in r.text
    # JSON 본체 추출 + 파싱
    m = re.search(r"DEMO_DATA = (\{.*?\});\s*$", r.text, re.S)
    assert m, "DEMO_DATA JSON 본체를 찾을 수 없음"
    data = json.loads(m.group(1))
    assert "samples" in data
    assert len(data["samples"]) == 12
    assert "legal" in data
    assert set(data["legal"].keys()) >= {"TS", "S1", "S2", "S3"}
    # 등급별 3개씩
    grades = [s["grade"] for s in data["samples"]]
    assert grades.count("TS") == 3
    assert grades.count("S1") == 3
    assert grades.count("S2") == 3
    assert grades.count("S3") == 3
    # 각 샘플에 토글 키워드 5개
    for s in data["samples"]:
        assert len(s["toggle_keywords"]) == 5


def test_demo_incident_js(client):
    r = client.get("/demo/incident.js")
    assert r.status_code == 200
    assert "INCIDENT" in r.text
    assert "fnr_sequence" in r.text
    assert "prevention_sequence" in r.text
    # JS 객체 리터럴: step: 6 + 5 = 11 (key 뒤에 콜론)
    assert r.text.count("step:") >= 11, f"step 카운트 부족: {r.text.count('step:')}"


def test_demo_sse_and_highlight_js(client):
    r = client.get("/demo/sse.js")
    assert r.status_code == 200
    assert "postSSE" in r.text
    r2 = client.get("/demo/highlight.js")
    assert r2.status_code == 200
    assert "renderBodyWithHighlights" in r2.text
    assert "focusLegalCard" in r2.text


# --------------------------------------------------------------------
# 7. /healthz 확장 필드
# --------------------------------------------------------------------

def test_healthz_exposes_demo_console_fields(client):
    r = client.get("/api/v1/healthz")
    assert r.status_code == 200
    body = r.json()
    # 데모 콘솔이 의존하는 8 필드
    for k in (
        "status",
        "model_version",
        "uptime_sec",
        "deploy_profile",
        "embedding_provider",
        "llm_provider",
        "vector_backend",
        "warmup_done",
    ):
        assert k in body, f"healthz 응답에 {k} 누락"
    assert body["status"] == "ok"
    assert isinstance(body["warmup_done"], bool)


# --------------------------------------------------------------------
# 8. /demo 가 OpenAPI 스키마에 노출되지 않음
# --------------------------------------------------------------------

def test_demo_not_in_openapi(client):
    r = client.get("/api/v1/openapi.json")
    assert r.status_code == 200
    spec = r.json()
    paths = spec.get("paths", {})
    # /demo 로 시작하는 경로 없음
    for p in paths:
        assert not p.startswith("/demo"), f"OpenAPI 에 /demo 경로 노출됨: {p}"


# --------------------------------------------------------------------
# 9. 빌드된 샘플 12건이 의도 등급으로 분류되는지 (회귀 보장)
# --------------------------------------------------------------------

def test_built_samples_classify_to_intended_grade():
    """samples.js 의 12 샘플을 ClassifyService 로 분류해 의도 등급 매칭 확인.

    빌드 스크립트(build_demo_samples.py)가 같은 검증을 하지만, CI 에서도
    회귀 확인. ClassifyService 호출이 무거우므로 1 회만 실행.
    """
    samples_path = STATIC / "samples.js"
    assert samples_path.exists(), "samples.js 가 생성되어 있어야 함"
    raw = samples_path.read_text(encoding="utf-8")
    m = re.search(r"DEMO_DATA = (\{.*?\});\s*$", raw, re.S)
    assert m
    data = json.loads(m.group(1))

    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.services.classify_service import ClassifyService

    svc = ClassifyService.get_instance()
    misses = []
    for s in data["samples"]:
        req = ClassifyRequest(
            doc_id=s["id"],
            content=s["body"],
            title=s["title"],
            use_rag=False,
            return_evidence=True,
        )
        res = svc.classify(req)
        actual = res.label.value if hasattr(res.label, "value") else str(res.label)
        if actual != s["grade"]:
            misses.append(f"{s['id']}: 기대 {s['grade']} 실제 {actual}")
    assert not misses, "샘플 등급 정합 실패: " + "; ".join(misses)
