"""W3 신규 라우터 7개 통합 테스트.

설계:
- TestClient로 모든 라우터의 happy path + 인증·검증 실패 케이스 검증
- DB 미가용일 때도 응답 형태가 OpenAPI 명세와 일치하는지 (best-effort 동작)
- PG 가용 시 추가 영속화 검증 (skipif)
"""

from __future__ import annotations

import io
import json
import sys
import uuid

import pytest
if any("not slow" in arg for arg in sys.argv):
    pytest.skip("slow API router integration tests excluded from lite collection", allow_module_level=True)
pytestmark = pytest.mark.slow
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.api.app import app
from lloydk.config import settings
from lloydk.db import engine


def _pg_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False
    except Exception:  # noqa: BLE001
        return False


_PG = _pg_ok()
HDR = {"X-API-Key": settings.api_key, "X-Actor-Role": "admin"}
ACTOR = {"user_id": "test-user", "role": "admin"}


# ============================================================
# /confirm + /relabel
# ============================================================

class TestConfirmRouter:
    def test_confirm_requires_api_key(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/confirm",
                json={"doc_id": "x", "confirmed_label": "S1", "actor": ACTOR},
            )
            assert r.status_code in (401, 422)

    def test_confirm_happy_path_without_db_match(self):
        """비-UUID doc_id → DB 미스 → 200 + warning이 아닌 정상 응답 스키마."""
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/confirm",
                headers=HDR,
                json={"doc_id": "non-uuid", "confirmed_label": "S1", "actor": ACTOR, "note": "ok"},
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "confirmation_id" in body
            assert "confirmed_at" in body

    def test_relabel_happy_path(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/relabel",
                headers=HDR,
                json={
                    "doc_id": "non-uuid",
                    "original_label": "S2",
                    "corrected_label": "S1",
                    "actor": ACTOR,
                    "reason": "missed top-secret keyword",
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert "relabel_id" in body
            assert "queue_size" in body
            assert "retrain_threshold" in body


# ============================================================
# /train + /train/jobs
# ============================================================

class TestTrainingRouter:
    def test_train_submit_returns_202(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/train",
                headers=HDR,
                json={
                    "training_type": "full",
                    "base_model": "kf-deberta-base",
                    "hyperparams": {"lr": 2e-5, "epochs": 5},
                    "actor": ACTOR,
                },
            )
            assert r.status_code == 202, r.text
            body = r.json()
            assert "train_job_id" in body
            assert body["status_url"].startswith("/api/v1/train/jobs/")

    def test_train_jobs_list_returns_shape(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/train/jobs", headers=HDR)
            assert r.status_code == 200
            body = r.json()
            assert "total" in body
            assert "items" in body
            assert isinstance(body["items"], list)

    def test_train_jobs_status_not_found(self):
        with TestClient(app) as cli:
            r = cli.get(f"/api/v1/train/jobs/{uuid.uuid4()}", headers=HDR)
            assert r.status_code == 404

    @pytest.mark.skipif(not _PG, reason="Postgres not reachable")
    def test_train_submit_then_status_round_trip(self):
        """제출 → 상태 폴링 → queued 응답."""
        with TestClient(app) as cli:
            r1 = cli.post(
                "/api/v1/train",
                headers=HDR,
                json={"training_type": "full", "actor": ACTOR},
            )
            train_job_id = r1.json()["train_job_id"]
            r2 = cli.get(f"/api/v1/train/jobs/{train_job_id}", headers=HDR)
            assert r2.status_code == 200
            assert r2.json()["status"] == "queued"


# ============================================================
# /synth/*
# ============================================================

class TestSynthesisRouter:
    def test_synth_generate_returns_202(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/synth/generate",
                headers=HDR,
                json={
                    "target_grade": "S1",
                    "domain": "tech",
                    "count": 10,
                    "llm_provider": "anthropic",
                    "actor": ACTOR,
                },
            )
            assert r.status_code == 202
            body = r.json()
            assert body["expected_count"] == 10
            assert body["estimated_cost_usd"] > 0

    def test_synth_generate_noop_zero_cost(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/synth/generate",
                headers=HDR,
                json={
                    "target_grade": "S2",
                    "count": 5,
                    "llm_provider": "noop",
                    "actor": ACTOR,
                },
            )
            assert r.status_code == 202
            assert r.json()["estimated_cost_usd"] == 0.0

    def test_synth_queue_returns_shape(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/synth/queue", headers=HDR)
            assert r.status_code == 200
            body = r.json()
            assert "total" in body
            assert "items" in body

    def test_synth_review_unknown_id_404(self):
        with TestClient(app) as cli:
            r = cli.post(
                f"/api/v1/synth/{uuid.uuid4()}/review",
                headers=HDR,
                json={"decision": "approve", "actor": ACTOR},
            )
            assert r.status_code in (404, 503)  # PG 미가용 시 service None → 404


# ============================================================
# /guide/documents
# ============================================================

class TestGuideRouter:
    def test_upload_then_list_versions(self):
        gid = f"guide-{uuid.uuid4().hex[:6]}"
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/guide/documents",
                headers=HDR,
                data={
                    "guide_id": gid,
                    "version": "v1.0",
                    "effective_date": "2026-06-01",
                    "change_summary": "initial",
                    "actor": json.dumps(ACTOR),
                },
                files={"file": ("guide.txt", io.BytesIO(b"sample guide content"), "text/plain")},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["guide_id"] == gid
            assert body["version"] == "v1.0"
            # W5 이후: indexed는 ES 가동·임베딩 모델 가용성에 따라 True/False.
            # 두 경우 모두 키 존재·타입만 검증 (best-effort 응답 보장).
            assert isinstance(body["indexed"], bool)
            assert "embedding_vector_count" in body

            # 목록 조회
            r2 = cli.get(f"/api/v1/guide/documents/{gid}", headers=HDR)
            assert r2.status_code == 200
            vlist = r2.json()
            assert vlist["guide_id"] == gid
            assert len(vlist["versions"]) >= 1

    def test_list_unknown_guide_404(self):
        with TestClient(app) as cli:
            r = cli.get(f"/api/v1/guide/documents/does-not-exist-{uuid.uuid4().hex[:6]}", headers=HDR)
            assert r.status_code == 404

    def test_upload_invalid_actor_json_422(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/guide/documents",
                headers=HDR,
                data={
                    "guide_id": "g",
                    "version": "v",
                    "actor": "{not json",
                },
                files={"file": ("g.txt", io.BytesIO(b"x"), "text/plain")},
            )
            assert r.status_code == 422


# ============================================================
# /schema/grades
# ============================================================

class TestSchemaAdminRouter:
    def test_get_returns_4_grades(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/schema/grades", headers=HDR)
            assert r.status_code == 200
            body = r.json()
            codes = [g["code"] for g in body["grades"]]
            assert set(codes) >= {"TS", "S1", "S2", "S3"}

    @pytest.mark.skipif(not _PG, reason="Postgres not reachable")
    def test_put_same_grades_no_retrain(self):
        with TestClient(app) as cli:
            current = cli.get("/api/v1/schema/grades", headers=HDR).json()
            r = cli.put(
                "/api/v1/schema/grades",
                headers=HDR,
                json={"grades": current["grades"], "actor": ACTOR},
            )
            assert r.status_code == 200
            assert r.json()["requires_retraining"] is False


# ============================================================
# /metrics/*
# ============================================================

class TestMetricsRouter:
    def test_latest_no_active_model_404(self):
        """현재 활성 모델 없음 → 404 (DB 미가용 시에도 동일)."""
        with TestClient(app) as cli:
            r = cli.get("/api/v1/metrics/latest", headers=HDR)
            assert r.status_code == 404

    def test_history_returns_shape(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/metrics/history?limit=5", headers=HDR)
            assert r.status_code == 200
            assert "items" in r.json()

    def test_cm_unknown_version_404(self):
        with TestClient(app) as cli:
            r = cli.get(
                "/api/v1/metrics/confusion-matrix/v-does-not-exist", headers=HDR
            )
            assert r.status_code == 404


# ============================================================
# /classify/async + /batch + /jobs/{id} + /{doc_id}
# ============================================================

class TestAsyncClassifyRouter:
    def test_async_submit_then_status(self):
        with TestClient(app) as cli:
            r1 = cli.post(
                "/api/v1/classify/async",
                headers=HDR,
                json={"doc_id": "non-uuid", "content": "특급기밀 설계도"},
            )
            assert r1.status_code == 202
            job_id = r1.json()["job_id"]
            r2 = cli.get(f"/api/v1/classify/jobs/{job_id}", headers=HDR)
            assert r2.status_code == 200
            body = r2.json()
            assert body["status"] in {"queued", "done"}
            if body["status"] == "done":
                assert body["completed"] == 1

    def test_batch_returns_total(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/classify/batch",
                headers=HDR,
                json={
                    "documents": [
                        {"doc_id": "n1", "content": "특급기밀 차세대 제품 설계도 핵심 원천기술"},
                        {"doc_id": "n2", "content": "공개 정보"},
                    ]
                },
            )
            assert r.status_code == 202
            assert r.json()["total"] == 2

    def test_batch_too_large_413(self):
        big = {"documents": [{"doc_id": f"d{i}", "content": "x"} for i in range(1001)]}
        with TestClient(app) as cli:
            r = cli.post("/api/v1/classify/batch", headers=HDR, json=big)
            assert r.status_code == 413

    def test_jobs_unknown_404(self):
        with TestClient(app) as cli:
            r = cli.get(f"/api/v1/classify/jobs/{uuid.uuid4()}", headers=HDR)
            assert r.status_code == 404

    def test_classify_by_doc_id_invalid_uuid_422(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/classify/non-uuid", headers=HDR)
            assert r.status_code == 422

    def test_classify_by_doc_id_unknown_404(self):
        with TestClient(app) as cli:
            r = cli.get(f"/api/v1/classify/{uuid.uuid4()}", headers=HDR)
            # PG 가용 시 404, 미가용 시 503
            assert r.status_code in (404, 503)
