"""여러 서빙 프로세스가 같은 활성 모델 변경을 독립적으로 따라가는 계약."""

from __future__ import annotations

from pathlib import Path

import koipa.services.classify_service as cs


class _Pipeline:
    def __init__(self, model_dir=None):
        self.model_dir = Path(model_dir) if model_dir else None
        self._model = object() if model_dir else None


def test_each_worker_refreshes_the_shared_active_model_without_direct_reload(monkeypatch):
    """같은 프로세스에서 두 서비스 인스턴스를 흉내 내되, 각자 polling 해야 함을 고정한다.

    실제 별도 프로세스/DB/Celery 기동 증거는 아니다. 다만 한 워커에서만 reload를 호출해
    나머지가 옛 모델에 남는 회귀를 단위 계약에서 막는다.
    """
    from koipa.config import settings

    active = {"dir": "model-a"}
    monkeypatch.setattr(cs, "InferencePipeline", _Pipeline)
    monkeypatch.setattr(cs, "_resolve_serving_model_dir", lambda: active["dir"])
    monkeypatch.setattr(cs, "_resolve_serving_model_dir_strict", lambda: active["dir"])
    monkeypatch.setattr(settings, "serving_model_refresh_seconds", 30.0)

    worker_one = cs.ClassifyService()
    worker_two = cs.ClassifyService()
    active["dir"] = "model-b"
    worker_one._refresh_checked_at = None
    worker_two._refresh_checked_at = None

    worker_one._maybe_refresh_model()
    worker_two._maybe_refresh_model()

    assert worker_one._serving_dir == "model-b"
    assert worker_two._serving_dir == "model-b"
    assert worker_one.inference.model_dir == Path("model-b")
    assert worker_two.inference.model_dir == Path("model-b")
