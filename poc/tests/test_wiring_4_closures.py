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


class _FakeReport:
    """TrainReport 대역 — __dict__로 out 구성 + 게이트/등록 입력."""
    def __init__(self):
        self.model_version = "v-test1234"
        self.accuracy = 0.85
        self.f1_macro = 0.82
        self.fnr_high = 0.03


def _patch_retrain_closure(*, rebuild, run_uuid, consume):
    """A2 재배선 협력자들을 한 번에 패치(컨텍스트 매니저 리스트 반환).

    DB·학습 없이 train_classifier_task의 배선 계약만 검증한다.
    """
    from lloydk.modules.m6_evaluation.corrections_rebuild import RebuildResult  # noqa: F401
    return [
        patch("lloydk.modules.m4_training.trainer.train_classifier", return_value=_FakeReport()),
        patch("lloydk.modules.m4_training.trainer.TrainSpec", lambda **kw: object()),
        patch("lloydk.modules.m6_evaluation.corrections_rebuild.build_labeled_rows_from_corrections",
              return_value=rebuild),
        patch("lloydk.modules.m6_evaluation.corrections_rebuild.merge_into_train_jsonl",
              return_value="datasets/demo_retrain/merged.jsonl"),
        patch("lloydk.workers.tasks._create_training_run_guarded", return_value=run_uuid),
        patch("lloydk.services.training_service.register_and_gate_model",
              return_value={"registered": True, "activated": False, "gate": {"passed": True}}),
        patch("lloydk.modules.m6_evaluation.active_learning.consume_corrections_for_run", consume),
    ]


def test_train_classifier_task_consumes_only_incorporated():
    """[A2-①] 재빌드에 반영된 correction_id만 그 run_id로 소비."""
    import contextlib
    from unittest.mock import MagicMock
    from lloydk.modules.m6_evaluation.corrections_rebuild import RebuildResult
    from lloydk.workers.tasks import train_classifier_task

    rebuild = RebuildResult(
        rows=[{"text": "비밀", "label": "TS"}], correction_ids=[101, 102], doc_count=1, reason="ok",
    )
    run_uuid = uuid.uuid4()
    consume = MagicMock(return_value=2)
    with contextlib.ExitStack() as stack:
        for cm in _patch_retrain_closure(rebuild=rebuild, run_uuid=run_uuid, consume=consume):
            stack.enter_context(cm)
        out = train_classifier_task(spec_kwargs={"train_path": "base.jsonl"})

    assert consume.call_count == 1
    _, kwargs = consume.call_args
    assert kwargs.get("correction_ids") == [101, 102]      # 반영분만
    assert out.get("corrections_consumed") == 2
    assert out.get("corrections_incorporated") == 2
    assert out.get("corrections_run_id") == str(run_uuid)
    assert out["deploy"]["registered"] is True


def test_train_classifier_task_no_consume_without_incorporation():
    """[A2-① 안전성] 반영된 교정이 없으면 소비하지 않는다(반영 없이 소비=유실 차단)."""
    import contextlib
    from unittest.mock import MagicMock
    from lloydk.modules.m6_evaluation.corrections_rebuild import RebuildResult
    from lloydk.workers.tasks import train_classifier_task

    rebuild = RebuildResult(rows=[], correction_ids=[], reason="no_unconsumed_corrections")
    consume = MagicMock(return_value=9)
    with contextlib.ExitStack() as stack:
        for cm in _patch_retrain_closure(rebuild=rebuild, run_uuid=uuid.uuid4(), consume=consume):
            stack.enter_context(cm)
        out = train_classifier_task(spec_kwargs={"train_path": "base.jsonl"})

    assert consume.call_count == 0                          # 소비 호출 자체가 없어야
    assert out.get("corrections_consumed") == 0
    assert out.get("corrections_incorporated") == 0


def test_train_classifier_task_handles_consume_failure():
    """consume_corrections가 실패해도 학습 자체 결과는 반환(-1 마크)."""
    import contextlib
    from unittest.mock import MagicMock
    from lloydk.modules.m6_evaluation.corrections_rebuild import RebuildResult
    from lloydk.workers.tasks import train_classifier_task

    rebuild = RebuildResult(rows=[{"text": "x", "label": "S1"}], correction_ids=[5], reason="ok")
    consume = MagicMock(side_effect=RuntimeError("db down"))
    with contextlib.ExitStack() as stack:
        for cm in _patch_retrain_closure(rebuild=rebuild, run_uuid=uuid.uuid4(), consume=consume):
            stack.enter_context(cm)
        out = train_classifier_task(spec_kwargs={"train_path": "base.jsonl"})
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
