"""회원사 등급 표기 → ICD 값 매핑표를 읽고 옮긴다.

■ 왜 이것이 필요한가 (2026-09-12 실측)

FUN-004 의 "사내 규정을 RAG 로 적용해 등급분류 보완" 설계안은 **규정집을 읽어 등급 매핑표를
채운다**는 전제 위에 있었다. 그 전제를 공개 규정 3건으로 시험했더니 대조에 정작 필요한 두 열이
**0/11** 이었다(우리 4등급 매핑 · 등급별 문서종류 예시). 규정은 등급을 "누설 시 피해 크기"라는
정성 문장으로만 정의하고, 어떤 문서가 그 등급인지는 비공개 별도 지침으로 넘긴다.

⇒ **매핑표는 규정에서 뽑는 산출물이 아니라 담당자가 채우는 입력물이다.**
   이 모듈은 그 입력물의 형식을 정하고, 채워진 것을 읽어 ICD 값으로 옮긴다.
   규정 조항 검색은 판정 입력이 아니라 **근거 표시 보조**로 내려간다.

■ 무엇을 옮기는가

관리성(M)은 본문에서 관측되지 않는다. 판단 근거는 **문서에 찍힌 표기와 열람 범위**이고,
그 둘을 ICD 어휘로 받는 자리가 이미 있다(`management_from_metadata`). 회원사는 자기 말로
등급을 부르므로("3급", "사내한", "부서한") 그 말을 ICD 어휘로 옮기는 표가 필요하다.

    회원사 표기 "대외비"   → security_marking = confidential
    회원사 표기 "사내한"   → access_scope     = all_employees
    회원사 표기 "3급"      → 회원사마다 다르다. **담당자만 안다.**

⚠ 우리가 추측해서 채우지 않는다. 모르면 비워 두고, 비어 있으면 그 문서는 M 을 못 받는다
   (= 상향 게이트가 안 걸린다). 추측해서 채우면 등급이 조용히 바뀐다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ICD §3.2·§3.3 의 어휘. 정본은 rule_engine 이고 여기서는 **검증에만** 쓴다.
VALID_MARKINGS = ("top_secret", "secret", "confidential", "none")
VALID_SCOPES = ("approved_only", "designated", "department", "all_employees")

# 담당자가 채워야 하는 것 — 규정만으로는 못 채운다고 실측된 네 가지.
REQUIRED_SECTIONS = ("markings", "scopes", "document_types", "lowest_grade_meaning")


@dataclass
class MappingIssue:
    """검증에서 걸린 것 하나. 사람에게 그대로 보여 줄 문장을 담는다."""

    section: str
    key: str
    message: str


@dataclass
class OrgMapping:
    org_id: str
    source: str = ""
    markings: dict[str, str] = field(default_factory=dict)
    scopes: dict[str, str] = field(default_factory=dict)
    document_types: dict[str, list[str]] = field(default_factory=dict)
    lowest_grade_meaning: str = ""
    notes: str = ""

    # ── 옮기기 ───────────────────────────────────────────────────────────────
    def to_marking(self, raw: object) -> str | None:
        """회원사 표기를 ICD security_marking 으로. 모르면 None — 추측하지 않는다."""
        key = str(raw or "").strip()
        return self.markings.get(key) or self.markings.get(key.lower()) or None

    def to_scope(self, raw: object) -> str | None:
        """회원사 열람범위 표현을 ICD access_scope 로. 모르면 None."""
        key = str(raw or "").strip()
        return self.scopes.get(key) or self.scopes.get(key.lower()) or None

    def translate(self, raw_marking: object = None, raw_scope: object = None) -> dict:
        """문서 하나의 회원사 표기를 ICD 값으로 옮긴 결과.

        옮기지 못한 것은 `unmapped` 에 남긴다 — 조용히 비우면 왜 M 이 안 붙었는지
        나중에 알 수 없다.
        """
        marking = self.to_marking(raw_marking)
        scope = self.to_scope(raw_scope)
        unmapped = []
        if raw_marking and marking is None:
            unmapped.append(f"security_marking:{raw_marking}")
        if raw_scope and scope is None:
            unmapped.append(f"access_scope:{raw_scope}")
        return {"security_marking": marking, "access_scope": scope, "unmapped": unmapped}


def _rows(block: object) -> dict[str, Any]:
    """표 한 칸의 내용. `_` 로 시작하는 키는 사람에게 주는 설명이라 건너뛴다.

    ⚠ 이 거르기가 없으면 양식 안의 안내문("_설명": "…")이 매핑 한 줄로 읽혀
      검증에서 '쓸 수 없는 값' 으로 걸린다 — 담당자는 자기가 뭘 잘못했는지 알 수 없다.
    """
    if not isinstance(block, dict):
        return {}
    return {str(k): v for k, v in block.items() if not str(k).startswith("_")}


def load(path: str | Path) -> OrgMapping:
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    return OrgMapping(
        org_id=str(data.get("org_id") or ""),
        source=str(data.get("source") or ""),
        markings={k: str(v) for k, v in _rows(data.get("markings")).items()},
        scopes={k: str(v) for k, v in _rows(data.get("scopes")).items()},
        document_types={k: list(v) for k, v in _rows(data.get("document_types")).items()},
        lowest_grade_meaning=str(data.get("lowest_grade_meaning") or ""),
        notes=str(data.get("notes") or ""),
    )


def validate(mapping: OrgMapping) -> list[MappingIssue]:
    """채워진 매핑표를 검사한다. 빈 목록이면 쓸 수 있다.

    ⚠ "채워져 있는가"만 보지 않는다. **ICD 어휘 밖의 값**을 잡아야 한다 —
       담당자가 우리 어휘를 모르고 자기 말로 적으면 조용히 무시되어 M 이 영영 안 붙는다.
    """
    issues: list[MappingIssue] = []
    if not mapping.org_id.strip():
        issues.append(MappingIssue("org_id", "", "회원사 식별자가 비어 있습니다"))

    if not mapping.markings:
        issues.append(MappingIssue(
            "markings", "", "문서에 찍히는 표기를 한 줄도 적지 않았습니다 — "
            "표기를 쓰지 않는 회사라면 'none' 으로 한 줄 적어 주십시오"))
    for raw, icd in mapping.markings.items():
        if icd not in VALID_MARKINGS:
            issues.append(MappingIssue(
                "markings", raw,
                f"'{icd}' 는 쓸 수 없는 값입니다. {' · '.join(VALID_MARKINGS)} 중에서 고르십시오"))

    if not mapping.scopes:
        issues.append(MappingIssue(
            "scopes", "", "열람 범위 표현을 한 줄도 적지 않았습니다"))
    for raw, icd in mapping.scopes.items():
        if icd not in VALID_SCOPES:
            issues.append(MappingIssue(
                "scopes", raw,
                f"'{icd}' 는 쓸 수 없는 값입니다. {' · '.join(VALID_SCOPES)} 중에서 고르십시오"))

    if not mapping.document_types:
        issues.append(MappingIssue(
            "document_types", "",
            "등급별 실제 문서종류 예시가 없습니다 — 규정에는 없고 담당자만 아는 항목입니다"))
    if not mapping.lowest_grade_meaning.strip():
        issues.append(MappingIssue(
            "lowest_grade_meaning", "",
            "가장 낮은 등급이 대외 공개인지 사내 전원 공개인지 적어 주십시오 — "
            "둘은 등급이 다릅니다"))
    return issues


def coverage(mapping: OrgMapping) -> dict:
    """얼마나 채워졌는가 — 분모와 함께 낸다."""
    filled = sum(1 for section in REQUIRED_SECTIONS if getattr(mapping, section))
    return {
        "sections_filled": filled,
        "sections_required": len(REQUIRED_SECTIONS),
        "markings": len(mapping.markings),
        "scopes": len(mapping.scopes),
        "document_type_grades": len(mapping.document_types),
        "usable": not validate(mapping),
    }
