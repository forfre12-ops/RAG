"""규정 한 판의 상태 — 정본. 표 `tad_rm_rgltn_mng.prcs_stts_cd` 의 값이다.

    indexing ──(색인 성공)──▶ ready ──(활성화)──▶ active ──(보관 / 같은 규정명 새 판 활성화)──▶ archived
        │                       │                                                              │
        └──(색인 실패)──▶ failed  └──(삭제: 보관·실패 판만)                    archived ──(재활성화)──▶ active

같은 규정명의 활성 판은 하나뿐이다 — 활성화하면 같은 트랜잭션에서 이전 활성 판이 archived 가 된다.
`ready` 는 "색인이 끝났고 아직 활성화 전(미리보기 가능)"이다.
"""

from __future__ import annotations

INDEXING = "indexing"
READY = "ready"
ACTIVE = "active"
ARCHIVED = "archived"
FAILED = "failed"

ALL = (INDEXING, READY, ACTIVE, ARCHIVED, FAILED)

# 화면·오류 문구에 쓰는 말 — 상태값(영문)을 사용자에게 그대로 보이지 않는다.
LABEL_KO = {INDEXING: "분석 중", READY: "사용 전", ACTIVE: "사용 중", ARCHIVED: "보관", FAILED: "분석 실패"}

# 활성화할 수 있는 상태(재활성화 = 보관 판 되살리기 포함)
ACTIVATABLE = (READY, ARCHIVED)
# 삭제할 수 있는 상태 — 사용 중이거나 색인 중인 판은 지우지 못한다
DELETABLE = (ARCHIVED, FAILED, READY)
# 조항 표시 대상을 고칠 수 있는 상태(색인 중에는 조항 행이 아직 만들어지는 중이다)
EDITABLE = (READY, ACTIVE, ARCHIVED)
# 조회 캐시에 올라가는 상태
SERVING = (ACTIVE,)


def label(status: str) -> str:
    return LABEL_KO.get(status, "알 수 없음")


def can_activate(status: str) -> bool:
    return status in ACTIVATABLE


def can_delete(status: str) -> bool:
    return status in DELETABLE


def can_edit(status: str) -> bool:
    return status in EDITABLE
