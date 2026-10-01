"""POST /classify/explain — S/V/M이 FactorDetail(관측/미관측)로 바뀐 뒤에도 안 터지는가.

왜 이 시험이 있는가(2026-10-02, 제3자 검토서). `EvaluationFactors.secrecy/value/management`가
float에서 FactorDetail(state/value/evidence)로 바뀌었는데, `_factor_decomposition`이 한동안
그 값을 `float(...)`로 바로 변환하고 있었다 — S/V/M이 있는 실제 HTTP 응답은 그대로
`TypeError: float() argument must be a string or a real number, not 'FactorDetail'`로 500을
냈다(직접 재현해 확인). 단위 시험(test_explain_factor_claim.py)은 `_factor_decomposition`을
직접 불러 이미 이 틀을 잠그지만, 그 함수가 실제로 FastAPI 라우트·JSON 직렬화 경로를 타고도
살아남는지는 end-to-end로 한 번은 확인해야 한다 — 그 틈을 이 시험이 메운다.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.api.explain import get_service
from koipa.schemas.classify import ClassifyResponse, EvaluationFactors, FactorDetail
from koipa.schemas.common import Grade


def _hdr():
    from koipa.config import settings
    return {"X-API-Key": settings.api_key or "test-key"}


class _FakeService:
    """실제 추론 파이프라인·DB를 타지 않는다 — 라우트·직렬화 경로만 본다."""

    def __init__(self, factors: EvaluationFactors, factors_source: str):
        self._factors = factors
        self._factors_source = factors_source

    def classify(self, req) -> ClassifyResponse:
        return ClassifyResponse(
            inference_id=uuid.uuid4(),
            doc_id=req.doc_id,
            label=Grade.S1,
            confidence=0.9,
            scores={"TS": 0.1, "S1": 0.9, "S2": 0.0, "S3": 0.0},
            evaluation_factors=self._factors,
            factors_source=self._factors_source,
            model_version="v-test",
            status="needs_review",
            elapsed_ms=5,
        )


def _call(factors: EvaluationFactors, factors_source: str):
    app.dependency_overrides[get_service] = lambda: _FakeService(factors, factors_source)
    try:
        return TestClient(app).post(
            "/api/v1/classify/explain",
            json={"doc_id": str(uuid.uuid4()), "content": "본문"},
            headers=_hdr(),
        )
    finally:
        app.dependency_overrides.pop(get_service, None)


def test_mixed_observed_and_unknown_axes_does_not_500():
    """M만 근거가 있고 S·V는 unknown — 역산 제거 전이라면 500이 났을 조합."""
    factors = EvaluationFactors(
        secrecy=FactorDetail(state="unknown"),
        value=FactorDetail(state="unknown"),
        management=FactorDetail(state="observed", value=1, evidence=["대외비"]),
    )
    res = _call(factors, "model_estimated")
    assert res.status_code == 200, res.text
    rows = {r["factor"]: r for r in res.json()["explain"]["factor_decomposition"]["rows"]}
    assert rows["secrecy"]["state"] == "unknown" and rows["secrecy"]["score"] is None
    assert rows["management"]["state"] == "observed" and rows["management"]["score"] == 1
    assert rows["management"]["evidence"] == ["대외비"]
    assert res.json()["explain"]["factor_decomposition"]["limiting_factor"] is None


def test_all_observed_axes_returns_limiting_factor():
    factors = EvaluationFactors(
        secrecy=FactorDetail(state="observed", value=2, evidence=["근거S"]),
        value=FactorDetail(state="observed", value=2, evidence=["근거V"]),
        management=FactorDetail(state="observed", value=0, evidence=["근거M"]),
    )
    res = _call(factors, "rule_evidenced")
    assert res.status_code == 200, res.text
    body = res.json()["explain"]["factor_decomposition"]
    assert body["limiting_factor"] == "management"
