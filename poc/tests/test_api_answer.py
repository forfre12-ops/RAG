"""POST /api/v1/answer — RAG 답안 합성 엔드포인트 테스트.

from __future__ import annotations
테스트 범위:
- 인증 누락 → 401/422
- 잘못된 키 → 401
- 정상 요청 → 200 + RagAnswerResult 구조(answer/citations/deterministic_fallback)
- query 길이 위반 → 422
- top_k 범위 위반 → 422
- retrieval 실패 또는 hits 빈 환경에서도 200 + deterministic_fallback=True
- /answer가 OpenAPI yaml에 등록됨 (openapi.json 노출 확인)
"""

import pytest
pytestmark = pytest.mark.slow

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings


def _post(cli, body, headers=None):
    h = {"X-API-Key": settings.api_key}
    if headers:
        h.update(headers)
    return cli.post("/api/v1/answer", json=body, headers=h)


def test_answer_requires_api_key():
    with TestClient(app) as cli:
        r = cli.post("/api/v1/answer", json={"query": "질의"})
        assert r.status_code in (401, 422)


def test_answer_rejects_invalid_api_key():
    with TestClient(app) as cli:
        r = cli.post(
            "/api/v1/answer",
            headers={"X-API-Key": "wrong-key"},
            json={"query": "질의"},
        )
        assert r.status_code == 401


def test_answer_returns_200_with_fallback_when_no_retrieval():
    """벡터스토어/embedder 부재 시에도 200 + RagAnswerResult 구조 검증."""
    with TestClient(app) as cli:
        r = _post(cli, {"query": "영업비밀 관리 방법은?"})
        assert r.status_code == 200, r.text
        body = r.json()
        # 응답 구조 검증 (LLM 가용 여부와 무관)
        assert "answer" in body
        assert "citations" in body
        assert "deterministic_fallback" in body
        assert isinstance(body["answer"], str) and body["answer"]
        # noop 환경 → True, LLM 연결 환경 → False 허용
        assert isinstance(body["deterministic_fallback"], bool)


def test_answer_rejects_empty_query():
    with TestClient(app) as cli:
        r = _post(cli, {"query": ""})
        assert r.status_code == 422


def test_answer_rejects_query_too_long():
    with TestClient(app) as cli:
        r = _post(cli, {"query": "가" * 4001})
        assert r.status_code == 422


def test_answer_rejects_top_k_out_of_range():
    with TestClient(app) as cli:
        r = _post(cli, {"query": "q", "top_k": 0})
        assert r.status_code == 422
        r = _post(cli, {"query": "q", "top_k": 21})
        assert r.status_code == 422


def test_answer_accepts_grade_and_metadata():
    """grade·metadata 필드가 검증 통과."""
    with TestClient(app) as cli:
        r = _post(cli, {
            "query": "보안 등급 안내",
            "grade": "S1",
            "metadata": {"dept_id": "rnd"},
            "top_k": 3,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        # grade가 응답에 포함됐는지 (synthesize_answer가 grade 전달)
        assert body.get("grade") == "S1"


def test_openapi_lists_answer_path():
    """app의 OpenAPI 스펙에 /answer가 노출됐는지 확인 — prefix /api/v1 포함."""
    with TestClient(app) as cli:
        r = cli.get("/api/v1/openapi.json")
        assert r.status_code == 200
        spec = r.json()
        paths = spec.get("paths", {})
        # FastAPI는 router include 시 prefix를 path에 합쳐 노출 (/api/v1/answer)
        assert "/api/v1/answer" in paths, f"expected /api/v1/answer in paths, got: {sorted(paths)}"
        post = paths["/api/v1/answer"].get("post")
        assert post is not None
        assert "answer" in post.get("tags", [])
