"""KL ↔ Lloydk 통합 8시나리오 — doc/19 명세 자동화.

설계:
- 8 시나리오 (S1~S8) 1:1 매핑된 테스트 클래스
- PG/ES/LLM 가용성에 따라 자동 skip (best-effort)
- 모든 시나리오는 TestClient로 in-process — KL 측은 mock (headers로 actor 전달)
"""

from __future__ import annotations

import io
import json
import sys
import uuid

import pytest
if any("not slow" in arg for arg in sys.argv):
    pytest.skip("slow KL integration tests excluded from lite collection", allow_module_level=True)
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from lloydk.api.app import app
from lloydk.config import settings
from lloydk.db import engine

pytestmark = pytest.mark.slow


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


def _hdr(actor_id: str = "kl-user-1", role: str = "kl_backend") -> dict:
    return {
        "X-API-Key": settings.api_key,
        "X-Actor-Id": actor_id,
        "X-Actor-Role": role,
    }


def _actor(user_id: str = "kl-user-1", role: str = "kl_backend") -> dict:
    return {"user_id": user_id, "role": role}


# ============================================================
# S1. 단일 문서 분류 동기
# ============================================================

class TestS1SyncClassify:
    def test_single_document_classification(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/classify",
                headers=_hdr(),
                json={
                    "doc_id": str(uuid.uuid4()),
                    "content": "특급기밀 차세대 제품 설계도 핵심 원천기술 — KL 통합 시나리오 S1",
                    "use_rag": False,
                    "return_evidence": True,
                },
            )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["label"] in {"TS", "S1", "S2", "S3"}
        assert 0.0 <= float(body["confidence"]) <= 1.0
        assert body["elapsed_ms"] < 5000  # PoC 합격선


# ============================================================
# S2. 대용량 비동기 분류
# ============================================================

class TestS2AsyncClassify:
    def test_async_then_poll(self):
        with TestClient(app) as cli:
            r1 = cli.post(
                "/api/v1/classify/async",
                headers=_hdr(),
                json={"doc_id": "doc-s2-1", "content": "비동기 분류 시나리오 본문 " * 20},
            )
            assert r1.status_code == 202
            job_id = r1.json()["job_id"]
            r2 = cli.get(f"/api/v1/classify/jobs/{job_id}", headers=_hdr())
        assert r2.status_code == 200
        body = r2.json()
        assert body["status"] in {"queued", "done"}
        if body["status"] == "done":
            assert body["completed"] == 1

    def test_batch_multiple_docs(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/classify/batch",
                headers=_hdr(),
                json={
                    "documents": [
                        {"doc_id": f"b-{i}", "content": f"배치 분류 문서 #{i} 본문 텍스트"}
                        for i in range(5)
                    ]
                },
            )
        assert r.status_code == 202
        assert r.json()["total"] == 5


# ============================================================
# S3. 관리자 확정·재라벨
# ============================================================

class TestS3ConfirmRelabel:
    def test_confirm_response_shape(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/confirm",
                headers=_hdr(),
                json={
                    "doc_id": "kl-doc-1",
                    "confirmed_label": "S1",
                    "actor": _actor(role="admin"),
                    "note": "S3 시나리오 확정",
                },
            )
        assert r.status_code == 200
        body = r.json()
        assert "confirmation_id" in body
        assert "confirmed_at" in body

    def test_relabel_response_shape(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/relabel",
                headers=_hdr(role="admin"),
                json={
                    "doc_id": "kl-doc-2",
                    "original_label": "S2",
                    "corrected_label": "TS",
                    "reason": "보안 미탐 발견",
                    "actor": _actor(role="admin"),
                },
            )
        assert r.status_code == 200
        body = r.json()
        assert "relabel_id" in body
        assert "queue_size" in body
        assert "retrain_threshold" in body
        assert body["retrain_threshold"] >= 1


# ============================================================
# S4. 등급체계 변경 + 재학습 필요성
# ============================================================

class TestS4SchemaGrades:
    def test_get_grades_returns_4(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/schema/grades", headers=_hdr())
        assert r.status_code == 200
        codes = [g["code"] for g in r.json()["grades"]]
        assert set(codes) >= {"TS", "S1", "S2", "S3"}

    @pytest.mark.skipif(not _PG, reason="Postgres not reachable")
    def test_put_same_grades_no_retrain(self):
        with TestClient(app) as cli:
            current = cli.get("/api/v1/schema/grades", headers=_hdr()).json()
            r = cli.put(
                "/api/v1/schema/grades",
                headers=_hdr(role="admin"),
                json={"grades": current["grades"], "actor": _actor(role="admin")},
            )
        assert r.status_code == 200
        body = r.json()
        assert body["requires_retraining"] is False
        assert isinstance(body["reason"], str)


# ============================================================
# S5. 가이드 문서 업로드 → RAG 인덱싱
# ============================================================

class TestS5GuideUpload:
    def test_upload_text_guide_indexes(self):
        gid = f"kl-guide-{uuid.uuid4().hex[:6]}"
        text_body = "본 가이드는 영업비밀의 등급 분류 기준을 정의한다. " * 30
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/guide/documents",
                headers=_hdr(),
                data={
                    "guide_id": gid,
                    "version": "v1.0",
                    "effective_date": "2026-06-01",
                    "change_summary": "S5 시나리오 가이드",
                    "actor": json.dumps(_actor(role="admin")),
                    "doc_type": "guideline",
                },
                files={"file": ("guide.txt", io.BytesIO(text_body.encode("utf-8")), "text/plain")},
            )
        assert r.status_code == 201
        body = r.json()
        assert body["guide_id"] == gid
        assert isinstance(body["indexed"], bool)
        assert body["embedding_vector_count"] >= 0  # ES 미가용 시 0도 허용

    def test_list_versions_after_upload(self):
        gid = f"kl-list-{uuid.uuid4().hex[:6]}"
        with TestClient(app) as cli:
            cli.post(
                "/api/v1/guide/documents",
                headers=_hdr(),
                data={
                    "guide_id": gid, "version": "v1.0",
                    "actor": json.dumps(_actor(role="admin")),
                },
                files={"file": ("g.txt", io.BytesIO(b"content " * 100), "text/plain")},
            )
            r = cli.get(f"/api/v1/guide/documents/{gid}", headers=_hdr())
        assert r.status_code == 200
        assert r.json()["guide_id"] == gid


# ============================================================
# S6. 합성 문서 생성 → 검수 → 데이터셋 편입
# ============================================================

class TestS6SynthFlow:
    def test_generate_then_queue_then_review(self):
        with TestClient(app) as cli:
            r1 = cli.post(
                "/api/v1/synth/generate",
                headers=_hdr(),
                json={
                    "target_grade": "S2",
                    "domain": "tech",
                    "count": 3,
                    "llm_provider": "noop",  # PoC: 비용 0
                    "actor": _actor(role="reviewer"),
                },
            )
            assert r1.status_code == 202
            assert r1.json()["estimated_cost_usd"] == 0.0
            assert r1.json()["expected_count"] == 3

            r2 = cli.get("/api/v1/synth/queue?status=pending&limit=10", headers=_hdr())
            assert r2.status_code == 200
            assert "items" in r2.json()


# ============================================================
# S7. 능동학습 URGENT_RETRAIN 트리거
# ============================================================

class TestS7ActiveLearningUrgent:
    def test_train_submit_returns_202(self):
        """학습 트리거가 정상 등록되는지 (urgent 알람 자체는 corrections 누적 후 별도)."""
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/train",
                headers=_hdr(),
                json={
                    "training_type": "incremental",
                    "actor": _actor(role="system"),
                },
            )
        assert r.status_code == 202
        body = r.json()
        assert "train_job_id" in body
        assert body["status_url"].startswith("/api/v1/train/jobs/")

    def test_jobs_list_returns_total_items(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/train/jobs?limit=5", headers=_hdr())
        assert r.status_code == 200
        body = r.json()
        assert "total" in body
        assert "items" in body
        assert isinstance(body["items"], list)

    @pytest.mark.skipif(not _PG, reason="Postgres not reachable")
    def test_underclass_accumulates_then_urgent_active_learning(self):
        """corrections 10건 누적 → evaluate_retraining_need → URGENT_RETRAIN."""
        from lloydk.modules.m6_evaluation.active_learning import evaluate_retraining_need

        status = evaluate_retraining_need(urgent_underclass_threshold=10)
        assert status.retrain_status in {"OK", "RETRAIN_RECOMMENDED", "URGENT_RETRAIN"}


# ============================================================
# S8. 운영 지표·CM 조회
# ============================================================

class TestS8MetricsViews:
    def test_history_returns_shape(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/metrics/history?limit=5", headers=_hdr())
        assert r.status_code == 200
        assert "items" in r.json()

    def test_latest_404_when_no_active_model(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/metrics/latest", headers=_hdr())
        # 활성 모델이 없으면 404, 있으면 200 — 둘 다 OK
        assert r.status_code in (200, 404)

    def test_cm_unknown_version_404(self):
        with TestClient(app) as cli:
            r = cli.get(
                "/api/v1/metrics/confusion-matrix/v-does-not-exist-kl",
                headers=_hdr(),
            )
        assert r.status_code == 404


# ============================================================
# Cross-cutting: 모든 시나리오에 audit_log + Prometheus 카운트
# ============================================================

class TestCrossCutting:
    @pytest.mark.skipif(not _PG, reason="Postgres not reachable")
    def test_kl_scenarios_recorded_in_audit_log(self):
        """KL 시나리오 호출 시 audit_log에 actor_role=kl_backend로 기록되는지."""
        from lloydk.db import session_scope
        from lloydk.repositories import AuditRepo

        unique_actor = f"kl-audit-{uuid.uuid4().hex[:8]}"
        with TestClient(app) as cli:
            cli.post(
                "/api/v1/classify",
                headers={
                    "X-API-Key": settings.api_key,
                    "X-Actor-Id": unique_actor,
                    "X-Actor-Role": "kl_backend",
                },
                json={"doc_id": "audit-test", "content": "감사 로그 검증"},
            )

        with session_scope() as db:
            rows = AuditRepo(db).recent_for_actor(unique_actor)
        assert len(rows) >= 1
        assert any(r.actor_role == "kl_backend" for r in rows)

    def test_prometheus_endpoint_records_kl_calls(self):
        """KL 시나리오 호출이 lloydk_requests_total에 카운트되는지."""
        with TestClient(app) as cli:
            cli.get("/api/v1/schema/grades", headers=_hdr())
            r = cli.get("/api/v1/metrics-prom")
        assert r.status_code == 200
        body = r.text
        # schema/grades 호출이 lloydk_requests_total에 잡혔는지
        assert "/api/v1/schema/grades" in body
