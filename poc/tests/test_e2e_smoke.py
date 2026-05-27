"""E2E smoke — POST /classify → POST /confirm → GET /metrics/latest 골든 패스.

설계:
- TestClient (in-memory) 단일 호출 체인 — PG 없어도 동작 (best-effort).
- 각 응답이 OpenAPI 스키마 필수 필드를 포함하는지 검증.
- metrics/latest는 PG 없을 때 active_model이 없어 404가 정상 — 200/404 모두 허용.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings


HDR = {"X-API-Key": settings.api_key}
ACTOR = {"user_id": "smoke-e2e-user", "role": "admin"}


def test_e2e_classify_confirm_metrics_golden_path():
    """1건 문서: classify → confirm → metrics/latest 순차 호출."""
    with TestClient(app) as cli:
        # --- 1) POST /classify ---
        r1 = cli.post(
            "/api/v1/classify",
            headers=HDR,
            json={
                "doc_id": "smoke-e2e-001",
                "tenant_id": "test",
                "content": "특급기밀 차세대 제품 설계도 핵심 원천기술 M&A 계획",
                "use_rag": False,
                "return_evidence": True,
            },
        )
        assert r1.status_code == 200, r1.text
        classify_body = r1.json()
        for key in (
            "inference_id",
            "doc_id",
            "label",
            "confidence",
            "scores",
            "model_version",
            "elapsed_ms",
            "status",
        ):
            assert key in classify_body, f"classify missing field: {key}"
        assert classify_body["doc_id"] == "smoke-e2e-001"
        assert classify_body["label"] in {"TS", "S1", "S2", "S3"}
        assert 0.0 <= float(classify_body["confidence"]) <= 1.0

        # --- 2) POST /confirm ---
        # 비-UUID doc_id로 DB 미스 케이스도 best-effort 200 (services/confirm_service)
        r2 = cli.post(
            "/api/v1/confirm",
            headers=HDR,
            json={
                "doc_id": classify_body["doc_id"],
                "confirmed_label": classify_body["label"],
                "actor": ACTOR,
                "note": "e2e smoke",
            },
        )
        assert r2.status_code == 200, r2.text
        confirm_body = r2.json()
        for key in ("confirmation_id", "confirmed_at"):
            assert key in confirm_body, f"confirm missing field: {key}"

        # --- 3) GET /metrics/latest ---
        # PG 없는 환경: active_model 없음 → 404. PG 있는 환경: 200 + MetricsReport.
        r3 = cli.get("/api/v1/metrics/latest", headers=HDR)
        assert r3.status_code in (200, 404), r3.text
        if r3.status_code == 200:
            metrics_body = r3.json()
            assert "model_version" in metrics_body
            # 나머지 필드는 Optional — 키 존재만 확인하면 됨 (None 허용)
            for key in (
                "accuracy",
                "precision_macro",
                "recall_macro",
                "f1_macro",
                "fnr_overall",
                "fnr_by_grade",
                "sample_count",
            ):
                assert key in metrics_body, f"metrics missing field: {key}"
