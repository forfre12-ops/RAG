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

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from lloydk.api._jwt_auth import require_auth
from lloydk.api._rbac import require_role
from lloydk.api.confirm import bind_authenticated_actor, resolve_actor_user_id
from lloydk.config import settings
from lloydk.golden_tiers import is_human_reviewer
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenRegisterRequest,
    GoldenSignoffRequest,
    GoldenSignoffResponse,
)
from lloydk.services.golden_build_service import GoldenBuildService

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


def _job_gate_html(job_id: UUID) -> HTMLResponse | None:
    """렌더 전 잡 상태를 확인해 not-found/진행중/실패를 구분한 HTMLResponse 를 돌려주거나,
    done 이면 None(호출부가 실제 렌더 진행). 진행중·실패·오id 를 같은 404 로 뭉개고 실패 error 를
    버리던 것 보완 — 검수자가 '기다릴지 재빌드할지'를 화면에서 판단하게 한다.
    """
    st = GoldenBuildService().get_status(job_id)
    if st is None:
        raise HTTPException(status_code=404, detail="golden build job not found")
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
    html = GoldenBuildService().render_review(job_id)
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
    if not is_human_reviewer(reviewer_id):
        raise HTTPException(
            status_code=403,
            detail=f"reviewer_id는 실계정이어야 합니다(머신/플레이스홀더 거부): {reviewer_id!r}",
        )
    result = GoldenBuildService().apply_signoff(
        job_id,
        req.decisions,
        reviewer_id=reviewer_id,
        publish=req.publish,
        dual_for_upper=req.dual_for_upper,
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
    )
