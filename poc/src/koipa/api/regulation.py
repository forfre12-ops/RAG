"""규정 참고 표시 API — 회원사 규정 등록·관리 + 검수 화면의 관련 규정 조회 (설계서 §2.6).

⛔ 등급을 바꾸지 않는다. 라우터는 `regulation_reference_enabled` 가 켜졌을 때만 붙는다(api/app.py).
오류는 **HTTP 상태로만** 표현한다(이 시스템은 심볼릭 오류코드를 운영하지 않는다).

권한
    쓰기(등록·활성화·보관·삭제·조항 수정)  admin · kl_backend
    읽기(목록·상세·조항·문서별 조회)        admin · reviewer · kl_backend
    공유 API 키(system 역할)는 둘 다 못 쓴다.
"""

from __future__ import annotations

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile

from koipa.api._jwt_auth import require_auth
from koipa.api._rbac import require_role
from koipa.api.rate_limit import limiter
from koipa.config import settings
from koipa.regulation.index import EvidenceItem
from koipa.schemas.regulation import (
    ActivateRequest,
    ClauseItem,
    ClauseListResponse,
    ClausePatchRequest,
    EvidenceClauseRef,
    EvidenceItemModel,
    EvidenceRegulationRef,
    PreviewRequest,
    PreviewResponse,
    PreviewResult,
    RegisterResponse,
    RegulationDetail,
    RegulationEvidenceResponse,
    RegulationListResponse,
)
from koipa.services.regulation_evidence_service import (
    EvidenceResult,
    RegulationEvidenceService,
    get_regulation_evidence_service,
)
from koipa.services.regulation_service import (
    RegulationError,
    RegulationService,
    get_regulation_service,
)

router = APIRouter(tags=["regulation"], dependencies=[Depends(require_auth)])

_WRITE = ("admin", "kl_backend")
_READ = ("admin", "reviewer", "kl_backend")


def _who(auth: dict) -> tuple[Optional[str], Optional[str]]:
    """감사 신원 — JWT sub(서명 = 위조 불가)가 있으면 그것. 공유 키는 개별 신원이 없다."""
    claims = auth.get("claims")
    sub = getattr(claims, "sub", None) if claims is not None else None
    return sub, auth.get("actor_role")


def _http(exc: RegulationError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _items(items: list[EvidenceItem]) -> list[EvidenceItemModel]:
    return [EvidenceItemModel(
        regulation=EvidenceRegulationRef(reg_id=i.rgltn_id, name=i.rgltn_nm, version_label=i.ver_lbl_nm),
        clause=EvidenceClauseRef(clause_id=i.clause_id, article_no=i.article_no, title=i.title),
        sentences=list(i.sentences), is_grade_list=i.is_grade_list) for i in items]


def _evidence_response(r: EvidenceResult) -> RegulationEvidenceResponse:
    return RegulationEvidenceResponse(doc_id=r.doc_id, indexed=r.indexed, reason=r.reason, items=_items(r.items))


# ── 규정 등록·관리 ─────────────────────────────────────────────────────────

@router.post(
    "/regulations",
    response_model=RegisterResponse,
    status_code=202,
    summary="규정 파일 등록 — 색인은 비동기(같은 파일이면 200 + 기존 reg_id)",
)
async def register_regulation(
    response: Response,
    file: UploadFile = File(...),
    name: str = Form(..., description="규정명. 같은 규정명의 판들이 한 계열이다"),
    version_label: str = Form(..., description="판 표기(예 v3.1)"),
    effective_date: Optional[str] = Form(default=None, description="시행일 YYYY-MM-DD(선택)"),
    auth: dict = Depends(require_role(*_WRITE)),
    svc: RegulationService = Depends(get_regulation_service),
) -> RegisterResponse:
    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared = getattr(file, "size", None)
    if declared is not None and declared > max_bytes:
        raise HTTPException(status_code=413, detail=f"file too large: {declared} > {max_bytes} bytes")
    body = await file.read()
    actor_id, actor_role = _who(auth)
    try:
        res = svc.register(data=body, filename=file.filename or "unknown", name=name, version_label=version_label,
                           effective_date=effective_date, actor_id=actor_id, actor_role=actor_role)
    except RegulationError as exc:
        raise _http(exc) from exc
    if res.duplicate:
        response.status_code = 200
    return RegisterResponse(reg_id=res.reg_id, status=res.status, duplicate=res.duplicate)


@router.get("/regulations", response_model=RegulationListResponse, summary="규정 목록")
def list_regulations(
    status: Optional[str] = Query(default=None, description="indexing | ready | active | archived | failed"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_role(*_READ)),
    svc: RegulationService = Depends(get_regulation_service),
) -> RegulationListResponse:
    try:
        return RegulationListResponse(**svc.list(status=status, limit=limit, offset=offset))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.get("/regulations/{reg_id}", response_model=RegulationDetail, summary="규정 상세·색인 진행")
def get_regulation(
    reg_id: uuid.UUID,
    auth: dict = Depends(require_role(*_READ)),
    svc: RegulationService = Depends(get_regulation_service),
) -> RegulationDetail:
    try:
        return RegulationDetail(**svc.get(str(reg_id)))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.get("/regulations/{reg_id}/clauses", response_model=ClauseListResponse, summary="규정의 조항 표")
def list_clauses(
    reg_id: uuid.UUID,
    kind: Optional[str] = Query(default=None, description="general | procedure | grade_def | handling | other"),
    display: Optional[bool] = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    auth: dict = Depends(require_role(*_READ)),
    svc: RegulationService = Depends(get_regulation_service),
) -> ClauseListResponse:
    try:
        return ClauseListResponse(**svc.list_clauses(str(reg_id), kind=kind, display=display, limit=limit, offset=offset))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.patch("/regulations/{reg_id}/clauses/{clause_id}", response_model=ClauseItem,
              summary="조항의 표시 대상·종류 수정")
def patch_clause(
    reg_id: uuid.UUID,
    clause_id: uuid.UUID,
    req: ClausePatchRequest,
    auth: dict = Depends(require_role(*_WRITE)),
    svc: RegulationService = Depends(get_regulation_service),
) -> ClauseItem:
    actor_id, actor_role = _who(auth)
    try:
        return ClauseItem(**svc.update_clause(str(reg_id), str(clause_id), display=req.display, kind=req.kind,
                                              actor_id=actor_id, actor_role=actor_role))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.post("/regulations/{reg_id}/preview", response_model=PreviewResponse,
             summary="활성화 전 미리보기 — 이 규정 하나로 문서마다 어떻게 보이는지")
def preview_regulation(
    reg_id: uuid.UUID,
    req: PreviewRequest,
    auth: dict = Depends(require_role(*_WRITE)),
    ev: RegulationEvidenceService = Depends(get_regulation_evidence_service),
) -> PreviewResponse:
    try:
        results = ev.preview(str(reg_id), req.doc_ids)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="regulation not found") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"미리보기를 할 수 없습니다: {type(exc).__name__}") from exc
    return PreviewResponse(reg_id=str(reg_id), results=[
        PreviewResult(doc_id=r.doc_id, indexed=r.indexed, reason=r.reason, items=_items(r.items)) for r in results])


@router.post("/regulations/{reg_id}/activate", response_model=RegulationDetail,
             summary="활성화 — 적용 대상 확인 필수. 같은 규정명의 이전 활성 판은 보관된다")
def activate_regulation(
    reg_id: uuid.UUID,
    req: ActivateRequest,
    auth: dict = Depends(require_role(*_WRITE)),
    svc: RegulationService = Depends(get_regulation_service),
) -> RegulationDetail:
    actor_id, actor_role = _who(auth)
    try:
        return RegulationDetail(**svc.activate(str(reg_id), scope_confirmed=req.scope_confirmed,
                                               scope_note=req.scope_note, actor_id=actor_id, actor_role=actor_role))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.post("/regulations/{reg_id}/archive", response_model=RegulationDetail, summary="보관(사용 중지)")
def archive_regulation(
    reg_id: uuid.UUID,
    auth: dict = Depends(require_role(*_WRITE)),
    svc: RegulationService = Depends(get_regulation_service),
) -> RegulationDetail:
    actor_id, actor_role = _who(auth)
    try:
        return RegulationDetail(**svc.archive(str(reg_id), actor_id=actor_id, actor_role=actor_role))
    except RegulationError as exc:
        raise _http(exc) from exc


@router.delete("/regulations/{reg_id}", status_code=204, summary="삭제 — 조항·문장·원본이 함께 지워진다(사용 중인 판 제외)")
def delete_regulation(
    reg_id: uuid.UUID,
    auth: dict = Depends(require_role(*_WRITE)),
    svc: RegulationService = Depends(get_regulation_service),
) -> Response:
    actor_id, actor_role = _who(auth)
    try:
        svc.delete(str(reg_id), actor_id=actor_id, actor_role=actor_role)
    except RegulationError as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


# ── 문서별 관련 규정(검수 화면) ────────────────────────────────────────────────

@router.get(
    "/documents/{doc_id}/regulation-evidence",
    response_model=RegulationEvidenceResponse,
    summary="이 문서와 관련된 규정 원문 문장(참고용 — 등급 판정 근거가 아니다)",
)
@limiter.limit("120/minute")
def regulation_evidence(
    request: Request,
    doc_id: uuid.UUID,
    max_items: Optional[int] = Query(default=None, ge=1, le=3),
    auth: dict = Depends(require_role(*_READ)),
    ev: RegulationEvidenceService = Depends(get_regulation_evidence_service),
) -> RegulationEvidenceResponse:
    """items 가 비어 있어도 **오류가 아니다** — reason 이 이유다(활성 규정 없음 · 문서 색인 전 · 임베더 불일치 …).

    문서가 없을 때만 404, 조회 인프라가 없을 때(임베더·DB)만 503 이다. 검수 화면은 이 실패로 멈추면 안 된다.
    """
    try:
        return _evidence_response(ev.find_for_document(str(doc_id), max_items=max_items))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="document not found") from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"관련 규정을 조회할 수 없습니다: {type(exc).__name__}") from exc


__all__ = ["router"]
