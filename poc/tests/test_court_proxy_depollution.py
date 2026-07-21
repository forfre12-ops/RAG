"""판례-프록시 디오염(scripts/depollute_court_proxy_gold.py) 재현성·불변식 가드.

- 술어/적용 로직의 결정론·멱등
- 정본이 '완전 디오염' 상태(미정정 0건)라는 트립와이어 — ungoverned 재유입 차단
- 정정 레코드가 전부 silver(학습분포)이고 eval 정답(locked)에 새지 않음
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.depollute_court_proxy_gold import apply_depollution, is_court_proxy_overlabel

from lloydk.golden_tiers import TIER_SILVER, is_real_locked_eval, tier_of

_GOLD = Path(__file__).resolve().parents[1] / "datasets" / "gold_real" / "classification_gold.jsonl"


def _load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def test_predicate_selects_only_court_llm_proxy_overlabels():
    pos = {"source": "판례", "label_source": "llm_judge_primary", "rule_grade": "S3", "label": "S2"}
    assert is_court_proxy_overlabel(pos)
    # 부정: 법적근거 tier / 룰이 이미 고등급 / 이미 S3 / 비-판례 는 대상 아님
    assert not is_court_proxy_overlabel({**pos, "label_source": "public_definitive"})
    assert not is_court_proxy_overlabel({**pos, "rule_grade": "S1"})
    assert not is_court_proxy_overlabel({**pos, "label": "S3"})
    assert not is_court_proxy_overlabel({**pos, "source": "금융보고서"})


def test_apply_is_deterministic_and_idempotent():
    r = {"source": "판례", "label_source": "llm_judge_primary", "rule_grade": "S3", "label": "S1"}
    apply_depollution(r)
    assert r["label"] == "S3" and r["label_original"] == "S1" and r["corrected"] is True
    assert r["corrected_by"] == "scripts/depollute_court_proxy_gold.py"
    # 정정 후 술어에 재매칭되지 않는다(멱등).
    assert not is_court_proxy_overlabel(r)


def test_committed_gold_is_fully_depolluted():
    """정본에 미정정 판례-프록시 과라벨이 0건이어야 한다(재현성 트립와이어)."""
    rows = _load(_GOLD)
    pending = [r for r in rows if is_court_proxy_overlabel(r)]
    assert pending == [], f"{len(pending)} un-depolluted court-proxy rows; run depollute_court_proxy_gold.py"


def test_corrected_records_are_silver_and_never_eval():
    """정정 레코드는 전부 silver(학습분포)이고 eval 정답(locked)에 새면 안 된다."""
    rows = _load(_GOLD)
    corrected = [r for r in rows if r.get("corrected")]
    assert corrected, "expected depolluted records present"
    assert all(tier_of(r) == TIER_SILVER for r in corrected)
    assert not any(is_real_locked_eval(r) for r in corrected)
