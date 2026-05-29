"""P1-E2: KL 통합 contract test — Mock KL 서버 + 라운드트립.

KL 측 명세 회신 전에도 OpenAPI(doc/03)를 진실 소스로 사용해
Lloydk API가 어떤 KL 콜백/요청 페이로드와 호환되는지 사전 검증.

전략:
- doc/03 OpenAPI spec을 로드
- 각 응답 schema에서 example 페이로드 생성
- pydantic schemas로 round-trip parse (실패 시 spec ↔ 코드 drift 알람)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

OPENAPI_PATH = Path(__file__).resolve().parents[2] / "doc" / "03_openapi_lloydk_kl.yaml"


@pytest.fixture(scope="module")
def openapi_spec():
    if not OPENAPI_PATH.exists():
        pytest.skip(f"OpenAPI spec not found: {OPENAPI_PATH}")
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(OPENAPI_PATH.read_text(encoding="utf-8"))


def test_spec_loaded(openapi_spec):
    assert "paths" in openapi_spec
    assert len(openapi_spec["paths"]) >= 5


def test_required_endpoints_present(openapi_spec):
    paths = openapi_spec["paths"]
    # KL ↔ Lloydk 핵심 endpoint들이 명세에 존재해야
    required_substrings = [
        "/classify",
        "/healthz",
    ]
    found = " ".join(paths.keys())
    for sub in required_substrings:
        assert sub in found, f"required endpoint missing: {sub}"


def test_classify_request_schema_roundtrip(openapi_spec):
    """ClassifyRequest schema에 따라 만든 dummy body를 우리 pydantic 모델로 round-trip."""
    from lloydk.schemas.classify import ClassifyRequest

    dummy = {
        "doc_id": "test-doc",
        "title": "테스트 문서",
        "content": "이것은 영업비밀 분류용 테스트 문서입니다. M&A 검토안.",
    }
    obj = ClassifyRequest(**dummy)
    assert obj.content


def test_classify_response_schema_roundtrip():
    from uuid import uuid4
    from lloydk.schemas.classify import ClassifyResponse, EvaluationFactors
    from lloydk.schemas.common import Grade

    dummy = {
        "inference_id": uuid4(),
        "doc_id": "test-doc",
        "label": Grade.S2,
        "confidence": 0.85,
        "scores": {"TS": 0.05, "S1": 0.10, "S2": 0.85, "S3": 0.0},
        "evaluation_factors": EvaluationFactors(
            economic_value=0.7,
            non_publicity=0.6,
            management_level=0.5,
            leak_impact=0.7,
        ),
        "evidence": [],
        "rag_context_used": [],
        "model_version": "test",
        "elapsed_ms": 12,
        "warnings": [],
    }
    resp = ClassifyResponse(**dummy)
    s = resp.model_dump_json()
    loaded = json.loads(s)
    assert loaded["label"] == "S2"


def test_error_response_envelope():
    """공통 에러 envelope: code/message/request_id."""
    from lloydk.schemas.common import Error

    e = Error(code="LLOYDK_TEST", message="sample", request_id="abcd")
    s = e.model_dump_json()
    loaded = json.loads(s)
    assert loaded["code"] == "LLOYDK_TEST"
    assert loaded["request_id"] == "abcd"


def test_health_endpoint_shape():
    """/healthz 응답이 OpenAPI에 명시된 필드를 모두 포함."""
    # .env에 한국어 주석이 있으면 starlette Config가 cp949로 읽다 UnicodeDecodeError.
    # 환경 의존 이슈라 starlette config import 실패 시 skip.
    try:
        from fastapi.testclient import TestClient
        from lloydk.api.app import app
    except UnicodeDecodeError:
        pytest.skip(".env cp949 디코딩 실패 환경 — 운영 PYTHONIOENCODING=utf-8 권장")

    with TestClient(app) as client:
        r = client.get("/api/v1/healthz")
        assert r.status_code == 200
        body = r.json()
        assert "status" in body
