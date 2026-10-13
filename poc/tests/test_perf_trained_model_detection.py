"""성능 하니스가 "학습 모델이 있다"를 무엇으로 판정하는가.

왜 이 시험이 있는가(2026-09-12). 211 에서 미탐 지표 넷(S1.3 F1 · S1.4 FNR · S9.2 적대적 FNR ·
S9.4 무음 미탐)이 **한 번도 재지지 않았다.** 매 회차 `missing: trained_model` 로 SKIP 됐는데,
모델이 없어서가 아니라 판정 함수가 **모델 파일을 보지 않았기** 때문이다. 보던 것은 셋뿐이었다:

    ① 강제 환경변수 KOIPA_TRAINED_MODEL
    ② 이 프로세스가 이미 적재한 ClassifyService 인스턴스 — 하니스는 새 프로세스라 늘 없다
    ③ DB 의 활성 ModelVersion 행 — 211 은 0행이다(9/10 PostgreSQL 재배포 때 안 옮겼다)

그런데 서버는 `CLASSIFIER_MODEL_DIR` 로 실제 서빙 중이고 healthz 는 model=loaded 였다.
RFP 가 "핵심 성능 목표 = 미탐 최소화"라고 적은 지표가 그 사이 계속 빈칸이었다.

그래서 **서빙이 쓰는 것과 같은 값**(설정된 모델 디렉터리에 가중치 파일이 있는지)을 함께 본다.
⚠ 빈 폴더를 '학습됨'으로 세지 않는다 — 그러면 dryrun 이 거짓 통과한다.
"""

from __future__ import annotations

import pytest

from koipa.perf import harness


@pytest.fixture
def no_db(monkeypatch):
    """DB 검사를 확실히 실패시켜, 디렉터리 검사만 남긴다."""
    monkeypatch.delenv("KOIPA_TRAINED_MODEL", raising=False)
    monkeypatch.setattr(harness, "_serving_model_loaded", lambda: False)
    return monkeypatch


def _point_settings_at(monkeypatch, path):
    from koipa.config import settings

    monkeypatch.setattr(settings, "classifier_model_dir", str(path), raising=False)


def test_weights_in_model_dir_count_as_trained(no_db, tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x")
    _point_settings_at(no_db, tmp_path)
    assert harness._detect_trained_model() is True


def test_legacy_bin_weights_also_count(no_db, tmp_path):
    (tmp_path / "pytorch_model.bin").write_bytes(b"x")
    _point_settings_at(no_db, tmp_path)
    assert harness._detect_trained_model() is True


def test_empty_dir_is_not_trained(no_db, tmp_path):
    """폴더만 있고 가중치가 없으면 학습 모델이 아니다 — dryrun 이 거짓 통과하면 안 된다."""
    _point_settings_at(no_db, tmp_path)
    assert harness._detect_trained_model() is False


def test_unset_model_dir_is_not_trained(no_db):
    _point_settings_at(no_db, "")
    assert harness._detect_trained_model() is False


def test_env_flag_still_wins(no_db, tmp_path):
    """CI 탈출구는 그대로다 — 끄는 쪽도 지켜야 한다."""
    (tmp_path / "model.safetensors").write_bytes(b"x")
    _point_settings_at(no_db, tmp_path)
    no_db.setenv("KOIPA_TRAINED_MODEL", "0")
    assert harness._detect_trained_model() is False
    no_db.setenv("KOIPA_TRAINED_MODEL", "1")
    assert harness._detect_trained_model() is True
