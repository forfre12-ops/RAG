"""check_data_quality 의 fake-green 차단 회귀 — train 풀 부재 시 무음 통과 금지.

CI에서 학습 코퍼스(gitignore)가 부재하면 gold↔train overlap·누출 검사가 '수행 못 됨'인데
과거엔 train_overlap=0 으로 조용히 green 이었다. 이제 COVERAGE-GAP 으로 가시화하고,
--strict-coverage 로 exit 1 강제할 수 있어야 한다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "check_data_quality.py"
GOLD = ROOT / "datasets" / "gold_real" / "classification_gold.jsonl"


def _run(tmp_path, *extra):
    report = tmp_path / "dq.json"
    cp = subprocess.run(
        [sys.executable, str(SCRIPT),
         "--train", str(tmp_path / "absent_train.jsonl"),
         "--gold", str(GOLD),
         "--train-pool", str(tmp_path / "absent_pool.jsonl"),
         "--holdout", str(tmp_path / "absent_holdout.jsonl"),
         "--report", str(report), *extra],
        capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT),
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    return cp, data


def test_absent_train_pool_is_visible_gap_not_silent_pass(tmp_path):
    cp, data = _run(tmp_path)
    # 무음 아님: 표준출력/에러에 COVERAGE-GAP 마커가 보여야 한다.
    assert "COVERAGE-GAP" in (cp.stdout + cp.stderr)
    assert len(data["coverage_gaps"]) >= 2  # gold train-overlap 미검사 + leak-check 미수행
    # gold 강화검사 통계에 train_overlap_checked=False 가 명시된다(0 을 '검증됨'으로 오독 금지).
    gold_stats = [s for s in data["splits"] if s.get("name") == "gold_real"]
    assert gold_stats and gold_stats[0].get("train_overlap_checked") is False


def test_strict_coverage_fails_closed(tmp_path):
    cp, data = _run(tmp_path, "--strict-coverage")
    assert cp.returncode == 1
    assert data["verdict"] == "FAIL"
    assert data["strict_coverage"] is True


def test_default_is_non_disruptive_pass(tmp_path):
    """기본(strict 아님)은 gap 을 기록하되 verdict 를 깨지 않는다(현 CI green 보존)."""
    cp, data = _run(tmp_path)
    assert cp.returncode == 0
    assert data["verdict"] == "PASS"
    assert data["coverage_gaps"]  # 그러나 gap 은 정직하게 기록됨
