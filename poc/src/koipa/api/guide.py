"""POST /guide/documents + GET /guide/documents/{guide_id} — 가이드 문서 버전 이력.

⚠ 종전 "FUN-002" 표기는 정본 요구사항 추적표에 없는 번호였다(2026-09 정정) — 계약
요건이 아니라 부가 기능이다. 자세한 것은 services/guide_service.py 모듈 docstring.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from koipa.api._jwt_auth import require_auth
from koipa.api._rbac import require_role
from koipa.api.confirm import bind_authenticated_actor
from koipa.schemas.guide import (
    GuideUploadResponse,
    GuideVersionList,
    GuideVersionRegisterRequest,
)
from koipa.services.guide_service import GuideService

router = APIRouter(tags=["guide"], dependencies=[Depends(require_auth)])


# 가이드 문서 업로드는 전역 기준 문서를 바꾸는 변경성 작업 →
# admin/kl_backend로 제한. 버전 조회(GET)는 인증된 사용자면 허용(전역 네임스페이스).
@router.post(
    "/guide/documents",
    response_model=GuideUploadResponse,
    status_code=201,
)
def register_guide_version(
    req: GuideVersionRegisterRequest,
    auth: dict = Depends(require_role("admin", "kl_backend")),
):
    """가이드 **버전 등록** — 파일을 받지 않는다.

    [2026-09-05] 종전에는 multipart 로 파일을 필수로 받고 20MB 한도까지 검사한 뒤
    **버렸다**(서비스 머리말이 그렇게 적고 있었다: "받되 버린다"). 세 가지가 잘못이었다 —
    발주처가 파일을 올리면 무언가 보관·활용된다고 읽고, 버릴 바이트에 검사·읽기 비용을
    들이며, 무엇보다 **원문이 우리 서버 메모리를 한 번 지났다**(무반출 원칙에서 굳이 만들
    경로가 아니다). RTM 요건도 아니다.

    파일명은 사람이 어느 문서를 등록했는지 적는 선택 메타로만 남는다.
    """
    actor_obj = req.actor
    # [#13] actor_user_id 감사 신원을 인증 principal 로 확정(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(actor_obj, auth)
    return GuideService.get_instance().upload(
        guide_id=req.guide_id,
        version=req.version,
        effective_date=req.effective_date,
        change_summary=req.change_summary,
        actor_user_id=actor_obj.user_id,
        # tenant 제거: 격리는 KL 포털 전담 → 전역 네임스페이스로 적재.
        doc_type=req.doc_type,
        filename=req.filename,
    )


@router.get("/guide/documents/{guide_id}", response_model=GuideVersionList)
def list_guide_versions(guide_id: str):
    # tenant 제거: 격리는 KL 포털 전담 → 전역 네임스페이스 조회.
    res = GuideService.get_instance().list_versions(guide_id)
    if res is None:
        raise HTTPException(status_code=404, detail="guide_id not found")
    return res
