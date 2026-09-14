# -*- coding: utf-8 -*-
"""재학습 후보 승격 판정 계약 — 한 축이라도 나빠지면 자동 승격이 없다.

왜(2026-09-14). 재학습이 여섯 번 실패했고 여섯 번 다 판정 기준이 없었다.
한 축만 보면 늘 좋아 보인다 — 과탐을 줄이면 미탐이 늘고 미탐을 줄이면 과탐이 는다.
그 교환을 강제로 한 화면에 놓는 것이 이 판정기의 목적이고, 그 규칙을 여기서 잠근다.
"""
from __future__ import annotations

import sys

import pytest
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from judge_model_candidate import HARD_AXES, SOFT_AXES, INPUT_CONTRACT_VERSION, judge  # noqa: E402
from measure_four_metrics import METRICS_SCHEMA_VERSION  # noqa: E402


def _face(name="f1", *, usable=True, overlap=0, excluded="", **rates):
    base = {
        "s2_underclass": 0.05,
        "exact_grade_error": 0.10,
        **{f"grade_error_{g}": 0.10 for g in ("TS", "S1", "S2", "S3")},
        "high_grade_auto_confirm_fn_rate": 0.05,
        "serving_recall": 0.05,
        "severe_overclass": 0.10,
        "model_overclass": 0.20,
        "serving_overclass": 0.20,
        "model_recall": 0.10,
        "review_load": 0.25,
    }
    base.update(rates)
    return {
        "input_contract_version": INPUT_CONTRACT_VERSION,
        "training_overlap_checked": True,
        "eval_sha256": "a" * 64, "records_sha256": "b" * 64,
        "training_manifest_sha256": "c" * 64, "measurement_config_sha256": "d" * 64,
        "model_id": "test-model", "policy_version": "reference-test-v1", "org_id": "test-org",
        "comparison_status": "DIAGNOSTIC_ONLY",
        "face": name, "usable_for_judgement": usable,
        "training_overlap": overlap, "excluded_reason": excluded,
        "truth_tier": "SILVER",
        "metrics": {"schema_version": METRICS_SCHEMA_VERSION,
                    **{k: {"rate": v, "n": 100} for k, v in base.items()}},
    }


def _baseline(*faces):
    return {"faces": list(faces)}


def test_identical_candidate_promotes() -> None:
    """아무것도 안 바뀌면 승격 가능하다(= 회귀 없음)."""
    b = _baseline(_face())
    r = judge(b, [_face()])
    assert r["verdict"] == "REGRESSION_OK"
    assert r["claim_status"] == "NOT_ASSESSED"
    assert r["judged_faces"] == 1
    assert not r["deltas"]


def test_improvement_on_all_axes_promotes() -> None:
    b = _baseline(_face())
    better = _face(high_grade_auto_confirm_fn_rate=0.02, severe_overclass=0.05,
                   serving_overclass=0.10)
    r = judge(b, [better])
    assert r["verdict"] == "REGRESSION_OK"
    assert all(not d.get("worse") for d in r["deltas"])


@pytest.mark.parametrize("axis,label", list(HARD_AXES))
def test_any_hard_axis_worse_rejects(axis, label) -> None:
    """미탐·격상이 늘면 즉시 기각 — 계약 핵심목표가 미탐 최소화다."""
    b = _baseline(_face())
    worse = _face(**{axis: 0.99})
    r = judge(b, [worse])
    assert r["verdict"] == "REJECT", f"{label} 이 나빠졌는데 기각이 아니다"
    assert any(label in x for x in r["reasons"])


@pytest.mark.parametrize("axis,label", list(SOFT_AXES))
def test_soft_axis_worse_holds_not_rejects(axis, label) -> None:
    """교환일 수 있는 축은 사람이 판단한다 — 자동 기각도 자동 승격도 아니다."""
    b = _baseline(_face())
    worse = _face(**{axis: 0.99})
    r = judge(b, [worse])
    assert r["verdict"] == "HOLD", f"{label}: {r['verdict']}"


def test_hard_reject_beats_soft_hold() -> None:
    """두 축이 동시에 나빠지면 기각이 이긴다."""
    b = _baseline(_face())
    worse = _face(severe_overclass=0.99, review_load=0.99)
    assert judge(b, [worse])["verdict"] == "REJECT"


def test_tradeoff_is_surfaced_not_hidden() -> None:
    """미탐이 줄고 과탐이 늘면 — 여섯 번 실패의 전형 — HOLD 로 사람 앞에 놓는다."""
    b = _baseline(_face())
    cand = _face(high_grade_auto_confirm_fn_rate=0.01, serving_overclass=0.60)
    r = judge(b, [cand])
    assert r["verdict"] == "HOLD"
    worse = [d for d in r["deltas"] if d.get("worse")]
    better = [d for d in r["deltas"] if not d.get("worse")]
    assert worse and better, "교환이 양쪽 다 보여야 한다"


def test_unusable_face_is_not_judged() -> None:
    """학습 겹침·제외면은 판정에서 빠지고 그 사실이 사유에 남는다."""
    b = _baseline(_face("f1"))
    r = judge(b, [_face("f1", usable=False, overlap=13)])
    assert r["judged_faces"] == 0
    assert r["verdict"] == "HOLD"
    assert any("판정면으로 못 씀" in x for x in r["reasons"])


def test_no_usable_face_cannot_promote() -> None:
    """판정 가능한 면이 0개면 절대 PROMOTE 가 나오면 안 된다."""
    b = _baseline(_face("f1"))
    r = judge(b, [_face("f1", usable=False, excluded="정답이 등급식과 반대")])
    assert r["verdict"] != "PROMOTE"
    assert any("판정 가능한 면이 하나도 없다" in x for x in r["reasons"])


def test_face_missing_from_baseline_holds() -> None:
    """기준선에 없는 면이 오면 비교 불가 — 조용히 통과시키지 않는다."""
    b = _baseline(_face("f1"))
    r = judge(b, [_face("f2")])
    assert r["verdict"] == "HOLD"
    assert any("기준선에 없는 면" in x for x in r["reasons"])


def test_missing_axis_holds_not_treated_as_zero() -> None:
    """A missing field is not proof of a zero denominator."""
    b = _baseline(_face())
    cand = _face()
    cand["metrics"]["severe_overclass"] = {"rate": None}
    r = judge(b, [cand])
    assert r["verdict"] == "HOLD"
    assert all(d["axis"] != "severe_overclass" for d in r["deltas"])


def test_genuinely_empty_denominator_on_both_sides_is_not_zero_percent():
    base, cand = _face(), _face()
    for face in (base, cand):
        face["metrics"]["severe_overclass"] = {"rate": None, "n": 0}
    assert judge(_baseline(base), [cand])["verdict"] == "REGRESSION_OK"


@pytest.mark.parametrize("tier", ["NONE", "UNKNOWN", "CIRCULAR"])
def test_untrusted_truth_cannot_pass_even_if_usable_flag_is_true(tier):
    face = _face()
    face["truth_tier"] = tier
    assert judge(_baseline(face), [face])["verdict"] == "HOLD"


def test_missing_required_face_cannot_improve_verdict():
    assert judge(_baseline(_face("a"), _face("b")), [_face("a")])["verdict"] == "HOLD"


@pytest.mark.parametrize("key", ["eval_sha256", "org_id", "policy_version", "measurement_config_sha256"])
def test_changed_input_or_policy_is_not_same_condition_comparison(key):
    face = _face()
    face[key] = "e" * 64
    assert judge(_baseline(_face()), [face])["verdict"] == "HOLD"


def test_legacy_snapshot_requires_remeasurement():
    old = _face()
    old.pop("input_contract_version")
    assert judge(_baseline(old), [_face()])["verdict"] == "HOLD"


def test_later_error_cannot_overwrite_hard_rejection():
    result = judge(_baseline(_face("a"), _face("b")),
                   [_face("a", s2_underclass=0.5), {"face": "b", "error": "missing"}])
    assert result["verdict"] == "REJECT"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.1, 1.1])
def test_invalid_metric_never_passes(value):
    assert judge(_baseline(_face()), [_face(s2_underclass=value)])["verdict"] == "HOLD"
