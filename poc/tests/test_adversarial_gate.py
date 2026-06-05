"""Adversarial regression gate (golden_100) — 데이터 적재 + 위험 미탐 정의 (lite, 모델 불필요).

실제 모델 실행 게이트는 `make adversarial-gate` / `make release-gate`(model 레인).
여기서는 게이트의 *플러밍*(golden_100 구조·HIGH 정의·미탐 카운팅)을 싸게 고정한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def test_golden_100_loads_with_gold():
    import eval_adversarial as ea

    rows = ea._load(Path("datasets/adversarial/golden_100.jsonl"))
    assert len(rows) == 100
    assert all(r["text"] and r["gold"] in {"TS", "S1", "S2", "S3"} for r in rows)
    # 고비밀 케이스가 충분해야 위험-미탐 게이트가 의미 있음
    n_high = sum(1 for r in rows if r["gold"] in ea.HIGH)
    assert n_high >= 50


def test_high_set_is_secret_grades():
    import eval_adversarial as ea

    assert ea.HIGH == {"TS", "S1", "S2"}  # S3(공개)는 비밀 아님


def test_high_risk_underclass_definition():
    """위험 미탐 = gold가 고비밀(TS/S1/S2)인데 예측이 S3(공개)인 경우만."""
    import eval_adversarial as ea

    rows = [{"gold": "TS"}, {"gold": "S1"}, {"gold": "S2"}, {"gold": "S3"}, {"gold": "S1"}]
    preds = ["S3", "S2", "S2", "S3", "TS"]  # TS→S3(미탐), S1→S2, S2→S2, S3→S3, S1→TS
    misses = [r for r, p in zip(rows, preds) if r["gold"] in ea.HIGH and p == "S3"]
    assert len(misses) == 1  # TS→S3 한 건만 (S1→S2/S1→TS는 미탐 아님, S3→S3 정상)
