"""[obs] 드리프트 히스테리시스 상태를 centroid baseline 지문으로 스코핑.

재학습이 centroid(고정 경로)를 overwrite하면 옛 baseline 대비 누적 위반은 무효 →
지문 불일치로 연속위반 카운트를 0으로 리셋. 구 상태파일(version 없음)은 하위호환 보존.
"""

from __future__ import annotations

import json

from lloydk.services.drift_monitor import (
    _centroid_fingerprint,
    _load_violation_state,
    _save_violation_state,
)


def test_fingerprint_stable_and_distinct():
    a = {"centroid": [0.1, 0.2, 0.3], "sample_size": 100, "dim": 3}
    b = {"centroid": [0.1, 0.2, 0.3], "sample_size": 100, "dim": 3}
    c = {"centroid": [0.9, 0.8, 0.7], "sample_size": 100, "dim": 3}
    assert _centroid_fingerprint(a) == _centroid_fingerprint(b)  # 동일 baseline → 동일 지문
    assert _centroid_fingerprint(a) != _centroid_fingerprint(c)  # 다른 baseline → 다른 지문
    assert _centroid_fingerprint(None) == ""


def test_count_persists_same_version(tmp_path):
    p = str(tmp_path / "s.json")
    _save_violation_state(2, p, version="v1")
    assert _load_violation_state(p, version="v1") == 2


def test_count_resets_on_baseline_change(tmp_path):
    p = str(tmp_path / "s.json")
    _save_violation_state(3, p, version="v1")
    assert _load_violation_state(p, version="v2") == 0  # baseline 바뀜 → 리셋


def test_legacy_state_without_version_preserved(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"consecutive_violations": 4}), encoding="utf-8")
    # 구 상태파일(version 키 없음) → 리셋 판정 안 함, 그대로 사용(하위호환)
    assert _load_violation_state(str(p), version="v1") == 4


def test_no_version_arg_reads_count(tmp_path):
    p = str(tmp_path / "s.json")
    _save_violation_state(5, p, version="v1")
    assert _load_violation_state(p) == 5  # version 미전달 → 리셋 판정 없이 카운트 반환


def test_save_includes_version(tmp_path):
    p = tmp_path / "s.json"
    _save_violation_state(1, str(p), version="abc123")
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["consecutive_violations"] == 1 and data["version"] == "abc123"
