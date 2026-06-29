"""배포 합격선 게이트 단위 테스트 — A2-② / C-ver.

순수 함수 evaluate_deploy_gate의 fail-SECURE 판정을 검증한다. DB·모델 불요.
영업비밀 안전원칙: 미탐(fnr_high) 악화·degenerate·검증불가는 무조건 배포 차단.
"""

from __future__ import annotations

from lloydk.modules.m6_evaluation.deploy_gate import (
    DeployDecision,
    evaluate_deploy_gate,
)


def _report(fnr_high=0.05, f1_macro=0.85, cm=None):
    d = {"fnr_high": fnr_high, "f1_macro": f1_macro}
    if cm is not None:
        d["confusion_matrix"] = cm
    return d


# ── 최초 배포 (baseline 없음) ────────────────────────────────────────────────


def test_first_deploy_passes_without_baseline():
    dec = evaluate_deploy_gate(_report(fnr_high=0.1, f1_macro=0.7), None)
    assert isinstance(dec, DeployDecision)
    assert dec.passed is True
    assert dec.had_baseline is False


def test_first_deploy_blocked_when_require_baseline():
    dec = evaluate_deploy_gate(_report(), None, require_baseline=True)
    assert dec.passed is False
    assert "baseline_present" in dec.reason


# ── [NEW-M2] 최초 배포 절대 FNR floor ────────────────────────────────────────


def test_first_deploy_floor_blocks_high_fnr():
    # floor 설정 시, baseline 없는 첫 모델도 미탐 하한 초과면 거부(fnr=10% 무조건통과 차단).
    dec = evaluate_deploy_gate(
        _report(fnr_high=0.10, f1_macro=0.8), None, first_deploy_fnr_high_max=0.05,
    )
    assert dec.passed is False
    assert "first_deploy_fnr_floor" in dec.reason


def test_first_deploy_floor_allows_within():
    dec = evaluate_deploy_gate(
        _report(fnr_high=0.03, f1_macro=0.8), None, first_deploy_fnr_high_max=0.05,
    )
    assert dec.passed is True


def test_first_deploy_floor_none_preserves_passthrough():
    # floor 미설정(기본 None) → 기존 동작 보존: 높은 fnr도 첫 배포 통과(degenerate 아니면).
    dec = evaluate_deploy_gate(_report(fnr_high=0.10, f1_macro=0.7), None)
    assert dec.passed is True
    assert not any(c.name == "first_deploy_fnr_floor" for c in dec.checks)


def test_floor_ignored_when_baseline_present():
    # baseline 있으면 floor 분기(else)에 안 들어감 — 회귀 비교가 우선.
    dec = evaluate_deploy_gate(
        _report(fnr_high=0.06), _report(fnr_high=0.05), first_deploy_fnr_high_max=0.01,
    )
    assert dec.passed is True   # +0.01 ≤ 회귀허용 0.02 통과, floor(0.01)는 미적용
    assert not any(c.name == "first_deploy_fnr_floor" for c in dec.checks)


# ── fnr_high 회귀 차단 (미탐 악화) ───────────────────────────────────────────


def test_blocks_fnr_high_regression():
    cand = _report(fnr_high=0.20)   # 미탐 악화
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base, fnr_high_tolerance=0.02)
    assert dec.passed is False
    assert "fnr_high_regression" in dec.reason


def test_allows_fnr_high_within_tolerance():
    cand = _report(fnr_high=0.06)   # +0.01 ≤ tol 0.02
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base, fnr_high_tolerance=0.02)
    assert dec.passed is True


def test_allows_fnr_high_improvement():
    cand = _report(fnr_high=0.01)   # 개선
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base)
    assert dec.passed is True


# ── f1 회귀 차단 ─────────────────────────────────────────────────────────────


def test_blocks_f1_regression():
    cand = _report(fnr_high=0.04, f1_macro=0.60)   # fnr는 좋아졌지만 f1 폭락
    base = _report(fnr_high=0.05, f1_macro=0.85)
    dec = evaluate_deploy_gate(cand, base, f1_drop_tolerance=0.05)
    assert dec.passed is False
    assert "f1_regression" in dec.reason


def test_allows_f1_within_tolerance():
    cand = _report(fnr_high=0.04, f1_macro=0.81)   # -0.04 ≤ tol 0.05
    base = _report(fnr_high=0.05, f1_macro=0.85)
    dec = evaluate_deploy_gate(cand, base, f1_drop_tolerance=0.05)
    assert dec.passed is True


# ── degenerate 가드 ──────────────────────────────────────────────────────────


def test_blocks_degenerate_all_one_class():
    # 4x4 cm: 모든 예측이 0번 열(TS)로 — '전부 TS' degenerate
    cm = [[10, 0, 0, 0], [10, 0, 0, 0], [10, 0, 0, 0], [10, 0, 0, 0]]
    cand = _report(fnr_high=0.0, f1_macro=0.9, cm=cm)   # fnr_high=0으로 게이밍 시도
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is False
    assert "degenerate" in dec.reason


def test_non_degenerate_cm_ok():
    cm = [[8, 1, 1, 0], [1, 7, 1, 1], [0, 1, 8, 1], [0, 0, 1, 9]]
    cand = _report(fnr_high=0.05, f1_macro=0.85, cm=cm)
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is True


# ── fail-CLOSED: fnr_high 부재 ───────────────────────────────────────────────


def test_blocks_when_fnr_high_missing():
    cand = {"f1_macro": 0.9}   # fnr_high 없음 — 검증 불가
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is False
    assert "fnr_high_present" in dec.reason


# ── 객체/딕셔너리 호환 (TrainReport 류) ──────────────────────────────────────


def test_accepts_object_with_to_dict():
    class FakeReport:
        def to_dict(self):
            return {"fnr_high": 0.03, "f1_macro": 0.88}

    dec = evaluate_deploy_gate(FakeReport(), None)
    assert dec.passed is True


def test_accepts_dataclass_like_dict_attr():
    class Bag:
        def __init__(self):
            self.fnr_high = 0.03
            self.f1_macro = 0.88

    dec = evaluate_deploy_gate(Bag(), None)
    assert dec.passed is True


def test_decision_to_dict_serializable():
    dec = evaluate_deploy_gate(_report(), _report())
    d = dec.to_dict()
    assert d["passed"] is True
    assert isinstance(d["checks"], list) and d["checks"]
    assert all({"name", "passed", "detail"} <= set(c) for c in d["checks"])


# ── 앵커 연결 (외부 사실 앵커 측정 미탐을 게이트에 주입) ─────────────────────────

from lloydk.modules.m6_evaluation.deploy_gate import summarize_anchor_high_grade  # noqa: E402


def _anchor_report(cards):
    """build_eval_cards.to_dict() 형태의 최소 앵커 리포트."""
    return {"cards": [{"slice": s, "grade": g, "underclass_fnr": f, "verdict": v}
                      for (s, g, f, v) in cards]}


def test_anchor_high_grade_fail_blocks_gate():
    # 앵커가 고등급(TS) 미탐 FAIL을 보이면 합성 게이트가 통과해도 hard block.
    anchor = _anchor_report([("anchor_nkt", "TS", 0.30, "FAIL"),
                             ("anchor_nkt", "S1", 0.0, "PASS")])
    dec = evaluate_deploy_gate(_report(fnr_high=0.0, f1_macro=0.9), _report(),
                               anchor_report=anchor)
    assert dec.passed is False
    assert "anchor_high_grade_miss" in dec.reason


def test_anchor_inconclusive_does_not_block_binary_gate():
    # INCONCLUSIVE는 측정불가 — 이진 게이트를 깨지 않는다(자동활성 veto는 locked-eval 소관).
    anchor = _anchor_report([("anchor_nkt", "TS", 0.03, "INCONCLUSIVE"),
                             ("holdout_gold", "S1", 0.0, "INCONCLUSIVE")])
    dec = evaluate_deploy_gate(_report(), _report(), anchor_report=anchor)
    assert dec.passed is True
    chk = [c for c in dec.checks if c.name == "anchor_high_grade_miss"][0]
    assert chk.passed is True


def test_anchor_pass_recorded_as_check():
    anchor = _anchor_report([("anchor_nkt", "S1", 0.0, "PASS")])
    dec = evaluate_deploy_gate(_report(), _report(), anchor_report=anchor)
    assert dec.passed is True
    assert any(c.name == "anchor_high_grade_miss" and c.passed for c in dec.checks)


def test_anchor_no_high_grade_cards_skips():
    # 고등급(TS/S1) 카드가 없으면(예: S2/S3만) 앵커 미탐 검사 생략.
    anchor = _anchor_report([("holdout_gold", "S2", 0.0, "INCONCLUSIVE"),
                             ("holdout_gold", "S3", 0.0, "N/A")])
    dec = evaluate_deploy_gate(_report(), _report(), anchor_report=anchor)
    chk = [c for c in dec.checks if c.name == "anchor_high_grade_miss"][0]
    assert chk.passed is True and "검사 생략" in chk.detail


def test_anchor_report_none_preserves_behavior():
    # anchor_report 미제공(기본 None) → 앵커 체크 자체가 없음(동작 보존).
    dec = evaluate_deploy_gate(_report(), _report())
    assert not any(c.name == "anchor_high_grade_miss" for c in dec.checks)


def test_summarize_anchor_high_grade_counts():
    anchor = _anchor_report([("a", "TS", 0.3, "FAIL"), ("a", "S1", 0.0, "PASS"),
                             ("a", "S2", 0.0, "FAIL")])  # S2는 고등급 아님 → 제외
    s = summarize_anchor_high_grade(anchor)
    assert s["high_grade_cards"] == 2 and s["fail"] == 1 and s["pass"] == 1
    assert s["fail_cells"] == ["a/TS"]
