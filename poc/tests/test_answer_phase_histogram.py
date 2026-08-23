"""POST /answer 단계별 latency Histogram 측정 검증.

§7: retrieve/synthesize 두 phase의 duration을 분해 측정해 운영 SLO 정의 +
§1 batch encode 효과 정량 입증.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.slow

# 운영 차단·미들웨어 의존을 우회 — TestClient로 부팅하기 전 env 세팅
os.environ.setdefault("SLOWAPI_SKIP_DOTENV", "1")
os.environ.setdefault("AUDIT_DISABLED", "1")
os.environ.setdefault("RATE_LIMIT_DISABLED", "1")
os.environ.setdefault("POC_MODE", "dryrun")

_TEST_API_KEY = "test-key-answer-hist"


@pytest.fixture
def client() -> TestClient:
    # 다른 테스트가 settings.api_key를 reload·monkeypatch로 갱신했을 수 있으므로
    # 본 픽스처에서 명시적으로 동기화 (격리 안전망).
    from koipa.api.app import app
    from koipa.config import settings
    settings.api_key = _TEST_API_KEY
    return TestClient(app)


def _stub_synthesize(query, hits, grade=None):
    """LLM 부재 환경에서 deterministic_fallback 응답 모사."""
    from koipa.schemas.rag_answer import RagAnswerResult, RagCitation
    return RagAnswerResult(
        answer="stub answer",
        citations=[RagCitation(source_doc="d1", chunk_id="c1", score=0.9, rank=1)],
        grade=grade or "S3",
        deterministic_fallback=True,
        warnings=["stub"],
    )


def _phase_count(phase: str) -> float:
    """ANSWER_PHASE_DURATION의 phase 라벨별 누적 _count sample."""
    from koipa.api import prom_metrics
    for m in prom_metrics.ANSWER_PHASE_DURATION.collect():
        for s in m.samples:
            if s.name.endswith("_count") and s.labels.get("phase") == phase:
                return s.value
    return 0.0


def test_answer_records_retrieve_and_synthesize_phases(client: TestClient) -> None:
    """/answer 호출 후 ANSWER_PHASE_DURATION 두 라벨 모두 카운트 증가."""
    retrieve_before = _phase_count("retrieve")
    synth_before = _phase_count("synthesize")

    with patch("koipa.api.answer.synthesize_answer", side_effect=_stub_synthesize), \
         patch("koipa.api.answer._fetch_hits", return_value=[]):
        resp = client.post(
            "/api/v1/answer",
            headers={"X-API-Key": _TEST_API_KEY},
            json={"query": "테스트 쿼리", "top_k": 3, "grade": "S3"},
        )

    assert resp.status_code == 200, resp.text
    assert _phase_count("retrieve") >= retrieve_before + 1
    assert _phase_count("synthesize") >= synth_before + 1


def test_answer_phase_metric_registered_in_registry() -> None:
    """ANSWER_PHASE_DURATION이 prom registry에 정상 등록됐는지."""
    from koipa.api import prom_metrics
    from prometheus_client import generate_latest
    text = generate_latest(prom_metrics.registry).decode()
    assert "koipa_answer_phase_duration_seconds" in text


def test_answer_logs_phase_breakdown(client: TestClient, caplog) -> None:
    """로그 라인에 retrieve_ms / synth_ms 둘 다 노출."""
    import logging
    caplog.set_level(logging.INFO, logger="koipa.api.answer")

    with patch("koipa.api.answer.synthesize_answer", side_effect=_stub_synthesize), \
         patch("koipa.api.answer._fetch_hits", return_value=[]):
        resp = client.post(
            "/api/v1/answer",
            headers={"X-API-Key": _TEST_API_KEY},
            json={"query": "phase log", "top_k": 3, "grade": "S3"},
        )
    assert resp.status_code == 200
    msgs = [r.message for r in caplog.records if r.name == "koipa.api.answer"]
    assert any("retrieve_ms=" in m and "synth_ms=" in m for m in msgs), msgs
