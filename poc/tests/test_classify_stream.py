"""A3: /classify/stream — SSE stage callback이 실제 단계와 동기화되는지 검증.

기존 모듈(P2-D5)은 asyncio.sleep(0)로 가짜 stage 6개를 무조건 yield했음.
이제 ClassifyService.classify(on_stage=...) 콜백이 실제 단계 진입 시점에만 호출.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings
from lloydk.schemas.classify import ClassifyRequest
from lloydk.services.classify_service import ClassifyService


@pytest.fixture
def client():
    # rate-limit 비활성 (다른 테스트와 동일 패턴)
    import os
    os.environ["RATE_LIMIT_DISABLED"] = "1"
    return TestClient(app)


def _make_req() -> dict:
    return ClassifyRequest(
        doc_id="not-a-uuid",
        content="이 문서는 영업비밀 등급 분류 대상입니다.",
        use_rag=False,
        return_evidence=False,
    ).model_dump()


def test_classify_unit_invokes_on_stage_callback():
    """단위 — classify(on_stage=cb) 호출 시 단계명이 순서대로 전달되는지."""
    svc = ClassifyService.get_instance()
    seen: list[str] = []
    req = ClassifyRequest(
        doc_id="not-a-uuid",
        content="테스트 문서",
        use_rag=False,
        return_evidence=False,
    )
    svc.classify(req, on_stage=seen.append)

    # 최소: extract → finalize 사이에 정상 단계가 들어 있어야 함
    assert "extract" in seen
    assert "finalize" in seen
    # 순서 — extract가 finalize보다 먼저
    assert seen.index("extract") < seen.index("finalize")
    # 가짜 stage(예: chunk)는 더 이상 발생하지 않음 — 본 콜백은 실제 진입한 단계만 받음
    assert "chunk" not in seen


def test_classify_stream_emits_real_stages(client):
    """엔드투엔드 — SSE 응답에 progress 이벤트와 result 이벤트가 둘 다 포함."""
    headers = {"X-API-Key": settings.api_key}
    with client.stream("POST", "/api/v1/classify/stream", headers=headers, json=_make_req()) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())

    assert "event: progress" in body
    assert "event: result" in body
    assert "event: done" in body
    # 실제 단계명 — 한 개라도 포함
    assert any(s in body for s in ("extract", "embed", "llm", "finalize"))


def test_classify_stream_emits_error_on_exception(client):
    headers = {"X-API-Key": settings.api_key}

    def boom(self, req, *, on_stage=None):  # noqa: ARG001
        if on_stage:
            on_stage("extract")
        raise RuntimeError("synthetic failure")

    with patch.object(ClassifyService, "classify", boom):
        with client.stream("POST", "/api/v1/classify/stream", headers=headers, json=_make_req()) as r:
            assert r.status_code == 200
            body = "".join(r.iter_text())

    assert "event: error" in body
    assert "synthetic failure" in body
    assert "event: done" in body
