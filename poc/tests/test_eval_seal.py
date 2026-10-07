"""평가셋 봉인 — 봉인 → 1회 개봉 측정 → 재봉인을 기계가 강제하는가.

왜 이 시험이 있는가(2026-09-09). 지금까지 봉인은 **관행**이었다. 파일 이름에 `sealed` 를
넣고 주석에 "⛔ 봉인이므로 쓰지 않는다"라고 적는 방식이라, 그 주석을 못 본 사람이 그냥
썼다. 감리 회신 5(6)이 절차를 약속했으므로 여기서 강제한다.

이 시험이 지키는 것은 **"한 번만 잰다"** 하나다. 같은 셋을 여러 번 재면 그 수치는
블라인드가 아니고, 블라인드가 아닌 수치를 블라인드라고 보고하면 그게 제일 나쁘다.
"""

from __future__ import annotations

import pytest

from koipa.eval_seal import (
    CONSUMED,
    OPENED,
    SEALED,
    UNSEALED,
    SealViolation,
    open_seal,
    require_measurable,
    seal,
    state_of,
)


@pytest.fixture
def evalset(tmp_path):
    p = tmp_path / "evalset.jsonl"
    p.write_text('{"label":"TS","text":"가"}\n{"label":"S3","text":"나"}\n', encoding="utf-8")
    return p


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "seals.jsonl"


def test_unsealed_sets_are_not_blocked(evalset, ledger):
    """봉인은 **선언한 것에만** 걸린다.

    모든 파일을 막으면 아무도 안 쓰고, 안 쓰이는 게이트는 없는 것과 같다.
    """
    assert state_of(evalset, ledger=ledger).state == UNSEALED
    require_measurable(evalset, ledger=ledger)      # 예외 없이 통과해야 한다


def test_sealed_set_cannot_be_measured(evalset, ledger):
    seal(evalset, reason="1차 기준선", owner="홍길동", ledger=ledger)
    assert state_of(evalset, ledger=ledger).state == SEALED
    with pytest.raises(SealViolation, match="봉인된 평가셋"):
        require_measurable(evalset, ledger=ledger)


def test_one_measurement_then_consumed(evalset, ledger):
    """개봉하면 **한 번** 잴 수 있고, 그 뒤로는 막힌다."""
    seal(evalset, reason="1차", owner="홍길동", ledger=ledger)
    open_seal(evalset, reason="1차 측정", owner="홍길동", ledger=ledger)
    assert state_of(evalset, ledger=ledger).state == OPENED

    require_measurable(evalset, owner="홍길동", ledger=ledger)
    assert state_of(evalset, ledger=ledger).state == CONSUMED

    with pytest.raises(SealViolation, match="이미 한 번 측정"):
        require_measurable(evalset, ledger=ledger)


def test_reseal_reopens_the_cycle(evalset, ledger):
    """다시 재려면 재봉인한다 — 그 사실이 원장에 남는 것이 요점이다."""
    seal(evalset, reason="1차", owner="홍길동", ledger=ledger)
    open_seal(evalset, reason="1차 측정", owner="홍길동", ledger=ledger)
    require_measurable(evalset, ledger=ledger)

    seal(evalset, reason="2차 — 모델 교체", owner="홍길동", ledger=ledger)
    open_seal(evalset, reason="2차 측정", owner="홍길동", ledger=ledger)
    require_measurable(evalset, ledger=ledger)      # 막히지 않는다
    assert state_of(evalset, ledger=ledger).events == 6


def test_changed_content_breaks_the_seal(evalset, ledger):
    """봉인해 놓고 내용을 고치면 '봉인된 셋으로 쟀다'가 거짓이 된다."""
    seal(evalset, reason="1차", owner="홍길동", ledger=ledger)
    evalset.write_text('{"label":"S1","text":"바뀜"}\n', encoding="utf-8")

    with pytest.raises(SealViolation, match="봉인 이후 내용이 바뀌었습니다"):
        open_seal(evalset, reason="1차 측정", owner="홍길동", ledger=ledger)


def test_reason_and_owner_are_required(evalset, ledger):
    """나중에 '누가 왜 열었나'를 답해야 한다 — 빈 값으로는 봉인·개봉이 안 된다."""
    with pytest.raises(SealViolation, match="사유와 담당자"):
        seal(evalset, reason="", owner="홍길동", ledger=ledger)
    seal(evalset, reason="1차", owner="홍길동", ledger=ledger)
    with pytest.raises(SealViolation, match="사유와 담당자"):
        open_seal(evalset, reason="1차 측정", owner="  ", ledger=ledger)


def test_open_twice_is_refused(evalset, ledger):
    seal(evalset, reason="1차", owner="홍길동", ledger=ledger)
    open_seal(evalset, reason="1차 측정", owner="홍길동", ledger=ledger)
    with pytest.raises(SealViolation, match="이미 열려 있습니다"):
        open_seal(evalset, reason="또", owner="홍길동", ledger=ledger)


def test_opening_a_never_sealed_path_is_refused(evalset, ledger):
    with pytest.raises(SealViolation, match="봉인된 적이 없는"):
        open_seal(evalset, reason="1차", owner="홍길동", ledger=ledger)


def test_holdout_eval_has_the_gate():
    """도구만 만들고 배선하지 않으면 수동 구간이 하나 늘 뿐이다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "eval_p1_holdout.py").read_text(
        encoding="utf-8"
    )
    assert "require_measurable" in src, "홀드아웃 평가에 봉인 게이트가 배선되지 않았다"
    assert "--ignore-seal" in src, "예비 측정용 우회구가 없으면 게이트를 통째로 끄게 된다"
