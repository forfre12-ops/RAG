"""[안전] 죽음의 나선 가드 음성테스트 — 교정 폭주가 재학습을 트리거하지 못함.

데스스파이럴 최약 고리는 admission 게이트다: 머신/미확정 교정이 재학습 트리거 카운트를
부풀려 '러버스탬프→악화→재학습' 루프를 돌리는 것. _correction_admissible(사람+finalized만)와
evaluate_retraining_need의 admission 필터가 단기 대량 교정 폭주를 막는지 고정한다.
(실제 활성화는 추가로 retrain_auto_activate=False + locked-eval 게이트가 막는다.)
"""

from __future__ import annotations

import contextlib
import datetime as dt

import pytest

from lloydk.modules.m6_evaluation import active_learning as al
from lloydk.modules.m6_evaluation.active_learning import evaluate_retraining_need
from lloydk.modules.m6_evaluation.corrections_rebuild import _correction_admissible


# ── admission 게이트(순수) ────────────────────────────────────────────────────

@pytest.mark.parametrize("reviewer,status,expected", [
    ("alice", "confirmed", True),       # 사람 + finalized
    ("alice", "corrected", True),       # 사람 + finalized
    ("alice", "staging", False),        # 사람이지만 미확정
    ("alice", "needs_second_review", False),  # 러버스탬프 직전(미확정)
    ("ai_assist_1", "confirmed", False),      # 머신 reviewer
    ("llm_judge_2", "corrected", False),      # 머신 reviewer
    ("public_gold", "confirmed", False),      # 플레이스홀더 reviewer
])
def test_correction_admissible(reviewer, status, expected):
    assert _correction_admissible(reviewer, status) is expected


def test_admissible_bypass_when_not_required():
    # require_human_confirmed=False(특수경로) → 전부 허용(옛 동작).
    assert _correction_admissible("ai_assist_1", "staging", require_human_confirmed=False) is True


# ── evaluate_retraining_need 데스스파이럴 시나리오 ────────────────────────────

def _rows(n, *, direction="underclass", reviewer="alice", status="confirmed"):
    base = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    return [
        (i + 1, direction, reviewer, base + dt.timedelta(seconds=i), status)
        for i in range(n)
    ]


def _patch_db(monkeypatch, rows):
    @contextlib.contextmanager
    def _scope():
        class _Res:
            def all(self_):
                return rows

        class _DB:
            def execute(self_, q):  # noqa: ARG002
                return _Res()

        yield _DB()

    monkeypatch.setattr(al, "session_scope", _scope)


def test_machine_correction_burst_does_not_trigger(monkeypatch):
    # 단기 100건 머신 underclass 폭주 → admission 전량 제외 → 트리거 안 함.
    _patch_db(monkeypatch, _rows(100, reviewer="ai_assist_bot", status="confirmed"))
    st = evaluate_retraining_need()
    assert st.pending_underclass == 0
    assert st.retrain_status == "OK"


def test_unfinalized_human_burst_does_not_trigger(monkeypatch):
    # 100건 사람 교정이지만 미확정(staging) → admission 제외 → 트리거 안 함.
    _patch_db(monkeypatch, _rows(100, reviewer="alice", status="staging"))
    st = evaluate_retraining_need()
    assert st.unconsumed_total == 0
    assert st.retrain_status == "OK"


@pytest.mark.parametrize("urgent", [15, 30, 50])
def test_machine_burst_never_urgent_regardless_of_threshold(monkeypatch, urgent):
    # 임계를 낮춰도 머신 폭주는 admission에서 0이라 URGENT 불가.
    _patch_db(monkeypatch, _rows(200, reviewer="llm_judge", status="confirmed"))
    st = evaluate_retraining_need(urgent_underclass_threshold=urgent)
    assert st.retrain_status == "OK"


def test_admissible_underclass_below_threshold_ok(monkeypatch):
    # 사람+finalized 9건(< urgent 10) → 아직 트리거 안 함.
    _patch_db(monkeypatch, _rows(9, reviewer="alice", status="confirmed"))
    st = evaluate_retraining_need()  # urgent 기본 10
    assert st.pending_underclass == 9
    assert st.retrain_status == "OK"


def test_admissible_underclass_at_threshold_urgent(monkeypatch):
    # 사람+finalized 12건(>= urgent 10) → URGENT (admissible 교정엔 정상 발동).
    _patch_db(monkeypatch, _rows(12, reviewer="alice", status="confirmed"))
    st = evaluate_retraining_need()
    assert st.pending_underclass == 12
    assert st.retrain_status == "URGENT_RETRAIN"
