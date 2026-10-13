"""학습 산출물에 학습 조건이 남는다 — 시드 · 결정성 · 등급별 손실 가중과 그 출처(2026-09-11).

같은 학습셋 · 같은 시드로 다시 학습해도 결과가 크게 갈렸다(v6 교정본 다섯 판의 고등급→S3 미탐 29~206).
두 가지가 원인일 수 있다 — GPU 연산의 비결정성(학습기가 set_seed 만 부른다)과, DB 유무에 따라
등급별 손실 가중이 시드값(TS 3.0 · S1 2.0) 또는 전부 1.0 으로 조용히 갈리는 것. 종전 산출물은
둘 다 기록하지 않아, 모델 두 벌을 비교할 때 조건이 같았는지 알 수 없었다.
"""
from __future__ import annotations

import contextlib
import dataclasses
import inspect

from koipa.modules.m4_training import trainer as T


def _fake_session(rows):
    class _Result:
        def all(self):
            return rows

    class _Session:
        def execute(self, *_a, **_k):
            return _Result()

    @contextlib.contextmanager
    def scope():
        yield _Session()

    return scope


def test_loss_weights_default_when_db_unavailable(monkeypatch) -> None:
    import koipa.db as db

    @contextlib.contextmanager
    def boom():
        raise RuntimeError("no db")
        yield  # pragma: no cover

    monkeypatch.setattr(db, "session_scope", boom)
    weights, source = T._level_loss_weights_with_source()
    assert source == "default"
    assert [float(w) for w in weights] == [1.0] * len(T._LABEL_LIST)


def test_loss_weights_from_db_are_labelled_db(monkeypatch) -> None:
    import koipa.db as db

    monkeypatch.setattr(db, "session_scope", _fake_session([("TS", 3.0), ("S1", 2.0), ("S2", 1.0), ("S3", 1.0)]))
    weights, source = T._level_loss_weights_with_source()
    assert source == "db"
    assert dict(zip(T._LABEL_LIST, map(float, weights))) == {"TS": 3.0, "S1": 2.0, "S2": 1.0, "S3": 1.0}


def test_old_helper_keeps_its_contract(monkeypatch) -> None:
    import koipa.db as db

    monkeypatch.setattr(db, "session_scope", _fake_session([("TS", 3.0)]))
    weights = T._level_loss_weights()
    assert float(weights[T._LABEL_LIST.index("TS")]) == 3.0


def test_report_and_trainer_carry_the_conditions() -> None:
    assert "training_conditions" in {f.name for f in dataclasses.fields(T.TrainReport)}
    assert T.TrainSpec().deterministic is False
    src = inspect.getsource(T.train_classifier)
    assert "full_determinism=spec.deterministic" in src
    # 보고서를 만드는 두 자리(일반 · 프록시 후보) 모두 조건을 싣는다.
    assert src.count("training_conditions=training_conditions") == 2
    assert '"level_loss_weights_source": level_loss_weights_source' in src
