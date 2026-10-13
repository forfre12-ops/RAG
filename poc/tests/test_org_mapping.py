"""회원사 등급 매핑표 — 읽기·검증·옮기기.

왜 이 시험이 있는가(2026-09-12). FUN-004 의 "사내 규정 RAG 보완" 설계안은 규정집을 읽어
등급 매핑표를 채운다는 전제였는데, 공개 규정 3건으로 시험하니 대조에 필요한 두 열이
**0/11** 이었다. 매핑표는 규정의 산출물이 아니라 **담당자가 채우는 입력물**이다.

그래서 받은 표를 그대로 믿으면 안 된다. 어휘 밖의 값이 섞이면 조용히 무시되고, 그 문서는
관리성(M)을 영영 못 받는다 — 정본 조합상 1급비밀이 나오는 길은 (2,2,0) 하나뿐이라
M 이 없으면 상위 등급이 구조적으로 도달 불가가 된다. 조용한 실패를 막는 것이 이 시험의 몫이다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.org_mapping import (
    OrgMapping,
    coverage,
    load,
    validate,
)

_POC = Path(__file__).resolve().parents[1]
TEMPLATE = _POC / "datasets" / "mapping_tables" / "TEMPLATE.json"


def _full(**overrides) -> OrgMapping:
    base = dict(
        org_id="member-0142",
        source="문서보안규정 v3.1",
        markings={"대외비": "confidential", "표기 없음": "none"},
        scopes={"부서 한정": "department"},
        document_types={"대외비": ["내부 운영 절차서"]},
        lowest_grade_meaning="사내 전원 공개",
    )
    base.update(overrides)
    return OrgMapping(**base)


def test_template_is_usable_as_shipped():
    """양식 그대로도 검사를 통과해야 한다 — 통과 못 하면 담당자가 첫 줄에서 막힌다."""
    assert validate(load(TEMPLATE)) == []


def test_underscore_keys_are_guidance_not_rows(tmp_path):
    """양식 안의 안내문('_설명')이 매핑 한 줄로 읽히면 안 된다."""
    path = tmp_path / "m.json"
    path.write_text(json.dumps({
        "org_id": "x", "markings": {"_설명": "여기에 적으십시오", "대외비": "confidential"},
        "scopes": {"부서": "department"}, "document_types": {"대외비": ["a"]},
        "lowest_grade_meaning": "사내 전원 공개",
    }, ensure_ascii=False), encoding="utf-8")
    mapping = load(path)
    assert "_설명" not in mapping.markings
    assert validate(mapping) == []


@pytest.mark.parametrize("section,bad", [
    ("markings", {"대외비": "대외비"}),          # ICD 값이 아니라 회원사 말 그대로
    ("scopes", {"부서 한정": "부서만"}),
])
def test_values_outside_icd_vocabulary_are_caught(section, bad):
    """어휘 밖 값은 잡아야 한다 — 놓치면 그 문서가 조용히 M 을 못 받는다."""
    issues = validate(_full(**{section: bad}))
    assert any(i.section == section for i in issues)


def test_missing_document_types_is_reported():
    """규정에 없고 담당자만 아는 항목이다. 비었으면 비었다고 말해야 한다."""
    issues = validate(_full(document_types={}))
    assert any(i.section == "document_types" for i in issues)


def test_missing_lowest_grade_meaning_is_reported():
    issues = validate(_full(lowest_grade_meaning="  "))
    assert any(i.section == "lowest_grade_meaning" for i in issues)


def test_translate_maps_known_values():
    out = _full().translate("대외비", "부서 한정")
    assert out["security_marking"] == "confidential"
    assert out["access_scope"] == "department"
    assert out["unmapped"] == []


def test_unknown_value_is_not_guessed():
    """모르는 표기를 임의로 채우지 않는다 — 추측하면 등급이 조용히 바뀐다."""
    out = _full().translate("3급", "부서 한정")
    assert out["security_marking"] is None
    assert "security_marking:3급" in out["unmapped"]


def test_empty_input_produces_no_unmapped_noise():
    """값을 안 준 것과 못 옮긴 것은 다르다 — 안 준 것은 흔적을 남기지 않는다."""
    out = _full().translate(None, None)
    assert out["unmapped"] == []


def test_coverage_reports_denominator():
    cov = coverage(_full())
    assert cov["sections_required"] == 4
    assert cov["sections_filled"] == 4
    assert cov["usable"] is True
