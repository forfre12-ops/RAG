"""요소 모델(v8) 섀도 — **결정을 바꾸지 않는다**는 계약을 고정한다.

왜. 배포본은 등급 우선·요소 후행 구조이고(모델이 등급을 내고 S/V/M 을 거기 맞춘다)
v8 은 요소 우선이다. 두 구조를 바로 합치면 결정이 바뀌는데, 그 전에 두 모델이 얼마나
다른지를 알아야 한다. 모르고 거부 조건을 걸면 검수량이 얼마나 늘지 예측할 수 없다.

그래서 섀도는 계량만 한다. 이 파일은 그 계약이 깨지지 않게 못을 박는다.

가중치가 없는 환경(CI)에서도 돌도록, 모델이 필요한 검사는 게이트 로직만 직접 부른다.
"""
from __future__ import annotations

import pytest

from koipa.modules.m5_inference.factor_model import (
    CLS_ABSENT,
    CLS_LV1,
    CLS_LV2,
    CLS_UNKNOWN,
    FactorInference,
    apply_serving_gate,
    cls_to_worst,
    shadow_compare,
)


def _p(*codes: int, conf: float = 1.0) -> list:
    """주어진 클래스가 argmax 가 되는 확률분포 3개."""
    out = []
    for c in codes:
        row = [(1.0 - conf) / 3.0] * 4
        row[c] = conf
        out.append(row)
    return out


# ── 보수적 완성 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("code", "worst"),
    [(CLS_ABSENT, 0), (CLS_LV1, 1), (CLS_LV2, 2), (CLS_UNKNOWN, 2)],
)
def test_unknown_fills_to_worst_not_zero(code, worst):
    """unknown 을 0 으로 채우면 미탐이 열린다 — 최악값이어야 한다."""
    assert cls_to_worst(code) == worst


# ── 1층: 출처 ────────────────────────────────────────────────────────────────
def test_public_source_short_circuits_to_s3():
    p = apply_serving_gate((CLS_LV2, CLS_LV2, CLS_LV2), _p(2, 2, 2), source_is_public=True)
    assert p.serving_grade == "S3"
    assert p.auto_confirmable is True
    assert "layer1:source_public" in p.reasons


# ── 2층: 하향 단언 문턱 ───────────────────────────────────────────────────────
def test_low_confidence_absent_becomes_unknown():
    """absent 는 무음 미탐을 만드는 클래스다. 확신 없으면 유보로 내린다."""
    p = apply_serving_gate((CLS_ABSENT, CLS_LV2, CLS_LV2), _p(0, 2, 2, conf=0.6), kappa=0.99)
    assert p.named["secrecy"] == "unknown"
    assert p.serving_grade == "TS"  # 보수적 완성이 채워 고등급으로 간다


def test_low_confidence_lv1_also_blocked():
    """lv1 단언도 고등급을 깬다 — s=1 이면 s==2 조건이 무너져 S2 가 상한이다.

    실측 2026-08-14: absent 만 지켰더니 비밀놓침이 3 -> 23 으로 뛰었다.
    """
    blocked = apply_serving_gate((CLS_LV1, CLS_LV2, CLS_LV2), _p(1, 2, 2, conf=0.6), kappa=0.99)
    assert blocked.named["secrecy"] == "unknown"
    assert blocked.serving_grade == "TS"
    # 확신이 충분하면 그대로 채택되고 등급이 내려간다.
    kept = apply_serving_gate((CLS_LV1, CLS_LV2, CLS_LV2), _p(1, 2, 2, conf=1.0), kappa=0.99)
    assert kept.named["secrecy"] == "lv1"
    assert kept.serving_grade == "S2"


def test_unknown_is_never_penalised_by_kappa():
    """유보에는 문턱이 없다 — 모르는 것을 모른다고 말할 길이 열려 있어야 한다."""
    p = apply_serving_gate((CLS_UNKNOWN, CLS_UNKNOWN, CLS_UNKNOWN), _p(3, 3, 3, conf=0.3))
    assert set(p.named.values()) == {"unknown"}
    assert p.serving_grade == "TS"


# ── 2.5층: 관리성을 문서 밖에서 ────────────────────────────────────────────────
def test_metadata_supplies_management_and_opens_s1():
    """S1 은 정본상 (2,2,0) 하나뿐이라 M 이 0 으로 확정될 때만 나온다."""
    base = apply_serving_gate((CLS_LV2, CLS_LV2, CLS_UNKNOWN), _p(2, 2, 3))
    assert base.serving_grade == "TS"  # M 미확인 -> 보수적 완성 -> TS
    opened = apply_serving_gate(
        (CLS_LV2, CLS_LV2, CLS_UNKNOWN), _p(2, 2, 3),
        metadata={"access_scope": "all_employees"},
    )
    assert opened.named["management"] == "proven_absent"
    assert opened.serving_grade == "S1"


def test_marking_beats_scope_per_icd():
    p = apply_serving_gate(
        (CLS_LV2, CLS_LV2, CLS_UNKNOWN), _p(2, 2, 3),
        metadata={"security_marking": "secret", "access_scope": "all_employees"},
    )
    assert p.named["management"] == "lv2"
    assert p.serving_grade == "TS"


# ── 3층: 자동확정 ────────────────────────────────────────────────────────────
def test_top_grade_is_auto_confirmable_without_confidence():
    """미탐은 낮게 본 오류이고 TS 는 최상단이라 정의상 미탐이 될 수 없다."""
    p = apply_serving_gate((CLS_UNKNOWN, CLS_UNKNOWN, CLS_UNKNOWN), _p(3, 3, 3, conf=0.3))
    assert p.serving_grade == "TS"
    assert p.auto_confirmable is True


def test_unsettled_low_grade_goes_to_review():
    p = apply_serving_gate((CLS_ABSENT, CLS_UNKNOWN, CLS_LV1), _p(0, 3, 1, conf=1.0), tau=0.99)
    assert p.serving_grade != "TS"
    assert p.auto_confirmable is False


# ── 섀도 대조 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("v5", "codes", "direction"),
    [
        ("TS", (CLS_LV2, CLS_LV2, CLS_LV2), "agree"),
        ("S2", (CLS_LV2, CLS_LV2, CLS_LV2), "factor_higher"),
        ("TS", (CLS_LV1, CLS_LV2, CLS_LV2), "factor_lower"),
    ],
)
def test_shadow_direction(v5, codes, direction):
    """방향이 중요하다 — factor_higher 는 v5 미탐 의심, factor_lower 는 과분류 의심."""
    p = apply_serving_gate(codes, _p(*codes, conf=1.0))
    assert shadow_compare(v5, p)["direction"] == direction


def test_missing_weights_disable_quietly():
    """가중치가 없으면 조용히 비활성 — 섀도 부재가 분류를 막으면 안 된다."""
    inf = FactorInference("artifacts/does-not-exist")
    assert inf.load() is False
    assert inf.available is False
    assert inf.predict("아무 문서") is None
    assert "not found" in (inf.load_error or "")
