"""표적 7·5·6·3 — 호출 체인 닫음 검증 (모두 단위, ML/실 인프라 없음).

7. outbox worker beat — deliver_outbox_tick task + beat schedule 등록 정적 확인
5. consume_corrections wiring — train_classifier_task가 학습 후 consume 호출
6. PII masker wiring — PreprocessPipeline.run_text가 mask_pii 자동 적용
3. CachedEmbedding redis_url — build_embedder가 settings.redis_url을 전달
"""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# 7. outbox worker beat
# ---------------------------------------------------------------------------


def test_deliver_outbox_tick_task_exists():
    from lloydk.workers.tasks import deliver_outbox_tick
    assert callable(deliver_outbox_tick)


def test_celery_beat_schedule_includes_outbox():
    from lloydk.workers.celery_app import celery_app
    sched = celery_app.conf.beat_schedule
    assert "outbox-deliver-every-60s" in sched
    entry = sched["outbox-deliver-every-60s"]
    assert entry["task"] == "lloydk.deliver_outbox_tick"
    assert entry["schedule"] == 60.0


def test_celery_routes_include_outbox_queue():
    from lloydk.workers.celery_app import celery_app
    routes = celery_app.conf.task_routes
    assert "lloydk.deliver_outbox_tick" in routes


def test_celery_worker_limits_follow_settings():
    from lloydk.config import settings
    from lloydk.workers.celery_app import celery_app
    assert celery_app.conf.result_expires == settings.celery_result_expires
    assert celery_app.conf.task_soft_time_limit == settings.celery_task_soft_time_limit
    assert celery_app.conf.task_time_limit == settings.celery_task_time_limit
    assert celery_app.conf.worker_max_tasks_per_child == settings.celery_worker_max_tasks_per_child


def test_deliver_outbox_tick_invokes_deliver_once():
    """task 본문이 deliver_once를 store + http_send와 함께 호출."""
    from lloydk.workers.tasks import deliver_outbox_tick

    fake_out = {"sent": 2, "failed": 0, "dlq": 0, "ready": 2}
    with patch("lloydk.services.outbox.deliver_once", return_value=fake_out) as d:
        out = deliver_outbox_tick(limit=10)
    assert out == fake_out
    assert d.call_count == 1
    # 두 번째 호출 인자: limit=10
    _, kwargs = d.call_args
    assert kwargs.get("limit") == 10
    assert "http_send" in kwargs


# ---------------------------------------------------------------------------
# 5. consume_corrections wiring
# ---------------------------------------------------------------------------


def test_train_classifier_task_consumes_corrections():
    """train_classifier 호출 후 consume_corrections_for_run이 호출되어야 함."""
    from lloydk.workers.tasks import train_classifier_task

    class FakeReport:
        def __init__(self):
            self.run_id = uuid.uuid4()
            self.accuracy = 0.85

    fake_report = FakeReport()
    with patch("lloydk.modules.m4_training.trainer.train_classifier", return_value=fake_report):
        with patch("lloydk.modules.m6_evaluation.active_learning.consume_corrections_for_run",
                   return_value=7) as consume:
            with patch("lloydk.modules.m4_training.trainer.TrainSpec", lambda **kw: object()):
                out = train_classifier_task()
    assert consume.call_count == 1
    assert out.get("corrections_consumed") == 7
    assert "corrections_run_id" in out


def test_train_classifier_task_handles_consume_failure():
    """consume_corrections가 실패해도 학습 자체 결과는 반환."""
    from lloydk.workers.tasks import train_classifier_task

    class FakeReport:
        def __init__(self):
            self.run_id = uuid.uuid4()

    with patch("lloydk.modules.m4_training.trainer.train_classifier", return_value=FakeReport()):
        with patch("lloydk.modules.m6_evaluation.active_learning.consume_corrections_for_run",
                   side_effect=RuntimeError("db down")):
            with patch("lloydk.modules.m4_training.trainer.TrainSpec", lambda **kw: object()):
                out = train_classifier_task()
    assert out.get("corrections_consumed") == -1


# ---------------------------------------------------------------------------
# 6. PII masker wiring
# ---------------------------------------------------------------------------


def test_preprocess_pipeline_run_text_masks_pii_by_default():
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
    p = PreprocessPipeline()
    text = "연락처는 010-1234-5678, 이메일 john@example.com 입니다."
    out = p.run_text(text)
    assert "010-1234-5678" not in out
    assert "john@example.com" not in out
    assert "[PHONE]" in out or "[EMAIL]" in out


def test_preprocess_pipeline_pii_masking_disabled():
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
    p = PreprocessPipeline(pii_masking=False)
    text = "전화 010-1234-5678 보존"
    out = p.run_text(text)
    assert "010-1234-5678" in out
    assert "[PHONE]" not in out


def test_preprocess_pipeline_run_text_full_records_pii_counts():
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline
    p = PreprocessPipeline()
    text = "주민번호 880101-1234567, 카드 1234-5678-9012-3456"
    result = p.run_text_full(text)
    assert sum(result.pii_counts.values()) >= 2
    assert "[RRN]" in result.text or "[CARD]" in result.text


# ---------------------------------------------------------------------------
# 3. CachedEmbedding redis_url 자동 주입
# ---------------------------------------------------------------------------


def test_redis_url_for_emb_cache_uses_explicit_env(monkeypatch):
    from lloydk.adapters.embedding import _redis_url_for_emb_cache
    monkeypatch.setenv("EMB_REDIS_URL", "redis://override:6379/3")
    assert _redis_url_for_emb_cache() == "redis://override:6379/3"


def test_redis_url_for_emb_cache_disabled(monkeypatch):
    from lloydk.adapters.embedding import _redis_url_for_emb_cache
    monkeypatch.delenv("EMB_REDIS_URL", raising=False)
    monkeypatch.setenv("EMB_REDIS_ENABLED", "0")
    assert _redis_url_for_emb_cache() is None


def test_redis_url_for_emb_cache_falls_back_to_settings(monkeypatch):
    from lloydk.adapters.embedding import _redis_url_for_emb_cache
    monkeypatch.delenv("EMB_REDIS_URL", raising=False)
    monkeypatch.delenv("EMB_REDIS_ENABLED", raising=False)
    # settings.redis_url는 기본값(redis://localhost:6379/0)이 있음
    result = _redis_url_for_emb_cache()
    assert result is not None
    assert result.startswith("redis://")


def test_build_embedder_force_hash_skips_cache():
    """HashEmbedding은 cache wrap 우회 — 결정론적 해시라 의미 없음."""
    from lloydk.adapters.embedding import build_embedder
    from lloydk.adapters.embedding.hash_embedding import HashEmbedding
    emb = build_embedder(force_hash=True)
    assert isinstance(emb, HashEmbedding)


# ---------------------------------------------------------------------------
# 정적 검증 — 변경된 파일에 마커가 있는지
# ---------------------------------------------------------------------------


def test_celery_app_source_contains_outbox_beat_entry():
    src = Path(__file__).resolve().parents[1] / "src" / "lloydk" / "workers" / "celery_app.py"
    text = src.read_text(encoding="utf-8")
    assert "outbox-deliver-every-60s" in text
    assert "lloydk.deliver_outbox_tick" in text


def test_tasks_source_imports_consume():
    src = Path(__file__).resolve().parents[1] / "src" / "lloydk" / "workers" / "tasks.py"
    text = src.read_text(encoding="utf-8")
    assert "consume_corrections_for_run" in text
