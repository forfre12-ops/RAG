"""[eval 라벨 경화] harden_eval_labels.adjudicate_disposition 결정적 producer 단위 테스트.

scripts/harden_eval_labels.py 는 CI/테스트 커버 0 이었다. 이 스크립트가 eval 홀드아웃
라벨을 바꾸는 **유일한 안전관련 결정론 변환**(분쟁 24건의 tier/라벨 판정, 특히 S2 dead-zone
floor 복원)이라, 파일 I/O 에서 순수함수로 추출한 adjudicate_disposition 을 직접 고정한다.
grade_from_svm_floored 자체는 test_rule_engine 이 커버하지만 assembler 내 사용은 무커버였다.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm_floored


@pytest.fixture(scope="module")
def hel():
    """scripts/harden_eval_labels.py 를 파일경로로 로드(scripts 는 sys.path 에 없음)."""
    script = Path(__file__).resolve().parents[1] / "scripts" / "harden_eval_labels.py"
    spec = importlib.util.spec_from_file_location("_harden_eval_labels", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_confirm_intent_keeps_label(hel):
    # cons==gold → 3출처 합의로 의도 재확인, 라벨 불변, anchor.
    tier, hard, disp = hel.adjudicate_disposition("S3", "S3", {"s": 1, "v": 1})
    assert (tier, hard, disp) == ("anchor", "S3", "confirm_intent")


def test_evidenced_upgrade_relabels(hel):
    # 근거 있는 상향(S2→S1, S1→TS) → adjudicated, 라벨=cons.
    assert hel.adjudicate_disposition("S2", "S1", {"s": 2, "v": 2}) == (
        "adjudicated", "S1", "upgrade_S2_to_S1",
    )
    assert hel.adjudicate_disposition("S1", "TS", {"s": 3, "v": 3}) == (
        "adjudicated", "TS", "upgrade_S1_to_TS",
    )


def test_dead_zone_floor_restore_to_S2(hel):
    # [핵심 안전 변환] cons==S3 & gold==S2 는 심판 M=0 아티팩트 → floor 재도출로 S2 복원.
    # 격리(quarantine) 아니라 anchor. svm(1,1)은 grade_from_svm_floored(1,1,0)=S2.
    tier, hard, disp = hel.adjudicate_disposition("S2", "S3", {"s": 1.4, "v": 1.2})
    assert tier == "anchor"          # dead-zone 은 격리하지 않는다 — 인버리언트
    assert hard == "S2"              # floor 복원 → S2
    assert disp.startswith("floor_restored_S2")


def test_dead_zone_restore_is_faithful_to_floored_rubric(hel):
    # 복원 라벨은 하드코딩이 아니라 grade_from_svm_floored(round s, round v, 0) 재도출과 동일.
    for s, v in [(1, 1), (2, 2), (3, 3), (2, 1)]:
        _, hard, _ = hel.adjudicate_disposition("S2", "S3", {"s": s, "v": v})
        assert hard == grade_from_svm_floored(s, v, 0)


def test_unresolved_conflict_quarantines(hel):
    # 상향도 dead-zone 도 아닌 잔여 불일치 → quarantine(사람 앵커 전까지 strict 제외).
    tier, hard, disp = hel.adjudicate_disposition("S1", "S3", {"s": 1, "v": 1})
    assert tier == "quarantine"
    assert hard == "S1"              # quarantine 은 gold(원 의도) 보존
    assert disp == "other_S1_to_S3"


def test_dead_zone_never_quarantined_invariant(hel):
    # 회귀 가드: gold=S2 & cons=S3 조합은 어떤 svm 이어도 절대 quarantine 되지 않는다
    # (floor 복원이 dead-zone 을 항상 흡수 — 이게 깨지면 S2 커버리지 갭이 되살아난다).
    for s in (1, 2, 3):
        for v in (1, 2, 3):
            tier, _, _ = hel.adjudicate_disposition("S2", "S3", {"s": s, "v": v})
            assert tier == "anchor", (s, v)
