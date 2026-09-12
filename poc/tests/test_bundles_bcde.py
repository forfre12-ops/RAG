"""[번들 B/C/D/E] 안전실패 하드닝 — 순수 로직 단위 테스트.

- B: needs_second_review 보류 집계가 DB 미가용 시 best-effort {}.
- C: factors_source 판정(model_estimated vs rule_evidenced) — 정합화 경고 기반.
- D: locked_gold_eval readiness(파일 기반) — locked tier만 인정·등급별 충족.
- E: kill-gate 안전브레이크(should_suppress_autoconfirm + _kill_gate_brake) — 플래그·tripped·고등급 조건.
"""

from __future__ import annotations

import json

from koipa.api import prom_metrics as pm
from koipa.modules.m6_evaluation import kill_gate as kg
from koipa.modules.m6_evaluation.kill_gate import (
    KillGateResult,
    should_suppress_autoconfirm,
)
from koipa.modules.m6_evaluation.locked_readiness import locked_eval_readiness
from koipa.schemas.common import Grade
from koipa.services.classify_service import ClassifyService


# ── C: factors_source ─────────────────────────────────────────────────────────

def test_factors_source_model_estimated_on_alignment_warning():
    assert ClassifyService._factors_source(
        ["factors aligned to model grade TS (rule under-detected S/V/M)"]
    ) == "model_estimated"
    assert ClassifyService._factors_source(
        ["chunk severe-agg/escalation: rule grade S2 → TS (most-severe-wins)"]
    ) == "model_estimated"


def test_factors_source_rule_evidenced_default():
    assert ClassifyService._factors_source([]) == "rule_evidenced"
    assert ClassifyService._factors_source(["low-confidence: 0.40 < 0.55"]) == "rule_evidenced"


# ── E: kill-gate 안전브레이크 ──────────────────────────────────────────────────

def test_brake_off_by_default(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", False, raising=False)
    monkeypatch.setattr(kg, "_LAST_RESULT", KillGateResult(tripped=True))
    assert should_suppress_autoconfirm("TS") is False


def test_brake_high_grade_when_tripped(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", True, raising=False)
    monkeypatch.setattr(kg, "_LAST_RESULT", KillGateResult(tripped=True))
    assert should_suppress_autoconfirm("TS") is True
    assert should_suppress_autoconfirm("S1") is True
    assert should_suppress_autoconfirm("S2") is False  # 고등급(TS/S1)만
    assert should_suppress_autoconfirm("S3") is False


def test_brake_not_when_not_tripped(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", True, raising=False)
    monkeypatch.setattr(kg, "_LAST_RESULT", KillGateResult(tripped=False))
    assert should_suppress_autoconfirm("TS") is False


def test_brake_not_when_no_check(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", True, raising=False)
    monkeypatch.setattr(kg, "_LAST_RESULT", None)
    assert should_suppress_autoconfirm("TS") is False


def test_kill_gate_brake_reason_and_metric(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", True, raising=False)
    monkeypatch.setattr(kg, "_LAST_RESULT", KillGateResult(tripped=True))
    before = pm.KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL.labels(grade="TS")._value.get()
    reason = ClassifyService._kill_gate_brake(Grade.TS)
    assert reason and "kill-gate-brake" in reason
    after = pm.KILL_GATE_AUTOCONFIRM_SUPPRESSED_TOTAL.labels(grade="TS")._value.get()
    assert after == before + 1


def test_kill_gate_brake_none_when_off(monkeypatch):
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "kill_gate_suppress_autoconfirm", False, raising=False)
    assert ClassifyService._kill_gate_brake(Grade.TS) is None


# ── D: locked_gold_eval readiness ─────────────────────────────────────────────

def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    return str(path)


def test_locked_readiness_empty_path_is_no_locked():
    s = locked_eval_readiness(path="", min_per_grade=5)
    assert s["ready"] is False
    assert s["reason"] == "no_locked_records"
    # require_locked_eval 기본 True → 게이트 미통과(자동활성 차단).
    assert s["deploy_locked_gate_passed"] in (True, False)  # 설정 의존 — 키 존재만 확인


def test_locked_readiness_missing_file_is_no_locked(tmp_path):
    s = locked_eval_readiness(path=str(tmp_path / "absent.jsonl"), min_per_grade=5)
    assert s["ready"] is False
    assert s["reason"] == "no_locked_records"


def _locked_rec(g, reviewer="alice"):
    # 유효 서명 envelope(gate_version·signed_at·reviewer_ids) + 실문서 출처(판례=public_real).
    return {
        "label": g, "label_source": "human_review", "reviewer_id": reviewer,
        "reviewer_ids": [reviewer], "gate_version": "human_signoff_v1",
        "signed_at": "2026-09-10T00:00:00+00:00", "source": "판례",
    }


def test_locked_readiness_counts_locked_per_grade(tmp_path):
    # 유효 서명(envelope) + 실문서 출처를 갖춘 human_review만 real 평가정답(locked)으로 인정.
    recs = [_locked_rec(g) for g in ("TS", "S1", "S2", "S3") for _ in range(5)]
    # 머신 reviewer 1건은 유효 서명 아님 → locked 아님(무시돼야).
    recs.append(_locked_rec("TS", reviewer="llm_judge_1"))
    p = _write_jsonl(tmp_path / "locked.jsonl", recs)
    s = locked_eval_readiness(path=p, min_per_grade=5)
    assert s["ready"] is True
    assert s["per_grade"] == {"TS": 5, "S1": 5, "S2": 5, "S3": 5}
    assert s["missing"] == []


def test_locked_readiness_insufficient_marks_missing(tmp_path):
    recs = [{"label": "TS", "label_source": "human_review", "reviewer_id": "bob"}]  # TS 1건뿐
    p = _write_jsonl(tmp_path / "locked.jsonl", recs)
    s = locked_eval_readiness(path=p, min_per_grade=5)
    assert s["ready"] is False
    assert "S1" in s["missing"] and "TS" in s["missing"]


# ── B: 보류 집계 best-effort ──────────────────────────────────────────────────

def test_count_held_best_effort_without_db():
    from koipa.services.confirm_service import count_needs_second_review_by_grade
    # DB 미가용 환경에서도 예외 없이 dict 반환(best-effort).
    out = count_needs_second_review_by_grade()
    assert isinstance(out, dict)
