"""조항 종류 태깅 — 제목 키워드 규칙(결정형). 화면에서 관리자가 고칠 수 있다.

종류와 표시 기본값:
    general    총칙·부칙(목적·적용 범위·정의·기본 원칙·책임·경과 조치)   표시 안 함
    procedure  등급 결정·변경 절차(기한·잠정·재분류·하향·대장)  표시 안 함
    grade_def  등급 정의("… 등급", "등급의 구분")           표시 안 함
    handling   취급 기준(문서 종류·열람·반출·외부 제공·업무 영역별) **표시**
    other      관리자가 지정                              관리자 결정

왜 앞의 셋을 표시에서 뺐나: 시험에서 "대외비 정의" 같은 조항이 문서 옆에 나열되어 검수자를 그 등급 쪽으로
끄는 오도(표본 20건 중 24%)의 주된 원인이었고, 이 셋을 색인에서 빼자 0/20이 되었다(2026-09-25).

⚠ 검증 상태: 시연용 규정 한 벌(53조)에서 손으로 뺀 21개와 21/21 일치했다. **규칙을 그 규정의 제목을 보고
  만들었으므로 일치는 당연하다** — 다른 규정으로 재검증이 끝나기 전까지는 "초안 규칙"이다(설계서 W-03).
  틀려도 관리자가 조항 표에서 표시 대상을 바꿀 수 있다.
"""

from __future__ import annotations

import re

KIND_GENERAL = "general"
KIND_PROCEDURE = "procedure"
KIND_GRADE_DEF = "grade_def"
KIND_HANDLING = "handling"
KIND_OTHER = "other"
KINDS = (KIND_GENERAL, KIND_PROCEDURE, KIND_GRADE_DEF, KIND_HANDLING, KIND_OTHER)

KIND_LABELS_KO = {
    KIND_GENERAL: "총칙",
    KIND_PROCEDURE: "절차",
    KIND_GRADE_DEF: "등급 정의",
    KIND_HANDLING: "취급 기준",
    KIND_OTHER: "기타",
}

SOURCE_AUTO = "auto"
SOURCE_ADMIN = "admin"

_GENERAL_TITLES = ("목적", "적용 범위", "적용범위", "용어의 정의", "용어 정의", "기본 원칙", "기본원칙",
                   "다른 규정과의 관계", "위원회", "업무", "책임", "의무")
_PROC_RE = re.compile(r"(판단의 세부 기준|결정 절차|결정 기한|잠정 등급|재분류|하향|등급 대장|절차)")
_DEF_RE = re.compile(r"(등급의 구분|\S+ 등급)$|등급\)?$")


def tag_clause(title: str, chapter: str = "") -> str:
    """조항 제목·장 제목 → 종류. 제목이 없으면(문단·번호 모드) 취급 기준으로 둔다."""
    title = (title or "").strip()
    chapter = (chapter or "").strip()
    if "총칙" in chapter or re.match(r"^부\s*칙", chapter) or any(t in title for t in _GENERAL_TITLES):
        return KIND_GENERAL      # 부칙(시행일·경과 조치·종전 규정 폐지)도 행정 조항이라 근거가 될 수 없다
    if _PROC_RE.search(title):
        return KIND_PROCEDURE
    if _DEF_RE.search(title):
        return KIND_GRADE_DEF
    return KIND_HANDLING


def default_display(kind: str) -> bool:
    """표시 대상 기본값 — 취급 기준만. `other` 는 관리자가 정하기 전까지 표시하지 않는다."""
    return kind == KIND_HANDLING
