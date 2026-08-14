"""ICD §3.2·§3.3 관리성 매핑 — 배포본 계약 고정.

왜 이 테스트가 필요한가. 관리성은 본문에서 관측되지 않는다(실문서 업무문서는 중앙값
167~236자이고 관리성 어휘를 가진 것이 TS 9/33 · S2 1/17 뿐이다). 그래서 지금까지
표시되던 M 은 **등급에서 역산한 값**이었지 근거가 아니었다. 고객사 시스템이 주는
접근권한이 진짜 근거이고, 그 매핑은 KL 과 이미 합의된 ICD 에 있다.

세 가지를 고정한다.
  1. 매핑이 ICD 표와 정확히 같다
  2. 메타데이터가 없으면 unknown 이고 **기존 동작이 보존된다**
  3. all_employees(M=0) 하향이 **무음으로 새지 않는다** — 경고를 남긴다
"""
from __future__ import annotations

import pytest

from koipa.modules.m3_labeling.rule_engine import (
    grade_from_svm,
    management_from_metadata,
    management_from_metadata_dict,
)


# ICD §3.2 보안표시 · §3.3 접근범위 표 그대로.
@pytest.mark.parametrize(
    ("marking", "scope", "state", "level"),
    [
        ("top_secret", None, "present", 2),
        ("secret", None, "present", 2),
        ("confidential", None, "present", 1),
        ("none", "approved_only", "present", 2),
        ("none", "designated", "present", 1),
        ("none", "department", "present", 1),
        ("none", "all_employees", "proven_absent", 0),
        (None, None, "unknown", None),
        ("", "", "unknown", None),
        ("  Secret  ", None, "present", 2),          # 공백·대문자 허용
        ("unknown_value", "designated", "present", 1),  # 미지의 표기는 접근범위로
    ],
)
def test_icd_mapping(marking, scope, state, level):
    st, lv, reason = management_from_metadata(marking, scope)
    assert (st, lv) == (state, level)
    assert reason


def test_marking_wins_over_scope():
    """ICD §3.2 — 명시표기가 있으면 접근범위보다 우선한다."""
    st, lv, _ = management_from_metadata("secret", "all_employees")
    assert (st, lv) == ("present", 2)


def test_unknown_is_not_absent():
    """unknown 과 proven_absent 를 뭉치면 안 된다 — 등급이 달라진다."""
    unk_state, _, _ = management_from_metadata(None, None)
    abs_state, abs_lv, _ = management_from_metadata("none", "all_employees")
    assert unk_state == "unknown"
    assert abs_state == "proven_absent"
    # 보수적 완성에서 unknown 은 2 로 채워지고 proven_absent 는 0 이다.
    assert grade_from_svm(2, 2, 2) == "TS"
    assert grade_from_svm(2, 2, abs_lv) == "S1"


def test_dict_helper_tolerates_junk():
    for junk in (None, "", [], 0, {"other": "x"}):
        assert management_from_metadata_dict(junk)[0] == "unknown"


def test_all_employees_opens_s1():
    """S1 은 정본상 (2,2,0) 하나뿐이라 M=0 이 확정될 때만 나온다.

    관리성을 못 받으면 S1 은 구조적으로 도달 불가이고 실문서 판정면의 28% 가 어떤
    모델에서도 TS 가 된다. 이 경로가 그것을 여는 유일한 길이다.
    """
    s1_combos = [
        (s, v, m)
        for s in (0, 1, 2) for v in (0, 1, 2) for m in (0, 1, 2)
        if grade_from_svm(s, v, m) == "S1"
    ]
    assert s1_combos == [(2, 2, 0)]
    _, lv, _ = management_from_metadata("none", "all_employees")
    assert grade_from_svm(2, 2, lv) == "S1"


# ── ICD §3.1 source_type enum — 배포본이 실제로 인식하는지 고정 ────────────────
#
# 왜. 실측 2026-08-14(인수 팩 A/B 실행): ICD 대로 source_type="public" 을 보냈는데
# 출처 prior 가 한 번도 걸리지 않았다. `_PUBLIC_SOURCE_TOKENS` 에 ICD 값이 하나도
# 없었다 — 합의된 인터페이스를 구현이 지키지 않고 있었고 KL 시험에서 그대로 터질
# 자리였다. 문서끼리 대조해서는 못 잡는다. 값을 소스에 넣어 봐야 잡힌다.
@pytest.mark.parametrize(
    ("source_type", "is_public"),
    [
        ("public", True),                 # ICD: S=0 (Gate-1)
        ("registered_patent", True),      # ICD: S=0 (Gate-1)
        ("academic", True),               # ICD: S=0 (Gate-1)
        ("internal", False),              # ICD: 텍스트로 S 산정
        ("external_confidential", False),  # ICD: 텍스트로 S 산정
        (None, False),
        ("", False),
    ],
)
def test_icd_source_type_enum(source_type, is_public):
    from koipa.modules.m5_inference.pipeline import _source_prior_is_public

    assert _source_prior_is_public(source_type) is is_public


@pytest.mark.parametrize(
    "source_type",
    ["non-public", "nonpublic", "unpublished", "private", "internal_draft", "미공개", "비공개"],
)
def test_public_token_does_not_swallow_negations(source_type):
    """'public' 을 양성 토큰에 넣어도 부정형이 공개로 새면 안 된다."""
    from koipa.modules.m5_inference.pipeline import _source_prior_is_public

    assert _source_prior_is_public(source_type) is False


# ── ICD §3 규약값 적합성 ──────────────────────────────────────────────────────
#
# 왜. 규약 밖의 값이 와도 아무 신호가 없었다. source_type="public" 이 인식되지 않는
# 상태로 배포돼 있었는데 경고 한 줄 없었고, 인수 팩을 태워 보고서야 발견했다.
@pytest.mark.parametrize(
    ("metadata", "n_warn"),
    [
        ({"source_type": "public"}, 0),
        ({"source_type": "PUBLIC  "}, 0),          # 대소문자·공백 허용
        ({"source_type": "internal", "security_marking": "none",
          "access_scope": "department"}, 0),
        ({"source_type": "공개"}, 1),               # 규약 밖
        ({"security_marking": "top-secret"}, 1),   # 하이픈 오타
        ({"access_scope": "everyone"}, 1),
        ({}, 0),
        (None, 0),
        ({"source_type": None, "security_marking": ""}, 0),  # 빈값은 미전달과 같다
    ],
)
def test_icd_metadata_validation(metadata, n_warn):
    from koipa.modules.m3_labeling.rule_engine import validate_icd_metadata

    assert len(validate_icd_metadata(metadata)) == n_warn


def test_upward_gate_inputs_are_flagged_as_fnr_risk():
    """security_marking·access_scope 가 깨지면 상향이 안 걸려 미탐이 된다.

    그 경우에만 검수로 보내야 하므로 경고 문구로 구분이 되어야 한다.
    """
    from koipa.modules.m3_labeling.rule_engine import validate_icd_metadata

    for key in ("security_marking", "access_scope"):
        (w,) = validate_icd_metadata({key: "bogus"})
        assert "미탐 위험" in w, w
    # source_type 은 하향(cap) 입력이라 미탐 방향이 아니다.
    (w,) = validate_icd_metadata({"source_type": "bogus"})
    assert "미탐 위험" not in w, w
