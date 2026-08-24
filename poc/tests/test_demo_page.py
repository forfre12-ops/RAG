"""KOIPA AI 데모 콘솔 — 단위 검증.

검증 범위:
1. GET /demo/ — 200 + text/html + <title> 마커 + V2 디자인 토큰 활용 흔적
2. GET /demo/styles.css — 200 + text/css + 토큰 변수
3. GET /demo/app.js — 200 + js + ES module import 확인
4. GET /demo/samples.js — 200 + 자동 빌드 export 존재
5. GET /demo/legal — samples.js 안에 legal 데이터 존재 확인 (별 파일 분리 X)
6. incident.js 부재 — 화면이 안 그리는 예시 시나리오 데이터는 배포에서 뺐다
7. /healthz 에 데모 콘솔용 필드 노출 (deploy_profile·warmup_done 등 8개)
8. /demo/ 가 OpenAPI 스키마에 노출되지 않음 (StaticFiles 자동 제외)
9. 빌드된 샘플 13건이 의도 등급으로 분류되는지 (회귀 보장)
10. 경계 데모 샘플: 토글 해제 → **룰 엔진** 등급이 S1→S2→S3로 하향되는지
   (최종 등급은 분류기가 잡고 있어 안 내려간다 — 그쪽은 check_demo_docs.py --set paste)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

STATIC = Path(__file__).resolve().parents[1] / "src" / "koipa" / "api" / "static"
pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def client():
    from koipa.api.app import app
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
    assert "한국지식재산보호원" in r.text
    # 신규 와우 컴포넌트가 마크업에 존재
    assert 'id="toggle-row"' in r.text  # 와우 A 키워드 토글 (정적)
    assert 'id="legal-grid"' in r.text  # 와우 C 법령 (정적)
    # 와우 B(BERT vs LLM 시간 경쟁)는 제거됐다 — 분류 latency 와 /answer(RAG 생성)
    # latency 를 서로 다른 GPU 에서 잰 값끼리 붙여 "≈22× 빠름"으로 보이게 하는
    # 비교였고, LLM 막대는 호출 없이 25.8s 를 재생하는 애니메이션이었다. 되살아나지
    # 않도록 부재를 단언한다.
    assert 'class="race"' not in r.text
    assert 'btn-race' not in r.text
    # 와우 D(fnr-sequence 사고 시뮬)·E(capability-stats)·§6(profile-grid)은 b774e3d 이후
    # app.js 클라이언트 주입이라 정적 GET 응답엔 없다 — 정적 HTML 마커가 아니므로 단언하지
    # 않고, 동적 컴포넌트 렌더 스크립트 존재로 대체(정적 마커만 검증).
    assert './app.js' in r.text


def test_demo_styles_css(client):
    r = client.get("/demo/styles.css")
    assert r.status_code == 200
    assert "text/css" in r.headers.get("content-type", "")
    # V2 핵심 토큰 차용 확인
    assert "--bg-surface" in r.text
    assert "--border-strong" in r.text
    assert "--font-mono" in r.text
    assert "Malgun Gothic" in r.text
    # 데모 신규 컴포넌트
    assert ".kw-toggle" in r.text
    assert ".legal-card" in r.text
    assert ".race" not in r.text  # 와우 B 제거 — 스타일까지 남기지 않는다
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
    # 12 표준 샘플(등급별 3개) + 경계 데모 샘플 1개 = 13
    assert len(data["samples"]) == 13
    assert "legal" in data
    assert set(data["legal"].keys()) >= {"TS", "S1", "S2", "S3"}

    BORDERLINE_ID = "경계-영업-주간공유"
    standard = [s for s in data["samples"] if s["id"] != BORDERLINE_ID]
    borderline = [s for s in data["samples"] if s["id"] == BORDERLINE_ID]
    assert len(standard) == 12
    assert len(borderline) == 1, "경계 데모 샘플이 정확히 1건 있어야 함"

    # 표준 12건: 등급별 3개씩 · 토글 키워드 5개
    grades = [s["grade"] for s in standard]
    assert grades.count("TS") == 3
    assert grades.count("S1") == 3
    assert grades.count("S2") == 3
    assert grades.count("S3") == 3
    for s in standard:
        assert len(s["toggle_keywords"]) == 5

    # 경계 샘플: ALL-ON 등급 S1 · 토글 4개(내용 기반 시드만)
    b = borderline[0]
    assert b["grade"] == "S1"
    assert len(b["toggle_keywords"]) == 4


def test_demo_incident_js_removed(client):
    """사고 시뮬(와우 D) 데이터는 렌더러가 사라진 뒤로 아무 화면도 안 그렸다.

    손해액은 예시 시나리오(미실측)라 화면에 남을 근거가 없다. 되살아나지 않도록
    부재를 단언한다 — app.js 의 import 도 함께 사라졌는지 본다.
    """
    assert client.get("/demo/incident.js").status_code == 404
    assert "incident.js" not in client.get("/demo/app.js").text


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
# 10. 경계 데모 샘플 — 토글 해제 시 등급이 실제로 하향되는지 (와우 회귀)
# --------------------------------------------------------------------

def _parse_neutral_replacements(app_js: str) -> dict:
    """app.js 의 NEUTRAL_REPLACEMENTS 객체 리터럴에서 "키":"값" 쌍을 추출."""
    block = re.search(
        r"NEUTRAL_REPLACEMENTS\s*=\s*\{(.*?)\};", app_js, re.S
    )
    assert block, "app.js 에서 NEUTRAL_REPLACEMENTS 를 찾을 수 없음"
    pairs = re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', block.group(1))
    return dict(pairs)


def _classifier_loaded(svc) -> bool:
    """서빙 분류기가 실제로 실려 있는지. 안 실려 있으면 룰 엔진 단독 경로다."""
    return getattr(getattr(svc, "inference", None), "_model", None) is not None


_NO_MODEL_SKIP = (
    "분류기 미로드 — 룰 엔진 단독 경로다. 이 시험이 약속하는 것(배포본이 내는 등급)은 "
    "여기서 확인할 수 없다. TESTING=1 이면 settings.classifier_model_dir 로 직행하는데"
    "(classify_service.py:125) 로컬은 그 값이 비어 있어 model_grade 가 None 이다. "
    "배포본 확인은 scripts/check_demo_docs.py --api <서버> 로 한다."
)


@pytest.mark.slow
def test_borderline_sample_toggle_lowers_rule_grade():
    """경계 샘플에서 토글 해제(→일반어 치환) 시 **룰 엔진** 등급이 S1→S2→S3 하향되는지.

    지키는 것은 하나다 — **치환어가 시드로 회귀하지 않는다**(치환어=비-시드 계약).
    그것이 깨지면 단어를 껐는데 룰 점수가 그대로여서 화면의 ① 카드가 안 움직인다.

    ⛔ **최종 등급은 단언하지 않는다.** 종전 이름은 ..._actually_changes_grade 였고
    최종 등급이 S1→S2→S3 로 내려가는 것을 단언했다. 배포본에서는 그렇게 되지 않는다
    (실측 2026-08-24 · 223 build 5c572ad31c11):

        단계        최종   룰   모델   모델의 S2 확률
        전부 켬      S2    S1   S2    0.970
        1개 해제     S2    S2   S2    0.970
        전부 해제    S2    S3   S2    0.965

    분류기는 929자 중 20자가 바뀐 것으로는 움직이지 않고(치환어도 뜻이 거의 같다),
    결합이 더 심각한 쪽을 택하므로 최종은 S2 에 머문다. 이 시험이 초록이었던 이유는
    로컬에 분류기가 안 실려 룰 등급이 곧 최종 등급이었기 때문이다 — 즉 **모델 경로를
    한 번도 안 본 채 "와우가 작동한다"고 보증하고 있었다.**

    화면 문구는 사실에 맞게 고쳤다(index.html — 룰은 내려가고 분류기는 유지된다).
    최종 등급 쪽 회귀는 배포본에 대고 본다: scripts/check_demo_docs.py --set paste
    """
    samples_path = STATIC / "samples.js"
    app_js_path = STATIC / "app.js"
    raw = samples_path.read_text(encoding="utf-8")
    m = re.search(r"DEMO_DATA = (\{.*?\});\s*$", raw, re.S)
    assert m
    data = json.loads(m.group(1))
    b = next((s for s in data["samples"] if s["id"] == "경계-영업-주간공유"), None)
    assert b is not None, "경계 데모 샘플이 없음"

    repl = _parse_neutral_replacements(app_js_path.read_text(encoding="utf-8"))
    body = b["body"]
    toggles = b["toggle_keywords"]


    def rule_grade_with_off(off_keywords):
        text = body
        for kw in off_keywords:
            text = text.replace(kw, repl.get(kw, "관련 자료"))
        from koipa.schemas.classify import ClassifyRequest
        from koipa.services.classify_service import ClassifyService
        res = ClassifyService.get_instance().classify(
            ClassifyRequest(
                doc_id=b["id"], content=text, title=b["title"],
                use_rag=False, return_evidence=False,
            )
        )
        rg = getattr(res, "rule_grade", None)
        return rg.value if hasattr(rg, "value") else (str(rg) if rg else None)

    # ALL ON → 룰 S1 (고객DB·원가구조 = 영업비밀 시드)
    assert rule_grade_with_off([]) == "S1"
    # 첫 토글(고객 데이터베이스) 하나만 해제 → S1 시드 소멸 → 룰 S2
    assert rule_grade_with_off([toggles[0]]) == "S2"
    # 전부 해제 → 시드 전무 → 룰 S3. 여기가 깨지면 치환어가 시드로 회귀한 것이다.
    assert rule_grade_with_off(toggles) == "S3"


# --------------------------------------------------------------------
# 9. 빌드된 샘플 13건이 의도 등급으로 분류되는지 (회귀 보장)
# --------------------------------------------------------------------

@pytest.mark.slow
def test_built_samples_classify_to_intended_grade():
    """samples.js 의 13 샘플을 ClassifyService 로 분류해 의도 등급 매칭 확인.

    빌드 스크립트(build_demo_samples.py)가 같은 검증을 하지만, CI 에서도
    회귀 확인. ClassifyService 호출이 무거우므로 1 회만 실행.
    """
    samples_path = STATIC / "samples.js"
    assert samples_path.exists(), "samples.js 가 생성되어 있어야 함"
    raw = samples_path.read_text(encoding="utf-8")
    m = re.search(r"DEMO_DATA = (\{.*?\});\s*$", raw, re.S)
    assert m
    data = json.loads(m.group(1))

    from koipa.schemas.classify import ClassifyRequest
    from koipa.services.classify_service import ClassifyService

    svc = ClassifyService.get_instance()
    # [2026-08-24] 위와 같은 이유로 건너뛴다. 이 시험은 3.6초에 통과하고 있었는데,
    # 그 시간에 BERT 가 실릴 리 없다 — 룰 엔진만 돌고 있었다는 뜻이다. 그 사이 배포본은
    # 13건 중 8건을 라벨과 다른 등급으로 내고 있었다(7건 상향 · 1건 하향).
    if not _classifier_loaded(svc):
        pytest.skip(_NO_MODEL_SKIP)
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
        # FNR-safe 모델: 상향 분류(S1→TS, S2→S1)는 허용.
        # 기대 등급보다 낮은 등급으로 내려가는 경우만 실패 (FNR 위반).
        _GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
        expected_order = _GRADE_ORDER.get(s["grade"], 99)
        actual_order = _GRADE_ORDER.get(actual, 99)
        if actual_order > expected_order:  # 실제 등급이 기대보다 낮으면 FNR 위반
            misses.append(f"{s['id']}: 기대 {s['grade']} 실제 {actual} (하향 분류 — FNR 위반)")
    assert not misses, "샘플 등급 정합 실패: " + "; ".join(misses)
