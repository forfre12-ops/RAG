"""POST /golden/build + GET /golden/jobs/{id} — 통합 골든셋 빌더 (G3b).

빌드(생성·변경) = admin/kl_backend/system. 상태 조회 = reviewer 포함 broad.
정본(classification_gold.jsonl)은 직접 변경하지 않고 run-스코프 후보 파일에 쓴다.
human_review 승격은 별개 경로(import_review_corrections, 지재원 관리자).
"""
from __future__ import annotations

import hashlib
import hmac
import html as _html
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse

from koipa.api._jwt_auth import require_auth
from koipa.api._rbac import require_role
from koipa.api.confirm import bind_authenticated_actor, resolve_actor_user_id
from koipa.config import settings
from koipa.golden_tiers import human_reviewer_rejection_reason, is_human_reviewer
from koipa.services.job_store import get_default_store
from koipa.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenCorpusSummary,
    GoldenJobListResponse,
    GoldenJobSummary,
    GoldenRegisterRequest,
    ProxyGoldCandidateDecisionRequest,
    ProxyGoldCandidateProvenanceRequest,
    ProxyGoldCandidateProvenanceResponse,
    ProxyGoldCandidateDecisionResponse,
    GoldenSignoffRequest,
    GoldenSignoffPreflightResponse,
    GoldenSignoffResponse,
)
from koipa.services.golden_build_service import GoldenBuildService
from koipa.services.proxy_gold_candidate_service import ProxyGoldCandidateService
from koipa.console_doc import DOC_CSS, DOC_RENDER_JS
from koipa.console_shell import SHELL_CSS, SHELL_MEDIA_CSS
from koipa.console_nav import HEADER_CSS, NAV_CSS, header_html, nav_bar_html

# 한국지식재산보호원 심볼 — 폐쇄망이라 외부 이미지를 못 쓰고, 원본 파일도 리포에 없어
# (있는 것은 Koipa 로고 3종뿐) 제공받은 도안을 보고 벡터로 옮긴 **근사본**이다.
# 원본 PNG/SVG 가 들어오면 이 상수만 갈아끼우면 두 콘솔에 함께 반영된다.
# currentColor 를 쓰므로 배경이 밝든 어둡든 부모의 color 만 바꾸면 된다.
_KOIPA_MARK_SVG = (
    '<svg viewBox="0 0 40 40" width="30" height="30" role="img" '
    'aria-label="한국지식재산보호원" fill="currentColor">'
    '<rect x="2" y="11" width="6" height="18" rx="1"/>'
    '<rect x="11" y="4" width="6" height="32" rx="1"/>'
    '<rect x="20" y="15" width="6" height="11" rx="1"/>'
    '<rect x="29" y="8" width="6" height="24" rx="1"/>'
    "</svg>"
)

router = APIRouter(tags=["golden"], dependencies=[Depends(require_auth)])

# 검수·서명 HTML 뷰 전용 라우터(인증 없음). 이 페이지들은 브라우저 window.open/직접 URL 로 여는데
# 브라우저 네비게이션은 커스텀 헤더(X-API-Key/Bearer)를 못 붙여, require_auth 라우터에 두면 무조건
# 401 이라 골든 검수 화면 자체가 안 열렸다(signoff.html 은 그 안에서 키를 입력받아 POST 하도록 설계된
# 닭-달걀 모순). → 별도 무인증 라우터로 분리. 실제 권한 행위인 POST /golden/jobs/{id}/signoff 는 그대로
# require_role 로 보호되고, 페이지가 입력받은 X-API-Key 를 POST 에 실어 보내므로 보안 불변.
html_router = APIRouter(tags=["golden"])


# ── [#14a] 골든 HTML 서명 URL 토큰 ────────────────────────────────────────────
# review.html/signoff.html 은 브라우저 네비게이션용 무인증 라우터라 헤더 인증을 못 붙인다.
# uuid4 job_id 만으로는 URL 만 새면 후보 full-text 가 무인증 노출된다. 비밀키 설정 시 job_id 에
# 바인딩된 HMAC 토큰(?t=)을 강제해 이를 닫는다(변경성 POST signoff 는 별도 require_role 유지).
# 비밀키 미설정(dev/test)이면 미강제 — 기존 브라우저 네비게이션·테스트 무변경(backward-compat).
# [죽은 코드 제거 2026-08-17] 아래 넷은 정의만 있고 호출이 0건이었다 - 라우터도 시험도
# 참조하지 않았고(git grep 전수), 자기들끼리만 불렀다.
#   _candidate_manager_html_token / _verify_candidate_manager_html_token  (토큰 헬퍼)
#   _render_proxy_candidate_manager_html / _render_proxy_gold_console_html (옛 콘솔 렌더러)
# 살아 있는 렌더러는 _render_specledger_gold_console_html / _render_console_login_html 둘이다
# (실문서 수집 화면은 2026-08-17 후보 관리의 업로드 모달로 흡수 — D1).
# ⚠ 지운 렌더러 하나에 협력사 표기(브랜드 문자열)가 남아 있었다. 하이픈이 들어간 철자라
#   `lloydk` 검색에 안 걸려 2026-08-16 정리 때 놓쳤던 것이다 - 죽은 코드가 흔적을 숨겼다.
def _golden_url_secret() -> str | None:
    # 전용 비밀키 설정 시에만 토큰 강제(opt-in) — api_key 로 폴백하지 않는다. 업그레이드 시
    # 기존 배포의 골든 HTML 북마크를 무단으로 깨지 않고, 운영자가 명시적으로 켜서 닫는다.
    secret = (getattr(settings, "golden_html_url_secret", "") or "").strip()
    return secret or None


def _mint_html_token(job_id) -> str | None:
    """job_id 바인딩 HMAC-SHA256 토큰(24 hex). 비밀키 미설정이면 None."""
    secret = _golden_url_secret()
    if not secret:
        return None
    mac = hmac.new(secret.encode("utf-8"), f"golden:{job_id}".encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()[:24]


def _verify_html_token(job_id, token: str | None) -> bool:
    """비밀키 설정 시 상수시간 검증(부재/위조 거부); 미설정이면 항상 통과(미강제)."""
    expected = _mint_html_token(job_id)
    if expected is None:
        return True
    return bool(token) and hmac.compare_digest(expected, token)


def _signed_html_urls(job_id) -> tuple[str, str]:
    """(review_url, signoff_url) — 비밀키 설정 시 ?t= 토큰 포함, 아니면 평문 경로."""
    base = f"/api/v1/golden/jobs/{job_id}"
    tok = _mint_html_token(job_id)
    q = f"?t={tok}" if tok else ""
    return f"{base}/review.html{q}", f"{base}/signoff.html{q}"






def _console_actor_id(auth: dict) -> str:
    """관리 콘솔의 감사 신원은 포털 JWT의 sub만 사용한다.

    공유 API Key는 개별 사람을 식별할 수 없으므로, 화면에서 임의 ID를 받는 우회로를 두지
    않는다. 이 정책으로 등급확정·폐기 이력의 책임자 위조를 막는다.
    """
    claims = auth.get("claims")
    actor_id = str(getattr(claims, "sub", "") or "").strip()
    if not actor_id:
        raise HTTPException(
            status_code=403,
            detail="golden console requires a portal JWT login; shared API keys are not allowed",
        )
    return actor_id


@router.post(
    "/golden/build",
    response_model=GoldenBuildResponse,
)
def golden_build(
    request: Request,
    req: GoldenBuildRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
) -> GoldenBuildResponse:
    # [#13] 감사 신원(created_by 등)을 인증 principal 로 확정(JWT sub 우선; api_key 포털 전파값 유지).
    bind_authenticated_actor(req.actor, auth)
    resp = GoldenBuildService().submit(req)
    # [#14a] 브라우저로 바로 열 수 있는 서명 검수/서명 URL 동봉(비밀키 설정 시 ?t= 토큰).
    resp.review_url, resp.signoff_url = _signed_html_urls(resp.golden_job_id)
    return resp


@router.get(
    "/golden/builds",
    summary="등록할 수 있는 슬레이트 파일 목록 — 화면이 고르게 한다",
)
def golden_list_builds(
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
) -> dict:
    """`POST /golden/jobs/register` 에 넣을 수 있는 파일을 화면에 내려 준다.

    왜(2026-08-20 사용자 지적). 등록 칸이 자유 입력이라 서버에 어떤 파일이 있는지 알 방법이
    없었고, placeholder 가 채워진 값처럼 보여 [등록]을 누르면 "경로를 입력하세요" 가 떴다.
    고르게 하면 그 함정이 사라진다.

    읽기 전용이고 목록은 datasets/ 하위로 한정된다(register 와 같은 샌드박스).
    """
    items = GoldenBuildService().list_registerable_builds()
    return {"total": len(items), "builds": items}


@router.post(
    "/golden/jobs/register",
    response_model=GoldenBuildResponse,
)
def golden_register_build(
    req: GoldenRegisterRequest,
    auth: dict = Depends(require_role("admin", "kl_backend", "system")),
) -> GoldenBuildResponse:
    """기존 build_*.jsonl(큐레이트 슬레이트)을 재라벨링 없이 골든 잡으로 등록 → signoff.html 연결.

    /golden/build 와 달리 LLM 재라벨링을 하지 않아 슬레이트 라벨을 보존한다(실서명 스프린트 34건
    등). 경로는 datasets/ 하위 샌드박스. 반환 job_id 로 GET /golden/jobs/{id}/signoff.html 을 열어
    검수자가 화면 서명한다.
    """
    bind_authenticated_actor(req.actor, auth)  # [#13] 감사 신원 = 인증 principal
    job_id = GoldenBuildService().register_build(
        req.build_path, actor_user_id=req.actor.user_id
    )
    if job_id is None:
        raise HTTPException(
            status_code=404, detail="build_path 없음 또는 datasets/ 밖(샌드박스 거부)"
        )
    review_url, signoff_url = _signed_html_urls(job_id)  # [#14a]
    return GoldenBuildResponse(
        golden_job_id=job_id, status_url=f"/golden/jobs/{job_id}",
        review_url=review_url, signoff_url=signoff_url,
    )


@router.get(
    "/golden/jobs/{job_id}",
    response_model=GoldenBuildStatus,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
)
def golden_job_status(job_id: UUID) -> GoldenBuildStatus:
    st = GoldenBuildService().get_status(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="golden build job not found")
    # [#14a] 콘솔이 이 값으로 review/signoff HTML 을 연다(인증 경로에서 서명 URL 발급).
    st.review_url, st.signoff_url = _signed_html_urls(job_id)
    return st


@router.get(
    "/golden/summary",
    response_model=GoldenCorpusSummary,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="정본 골든셋 구성 집계 (tier·등급·출처·라벨출처)",
    description=(
        "골든셋이 지금 어떤 tier(locked_gold_eval·held_review·legal_floor·gold_candidate·"
        "silver_train)·등급·문서출처로 구성돼 있는지 집계한다. tier 는 저장 컬럼이 아니라 "
        "label_source/review_status/서명 envelope 에서 파생되므로 golden_tiers 의 tier_of 로 "
        "유도한다. 읽기 전용 — 정본 변경은 promote_golden_candidates.py 게이트만 담당한다."
    ),
)
def golden_corpus_summary(path: str | None = None) -> GoldenCorpusSummary:
    """정본 골든셋 구성 집계. path 미지정이면 settings.golden_corpus_jsonl."""
    target = (path or "").strip() or getattr(
        settings, "golden_corpus_jsonl", "datasets/gold_real/classification_gold.jsonl"
    )
    res = GoldenBuildService().corpus_summary(target)
    if res is None:
        raise HTTPException(
            status_code=404,
            detail=f"골든셋 정본을 찾을 수 없습니다: {target} (datasets/ 하위만 허용)",
        )
    return GoldenCorpusSummary(**res)


@router.get(
    "/golden/candidates",
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="합성 Proxy Gold 후보 목록",
)
def proxy_gold_candidate_list(
    status: str | None = None, grade: str | None = None,
    origin: str | None = None, query: str | None = None,
    review_batch: str | None = None,
) -> dict:
    """관리 후보 목록. 승인도 approved_proxy일 뿐 locked/실문서 골든이 아니다.

    review_batch 는 검수 전달본 단위 표식이다. 콘솔 전체가 306건인데 한 회차 검수
    대상은 그중 일부라, 표식이 없으면 검수자가 어느 문서를 봐야 하는지 알 수 없다.
    """
    if status and status not in {"proposed", "under_review", "approved_proxy",
                                 "grade_fixed_unlocked", "deferred", "discarded", "out_of_scope"}:
        raise HTTPException(status_code=422, detail="invalid candidate status")
    if grade and grade not in {"TS", "S1", "S2", "S3"}:
        raise HTTPException(status_code=422, detail="invalid grade")
    return ProxyGoldCandidateService().list_candidates(
        status=status, grade=grade, origin=origin, query=query, review_batch=review_batch)


@router.get(
    "/golden/candidates/summary",
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="골든셋 관리 콘솔 집계",
)
def proxy_gold_candidate_summary() -> dict:
    """후보의 확정·미확정·보류·폐기·출처·등급 분포를 반환한다."""
    return ProxyGoldCandidateService().summary()


@router.get(
    "/golden/candidates/decisions",
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="결정 원장 최근 기록(보류·폐기·번복 포함)",
)
def proxy_gold_candidate_decisions(limit: int = Query(default=100, ge=1, le=500)) -> dict:
    """append-only 결정 원장을 최신순으로 돌려준다. 문서를 열지 않고 감사할 수 있게."""
    return ProxyGoldCandidateService().recent_decisions(limit=limit)


@router.get(
    "/golden/candidates/session",
    summary="골든셋 관리 콘솔 로그인 신원",
)
def proxy_gold_candidate_session(
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
) -> dict:
    """화면에 표시할 인증된 신원. 클라이언트가 ID를 제출하지 않는다.

    [C2 2026-08-17] reviewer 역할을 허용 목록에 넣었다. 서명 API 는 reviewer 를 받는데
    (golden_job_signoff 의 require_role) 이 신원 조회는 admin·kl_backend 만 받고 있어서,
    **검수자는 서명은 되는데 자기 이름은 못 읽는** 상태였다. 서명 화면이 이 응답으로
    서명자를 표시하므로 그대로 두면 검수자 화면이 403 으로 막힌다.

    actor_role 을 함께 돌려준다 — 화면이 요청 본문의 actor.role 을 자칭하지 않고
    인증된 역할을 그대로 싣게 하기 위해서다(값 범위는 VALID_ROLES = Actor.role 패턴과 동일).
    """
    return {
        "actor_id": _console_actor_id(auth),
        "auth_mode": auth.get("mode"),
        "actor_role": auth.get("actor_role") or "",
    }


@router.post(
    "/golden/candidates/upload",
    status_code=201,
    summary="골든셋 관리 콘솔 문서 업로드",
)
async def proxy_gold_candidate_upload(
    file: UploadFile = File(...),
    document_origin: str = Form(default="uploaded_document"),
    source_reference: str = Form(default=""),
    authorization_basis: str = Form(default=""),
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> dict:
    """문서를 미확정 검수 항목으로만 저장한다. 업로드 자체는 골든 승격이 아니다."""
    actor_id = _console_actor_id(auth)
    max_bytes = int(getattr(settings, "max_upload_mb", 50)) * 1024 * 1024
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > max_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    content = await file.read()
    if len(content) > max_bytes:
        raise HTTPException(status_code=413, detail="file too large")
    try:
        candidate = ProxyGoldCandidateService().create_uploaded_candidate(
            filename=file.filename or "uploaded_document", content=content,
            actor_id=actor_id, document_origin=document_origin,
            source_reference=source_reference, authorization_basis=authorization_basis,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {k: v for k, v in candidate.items() if k != "text"}


@router.get(
    "/golden/candidates/{doc_id}",
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="합성 Proxy Gold 후보 상세",
)
def proxy_gold_candidate_detail(doc_id: str) -> dict:
    candidate = ProxyGoldCandidateService().get_candidate(doc_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="proxy-gold candidate not found")
    return candidate


@router.post(
    "/golden/candidates/{doc_id}/provenance",
    response_model=ProxyGoldCandidateProvenanceResponse,
    summary="실문서 후보의 출처를 나중에 기록",
)
def proxy_gold_candidate_provenance(
    doc_id: str,
    req: ProxyGoldCandidateProvenanceRequest,
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> ProxyGoldCandidateProvenanceResponse:
    """이미 올라간 실문서에 원천 위치·사용 권한 근거를 채운다.

    왜 결정 API 가 아닌가. 결정은 action 마다 status 를 정하는 표를 갖고 있어
    "등급은 그대로 두고 출처만 기록" 을 표현할 수 없다.

    실측 2026-08-17(223): 실문서 74건 중 62건이 출처는 있고 **사용 권한 근거가 없는**
    상태였는데(적재 스크립트가 top-level 에만 썼다), 그것을 채울 경로가 화면에도 API 에도
    없었다. 이 엔드포인트가 그 자리다.

    원장에는 event_kind="provenance" 로 남는다 - 결정 이벤트가 아니므로 등급·상태를 안 덮는다.
    """
    actor_id = _console_actor_id(auth)
    try:
        candidate = ProxyGoldCandidateService().record_provenance(
            doc_id=doc_id,
            source_reference=req.source_reference,
            authorization_basis=req.authorization_basis,
            reason=req.reason,
            actor_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if candidate is None:
        raise HTTPException(status_code=404, detail="proxy-gold candidate not found")
    return ProxyGoldCandidateProvenanceResponse(
        doc_id=doc_id,
        provenance=candidate.get("provenance") or {},
        status=candidate["status"],
        final_grade=candidate.get("final_grade"),
    )


@router.post(
    "/golden/candidates/{doc_id}/decision",
    response_model=ProxyGoldCandidateDecisionResponse,
    summary="합성 Proxy Gold 후보의 관리자 결정 기록",
)
def proxy_gold_candidate_decision(
    doc_id: str,
    req: ProxyGoldCandidateDecisionRequest,
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> ProxyGoldCandidateDecisionResponse:
    """결정을 append-only 원장에 남긴다. 이 엔드포인트는 locked 승격을 수행하지 않는다."""
    actor_id = _console_actor_id(auth)
    try:
        candidate = ProxyGoldCandidateService().decide(
            doc_id=doc_id, action=req.action, grade=req.grade,
            reason=req.reason, actor_id=actor_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if candidate is None:
        raise HTTPException(status_code=404, detail="proxy-gold candidate not found")
    return ProxyGoldCandidateDecisionResponse(
        doc_id=doc_id,
        status=candidate["status"],
        final_grade=candidate["final_grade"],
        latest_decision=candidate["latest_decision"],
    )


@router.get(
    "/golden/jobs",
    response_model=GoldenJobListResponse,
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
)
def golden_job_list(limit: int = 20) -> GoldenJobListResponse:
    """최근 골든 잡 목록.

    목록이 없으면 콘솔은 마지막 job_id 를 메모리에만 들고 있어 새로고침 한 번에 검수하던
    후보로 돌아갈 길이 사라진다(229건을 절반 서명하다 F5 를 누르면 길을 잃는다).

    JobStore 에는 골든 외 잡(분류·학습)도 섞이므로 kind 로 걸러낸다. 정렬은 백엔드에 따라
    best-effort(Redis 는 SCAN 순서) — 응답 ordering 필드로 그 한계를 명시한다.
    """
    limit = max(1, min(100, limit))
    # 필터로 걸러지는 만큼 여유 있게 읽고 자른다(골든 잡이 뒤로 밀려 안 보이는 것 방지).
    raw = get_default_store().list_recent(limit=limit * 5)
    jobs: list[GoldenJobSummary] = []
    for j in raw:
        if j.get("kind") not in ("golden_build", "golden_register"):
            continue
        jid = str(j.get("job_id") or "")
        if not jid:
            continue
        try:
            review_url, signoff_url = _signed_html_urls(UUID(jid))
        except ValueError:      # job_id 형식 이상 — 목록에서 제외하지 않고 링크만 생략
            review_url = signoff_url = None
        jobs.append(GoldenJobSummary(
            job_id=jid,
            kind=str(j.get("kind") or ""),
            status=str(j.get("status") or ""),
            actor=str(j.get("actor") or ""),
            submitted_at=j.get("submitted_at"),
            source_type=j.get("source_type"),
            gold_count=j.get("gold_count"),
            uncertain_count=j.get("uncertain_count"),
            error=j.get("error"),
            review_url=review_url,
            signoff_url=signoff_url,
        ))
    return GoldenJobListResponse(jobs=jobs[:limit])


def _job_gate_html(job_id: UUID) -> HTMLResponse | None:
    """렌더 전 잡 상태를 확인해 not-found/진행중/실패를 구분한 HTMLResponse 를 돌려주거나,
    done 이면 None(호출부가 실제 렌더 진행). 진행중·실패·오id 를 같은 404 로 뭉개고 실패 error 를
    버리던 것 보완 — 검수자가 '기다릴지 재빌드할지'를 화면에서 판단하게 한다.
    """
    st = GoldenBuildService().get_status(job_id)
    if st is None:
        # 여기는 **검수자가 링크를 눌러 도착하는 자리**다. 원시 JSON 404 를 주면 무슨 일이
        # 일어났는지 알 수 없다. 실제로 흔한 상황이기도 하다 — JobStore 가 in-memory 면
        # API 재시작에 잡이 사라지고, Redis 라도 TTL 이 있다. 재등록은 멱등이라 관리자가
        # 스크립트를 한 번 더 돌리면 끝난다. 그 사실을 화면에서 알려 준다.
        return HTMLResponse(
            content="<!doctype html><meta charset=utf-8>"
            "<body style='font-family:sans-serif;padding:40px;line-height:1.7'>"
            "<h2>이 검수 잡이 서버에 없습니다</h2>"
            f"<p>job {_html.escape(str(job_id))}</p>"
            "<p>링크가 잘못된 것이 아닙니다. 골든 빌드 잡은 API 재시작이나 보관 기간 만료로 "
            "사라질 수 있습니다.</p>"
            "<p><b>관리자에게 재등록을 요청하세요.</b> 재등록은 멱등이며, 같은 후보 파일이면 "
            "기존 잡을 재사용합니다 — 검수 내용은 사라지지 않습니다.</p>"
            "<pre style='background:#f6f6f4;padding:12px;border-radius:4px;white-space:pre-wrap'>"
            "python3 scripts/register_review_signoff_job.py \\\n"
            "    --base-url &lt;서버&gt; --actor &lt;실계정&gt; --token &lt;관리자 토큰&gt;</pre>"
            "<p style='color:#70757a;font-size:13px'>재등록하면 새 주소(?t= 포함)가 인쇄됩니다. "
            "그 주소를 통째로 받아 여세요.</p></body>",
            status_code=404,
        )
    status = getattr(st, "status", None)
    if status in ("queued", "running", "pending"):
        return HTMLResponse(
            content="<!doctype html><meta charset=utf-8>"
            "<body style='font-family:sans-serif;padding:40px'>"
            f"<h2>골든 빌드 진행 중…</h2><p>job {job_id} · status={_html.escape(str(status))}. "
            "완료 후 이 페이지를 새로고침하세요.</p></body>",
            status_code=202,
        )
    if status == "failed":
        err = getattr(st, "error", None) or "(사유 미기록)"
        return HTMLResponse(
            content="<!doctype html><meta charset=utf-8>"
            "<body style='font-family:sans-serif;padding:40px'>"
            "<h2 style='color:#dc2626'>골든 빌드 실패</h2>"
            f"<p>job {job_id}</p><pre style='background:#fef2f2;padding:12px;border-radius:8px;"
            f"white-space:pre-wrap'>{_html.escape(str(err))}</pre></body>",
            status_code=200,
        )
    return None






def _render_specledger_gold_console_html() -> str:
    """Golden-set console using the supplied Specledger visual language."""
    return r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>한국지식재산보호원 | 골든셋 등록</title>
<style>
:root{--ink:#111111;--red:#e72d44;--paper:#fff;--soft:#f7f7f5;--line:#e1e1de;--mute:#8f9498;--green:#e8f7ef;--orange:#fff0dc}*{box-sizing:border-box}html{scroll-behavior:smooth}""" + SHELL_CSS + r""".workspace b{margin-left:10px;color:#222;font-size:12px;border:1px solid #d9d9d6;padding:9px 14px;border-radius:6px}.workname{margin-top:18px;font-size:22px;font-weight:850;letter-spacing:-.7px}.workdesc{margin-top:11px;color:#6b737b;font-size:14px;max-width:230px}.branch{margin-top:22px;color:#969da3;font:12px ui-monospace,monospace}.nav{margin-top:36px;border-top:1px solid var(--line);padding-top:26px}.nav a{display:flex;text-decoration:none;color:#68717a;gap:20px;padding:13px 14px;margin:2px -14px;font-size:16px}.nav a span{font:12px ui-monospace,monospace;color:#9aa1a6;padding-top:3px}.nav a.active{background:linear-gradient(90deg,#fafaf8 0%,#fff 100%);color:#161616;font-weight:800;border-left:3px solid #161616;padding-left:11px}.ledger{margin-top:auto;border-top:1px solid var(--line);padding-top:24px;font-size:12px;color:#8c949a}.ledger b{display:block;color:#50575d;margin:8px 0}.hero h1{font-size:clamp(41px,5.1vw,76px);line-height:1.06;letter-spacing:-4px;margin:22px 0 20px;font-weight:850}.hero h1 em{font-style:normal;color:var(--red)}.hero p{color:#667079;font-size:17px;max-width:720px;margin:0}.flow{display:flex;gap:10px;align-items:center;margin-top:28px;color:#7c858c;font:12px ui-monospace,monospace}.flow i{color:var(--red);font-style:normal;font-size:20px}.flow span{border:1px solid var(--line);padding:8px 11px;background:#fff}.gate strong{font:800 43px ui-monospace,monospace;letter-spacing:-2px;color:var(--red);display:block;margin:26px 0 8px}.gate p{font-size:13px;color:#879097}.btn.red{background:var(--red);border-color:var(--red);color:#fff}.summaryIntro h2{margin:12px 0 5px;font-size:25px;letter-spacing:-1px}.summaryIntro p{margin:0;color:#68727a;font-size:13px}.metric b{font:800 40px ui-monospace,monospace;letter-spacing:-2px;display:block;margin:12px 0 2px}.metric small{color:#8a9299}.section h2{display:inline;font-size:42px;letter-spacing:-2px;margin:0}.sectionTop p{color:#727c84;margin:10px 0 0 48px;font-size:15px}.filters{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.filters input,.filters select{border:1px solid var(--line);background:#fff;padding:10px;font-size:12px;min-width:110px}.filters input{width:170px}.rowhead,.candidate{display:grid;grid-template-columns:110px minmax(180px,1.2fr) minmax(160px,1.4fr) 130px 86px;gap:16px;align-items:center}.candidate{width:100%;border:0;border-bottom:1px solid #e8e8e5;background:#fff;padding:18px 12px;text-align:left;cursor:pointer;color:inherit}.candidate:hover,.candidate.selected{background:#fff8f8}.candidate.selected{box-shadow:inset 3px 0 0 var(--red)}.docid{font:12px ui-monospace,monospace;color:#929aa1}.doctitle{font-size:17px;font-weight:850;letter-spacing:-.5px}.docsub{font-size:12px;color:#778088;margin-top:3px}.grade{font:700 15px ui-monospace,monospace}.origin{font-size:12px;color:#737b82}.pill.proposed,.pill.review{background:#eef1f4;color:#56616b}.pill.fixed{background:var(--green);color:#087341}.pill.defer{background:var(--orange);color:#b25c00}.pill.discard{background:#ffe8ea;color:#c72c40}.chars{text-align:right;color:#7f878d;font:12px ui-monospace,monospace}.detail{margin-top:38px;display:none;border-top:3px solid #111}.detail.show{display:grid;grid-template-columns:minmax(0,1fr) 315px}.detailMain{padding:31px 34px 38px;border-right:1px solid var(--line)}.detailMain h3{font-size:27px;margin:10px 0 5px;letter-spacing:-1px}.metas{display:flex;gap:7px;flex-wrap:wrap;margin:15px 0}.metas span{font:11px ui-monospace,monospace;background:#f5f5f3;border:1px solid #e8e8e5;padding:5px 7px;color:#656d74}.scope{border-left:3px solid var(--red);padding:9px 12px;color:#69727a;background:#fcfcfa;font-size:13px}
.viewbar{display:flex;align-items:center;gap:7px;margin-top:16px;padding-top:14px;border-top:1px solid var(--line)}

.viewbar .btn.on{background:#111;color:#fff;border-color:#111}
.viewnote{margin-left:auto;font-size:11.5px;color:#8a9299}
""" + DOC_CSS + r""".sideDetail{background:#fafaf8;padding:25px}.sideDetail h4{font:800 12px ui-monospace,monospace;color:#8b949b;margin:0 0 14px}.action label{display:block;font-size:12px;color:#6b7378;margin:12px 0 4px}.action select,.action textarea{width:100%;border:1px solid var(--line);padding:9px;background:#fff}.action textarea{height:95px;resize:vertical}.history{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}.event{border-left:3px solid var(--red);padding:4px 0 4px 10px;margin:10px 0;font-size:12px}.event .when{color:#8b949b;margin-top:3px}.modal{position:fixed;inset:0;background:#111b;display:none;align-items:center;justify-content:center;padding:20px}.modal.show{display:flex}.dialog{background:#fff;border:1px solid #ddd;max-width:540px;width:100%;padding:28px;box-shadow:10px 10px 0 #111}.dialog h3{font-size:25px;margin:0 0 8px}.drop{border:1px dashed #aab0b3;padding:24px;margin:20px 0;background:#fafaf8}.drop input{width:100%}.dialogActions{display:flex;justify-content:flex-end;gap:8px}""" + SHELL_MEDIA_CSS + r"""@media(max-width:1050px){.detail.show{grid-template-columns:1fr}.detailMain{border-right:0}.sideDetail{border-top:1px solid var(--line)}}@media(max-width:700px){.candidate{grid-template-columns:1fr 70px}.candidate .origin,.candidate .chars{display:none}}
.drop.over{border-color:#111;background:#f4f4f2}
.rule{border-top:1px solid #eceae7;padding:10px 0}.rule b{display:block;margin-bottom:4px;font-size:12.5px}.rule span{color:#70757a;font-size:12px;line-height:1.5}"""  + HEADER_CSS + NAV_CSS + r"""</style></head><body>""" + header_html("골든셋 등록", "manage", trailing=r"""<div class="topmid"><span class="dot"></span>등급 미확정 <span id="topCount">–</span>건 <span class="revision">GOLDEN-v1</span></div><div class="workspace">WORKSPACE <b>지식재산보호원 AI 영업비밀</b></div>""") + r"""<div class="frame"><aside class="side"><div class="cap">CURRENT WORKSPACE</div><div class="workname">koipa-ai</div><div class="workdesc">Koipa AI Engine for KOIPA Trade-Secret System (PoC)</div><div class="branch">goldset/console-review</div><nav class="nav"><a href="#overview" class="active"><span>01</span>개요</a><a href="#candidates"><span>02</span>골든셋 후보</a><a href="#detail"><span>03</span>문서 상세·결정</a><a href="#ledgerAll"><span>04</span>보류·폐기 이력</a><a href="#quality"><span>05</span>품질 지표</a></nav><div class="ledger"><div class="cap">SNAPSHOT LEDGER</div><b id="session">포털 로그인 확인 중</b><div>origin · audit · decision</div></div></aside><main class="main"><section id="overview" class="hero"><div><div class="eyebrow">GOLDEN SET REVIEW CONSOLE</div><h1>검수 가능한<br><em>골든셋</em>을 관리합니다.</h1><p>합성 후보와 실문서를 한 곳에서 검수하고, 등급 확정·보류·폐기를 누가 왜 그렇게 정했는지와 함께 남깁니다. 기록은 덮어쓰지 않고 계속 쌓입니다.</p><div class="flow"><span>문서 수집</span><i>→</i><span>후보·검토</span><i>→</i><span>등급 확정</span><i>→</i><span>이력 보존</span></div></div><aside class="gate"><div class="glabel">등급 확정 진행</div><strong id="readiness">–</strong><div id="readinessNote" style="font-size:11.5px;color:#8d949b;margin:2px 0 6px"></div><p>확정된 후보 수 / 전체 후보 수입니다. 여기서 확정해도 <b>평가 정답지로 승격되지는 않습니다</b> — 승격은 사람 서명을 거치는 별도 절차입니다.</p><span class="status">● 후보 단계</span><div class="actions"><button class="btn black" id="openUpload">문서 업로드</button><button class="btn" id="refresh">새로고침</button></div></aside></section><section class="summary" id="kpis"><div class="summaryIntro"><div class="cap">DATASET SNAPSHOT</div><h2>골든셋 현황</h2><p>현재 워크스페이스의 검수 상태와 등급 분포입니다.</p></div></section><div id="flash" class="flash" role="status" aria-live="polite"></div><section id="candidates" class="section"><div class="sectionTop"><div><span class="secNum">02</span><h2>골든셋 후보</h2><p>문서를 선택하면 전문과 등급 결정 이력이 열립니다.</p></div><div class="filters"><input id="review_batch" placeholder="검수 배치(예: ff5a822c)" title="검수 전달본 단위로 목록을 좁힙니다. 비우면 전체 후보가 보입니다."><input id="query" placeholder="문서 ID 또는 제목" aria-label="문서 ID 또는 제목 검색"><select id="status"><option value="">전체 상태</option><option value="proposed">제안</option><option value="under_review">검토중</option><option value="approved_proxy">Proxy 확정</option><option value="grade_fixed_unlocked">등급 확정</option><option value="deferred">보류</option><option value="discarded">폐기</option><option value="out_of_scope">검수 대상 아님</option></select><select id="grade"><option value="">전체 등급</option><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select><select id="origin"><option value="">전체 출처</option><option value="synthetic">합성 후보</option><option value="public_real">공개 실문서</option><option value="organization_real">조직 보유 실문서</option></select><button id="filter" class="btn">필터</button></div></div><div class="list"><div class="rowhead"><div>DOCUMENT ID</div><div>DOCUMENT</div><div>GRADE / ORIGIN</div><div>STATUS</div><div>SIZE</div></div><div id="rows"><div class="empty">문서 목록을 불러오는 중입니다.</div></div></div></section><section id="detail" class="detail"><div class="detailMain"><div class="eyebrow">DOCUMENT EVIDENCE</div><h3 id="detailTitle">문서 상세</h3><div id="metas" class="metas"></div><div id="scope" class="scope"></div><div class="viewbar"><button class="btn sm" id="viewRendered" aria-pressed="true">읽기 좋게</button><button class="btn sm" id="viewRaw" aria-pressed="false">원문 그대로</button><span class="viewnote">검수 판단은 원문 기준입니다. 읽기 좋게 보기는 서식만 입힌 같은 내용입니다.</span></div><div id="documentRendered" class="docbody md"></div><pre id="document" class="docbody" style="display:none"></pre></div><aside class="sideDetail"><h4>등급 결정</h4><div class="action"><label>결정</label><select id="action"></select><div id="gradeWrap"><label>확정 등급</label><select id="finalGrade"><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></div><label>사유 / 메모</label><textarea id="reason" placeholder="등급 지정·보류·폐기에는 사유가 필요합니다."></textarea><button id="save" class="btn black" style="width:100%;margin-top:12px">결정 저장</button></div><div id="provBox" class="action" style="display:none;margin-top:18px;border-top:1px solid #dededb;padding-top:16px"><h4 style="margin:0 0 8px">출처 기록 <span id="provStatus" style="font-weight:400;color:#70757a;font-size:12.5px"></span></h4><p style="font-size:12px;color:#70757a;line-height:1.5;margin:0 0 10px">실문서는 원천 위치와 사용 권한 근거가 둘 다 있어야 기록으로 칩니다. 등급·상태는 바뀌지 않습니다.</p><label>원천 위치 / 식별 정보</label><input id="provSrc" placeholder="예: 품질관리/운영절차/2026 또는 공개기관 URL"><label>사용 권한 또는 공개 근거</label><input id="provBasis" placeholder="예: 소유부서 검수용 사용 승인 / 공개 라이선스"><label>메모(선택)</label><input id="provReason" placeholder="사후 기록 사유"><div id="provOrigin" style="font-size:11.5px;color:#8d949b;margin-top:6px"></div><button id="provSave" class="btn" style="width:100%;margin-top:10px">출처 저장</button></div><div class="history"><h4>DECISION LEDGER</h4><div id="history" class="empty">결정 이력이 없습니다.</div></div></aside></section><section id="ledgerAll" class="section"><div class="sectionTop"><div><span class="secNum">04</span><h2>보류 · 폐기 이력</h2><p>문서를 열지 않고 전체 결정 기록을 최신순으로 봅니다. 기록은 덧붙이기만 하며 지워지지 않습니다.</p></div><div class="filters"><select id="ledgerFilter"><option value="">전체 결정</option><option value="defer">보류</option><option value="discard">폐기</option><option value="exclude">검수 대상 아님</option><option value="change">등급 변경</option><option value="approve">승인</option><option value="reopen">재검토</option></select><button id="ledgerReload" class="btn">불러오기</button></div></div><div class="list"><div class="rowhead" style="grid-template-columns:150px 110px 120px minmax(180px,1.6fr) 150px"><div>DOCUMENT ID</div><div>ACTION</div><div>GRADE</div><div>REASON</div><div>WHO / WHEN</div></div><div id="ledgerRows"><div class="empty">결정 기록이 없습니다.</div></div></div></section><section id="quality" class="section"><div class="sectionTop"><div><span class="secNum">05</span><h2>품질 지표</h2><p>등급을 맞히는 데 본문 말고 다른 단서가 섞였는지 봅니다. 여기가 나쁘면 이 골든셋으로 잰 정확도는 부풀려집니다.</p></div></div><div id="qualityBody"><div class="empty">지표를 불러오는 중입니다.</div></div></section></main></div><div id="modal" class="modal"><section class="dialog"><div class="eyebrow">DOCUMENT INTAKE</div><h3>문서 업로드</h3><p>업로드 문서는 검토 대기 상태로만 저장됩니다. 자동 골든 승격이나 외부 LLM 전송은 하지 않습니다.</p><div class="drop" id="drop"><input id="file" type="file" accept=".txt,.md,.csv,.pdf,.doc,.docx,.hwp,.hwpx,.xlsx,.xls,.pptx"><p><b id="dropName">여기로 파일을 끌어다 놓거나 위에서 선택하세요</b><br>TXT · PDF · Word · HWP/HWPX · Excel · PPTX<br><span style="color:#8a9299">스캔 이미지는 지원하지 않습니다 — 본문 텍스트가 있는 파일만 등록됩니다.</span></p></div><div class="action"><label>문서 출처</label><select id="upOrigin"><option value="organization_real">조직 보유 실문서 — S2/S3</option><option value="public_real">공개 실문서 — S3 우선</option></select><label>원천 위치 / 식별 정보</label><input id="upSource" placeholder="예: 품질관리/운영절차/2026 또는 공개기관 URL" style="width:100%;border:1px solid var(--line);padding:9px"><label>사용 권한 또는 공개 근거</label><select id="upBasisSel"><option value="">선택하세요</option><option value="소유부서 검수용 사용 승인">소유부서 검수용 사용 승인</option><option value="기관 공개자료(공개 라이선스)">기관 공개자료(공개 라이선스)</option><option value="공공누리 등 공공저작물 이용허락">공공누리 등 공공저작물 이용허락</option><option value="사내 교육·배포용 승인">사내 교육·배포용 승인</option><option value="__custom__">직접 입력…</option></select><input id="upBasis" placeholder="근거를 직접 적으십시오" style="width:100%;border:1px solid var(--line);padding:9px;display:none;margin-top:6px"><p style="font-size:12px;color:#8a9299;margin:10px 0 0">출처와 권한을 남기지 않으면 나중에 평가셋으로 쓸 수 없습니다. 고객사 원문·반출 승인이 없는 문서는 등록하지 마십시오.</p></div><div class="rules" style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px"><div class="eyebrow" style="margin-bottom:8px">INTAKE RULES · 등록 기준</div><div class="rule"><b>S3 · 공개·일반</b><span>공식 공지, 공개 매뉴얼, 일반 안내. 기관·버전·공개 위치를 기록합니다.</span></div><div class="rule"><b>S2 · 조직 내부</b><span>운영 절차, 품질 이력, 교육 자료, 변경·장애 후속조치 등. 소유부서의 검수·사용 근거를 기록합니다.</span></div><div class="rule"><b>제외</b><span>고객사 원문, 반출 승인이 없는 문서, 권한이 불명확한 파일은 등록하지 않습니다.</span></div><div class="rule"><b>다음 단계</b><span>여기 등록은 Locked Gold 승격이 아닙니다. 등급을 잠정 지정하고, 사람 검수 완료 뒤에만 별도 승격 절차를 사용합니다.</span></div></div><div class="dialogActions"><button id="cancelUpload" class="btn">취소</button><button id="upload" class="btn black">업로드 및 추출</button></div></section></div><script>
let selected=null;const $=id=>document.getElementById(id);const api='/api/v1/golden/candidates';function msg(v,bad=false){const e=$('flash');e.textContent=v;e.className=bad?'flash error':'flash'}function hdr(json=true){let h={};if(json)h['Content-Type']='application/json';return h}function qurl(){let q=new URLSearchParams();['status','grade','origin','query','review_batch'].forEach(id=>{let v=$(id).value.trim();if(v)q.set(id,v)});return api+(q.size?'?'+q:'')}async function req(url,opt={}){if(window.__GOLDEN_PREVIEW__){const p=window.__GOLDEN_PREVIEW__;if(opt.method)throw new Error('로컬 미리보기는 읽기 전용입니다. 업로드·등급 변경·폐기는 KL 콘솔 배포 후 사용할 수 있습니다.');if(url.endsWith('/session'))return {actor_id:'로컬 미리보기 · 읽기 전용'};const id=url.split('?')[0].slice((api+'/').length);if(id&&p.by_id[id])return p.by_id[id];return p.list}const r=await fetch(url,{...opt,credentials:'same-origin',headers:{...hdr(opt.json!==false),...(opt.headers||{})}});if(!r.ok)throw new Error((await r.text())||r.status);return r.json()}function metric(label,value,hint){let d=document.createElement('div');d.className='metric';d.innerHTML='<div class="mcap"></div><b></b><small></small>';d.children[0].textContent=label;d.children[1].textContent=value;d.children[2].textContent=hint;return d}function renderSummary(s,b){$('kpis').replaceChildren();let intro=document.createElement('div');intro.className='summaryIntro';intro.innerHTML='<div class="cap">DATASET SNAPSHOT</div><h2>골든셋 현황</h2><p>현재 워크스페이스의 검수 상태와 등급 분포입니다.</p>';$('kpis').append(intro,metric('전체 후보',s.total+'건','원장 전량 · 폐기 '+(s.discarded||0)+'건 포함'),metric('등급 확정',s.fixed+'건','Proxy / Unlocked'),metric('미확정',s.unfixed+'건','검토 필요'),metric('보류 · 폐기',(s.deferred+s.discarded)+'건','감사 이력 보존'),metric('검수 대상 아님',(s.out_of_scope||0)+'건','범위 밖 · 되돌릴 수 있음'),metric('실문서',(s.actual_document_intake||0)+'건','출처 기록 대상'),metric('출처 기록',(s.actual_provenance_recorded||0)+'건','미완 '+(s.actual_provenance_partial||0)+'건 · 옛 자리 '+(s.actual_provenance_legacy||0)+'건'));var scoped=b&&b.total!==s.total;$('readiness').textContent=scoped?(b.terminal+'/'+b.total):(s.fixed+'/'+s.total);$('readinessNote').textContent=scoped?('이 배치 기준 · 남은 '+b.pending+'건'+(b.deferred?' · 보류 '+b.deferred+'건 포함':'')):'전체 후보 기준';$('topCount').textContent=scoped?b.pending:s.unfixed;renderQuality(s.quality);loadLedger()}function pill(status){let e=document.createElement('span');e.className='pill '+(status==='discarded'?'discard':status==='out_of_scope'?'discard':status==='deferred'?'defer':status.includes('fixed')||status==='approved_proxy'?'fixed':status==='under_review'?'review':'proposed');e.textContent={proposed:'제안',under_review:'검토중',approved_proxy:'Proxy 확정',grade_fixed_unlocked:'등급 확정',deferred:'보류',discarded:'폐기',out_of_scope:'검수 대상 아님'}[status]||status;return e}function renderRows(data){let box=$('rows');box.replaceChildren();renderSummary(data.summary,data.batch_summary);if(!data.candidates.length){box.innerHTML='<div class="empty">조건에 맞는 문서가 없습니다.</div>';return}data.candidates.forEach(c=>{let row=document.createElement('button');row.className='candidate'+(selected&&selected.doc_id===c.doc_id?' selected':'');let grade=(c.proposed_grade||'–')+(c.final_grade?' → '+c.final_grade:'');row.innerHTML='<div class="docid"></div><div><div class="doctitle"></div><div class="docsub"></div></div><div><div class="grade"></div><div class="origin"></div></div><div></div><div class="chars"></div>';row.children[0].textContent=c.doc_id;row.children[1].children[0].textContent=c.title;row.children[1].children[1].textContent=({synthetic:'합성 후보',public_real:'공개 실문서',organization_real:'조직 보유 실문서'}[c.document_origin]||c.document_origin);row.children[2].children[0].textContent=grade;row.children[2].children[1].textContent=c.document_origin;row.children[3].append(pill(c.status));row.children[4].textContent=c.characters.toLocaleString();row.onclick=()=>show(c.doc_id);box.append(row)})}function option(v,t){let o=document.createElement('option');o.value=v;o.textContent=t;return o}function setActions(c){let a=$('action');a.replaceChildren();if(c.document_origin==='synthetic'&&c.proposed_grade)a.append(option('approve','승인 (제안 등급 그대로 확정)'));a.append(option('change','등급 지정/변경'),option('defer','보류'),option('discard','폐기'),option('exclude','검수 대상 아님'));if(c.status==='deferred'||c.status==='discarded'||c.status==='out_of_scope')a.append(option('reopen','재검토로 되돌림'));toggleGrade()}function toggleGrade(){$('gradeWrap').style.display=$('action').value==='change'?'block':'none'}function history(events){let box=$('history');box.replaceChildren();if(!events.length){box.textContent='결정 이력이 없습니다.';return}events.slice().reverse().forEach(e=>{let d=document.createElement('div');d.className='event';let a=document.createElement('b');a.textContent=(e.action||'결정')+' · '+(e.status||'');let w=document.createElement('div');w.className='when';w.textContent=(e.decided_at||'')+' · '+(e.actor_id||'');let r=document.createElement('div');r.textContent=e.reason||'사유 없음';d.append(a,w,r);box.append(d)})}async function load(){try{let session=await req(api+'/session');$('session').textContent='로그인 관리자 · '+session.actor_id;let data=await req(qurl());renderRows(data);msg(data.total+'건을 불러왔습니다.'+(data.listed_excludes_discarded&&data.summary.discarded?' (폐기 '+data.summary.discarded+'건은 목록에서 뺐습니다 — 상태 필터를 폐기로 두면 보입니다)':''))}catch(e){msg('불러오기 실패: '+e.message,true)}}function provLabel(c){if(!c.is_actual_document)return '해당 없음(합성)';
const p=c.provenance||{};const st=p.status||'';
if(st==='recorded')return '기록됨';
if(st==='partial')return '미완 — 사용 권한 근거 없음';
return '없음'}
// [E3a-4] 출처 사후 기록. 실측 2026-08-17(223): 실문서 74건 중 62건이 출처는 있는데
// 사용 권한 근거가 없었고, 화면에 그것을 채울 칸이 없어 영원히 미완이었다.
function renderProv(c){const box=$('provBox');if(!box)return;
if(!c.is_actual_document){box.style.display='none';return}
const p=c.provenance||{};box.style.display='';
$('provStatus').textContent=provLabel(c);
$('provSrc').value=p.source_reference||'';
$('provBasis').value=p.authorization_basis||'';
$('provReason').value='';
$('provOrigin').textContent=p.origin?('기록 경로: '+p.origin):'';
$('provSave').disabled=(p.status==='recorded');}
async function saveProv(){try{if(!selected)throw new Error('문서를 먼저 선택하세요.');
const src=$('provSrc').value.trim(),basis=$('provBasis').value.trim();
if(!src||!basis)throw new Error('원천 위치와 사용 권한 근거를 모두 적어야 합니다.');
const r=await req(api+'/'+encodeURIComponent(selected.doc_id)+'/provenance',
 {method:'POST',body:JSON.stringify({source_reference:src,authorization_basis:basis,reason:$('provReason').value.trim()})});
msg('출처를 기록했습니다. 등급·상태는 바뀌지 않습니다.');await show(selected.doc_id)}
catch(e){msg('출처 기록 실패: '+e.message,true)}}
async function show(id){try{let c=await req(api+'/'+encodeURIComponent(id));selected=c;$('detailTitle').textContent=c.doc_id+' · '+c.title;$('metas').replaceChildren();[['상태',c.status],['출처',c.document_origin],['제안',c.proposed_grade||'없음'],['확정',c.final_grade||'미확정'],['글자 수',c.characters.toLocaleString()],['SHA-256',c.document_sha256.slice(0,14)+'…'],['출처 기록',provLabel(c)]].forEach(x=>{let m=document.createElement('span');m.textContent=x[0]+': '+x[1];$('metas').append(m)});$('scope').textContent=c.claim_scope;$('document').textContent=c.text;$('documentRendered').innerHTML=mdToHtml(c.text);$('finalGrade').value=c.final_grade||c.proposed_grade||'S3';$('reason').value='';setActions(c);renderProv(c);history(c.decision_history||[]);$('detail').classList.add('show');$('detail').scrollIntoView({behavior:'smooth',block:'start'});await load()}catch(e){msg('상세 조회 실패: '+e.message,true)}}async function save(){try{if(!selected)throw new Error('문서를 먼저 선택하세요.');const action=$('action').value,reason=$('reason').value.trim();if(['change','defer','discard','exclude'].includes(action)&&!reason)throw new Error('등급 지정·보류·폐기·검수 대상 아님에는 사유가 필요합니다.');let body={action,reason};if(action==='change')body.grade=$('finalGrade').value;let out=await req(api+'/'+encodeURIComponent(selected.doc_id)+'/decision',{method:'POST',body:JSON.stringify(body)});msg('저장 완료: '+out.status);await show(selected.doc_id)}catch(e){msg('저장 실패: '+e.message,true)}}""" + DOC_RENDER_JS + r"""function setDocView(rendered){
  $('documentRendered').style.display=rendered?'':'none';
  $('document').style.display=rendered?'none':'';
  $('viewRendered').classList.toggle('on',rendered);
  $('viewRaw').classList.toggle('on',!rendered);
  $('viewRendered').setAttribute('aria-pressed', rendered?'true':'false');
  $('viewRaw').setAttribute('aria-pressed', rendered?'false':'true');
}
async function upload(){try{let file=$('file').files[0];if(!file)throw new Error('업로드할 파일을 선택하세요.');let src=$('upSource').value.trim();let selv=($('upBasisSel')||{}).value||'';let bas=(selv&&selv!=='__custom__')?selv:$('upBasis').value.trim();if(!src||!bas)throw new Error('원천 위치와 사용 권한 근거를 채우세요. 이 기록이 없으면 나중에 평가셋으로 쓸 수 없습니다.');let form=new FormData();form.append('file',file);form.append('document_origin',$('upOrigin').value);form.append('source_reference',src);form.append('authorization_basis',bas);let ub=$('upload');if(ub){ub.disabled=true;ub.dataset.label=ub.textContent;ub.textContent='분석 중…';}msg('문서를 분석하고 있습니다 — '+file.name+' 에서 글자를 뽑는 중입니다. 파일이 크면 몇 초 걸립니다.');let out=await req(api+'/upload',{method:'POST',body:form,json:false});$('modal').classList.remove('show');$('upSource').value='';$('upBasis').value='';if($('upBasisSel')){$('upBasisSel').value='';$('upBasis').style.display='none';}$('file').value='';if($('dropName'))$('dropName').textContent='여기로 파일을 끌어다 놓거나 위에서 선택하세요';msg('업로드 완료: '+out.doc_id+' · 검토 대기 상태입니다.');await load();await show(out.doc_id)}catch(e){msg('업로드 실패: '+e.message,true)}finally{let b=$('upload');if(b){b.disabled=false;b.textContent=b.dataset.label||'업로드 및 추출';}}}
function qcard(label,value,note,bad){let d=document.createElement('div');d.className='metric';d.innerHTML='<div class="mcap">'+label+'</div><b'+(bad?' style="color:#bf2337"':'')+'>'+value+'</b><small>'+note+'</small>';return d}
// 품질 카드. 부제는 **기준값**만 준다 - 판단은 숫자로 하게 한다.
// ⚠ 종전 부제가 "높을수록 길이가 정답을 흘림" 이었다. 두 가지가 문제였다.
//   1) "흘림" 은 비유인데다 한국어에서 흘림체(글씨체)로 먼저 읽힌다 - 학습 데이터에
//      실제로 `흘림 한글(가).HWP` 같은 서식이 있다.
//   2) 경고선(0.40)이 코드에만 있어 화면의 0.301 이 좋은 값인지 알 수 없었다.
// 무엇이 나쁜지는 카드 아래 설명이 이미 평이하게 말한다("모델이 내용 대신 길이를 외웁니다").
function renderQuality(q){let box=$('qualityBody');box.replaceChildren();if(!q||!q.documents){box.className='empty';box.textContent='등급이 있는 문서가 없어 지표를 낼 수 없습니다.';return}box.className='';let grid=document.createElement('div');grid.className='summary';grid.style.marginTop='0';grid.style.gridTemplateColumns='repeat(auto-fit,minmax(200px,1fr))';const LEAK_MAX=0.40;let leakBad=q.length_only_1nn>LEAK_MAX,expBad=q.grade_token_exposed>0;grid.append(qcard('길이가 등급을 알려주는 정도',q.length_only_1nn.toFixed(3),'무작위 '+q.length_only_random+' · <b>낮을수록 좋습니다</b>. 높으면 모델이 내용 대신 길이를 외운 것이라, 이 셋으로 잰 정확도는 부풀려집니다. '+LEAK_MAX.toFixed(2)+' 초과면 경고.',leakBad),qcard('본문에 등급 노출',q.grade_token_exposed+'건','문서에 답이 적혀 있으면 검수가 무의미',expBad),qcard('실문서 비율',(q.real_ratio*100).toFixed(0)+'%',q.real_documents+' / '+q.documents+'건'),qcard('등급 균형',q.grade_balance_ratio+'x','최다등급 ÷ 최소등급 (1에 가까울수록 균형)'),qcard('본문 길이',q.length.p50.toLocaleString()+'자',q.length.min.toLocaleString()+' ~ '+q.length.max.toLocaleString()+'자 (중앙값)'));box.append(grid);let t=document.createElement('div');t.className='list';t.style.marginTop='26px';let head=document.createElement('div');head.className='rowhead';head.style.gridTemplateColumns='90px 90px repeat(3,1fr)';head.innerHTML='<div>GRADE</div><div>N</div><div>MIN</div><div>P50</div><div>MAX</div>';t.append(head);['TS','S1','S2','S3'].forEach(g=>{let v=q.length_by_grade[g];if(!v)return;let r=document.createElement('div');r.className='candidate';r.style.gridTemplateColumns='90px 90px repeat(3,1fr)';r.style.cursor='default';r.innerHTML='<div class="grade">'+g+'</div><div class="chars" style="text-align:left">'+v.n+'</div><div class="chars" style="text-align:left">'+v.min.toLocaleString()+'</div><div class="chars" style="text-align:left">'+v.p50.toLocaleString()+'</div><div class="chars" style="text-align:left">'+v.max.toLocaleString()+'</div>';t.append(r)});box.append(t);let note=document.createElement('p');note.style.cssText='color:#727c84;font-size:13px;margin-top:16px';note.textContent='등급마다 길이 분포가 겹칠수록 좋습니다. 특정 등급만 길거나 짧으면 모델이 내용 대신 길이를 외웁니다.';box.append(note)}
async function loadLedger(){try{let out=await req(api+'/decisions?limit=300');let f=$('ledgerFilter').value;let ev=out.events.filter(e=>!f||e.action===f);let box=$('ledgerRows');box.replaceChildren();if(!ev.length){box.className='empty';box.textContent='해당하는 결정 기록이 없습니다.';return}box.className='';ev.forEach(e=>{let r=document.createElement('div');r.className='candidate';r.style.gridTemplateColumns='150px 110px 120px minmax(180px,1.6fr) 150px';r.style.cursor='pointer';r.onclick=()=>show(e.doc_id);let lbl={approve:'승인',change:'등급 변경',defer:'보류',discard:'폐기',reject:'반려',reopen:'재검토',exclude:'검수 대상 아님'}[e.action]||e.action;r.innerHTML='<div class="docid">'+e.doc_id+'</div><div><span class="pill '+(e.action==='discard'||e.action==='reject'?'discard':e.action==='defer'?'defer':e.action==='reopen'?'proposed':'fixed')+'">'+lbl+'</span></div><div class="grade">'+(e.proposed_grade||'–')+' → '+(e.final_grade||'–')+'</div><div class="docsub" style="margin:0">'+(e.reason||'사유 없음')+'</div><div class="docsub" style="margin:0">'+(e.actor_id||'')+'<br>'+String(e.decided_at||'').slice(0,19).replace('T',' ')+'</div>';box.append(r)})}catch(e){msg('결정 이력 조회 실패: '+e.message,true)}}
$('refresh').onclick=load;$('filter').onclick=load;$('action').onchange=toggleGrade;$('provSave').onclick=saveProv;$('save').onclick=save;$('openUpload').onclick=()=>$('modal').classList.add('show');// [2026-08-18] 드롭존을 실제로 배선한다. 종전에는 class="drop" 로 **생긴 것만**
// 드롭존이고 dragover/drop 처리가 0건이라, 끌어다 놓으면 브라우저가 그 파일로 이동해
// 검수 화면을 벗어났다.
(function(){
  var dz=$('drop'); if(!dz) return;
  function name(){ var f=$('file').files[0];
    $('dropName').textContent=f?('선택됨 · '+f.name):'여기로 파일을 끌어다 놓거나 위에서 선택하세요'; }
  // [2026-08-19] dragleave 는 **자식 요소로 커서가 옮겨갈 때도** 발생한다.
  // 드롭존 안에 <input>·<p>·<b> 가 있어서, 그 위를 지나갈 때마다 강조가 깜빡였다.
  // enter/leave 를 세어 0 이 될 때만 강조를 끈다.
  var over=0;
  dz.addEventListener('dragenter',function(e){
    e.preventDefault();e.stopPropagation();over++;dz.classList.add('over');});
  dz.addEventListener('dragover',function(e){e.preventDefault();e.stopPropagation();});
  dz.addEventListener('dragleave',function(e){
    e.preventDefault();e.stopPropagation();
    if(--over<=0){over=0;dz.classList.remove('over');}});
  dz.addEventListener('drop',function(e){
    e.preventDefault();e.stopPropagation();over=0;dz.classList.remove('over');
    var f=e.dataTransfer&&e.dataTransfer.files; if(!f||!f.length) return;
    $('file').files=f; name();
  });
  $('file').addEventListener('change',name);
  // 페이지 밖으로 끌어다 놓아 브라우저가 파일을 열어 버리는 것을 막는다.
  ['dragover','drop'].forEach(function(ev){
    window.addEventListener(ev,function(e){ if(!dz.contains(e.target)) e.preventDefault(); });
  });
})();
// 권한 근거 — 목록에 없으면 직접 입력.
(function(){
  var sel=$('upBasisSel'); if(!sel) return;
  sel.addEventListener('change',function(){
    var custom=sel.value==='__custom__';
    $('upBasis').style.display=custom?'block':'none';
    if(custom) $('upBasis').focus();
  });
})();
$('cancelUpload').onclick=()=>$('modal').classList.remove('show');$('upload').onclick=upload;$('ledgerReload').onclick=loadLedger;$('ledgerFilter').onchange=loadLedger;
$('viewRendered').onclick=()=>setDocView(true);$('viewRaw').onclick=()=>setDocView(false);setDocView(true);
load();
</script></body></html>"""




def _login_prefill_block() -> str:
    """토큰 입력란 — settings.console_login_prefill_token 이 있으면 미리 채우고 경고를 띄운다.

    편의를 위해 무인증 페이지에 관리자 토큰을 싣는 것은 사실상 인증 해제다. 조용히 하지 않고
    화면에 그대로 적어, 외부 노출 서버에서 켜져 있으면 바로 눈에 띄게 한다.
    """
    token = str(getattr(settings, "console_login_prefill_token", "") or "").strip()
    if not token:
        return '<textarea id="t" placeholder="eyJhbGciOiJSUzI1NiIs..." autofocus></textarea>'
    return (
        '<div class="note" style="border-left-color:#c47a00;background:#fff8e8">'
        "<b>주의:</b> 이 서버는 접속 토큰이 미리 채워져 있습니다(시연 설정). "
        "이 주소를 여는 누구나 관리자로 들어올 수 있으므로 외부 공개 환경에서는 "
        "<code>CONSOLE_LOGIN_PREFILL_TOKEN</code> 을 비워야 합니다.</div>"
        f'<textarea id="t" autofocus>{_html.escape(token)}</textarea>'
    )


def _render_console_login_html() -> str:
    """토큰을 붙여넣어 세션 쿠키를 심는 진입 화면(무인증·데이터 없음).

    manage.html 은 포털 JWT 를 요구하므로, 쿠키 없이 열면 브라우저에 401 JSON 한 줄만
    보인다("화면이 안 뜬다"). 개발자도구로 document.cookie 를 직접 넣으라고 안내하는 것은
    현장에서 쓸 수 없다. 이 페이지는 **토큰을 URL 에 싣지 않고**(액세스로그·히스토리 잔존
    방지) 입력값을 same-site 쿠키로만 저장한 뒤 관리 화면으로 이동시킨다.
    검증은 그대로 서버가 한다 — 이 화면은 어떤 데이터도 노출하지 않는다.
    """
    return (
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>한국지식재산보호원 | 골든셋 검수 로그인</title><style>"
        ":root{--ink:#111;--red:#e72d44;--line:#dededb;--mute:#70757a}"
        "*{box-sizing:border-box}body{margin:0;color:var(--ink);font:15px Arial,sans-serif}"
        # [2026-08-20] .top/.mark/.brand/.product 를 여기서 따로 갖고 있었고 값이 검수
        # 화면과 달랐다(높이 86 vs 84 · padding 40 vs 34 · 자간 2 vs 1px). 다섯 화면을
        # 합치면서 console_nav.HEADER_CSS 한 곳으로 옮겼다.
        ".wrap{max-width:720px;margin:0 auto;padding:64px 40px}"
        ".eyebrow{font:700 12px monospace;letter-spacing:1px;color:#8d949b}"
        "h1{font-size:40px;line-height:1.1;letter-spacing:-2px;margin:14px 0 10px}"
        "h1 em{font-style:normal;color:var(--red)}p{line-height:1.7;color:#535960}"
        "textarea{width:100%;height:150px;border:1px solid #cfd0ce;padding:13px;font:13px monospace;"
        "word-break:break-all}button{margin-top:18px;width:100%;border:0;background:#111;color:#fff;"
        "padding:15px;font-weight:800;font-size:15px;cursor:pointer}"
        ".msg{margin-top:16px;padding:14px;background:#fff0f1;color:#a31429;display:none}"
        ".note{border-left:4px solid var(--red);padding:16px 20px;background:#fafafa;margin:24px 0}"
        + HEADER_CSS + NAV_CSS +
        "</style><body>"
        # 로그인 화면은 아직 쿠키가 없다 - 메뉴를 눌러도 401 이 정상이다.
        # 그래도 '어떤 화면들이 있는지' 를 보여주는 것이 주소를 외우게 하는 것보다 낫다.
        + header_html("골든셋 검수 로그인")
        + '<main class="wrap"><div class="eyebrow">CONSOLE ACCESS</div>'
        "<h1>관리자 <em>토큰</em>으로<br>로그인합니다.</h1>"
        "<p>발급받은 접속 토큰을 붙여넣으십시오. 토큰은 이 브라우저에만 저장되며 주소창에 남지 않습니다.</p>"
        '<div class="note">누가 등급을 정했는지 기록에 남기기 위해 공유 API 키로는 열 수 없습니다. '
        "토큰의 계정 이름이 검수 이력에 그대로 기록됩니다.</div>"
        + _login_prefill_block()
        + '<button id="go">로그인</button><div class="msg" id="m"></div></main><script>'
        "var $=function(x){return document.getElementById(x)};"
        # [2026-08-20] 쿠키를 **토큰 만료까지** 살린다.
        # 종전에는 Max-Age 가 없어 세션 쿠키였다 — 브라우저를 닫으면 로그인이 풀려서
        # 검수자가 매번 다시 붙여넣어야 했다. 토큰 payload 의 exp 를 읽어 그 시각까지
        # 준다(읽지 못하면 12시간). 토큰보다 오래 살리지 않는 것이 요점이다.
        "function maxAge(v){try{var p=v.split('.')[1].replace(/-/g,'+').replace(/_/g,'/');"
        "p+='='.repeat((4-p.length%4)%4);var e=JSON.parse(atob(p)).exp;"
        "var s=Math.floor(e-Date.now()/1000);return s>60?s:0}catch(err){return 43200}}"
        "async function login(v,quiet){"
        "if(v.split('.').length!==3){if(!quiet){$('m').style.display='block';"
        "$('m').textContent='토큰 형식이 아닙니다. 점(.)으로 구분된 3부분이어야 합니다.'}return false}"
        "document.cookie='koipa_access_token='+v+'; path=/; SameSite=Lax; Max-Age='+maxAge(v);"
        "var r=await fetch('/api/v1/golden/candidates/session',{credentials:'same-origin'});"
        "if(!r.ok){document.cookie='koipa_access_token=; path=/; Max-Age=0';"
        "if(!quiet){$('m').style.display='block';"
        "$('m').textContent='로그인 실패('+r.status+'). 토큰이 만료됐거나 권한이 없습니다.'}return false}"
        "location.href='/api/v1/golden/candidates/manage.html';return true}"
        "$('go').onclick=function(){login($('t').value.trim(),false)};"
        "$('t').addEventListener('keydown',function(e){if(e.key==='Enter'&&e.ctrlKey)$('go').click()});"
        # 토큰이 미리 채워져 있으면 **버튼을 누르지 않아도** 들어간다.
        # 사용자 지시(2026-08-20): 시연 중에 로그인이 막히면 안 된다. 주소만 열면 끝이어야
        # 한다. 실패하면 조용히 화면에 남아 손으로 붙여넣을 수 있게 둔다(quiet=true).
        "(function(){var v=$('t').value.trim();if(v)login(v,true)})();"
        "</script></body></html>"
    )


@html_router.get("/golden/candidates/login.html", response_class=HTMLResponse)
def proxy_gold_console_login_html() -> HTMLResponse:
    """콘솔 진입 화면 — 무인증. 데이터는 없고 토큰을 쿠키로 심기만 한다."""
    return HTMLResponse(content=_render_console_login_html())


@html_router.get("/golden/candidates/manage.html", response_class=HTMLResponse)
def proxy_gold_candidate_manager_html(
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> HTMLResponse:
    """포털 JWT 세션으로만 여는 관리 화면. URL 토큰·API Key 입력을 사용하지 않는다."""
    _console_actor_id(auth)
    return HTMLResponse(content=_render_specledger_gold_console_html())




@html_router.get("/golden/jobs/{job_id}/review.html", response_class=HTMLResponse)
def golden_job_review_html(
    job_id: UUID, t: str | None = Query(default=None)
) -> HTMLResponse:
    """빌드 잡의 후보를 지재원 관리자 검수용 인터랙티브 HTML로 반환(브라우저 직접 접속)."""
    # [#14a] 비밀키 설정 시 서명 URL 토큰(?t=)을 강제 — 무인증 full-text 노출 차단.
    if not _verify_html_token(job_id, t):
        raise HTTPException(status_code=403, detail="invalid or missing signed-URL token (?t=)")
    gate = _job_gate_html(job_id)
    if gate is not None:
        return gate
    # [통합 2026-08-18] 검토본과 서명은 **같은 화면**이다. 같은 job 의 같은 후보를 보는데
    # 화면이 둘이라 검수자가 같은 목록을 두 번 봤다.
    # ⚠ 이 주소는 없앨 수 없다 — 감리정본 화면설계서 UI-04 이고 build_offline_bundle·
    #   demo_e2e_golden·register_review_signoff_job·OPERATION.md·사용설명서가 참조한다.
    #   주소는 남기고 같은 화면을 준다.
    html = GoldenBuildService().render_signoff(job_id, title="골든셋 검수 · 서명")
    if html is None:
        raise HTTPException(status_code=404, detail="golden build review not found (후보 없음)")
    return HTMLResponse(content=html)


@html_router.get("/golden/jobs/{job_id}/signoff.html", response_class=HTMLResponse)
def golden_job_signoff_html(
    job_id: UUID, t: str | None = Query(default=None)
) -> HTMLResponse:
    """빌드 잡의 gold 후보를 화면 서명용 인터랙티브 HTML로 반환(골든셋 검수·브라우저 직접 접속).

    review.html(보기 전용)과 달리 승인/등급변경/거부 폼 + 제출 버튼을 붙여, 제출 시
    POST /golden/jobs/{id}/signoff 로 서명을 보낸다(그 POST 는 require_role 로 보호).
    """
    # [#14a] 비밀키 설정 시 서명 URL 토큰(?t=)을 강제 — 무인증 full-text 노출 차단.
    if not _verify_html_token(job_id, t):
        raise HTTPException(status_code=403, detail="invalid or missing signed-URL token (?t=)")
    gate = _job_gate_html(job_id)
    if gate is not None:
        return gate
    html = GoldenBuildService().render_signoff(job_id)
    if html is None:
        raise HTTPException(status_code=404, detail="golden build signoff view not found (gold 후보 없음)")
    return HTMLResponse(content=html)


@router.get(
    "/golden/jobs/{job_id}/signoff/preflight",
    response_model=GoldenSignoffPreflightResponse,
    summary="서명 전 점검 — 무엇이 막을지 미리 알려준다",
)
def golden_job_signoff_preflight(
    job_id: UUID,
    publish: bool = Query(default=False),
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
) -> GoldenSignoffPreflightResponse:
    """부작용 0. 서명하려는 사람이 그대로 부를 수 있어야 하므로 권한은 POST signoff 와 같다.

    [신원] 검사 대상은 '화면에 미리 채워진 이름' 이 아니라 **실제로 서명할 사람**이다 —
    2026-08-17 부터 신원은 로그인 쿠키(JWT sub)에서만 온다. 공유 API Key 로 부르면 개별
    신원이 없어 빈 값이 되고, 그때는 신원 검사를 건너뛴다(POST 시점에 걸린다).
    """
    reviewer_id, _overridden = resolve_actor_user_id("", auth)
    out = GoldenBuildService().signoff_preflight(
        job_id, reviewer_id=reviewer_id or "", publish=publish
    )
    if out is None:
        raise HTTPException(status_code=404, detail="golden build job not found")
    return GoldenSignoffPreflightResponse(**out)


@router.post(
    "/golden/jobs/{job_id}/signoff",
    response_model=GoldenSignoffResponse,
)
def golden_job_signoff(
    job_id: UUID,
    req: GoldenSignoffRequest,
    auth: dict = Depends(require_role("admin", "reviewer", "kl_backend")),
) -> GoldenSignoffResponse:
    """검수 결정(승인/등급변경/거부)을 골든 후보에 적용해 locked_gold_eval 로 승격(골든셋 검수 서명).

    [신원 무결성] reviewer_id 는 인증 신원(jwt sub 우선)으로 확정한다 — 클라 자칭 위조 차단
    (/confirm 과 동일한 resolve_actor_user_id). 머신/플레이스홀더 reviewer 는 is_human_reviewer
    로 사전 거부(promote_to_locked 도 재차 거부하나 명시적 403 로 UX 개선). 정본 미변경, 라이브
    readiness 반영은 publish=True 일 때만(기본 미리보기).
    """
    reviewer_id, overridden = resolve_actor_user_id(req.actor.user_id, auth)
    # 사유를 함께 준다 — 거부 조건이 다섯 갈래인데 뒤 둘은 이름이 아니라 **설정** 때문에
    # 막히는 것이라, 이유가 없으면 이름만 계속 바꿔 보게 된다.
    reject = human_reviewer_rejection_reason(reviewer_id)
    if reject:
        raise HTTPException(
            status_code=403,
            detail=f"reviewer_id는 실계정이어야 합니다: {reject}",
        )
    result = GoldenBuildService().apply_signoff(
        job_id,
        req.decisions,
        reviewer_id=reviewer_id,
        publish=req.publish,
        dry_run=req.dry_run,
    )
    if result is None:
        raise HTTPException(
            status_code=404, detail="golden build job not found (or no gold candidates)"
        )
    return GoldenSignoffResponse(
        locked=result["locked"],
        rejected=result["rejected"],
        locked_by_grade=result["locked_by_grade"],
        rejected_reasons=result["rejected_reasons"],
        readiness=result["readiness"],
        published=result["published"],
        reviewer_id=reviewer_id,
        overridden=overridden,
        publish_note=result.get("publish_note"),
        dry_run=bool(result.get("dry_run")),
    )
