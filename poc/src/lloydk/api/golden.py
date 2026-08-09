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

from lloydk.api._jwt_auth import require_auth
from lloydk.api._rbac import require_role
from lloydk.api.confirm import bind_authenticated_actor, resolve_actor_user_id
from lloydk.config import settings
from lloydk.golden_tiers import is_human_reviewer
from lloydk.services.job_store import get_default_store
from lloydk.schemas.golden import (
    GoldenBuildRequest,
    GoldenBuildResponse,
    GoldenBuildStatus,
    GoldenCorpusSummary,
    GoldenJobListResponse,
    GoldenJobSummary,
    GoldenRegisterRequest,
    ProxyGoldCandidateDecisionRequest,
    ProxyGoldCandidateDecisionResponse,
    GoldenSignoffRequest,
    GoldenSignoffResponse,
)
from lloydk.services.golden_build_service import GoldenBuildService
from lloydk.services.proxy_gold_candidate_service import ProxyGoldCandidateService

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


def _candidate_manager_html_token() -> str | None:
    """관리 화면 쉘 전용 HMAC. 실제 후보 본문 API는 별도 인증이 필요하다."""
    secret = _golden_url_secret()
    if not secret:
        return None
    mac = hmac.new(secret.encode("utf-8"), b"golden:candidate-manager", hashlib.sha256)
    return mac.hexdigest()[:24]


def _verify_candidate_manager_html_token(token: str | None) -> bool:
    expected = _candidate_manager_html_token()
    return expected is None or (bool(token) and hmac.compare_digest(expected, token))


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
) -> dict:
    """관리 후보 목록. 승인도 approved_proxy일 뿐 locked/실문서 골든이 아니다."""
    if status and status not in {"proposed", "under_review", "approved_proxy", "grade_fixed_unlocked", "deferred", "discarded"}:
        raise HTTPException(status_code=422, detail="invalid candidate status")
    if grade and grade not in {"TS", "S1", "S2", "S3"}:
        raise HTTPException(status_code=422, detail="invalid grade")
    return ProxyGoldCandidateService().list_candidates(status=status, grade=grade, origin=origin, query=query)


@router.get(
    "/golden/candidates/summary",
    dependencies=[Depends(require_role("admin", "kl_backend", "reviewer", "system"))],
    summary="골든셋 관리 콘솔 집계",
)
def proxy_gold_candidate_summary() -> dict:
    """후보의 확정·미확정·보류·폐기·출처·등급 분포를 반환한다."""
    return ProxyGoldCandidateService().summary()


@router.get(
    "/golden/candidates/session",
    summary="골든셋 관리 콘솔 로그인 신원",
)
def proxy_gold_candidate_session(
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> dict:
    """화면에 표시할 인증된 관리자 정보. 클라이언트가 ID를 제출하지 않는다."""
    return {"actor_id": _console_actor_id(auth), "auth_mode": auth.get("mode")}


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


def _render_proxy_candidate_manager_html() -> str:
    """Small browser shell; candidate text is fetched only through authenticated API calls."""
    return r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Proxy Gold 후보 관리</title>
<style>
body{margin:0;background:#f4f7fb;color:#172033;font:14px/1.5 system-ui,sans-serif}main{max-width:1400px;margin:auto;padding:28px}h1{margin:0 0 4px}.notice{background:#fff8df;border:1px solid #f1d686;padding:12px;border-radius:8px}.bar,.filters,.decision{display:flex;gap:10px;align-items:end;flex-wrap:wrap;margin:16px 0}.bar label,.filters label,.decision label{display:grid;gap:4px;font-weight:600}input,select,textarea,button{font:inherit;padding:8px;border:1px solid #b9c4d2;border-radius:6px}button{background:#155eef;color:white;border:0;cursor:pointer}button.secondary{background:#475467}.stats{display:flex;gap:8px;flex-wrap:wrap}.chip{background:#e8eef9;padding:6px 10px;border-radius:999px}.grid{display:grid;grid-template-columns:minmax(420px,1fr) minmax(500px,1.3fr);gap:16px}.pane{background:white;border:1px solid #d9e1ec;border-radius:10px;padding:16px;min-width:0}table{border-collapse:collapse;width:100%}th,td{padding:8px;border-bottom:1px solid #e7ebf1;text-align:left;vertical-align:top}tr:hover{background:#f8fbff}pre{white-space:pre-wrap;word-break:break-word;background:#f8fafc;padding:14px;border-radius:6px;max-height:560px;overflow:auto}#result{min-height:20px;color:#0b6b37}.error{color:#b42318}.muted{color:#667085}.hide{display:none}@media(max-width:900px){.grid{grid-template-columns:1fr}}
</style></head><body><main>
<h1>Proxy Gold 후보 관리</h1><p class="muted">합성 후보를 검수·관리하는 화면입니다. 여기서의 승인은 <b>approved_proxy</b>이며, 실문서 골든 또는 locked 평가 정답지 승격이 아닙니다.</p>
<div class="notice">등급 변경·보류·반려에는 사유가 필수이며, 모든 결정은 관리자·시각·문서 해시와 함께 append-only 원장에 남습니다.</div>
<section class="bar"><label>관리자 API Key<input id="apiKey" type="password" autocomplete="off"></label><label>관리자 ID<input id="actor" placeholder="실계정 ID"></label><button id="load">후보 불러오기</button></section>
<section class="filters"><label>상태<select id="status"><option value="">전체</option><option value="proposed">제안</option><option value="approved_proxy">Proxy 승인</option><option value="deferred">보류</option><option value="rejected">반려</option></select></label><label>제안/확정 등급<select id="grade"><option value="">전체</option><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></label><button class="secondary" id="filter">필터 적용</button></section>
<div id="stats" class="stats"></div><p id="result" role="status"></p>
<section class="grid"><div class="pane"><h2>후보 목록</h2><table><thead><tr><th>ID / 제목</th><th>제안</th><th>상태</th><th>길이</th></tr></thead><tbody id="rows"><tr><td colspan="4" class="muted">API Key를 입력한 뒤 후보를 불러오세요.</td></tr></tbody></table></div>
<div class="pane"><h2 id="detailTitle">후보 상세</h2><div id="meta" class="muted">목록에서 문서를 선택하세요.</div><pre id="document" class="hide"></pre><div id="decisionBox" class="decision hide"><label>결정<select id="action"><option value="approve">제안 등급 승인</option><option value="change">등급 변경 후 Proxy 승인</option><option value="defer">보류</option><option value="reject">반려</option></select></label><label id="gradeBox" class="hide">확정 등급<select id="finalGrade"><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></label><label style="flex:1">사유 / 메모<textarea id="reason" rows="3" placeholder="등급 변경·보류·반려 시 필수"></textarea></label><button id="save">결정 저장</button></div></div></section>
</main><script>
let selected=null; const $=id=>document.getElementById(id);
function headers(){return {'X-API-Key':$('apiKey').value.trim(),'X-Actor-Role':'admin','Content-Type':'application/json'}}
function message(v,err=false){const e=$('result');e.textContent=v;e.className=err?'error':''}
function url(){const q=new URLSearchParams();if($('status').value)q.set('status',$('status').value);if($('grade').value)q.set('grade',$('grade').value);return '/api/v1/golden/candidates'+(q.size?'?'+q:'')}
async function get(path){const r=await fetch(path,{headers:headers()});if(!r.ok)throw new Error((await r.text())||r.status);return r.json()}
function cell(tr,value){const td=document.createElement('td');td.textContent=value;tr.append(td)}
function renderList(data){$('rows').replaceChildren(); $('stats').replaceChildren(); [['총 후보',data.total],...Object.entries(data.by_status||{}),...Object.entries(data.by_proposed_grade||{}).map(([k,v])=>['제안 '+k,v])].forEach(([k,v])=>{const s=document.createElement('span');s.className='chip';s.textContent=k+': '+v;$('stats').append(s)});data.candidates.forEach(c=>{const tr=document.createElement('tr');tr.tabIndex=0;tr.style.cursor='pointer';const first=document.createElement('td');const b=document.createElement('b');b.textContent=c.doc_id;first.append(b,document.createElement('br'),document.createTextNode(c.title));tr.append(first);cell(tr,c.proposed_grade+(c.final_grade?' → '+c.final_grade:''));cell(tr,c.status);cell(tr,String(c.characters));tr.onclick=()=>show(c.doc_id);$('rows').append(tr)});if(!data.candidates.length){$('rows').innerHTML='<tr><td colspan="4" class="muted">조건에 맞는 후보가 없습니다.</td></tr>'}}
async function load(){try{if(!$('apiKey').value.trim())throw new Error('관리자 API Key가 필요합니다.');const data=await get(url());renderList(data);message(data.total+'건을 불러왔습니다.')}catch(e){message('불러오기 실패: '+e.message,true)}}
async function show(id){try{const c=await get('/api/v1/golden/candidates/'+encodeURIComponent(id));selected=c;$('detailTitle').textContent=c.doc_id+' · '+c.title;$('meta').textContent='제안 '+c.proposed_grade+' / 상태 '+c.status+' / '+c.characters+'자 / '+c.document_origin+' / SHA-256 '+c.document_sha256;$('document').textContent=c.text;$('document').classList.remove('hide');$('decisionBox').classList.remove('hide');$('finalGrade').value=c.final_grade||c.proposed_grade;$('reason').value='';message('문서를 열었습니다.');}catch(e){message('상세 조회 실패: '+e.message,true)}}
function actionChanged(){$('gradeBox').classList.toggle('hide',$('action').value!=='change')}
async function save(){try{if(!selected)throw new Error('먼저 후보를 선택하세요.');const actor=$('actor').value.trim();if(!actor)throw new Error('관리자 ID가 필요합니다.');const action=$('action').value,reason=$('reason').value.trim();if(['change','defer','reject'].includes(action)&&!reason)throw new Error('등급 변경·보류·반려에는 사유가 필요합니다.');const body={action,reason,actor:{user_id:actor,role:'admin'}};if(action==='change')body.grade=$('finalGrade').value;const r=await fetch('/api/v1/golden/candidates/'+encodeURIComponent(selected.doc_id)+'/decision',{method:'POST',headers:headers(),body:JSON.stringify(body)});if(!r.ok)throw new Error((await r.text())||r.status);const out=await r.json();message('저장 완료: '+out.doc_id+' → '+out.status);await load();await show(selected.doc_id);}catch(e){message('저장 실패: '+e.message,true)}}
$('load').onclick=load;$('filter').onclick=load;$('action').onchange=actionChanged;$('save').onclick=save;actionChanged();
</script></body></html>"""


def _render_proxy_gold_console_html() -> str:
    """Operational console shell; documents remain behind authenticated APIs."""
    return r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>골든셋 관리 콘솔</title>
<style>
:root{--ink:#172033;--muted:#667085;--line:#dfe5ee;--blue:#315efb;--navy:#111b35;--bg:#f4f7fb;--card:#fff;--green:#057a55;--orange:#b54708;--red:#b42318;--violet:#6938ef}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.55 Inter,ui-sans-serif,system-ui,sans-serif}.shell{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}.side{background:var(--navy);padding:26px 16px;color:#dbe4ff}.brand{font-weight:800;font-size:18px;color:#fff;letter-spacing:-.3px}.brand small{display:block;color:#9fb2e6;font-size:11px;font-weight:600;margin-top:3px}.nav{margin-top:38px}.nav div{padding:11px 12px;border-radius:8px;margin:5px 0}.nav .active{background:#25345c;color:#fff;font-weight:700}.nav .quiet{color:#9fb2e6;font-size:12px;margin-top:22px}.main{padding:30px 36px 48px;max-width:1600px;width:100%;margin:auto}.head{display:flex;justify-content:space-between;gap:20px;align-items:start}.eyebrow{font-size:12px;font-weight:750;color:var(--blue);letter-spacing:.08em;text-transform:uppercase}.head h1{margin:3px 0 2px;font-size:27px;letter-spacing:-.8px}.sub{margin:0;color:var(--muted)}.auth{background:#fff;border:1px solid var(--line);border-radius:12px;padding:10px 12px;display:flex;gap:8px;align-items:end}.auth label,.filters label,.action label{display:grid;gap:4px;font-size:11px;font-weight:700;color:#475467}input,select,textarea,button{font:inherit}input,select,textarea{border:1px solid #c9d3e0;border-radius:7px;padding:8px 9px;background:#fff;color:var(--ink)}button{border:0;border-radius:7px;padding:9px 13px;background:var(--blue);color:#fff;font-weight:700;cursor:pointer}button.secondary{background:#eef2f7;color:#344054}button.danger{background:#b42318}.notice{margin:22px 0;background:#fff8df;border:1px solid #f0d798;border-radius:10px;padding:11px 14px;color:#795c10}.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px}.kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}.kpi .label{color:var(--muted);font-size:12px;font-weight:650}.kpi .num{font-size:27px;font-weight:800;margin:2px 0}.kpi .hint{font-size:11px;color:var(--muted)}.panel{background:var(--card);border:1px solid var(--line);border-radius:12px}.toolbar{margin-top:18px;padding:14px;display:flex;gap:10px;flex-wrap:wrap;align-items:end}.toolbar label{display:grid;gap:4px;font-size:11px;font-weight:700;color:#475467}.toolbar .search{flex:1;min-width:190px}.toolbar .upload{margin-left:auto}.layout{display:grid;grid-template-columns:minmax(460px,1fr) minmax(480px,1.15fr);gap:16px;margin-top:16px}.list{overflow:hidden}.listhead{padding:14px 16px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line)}.listhead h2,.detail h2{font-size:15px;margin:0}.tablewrap{overflow:auto;max-height:650px}table{border-collapse:collapse;width:100%;font-size:13px}th{position:sticky;top:0;background:#f8fafc;color:#667085;font-size:11px;text-align:left;padding:9px 12px;border-bottom:1px solid var(--line)}td{padding:11px 12px;border-bottom:1px solid #eef1f5;vertical-align:top}tr.row{cursor:pointer}tr.row:hover,tr.row.selected{background:#f3f6ff}.id{font-weight:800;color:#273b77}.title{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.badge{display:inline-block;padding:3px 7px;border-radius:99px;font-size:11px;font-weight:750;background:#eef2f7;color:#344054}.badge.fixed{background:#e9fbf1;color:var(--green)}.badge.defer{background:#fff1e7;color:var(--orange)}.badge.discard{background:#fff0f0;color:var(--red)}.badge.review{background:#eef2ff;color:#3d4ab6}.detail{padding:18px;min-width:0}.empty{padding:60px 20px;text-align:center;color:var(--muted)}.docmeta{display:flex;flex-wrap:wrap;gap:7px;margin:10px 0 14px}.meta{background:#f5f7fa;border-radius:6px;padding:4px 7px;color:#475467;font-size:11px}.source{border-left:3px solid var(--violet);background:#f6f3ff;padding:9px 11px;margin:10px 0;color:#4a368d}.doc{white-space:pre-wrap;word-break:break-word;max-height:350px;overflow:auto;background:#fbfcfe;border:1px solid #e8edf4;border-radius:8px;padding:14px;margin:12px 0}.action{border-top:1px solid var(--line);padding-top:14px;display:grid;grid-template-columns:145px 105px minmax(180px,1fr) auto;gap:8px;align-items:end}.history{margin-top:16px;border-top:1px solid var(--line);padding-top:14px}.history h3{font-size:13px;margin:0 0 8px}.event{border-left:2px solid #d6deee;padding:4px 0 4px 10px;margin:7px 0;font-size:12px}.event .when{color:var(--muted)}.modal{position:fixed;inset:0;background:#111827aa;display:none;align-items:center;justify-content:center;padding:20px}.modal.show{display:flex}.dialog{background:#fff;border-radius:14px;max-width:560px;width:100%;padding:22px;box-shadow:0 24px 70px #10182866}.dialog h2{margin:0 0 4px}.drop{border:1.5px dashed #98a6ba;border-radius:9px;padding:18px;text-align:center;margin:14px 0;background:#fafcff}.result{min-height:19px;font-size:12px;color:var(--green);margin-top:10px}.error{color:var(--red)}.hide{display:none}@media(max-width:1100px){.shell{grid-template-columns:1fr}.side{display:none}.kpis{grid-template-columns:repeat(3,1fr)}.layout{grid-template-columns:1fr}.main{padding:22px}.auth{width:100%}.head{flex-direction:column}}@media(max-width:650px){.kpis{grid-template-columns:repeat(2,1fr)}.action{grid-template-columns:1fr}.auth{flex-wrap:wrap}.main{padding:16px}}
</style></head><body><div class="shell"><aside class="side"><div class="brand">LLoyd-K <small>GOLDSET OPERATIONS</small></div><nav class="nav"><div class="active">골든셋 관리</div><div>후보 문서</div><div>검수 이력</div><div class="quiet">합성 후보와 업로드 문서는 Locked Gold와 분리되어 관리됩니다.</div></nav></aside><main class="main">
<header class="head"><div><div class="eyebrow">Golden Set Operations</div><h1>골든셋 관리 콘솔</h1><p class="sub">업로드 · 검토 · 등급 확정 · 보류 · 폐기를 한 흐름으로 관리합니다.</p></div><section class="auth"><span id="session" class="meta">포털 로그인 확인 중</span><button id="refresh" class="secondary">새로고침</button></section></header>
<div class="notice"><b>상태 원칙</b> · 합성 문서가 확정되면 <b>Proxy Gold</b>, 업로드 문서가 등급 확정되면 <b>Unlocked</b>입니다. 둘 다 사람 검수·출처 검증 전에는 Locked Gold가 아닙니다.</div>
<section class="kpis" id="kpis"><article class="kpi"><div class="label">전체</div><div class="num">–</div></article><article class="kpi"><div class="label">등급 확정</div><div class="num">–</div></article><article class="kpi"><div class="label">미확정</div><div class="num">–</div></article><article class="kpi"><div class="label">보류</div><div class="num">–</div></article><article class="kpi"><div class="label">폐기</div><div class="num">–</div></article></section>
<section class="panel toolbar"><label class="search">검색<input id="query" placeholder="문서 ID 또는 제목"></label><label>상태<select id="status"><option value="">전체</option><option value="proposed">제안</option><option value="under_review">업로드 검토중</option><option value="approved_proxy">Proxy 확정</option><option value="grade_fixed_unlocked">등급 확정(미잠금)</option><option value="deferred">보류</option><option value="discarded">폐기</option></select></label><label>등급<select id="grade"><option value="">전체</option><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></label><label>출처<select id="origin"><option value="">전체</option><option value="synthetic">합성 후보</option><option value="uploaded_document">업로드 문서</option></select></label><button class="secondary" id="filter">필터</button><button class="upload" id="openUpload">문서 업로드</button></section>
<section class="layout"><section class="panel list"><div class="listhead"><h2>문서 목록</h2><span id="count" class="badge">0건</span></div><div class="tablewrap"><table><thead><tr><th>문서</th><th>출처</th><th>등급</th><th>상태</th><th>길이</th></tr></thead><tbody id="rows"><tr><td colspan="5" class="empty">API Key를 입력하고 문서를 불러오세요.</td></tr></tbody></table></div></section>
<section class="panel detail"><h2 id="detailTitle">문서 상세</h2><div id="empty" class="empty">왼쪽에서 문서를 선택하면 전문·등급·결정 이력이 표시됩니다.</div><div id="detail" class="hide"><div id="docmeta" class="docmeta"></div><div id="scope" class="source"></div><pre id="document" class="doc"></pre><section class="action"><label>결정<select id="action"></select></label><label id="gradeWrap">확정 등급<select id="finalGrade"><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></label><label>사유 / 메모<textarea id="reason" rows="2" placeholder="등급 변경·보류·폐기 시 필수"></textarea></label><button id="save">결정 저장</button></section><section class="history"><h3>결정 이력</h3><div id="history" class="muted">결정 이력이 없습니다.</div></section></div></section></section>
</main></div><div id="modal" class="modal"><section class="dialog"><h2>문서 업로드</h2><p class="sub">파일은 검토 대기 상태로만 저장됩니다. 자동으로 골든셋에 포함하거나 외부 LLM으로 전송하지 않습니다.</p><div class="drop"><input id="file" type="file" accept=".txt,.md,.csv,.pdf,.doc,.docx,.hwp,.hwpx,.xlsx,.xls,.pptx,.jpg,.jpeg,.png,.tiff"><p class="muted">TXT, PDF, Word, HWP/HWPX, Excel, PPTX, 이미지 OCR 지원</p></div><div style="display:flex;gap:8px;justify-content:flex-end"><button class="secondary" id="cancelUpload">취소</button><button id="upload">업로드 및 추출</button></div></section></div><script>
let selected=null;const $=id=>document.getElementById(id);const api='/api/v1/golden/candidates';
function hdr(json=true){const h={};if(json)h['Content-Type']='application/json';return h}function msg(v,bad=false){const e=$('result')||document.createElement('div');e.textContent=v;e.className=bad?'result error':'result';if(!e.parentNode){e.id='result';document.querySelector('.toolbar').after(e)}}function qurl(){let q=new URLSearchParams();['status','grade','origin','query'].forEach(id=>{let v=$(id).value.trim();if(v)q.set(id,v)});return api+(q.size?'?'+q:'')}async function req(url,opt={}){if(window.__GOLDEN_PREVIEW__){const p=window.__GOLDEN_PREVIEW__;if(opt.method)throw new Error('로컬 미리보기는 읽기 전용입니다. 업로드·등급 변경·폐기는 KL 콘솔 배포 후 사용할 수 있습니다.');if(url.endsWith('/session'))return {actor_id:'로컬 미리보기 · 읽기 전용'};const id=url.split('?')[0].slice((api+'/').length);if(id&&p.by_id[id])return p.by_id[id];return p.list}let r=await fetch(url,{...opt,credentials:'same-origin',headers:{...hdr(opt.json!==false),...(opt.headers||{})}});if(!r.ok)throw new Error((await r.text())||r.status);return r.json()}
function card(label,num,hint){let a=document.createElement('article');a.className='kpi';a.innerHTML='<div class="label"></div><div class="num"></div><div class="hint"></div>';a.children[0].textContent=label;a.children[1].textContent=num;a.children[2].textContent=hint||'';return a}function renderSummary(s){$('kpis').replaceChildren(card('전체',s.total,'관리 문서'),card('등급 확정',s.fixed,'Proxy / Unlocked'),card('미확정',s.unfixed,'검토 필요'),card('보류',s.deferred,'근거 보강 대기'),card('폐기',s.discarded,'감사 이력 보존'))}
function badge(status){let x=document.createElement('span');x.className='badge '+(status==='discarded'?'discard':status==='deferred'?'defer':status.includes('fixed')||status==='approved_proxy'?'fixed':'review');x.textContent={'proposed':'제안','under_review':'검토중','approved_proxy':'Proxy 확정','grade_fixed_unlocked':'등급 확정','deferred':'보류','discarded':'폐기'}[status]||status;return x}function td(tr,text,cls=''){let x=document.createElement('td');x.textContent=text??'–';if(cls)x.className=cls;tr.append(x)}function renderRows(data){$('rows').replaceChildren();$('count').textContent=data.total+'건';renderSummary(data.summary);for(const c of data.candidates){let tr=document.createElement('tr');tr.className='row'+(selected&&selected.doc_id===c.doc_id?' selected':'');let first=document.createElement('td');let id=document.createElement('div');id.className='id';id.textContent=c.doc_id;let title=document.createElement('div');title.className='title';title.textContent=c.title;first.append(id,title);tr.append(first);td(tr,c.document_origin==='synthetic'?'합성 후보':'업로드');td(tr,(c.proposed_grade||'–')+(c.final_grade?' → '+c.final_grade:''));let st=document.createElement('td');st.append(badge(c.status));tr.append(st);td(tr,c.characters.toLocaleString());tr.onclick=()=>show(c.doc_id);$('rows').append(tr)}if(!data.candidates.length)$('rows').innerHTML='<tr><td colspan="5" class="empty">조건에 맞는 문서가 없습니다.</td></tr>'}
async function load(){try{let session=await req(api+'/session');$('session').textContent='로그인 관리자 · '+session.actor_id;let data=await req(qurl());renderRows(data);msg(data.total+'건을 불러왔습니다.')}catch(e){msg('불러오기 실패: '+e.message,true)}}function option(v,t){let o=document.createElement('option');o.value=v;o.textContent=t;return o}function actions(c){let a=$('action');a.replaceChildren();if(c.document_origin==='synthetic'&&c.proposed_grade)a.append(option('approve','제안 등급 승인'));a.append(option('change','등급 지정/변경'));a.append(option('defer','보류'));a.append(option('discard','폐기'));if(c.status==='deferred'||c.status==='discarded')a.append(option('reopen','재검토로 되돌림'));toggleGrade()}function toggleGrade(){$('gradeWrap').classList.toggle('hide',$('action').value!=='change')}function renderHistory(events){let box=$('history');box.replaceChildren();if(!events.length){box.textContent='결정 이력이 없습니다.';return}for(const e of events.slice().reverse()){let d=document.createElement('div');d.className='event';let b=document.createElement('b');b.textContent=(e.action||'결정')+' · '+(e.status||'');let w=document.createElement('div');w.className='when';w.textContent=(e.decided_at||'')+' · '+(e.actor_id||'');let r=document.createElement('div');r.textContent=e.reason||'사유 없음';d.append(b,w,r);box.append(d)}}async function show(id){try{let c=await req(api+'/'+encodeURIComponent(id));selected=c;$('detailTitle').textContent=c.doc_id+' · '+c.title;$('docmeta').replaceChildren();[['상태',c.status],['출처',c.document_origin],['제안 등급',c.proposed_grade||'없음'],['확정 등급',c.final_grade||'미확정'],['글자 수',c.characters.toLocaleString()],['SHA-256',c.document_sha256.slice(0,16)+'…']].forEach(([k,v])=>{let x=document.createElement('span');x.className='meta';x.textContent=k+': '+v;$('docmeta').append(x)});$('scope').textContent=c.claim_scope;$('document').textContent=c.text;$('finalGrade').value=c.final_grade||c.proposed_grade||'S3';$('reason').value='';actions(c);renderHistory(c.decision_history||[]);$('empty').classList.add('hide');$('detail').classList.remove('hide');await load()}catch(e){msg('상세 조회 실패: '+e.message,true)}}async function save(){try{if(!selected)throw new Error('문서를 먼저 선택하세요.');let action=$('action').value,reason=$('reason').value.trim();if(['change','defer','discard'].includes(action)&&!reason)throw new Error('등급 지정·보류·폐기에는 사유가 필요합니다.');let body={action,reason};if(action==='change')body.grade=$('finalGrade').value;let out=await req(api+'/'+encodeURIComponent(selected.doc_id)+'/decision',{method:'POST',body:JSON.stringify(body)});msg('저장 완료: '+out.status);await show(selected.doc_id)}catch(e){msg('저장 실패: '+e.message,true)}}async function upload(){try{let f=$('file').files[0];if(!f)throw new Error('업로드할 파일을 선택하세요.');let form=new FormData();form.append('file',f);let out=await req(api+'/upload',{method:'POST',body:form,json:false});$('modal').classList.remove('show');msg('업로드 완료: '+out.doc_id+' · 검토 대기 상태입니다.');await load();await show(out.doc_id)}catch(e){msg('업로드 실패: '+e.message,true)}}$('refresh').onclick=load;$('filter').onclick=load;$('action').onchange=toggleGrade;$('save').onclick=save;$('openUpload').onclick=()=>$('modal').classList.add('show');$('cancelUpload').onclick=()=>$('modal').classList.remove('show');$('upload').onclick=upload;load();
</script></body></html>"""


def _render_specledger_gold_console_html() -> str:
    """Golden-set console using the supplied Specledger visual language."""
    return r"""<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SPECLEDGER | GOLDSET OPS</title>
<style>
:root{--ink:#111111;--red:#e72d44;--paper:#fff;--soft:#f7f7f5;--line:#e1e1de;--mute:#8f9498;--green:#e8f7ef;--orange:#fff0dc}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font-family:Arial,"Noto Sans KR",sans-serif;line-height:1.45}.top{height:84px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 34px;gap:18px}.mark{width:38px;height:38px;background:#111;border-radius:7px;position:relative;box-shadow:inset 0 0 0 2px #333}.mark:before,.mark:after{content:"";position:absolute;background:var(--red);transform:skew(30deg)}.mark:before{width:19px;height:7px;top:10px;left:9px}.mark:after{width:15px;height:6px;top:20px;left:14px}.brand{font-size:17px;font-weight:900;letter-spacing:1px}.divider{height:23px;border-left:1px solid #cfcfcb}.product{font-size:14px;font-weight:800;letter-spacing:.8px;color:#9da0a1}.topmid{margin:auto;color:#777;font-size:14px}.dot{display:inline-block;width:10px;height:10px;background:#bf6c00;border-radius:50%;margin-right:8px;box-shadow:0 0 0 4px #fff4df}.revision{margin-left:14px;padding:7px 11px;border:1px solid var(--line);border-radius:6px;font:700 12px ui-monospace,monospace;color:#555}.workspace{font-size:11px;font-weight:800;color:#949494;letter-spacing:.5px}.workspace b{margin-left:10px;color:#222;font-size:12px;border:1px solid #d9d9d6;padding:9px 14px;border-radius:6px}.frame{display:grid;grid-template-columns:326px minmax(0,1fr);min-height:calc(100vh - 84px)}.side{border-right:1px solid var(--line);padding:42px 28px;display:flex;flex-direction:column}.cap{font:800 12px ui-monospace,monospace;letter-spacing:.7px;color:#969da3}.workname{margin-top:18px;font-size:22px;font-weight:850;letter-spacing:-.7px}.workdesc{margin-top:11px;color:#6b737b;font-size:14px;max-width:230px}.branch{margin-top:22px;color:#969da3;font:12px ui-monospace,monospace}.nav{margin-top:36px;border-top:1px solid var(--line);padding-top:26px}.nav a{display:flex;text-decoration:none;color:#68717a;gap:20px;padding:13px 14px;margin:2px -14px;font-size:16px}.nav a span{font:12px ui-monospace,monospace;color:#9aa1a6;padding-top:3px}.nav a.active{background:linear-gradient(90deg,#fafaf8 0%,#fff 100%);color:#161616;font-weight:800;border-left:3px solid #161616;padding-left:11px}.ledger{margin-top:auto;border-top:1px solid var(--line);padding-top:24px;font-size:12px;color:#8c949a}.ledger b{display:block;color:#50575d;margin:8px 0}.main{padding:68px min(6.5vw,110px) 90px;max-width:1500px}.hero{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:35px;padding-bottom:56px;border-bottom:1px solid var(--line)}.eyebrow{font:800 12px ui-monospace,monospace;letter-spacing:.8px;color:#8a9298}.hero h1{font-size:clamp(41px,5.1vw,76px);line-height:1.06;letter-spacing:-4px;margin:22px 0 20px;font-weight:850}.hero h1 em{font-style:normal;color:var(--red)}.hero p{color:#667079;font-size:17px;max-width:720px;margin:0}.flow{display:flex;gap:10px;align-items:center;margin-top:28px;color:#7c858c;font:12px ui-monospace,monospace}.flow i{color:var(--red);font-style:normal;font-size:20px}.flow span{border:1px solid var(--line);padding:8px 11px;background:#fff}.gate{border:1px solid var(--line);border-left:5px solid var(--red);align-self:center;padding:25px 24px;min-height:218px}.gate .glabel{font:12px ui-monospace,monospace;color:#8a949a}.gate strong{font:800 43px ui-monospace,monospace;letter-spacing:-2px;color:var(--red);display:block;margin:26px 0 8px}.gate p{font-size:13px;color:#879097}.gate .status{display:inline-block;border-radius:20px;background:var(--green);color:#087341;padding:5px 10px;font-size:11px;font-weight:800}.actions{display:flex;gap:9px;margin-top:20px}.btn{border:1px solid #d8d8d5;background:#fff;padding:11px 14px;font-weight:800;font-size:13px;cursor:pointer}.btn.black{background:#111;color:#fff;border-color:#111}.btn.red{background:var(--red);border-color:var(--red);color:#fff}.summary{margin-top:40px;border-top:3px solid #111;background:#fafaf8;display:grid;grid-template-columns:1.18fr repeat(4,1fr)}.summaryIntro,.metric{padding:29px;border-right:1px solid var(--line);min-height:154px}.summaryIntro h2{margin:12px 0 5px;font-size:25px;letter-spacing:-1px}.summaryIntro p{margin:0;color:#68727a;font-size:13px}.metric:last-child{border-right:0}.metric .mcap{font-size:12px;color:#8a9299}.metric b{font:800 40px ui-monospace,monospace;letter-spacing:-2px;display:block;margin:12px 0 2px}.metric small{color:#8a9299}.section{margin-top:76px}.sectionTop{display:flex;align-items:end;justify-content:space-between;gap:20px;border-bottom:1px solid var(--line);padding-bottom:23px}.secNum{display:inline-block;border:1px solid #dadad7;padding:5px 8px;border-radius:4px;color:#92999f;font:12px ui-monospace,monospace;margin-right:13px;vertical-align:8px}.section h2{display:inline;font-size:42px;letter-spacing:-2px;margin:0}.sectionTop p{color:#727c84;margin:10px 0 0 48px;font-size:15px}.filters{display:flex;gap:7px;align-items:center;flex-wrap:wrap}.filters input,.filters select{border:1px solid var(--line);background:#fff;padding:10px;font-size:12px;min-width:110px}.filters input{width:170px}.list{width:100%}.rowhead,.candidate{display:grid;grid-template-columns:110px minmax(180px,1.2fr) minmax(160px,1.4fr) 130px 86px;gap:16px;align-items:center}.rowhead{padding:13px 12px;background:#f7f7f5;color:#899199;font:11px ui-monospace,monospace}.candidate{width:100%;border:0;border-bottom:1px solid #e8e8e5;background:#fff;padding:18px 12px;text-align:left;cursor:pointer;color:inherit}.candidate:hover,.candidate.selected{background:#fff8f8}.candidate.selected{box-shadow:inset 3px 0 0 var(--red)}.docid{font:12px ui-monospace,monospace;color:#929aa1}.doctitle{font-size:17px;font-weight:850;letter-spacing:-.5px}.docsub{font-size:12px;color:#778088;margin-top:3px}.grade{font:700 15px ui-monospace,monospace}.origin{font-size:12px;color:#737b82}.pill{display:inline-block;border-radius:18px;padding:5px 9px;font-size:11px;font-weight:800}.pill.proposed,.pill.review{background:#eef1f4;color:#56616b}.pill.fixed{background:var(--green);color:#087341}.pill.defer{background:var(--orange);color:#b25c00}.pill.discard{background:#ffe8ea;color:#c72c40}.chars{text-align:right;color:#7f878d;font:12px ui-monospace,monospace}.detail{margin-top:38px;display:none;border-top:3px solid #111}.detail.show{display:grid;grid-template-columns:minmax(0,1fr) 315px}.detailMain{padding:31px 34px 38px;border-right:1px solid var(--line)}.detailMain h3{font-size:27px;margin:10px 0 5px;letter-spacing:-1px}.metas{display:flex;gap:7px;flex-wrap:wrap;margin:15px 0}.metas span{font:11px ui-monospace,monospace;background:#f5f5f3;border:1px solid #e8e8e5;padding:5px 7px;color:#656d74}.scope{border-left:3px solid var(--red);padding:9px 12px;color:#69727a;background:#fcfcfa;font-size:13px}.docbody{white-space:pre-wrap;word-break:break-word;max-height:410px;overflow:auto;padding:20px 0 0;font-size:14px;color:#333}.sideDetail{background:#fafaf8;padding:25px}.sideDetail h4{font:800 12px ui-monospace,monospace;color:#8b949b;margin:0 0 14px}.action label{display:block;font-size:12px;color:#6b7378;margin:12px 0 4px}.action select,.action textarea{width:100%;border:1px solid var(--line);padding:9px;background:#fff}.action textarea{height:95px;resize:vertical}.history{border-top:1px solid var(--line);margin-top:24px;padding-top:18px}.event{border-left:3px solid var(--red);padding:4px 0 4px 10px;margin:10px 0;font-size:12px}.event .when{color:#8b949b;margin-top:3px}.empty{padding:45px 14px;color:#8a9299}.flash{min-height:20px;color:#087341;font-size:13px;margin:14px 0}.flash.error{color:#bf2337}.modal{position:fixed;inset:0;background:#111b;display:none;align-items:center;justify-content:center;padding:20px}.modal.show{display:flex}.dialog{background:#fff;border:1px solid #ddd;max-width:540px;width:100%;padding:28px;box-shadow:10px 10px 0 #111}.dialog h3{font-size:25px;margin:0 0 8px}.drop{border:1px dashed #aab0b3;padding:24px;margin:20px 0;background:#fafaf8}.drop input{width:100%}.dialogActions{display:flex;justify-content:flex-end;gap:8px}@media(max-width:1050px){.frame{grid-template-columns:1fr}.side{display:none}.main{padding:45px 30px}.hero{grid-template-columns:1fr}.summary{grid-template-columns:repeat(3,1fr)}.summaryIntro{grid-column:1/-1}.detail.show{grid-template-columns:1fr}.detailMain{border-right:0}.sideDetail{border-top:1px solid var(--line)}}@media(max-width:700px){.top{padding:0 16px}.topmid,.workspace{display:none}.main{padding:35px 18px}.hero h1{letter-spacing:-2.5px}.summary{grid-template-columns:repeat(2,1fr)}.rowhead{display:none}.candidate{grid-template-columns:1fr 70px}.candidate .origin,.candidate .chars{display:none}.section h2{font-size:31px}.sectionTop{align-items:start;flex-direction:column}.sectionTop p{margin-left:0}.filters{width:100%}.filters input{width:100%}}
</style></head><body><header class="top"><div class="mark"></div><div class="brand">SPECLEDGER</div><div class="divider"></div><div class="product">GOLDSET OPS</div><div class="topmid"><span class="dot"></span>검수 큐 변경사항 <span id="topCount">–</span>건 <span class="revision">GOLDEN-v1</span></div><div class="workspace">WORKSPACE <b>지식재산보호원 AI 영업비밀</b></div></header><div class="frame"><aside class="side"><div class="cap">CURRENT WORKSPACE</div><div class="workname">lloydk-ai</div><div class="workdesc">Lloydk AI Engine for KOIPA Trade-Secret System (PoC)</div><div class="branch">goldset/console-review</div><nav class="nav"><a href="#overview" class="active"><span>01</span>개요</a><a href="#candidates"><span>02</span>골든셋 후보</a><a href="#detail"><span>03</span>등급 확정</a><a href="#detail"><span>04</span>보류·폐기 이력</a><a href="#overview"><span>05</span>품질 지표</a></nav><div class="ledger"><div class="cap">SNAPSHOT LEDGER</div><b id="session">포털 로그인 확인 중</b><div>origin · audit · decision</div></div></aside><main class="main"><section id="overview" class="hero"><div><div class="eyebrow">GOLDEN SET REVIEW CONSOLE</div><h1>검수 가능한<br><em>골든셋</em>을 관리합니다.</h1><p>합성 후보와 업로드 문서를 하나의 증적 모델로 관리하고, 등급 확정·보류·폐기 이력을 문서별 근거와 함께 남깁니다.</p><div class="flow"><span>문서 수집</span><i>→</i><span>후보·검토</span><i>→</i><span>등급 확정</span><i>→</i><span>이력 보존</span></div></div><aside class="gate"><div class="glabel">GOLDSET READINESS</div><strong id="readiness">–</strong><p>현재 확정·미확정·보류 상태를 구분해 표시합니다. Proxy Gold는 Locked Gold와 분리됩니다.</p><span class="status">● 관측값</span><div class="actions"><button class="btn black" id="openUpload">문서 업로드</button><button class="btn" id="refresh">새로고침</button></div></aside></section><section class="summary" id="kpis"><div class="summaryIntro"><div class="cap">DATASET SNAPSHOT</div><h2>골든셋 현황</h2><p>현재 워크스페이스의 검수 상태와 등급 분포입니다.</p></div></section><div id="flash" class="flash"></div><section id="candidates" class="section"><div class="sectionTop"><div><span class="secNum">02</span><h2>골든셋 후보</h2><p>문서를 선택하면 전문과 등급 결정 이력이 열립니다.</p></div><div class="filters"><input id="query" placeholder="문서 ID 또는 제목"><select id="status"><option value="">전체 상태</option><option value="proposed">제안</option><option value="under_review">검토중</option><option value="approved_proxy">Proxy 확정</option><option value="grade_fixed_unlocked">등급 확정</option><option value="deferred">보류</option><option value="discarded">폐기</option></select><select id="grade"><option value="">전체 등급</option><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select><select id="origin"><option value="">전체 출처</option><option value="synthetic">합성 후보</option><option value="uploaded_document">업로드 문서</option></select><button id="filter" class="btn">필터</button></div></div><div class="list"><div class="rowhead"><div>DOCUMENT ID</div><div>DOCUMENT</div><div>GRADE / ORIGIN</div><div>STATUS</div><div>SIZE</div></div><div id="rows"><div class="empty">문서 목록을 불러오는 중입니다.</div></div></div></section><section id="detail" class="detail"><div class="detailMain"><div class="eyebrow">DOCUMENT EVIDENCE</div><h3 id="detailTitle">문서 상세</h3><div id="metas" class="metas"></div><div id="scope" class="scope"></div><pre id="document" class="docbody"></pre></div><aside class="sideDetail"><h4>등급 결정</h4><div class="action"><label>결정</label><select id="action"></select><div id="gradeWrap"><label>확정 등급</label><select id="finalGrade"><option>TS</option><option>S1</option><option>S2</option><option>S3</option></select></div><label>사유 / 메모</label><textarea id="reason" placeholder="등급 지정·보류·폐기에는 사유가 필요합니다."></textarea><button id="save" class="btn black" style="width:100%;margin-top:12px">결정 저장</button></div><div class="history"><h4>DECISION LEDGER</h4><div id="history" class="empty">결정 이력이 없습니다.</div></div></aside></section></main></div><div id="modal" class="modal"><section class="dialog"><div class="eyebrow">DOCUMENT INTAKE</div><h3>문서 업로드</h3><p>업로드 문서는 검토 대기 상태로만 저장됩니다. 자동 골든 승격이나 외부 LLM 전송은 하지 않습니다.</p><div class="drop"><input id="file" type="file" accept=".txt,.md,.csv,.pdf,.doc,.docx,.hwp,.hwpx,.xlsx,.xls,.pptx,.jpg,.jpeg,.png,.tiff"><p>TXT · PDF · Word · HWP/HWPX · Excel · PPTX · OCR 이미지</p></div><div class="dialogActions"><button id="cancelUpload" class="btn">취소</button><button id="upload" class="btn black">업로드 및 추출</button></div></section></div><script>
let selected=null;const $=id=>document.getElementById(id);const api='/api/v1/golden/candidates';function msg(v,bad=false){const e=$('flash');e.textContent=v;e.className=bad?'flash error':'flash'}function hdr(json=true){let h={};if(json)h['Content-Type']='application/json';return h}function qurl(){let q=new URLSearchParams();['status','grade','origin','query'].forEach(id=>{let v=$(id).value.trim();if(v)q.set(id,v)});return api+(q.size?'?'+q:'')}async function req(url,opt={}){if(window.__GOLDEN_PREVIEW__){const p=window.__GOLDEN_PREVIEW__;if(opt.method)throw new Error('로컬 미리보기는 읽기 전용입니다. 업로드·등급 변경·폐기는 KL 콘솔 배포 후 사용할 수 있습니다.');if(url.endsWith('/session'))return {actor_id:'로컬 미리보기 · 읽기 전용'};const id=url.split('?')[0].slice((api+'/').length);if(id&&p.by_id[id])return p.by_id[id];return p.list}const r=await fetch(url,{...opt,credentials:'same-origin',headers:{...hdr(opt.json!==false),...(opt.headers||{})}});if(!r.ok)throw new Error((await r.text())||r.status);return r.json()}function metric(label,value,hint){let d=document.createElement('div');d.className='metric';d.innerHTML='<div class="mcap"></div><b></b><small></small>';d.children[0].textContent=label;d.children[1].textContent=value;d.children[2].textContent=hint;return d}function renderSummary(s){$('kpis').replaceChildren();let intro=document.createElement('div');intro.className='summaryIntro';intro.innerHTML='<div class="cap">DATASET SNAPSHOT</div><h2>골든셋 현황</h2><p>현재 워크스페이스의 검수 상태와 등급 분포입니다.</p>';$('kpis').append(intro,metric('전체 후보',s.total+'건','관리 대상'),metric('등급 확정',s.fixed+'건','Proxy / Unlocked'),metric('미확정',s.unfixed+'건','검토 필요'),metric('보류 · 폐기',(s.deferred+s.discarded)+'건','감사 이력 보존'));$('readiness').textContent=s.fixed+'/'+s.total;$('topCount').textContent=s.unfixed}function pill(status){let e=document.createElement('span');e.className='pill '+(status==='discarded'?'discard':status==='deferred'?'defer':status.includes('fixed')||status==='approved_proxy'?'fixed':status==='under_review'?'review':'proposed');e.textContent={proposed:'제안',under_review:'검토중',approved_proxy:'Proxy 확정',grade_fixed_unlocked:'등급 확정',deferred:'보류',discarded:'폐기'}[status]||status;return e}function renderRows(data){let box=$('rows');box.replaceChildren();renderSummary(data.summary);if(!data.candidates.length){box.innerHTML='<div class="empty">조건에 맞는 문서가 없습니다.</div>';return}data.candidates.forEach(c=>{let row=document.createElement('button');row.className='candidate'+(selected&&selected.doc_id===c.doc_id?' selected':'');let grade=(c.proposed_grade||'–')+(c.final_grade?' → '+c.final_grade:'');row.innerHTML='<div class="docid"></div><div><div class="doctitle"></div><div class="docsub"></div></div><div><div class="grade"></div><div class="origin"></div></div><div></div><div class="chars"></div>';row.children[0].textContent=c.doc_id;row.children[1].children[0].textContent=c.title;row.children[1].children[1].textContent=c.document_origin==='synthetic'?'합성 후보':'업로드 문서';row.children[2].children[0].textContent=grade;row.children[2].children[1].textContent=c.document_origin;row.children[3].append(pill(c.status));row.children[4].textContent=c.characters.toLocaleString();row.onclick=()=>show(c.doc_id);box.append(row)})}function option(v,t){let o=document.createElement('option');o.value=v;o.textContent=t;return o}function setActions(c){let a=$('action');a.replaceChildren();if(c.document_origin==='synthetic'&&c.proposed_grade)a.append(option('approve','제안 등급 승인'));a.append(option('change','등급 지정/변경'),option('defer','보류'),option('discard','폐기'));if(c.status==='deferred'||c.status==='discarded')a.append(option('reopen','재검토로 되돌림'));toggleGrade()}function toggleGrade(){$('gradeWrap').style.display=$('action').value==='change'?'block':'none'}function history(events){let box=$('history');box.replaceChildren();if(!events.length){box.textContent='결정 이력이 없습니다.';return}events.slice().reverse().forEach(e=>{let d=document.createElement('div');d.className='event';let a=document.createElement('b');a.textContent=(e.action||'결정')+' · '+(e.status||'');let w=document.createElement('div');w.className='when';w.textContent=(e.decided_at||'')+' · '+(e.actor_id||'');let r=document.createElement('div');r.textContent=e.reason||'사유 없음';d.append(a,w,r);box.append(d)})}async function load(){try{let session=await req(api+'/session');$('session').textContent='로그인 관리자 · '+session.actor_id;let data=await req(qurl());renderRows(data);msg(data.total+'건을 불러왔습니다.')}catch(e){msg('불러오기 실패: '+e.message,true)}}async function show(id){try{let c=await req(api+'/'+encodeURIComponent(id));selected=c;$('detailTitle').textContent=c.doc_id+' · '+c.title;$('metas').replaceChildren();[['상태',c.status],['출처',c.document_origin],['제안',c.proposed_grade||'없음'],['확정',c.final_grade||'미확정'],['글자 수',c.characters.toLocaleString()],['SHA-256',c.document_sha256.slice(0,14)+'…']].forEach(x=>{let m=document.createElement('span');m.textContent=x[0]+': '+x[1];$('metas').append(m)});$('scope').textContent=c.claim_scope;$('document').textContent=c.text;$('finalGrade').value=c.final_grade||c.proposed_grade||'S3';$('reason').value='';setActions(c);history(c.decision_history||[]);$('detail').classList.add('show');$('detail').scrollIntoView({behavior:'smooth',block:'start'});await load()}catch(e){msg('상세 조회 실패: '+e.message,true)}}async function save(){try{if(!selected)throw new Error('문서를 먼저 선택하세요.');const action=$('action').value,reason=$('reason').value.trim();if(['change','defer','discard'].includes(action)&&!reason)throw new Error('등급 지정·보류·폐기에는 사유가 필요합니다.');let body={action,reason};if(action==='change')body.grade=$('finalGrade').value;let out=await req(api+'/'+encodeURIComponent(selected.doc_id)+'/decision',{method:'POST',body:JSON.stringify(body)});msg('저장 완료: '+out.status);await show(selected.doc_id)}catch(e){msg('저장 실패: '+e.message,true)}}async function upload(){try{let file=$('file').files[0];if(!file)throw new Error('업로드할 파일을 선택하세요.');let form=new FormData();form.append('file',file);let out=await req(api+'/upload',{method:'POST',body:form,json:false});$('modal').classList.remove('show');msg('업로드 완료: '+out.doc_id);await load();await show(out.doc_id)}catch(e){msg('업로드 실패: '+e.message,true)}}$('refresh').onclick=load;$('filter').onclick=load;$('action').onchange=toggleGrade;$('save').onclick=save;$('openUpload').onclick=()=>$('modal').classList.add('show');$('cancelUpload').onclick=()=>$('modal').classList.remove('show');$('upload').onclick=upload;load();
</script></body></html>"""


def _render_actual_document_intake_html() -> str:
    """Focused intake page for real S2/S3 source documents."""
    return """<!doctype html><html lang=\"ko\"><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>SPECLEDGER | 실문서 Intake</title><style>:root{--ink:#111;--red:#e72d44;--line:#dededb;--mute:#70757a}*{box-sizing:border-box}body{margin:0;color:var(--ink);font:15px Arial,sans-serif}.top{height:86px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 40px;gap:16px}.mark{width:36px;height:36px;background:#111;border-radius:7px;position:relative}.mark:after{content:'';position:absolute;background:var(--red);width:17px;height:6px;transform:skew(25deg);left:10px;top:12px}.brand{font-weight:900;letter-spacing:2px}.product{border-left:1px solid var(--line);padding-left:16px;color:#999;font-weight:700;letter-spacing:1px}.wrap{max-width:1150px;margin:0 auto;padding:74px 48px}.eyebrow{font:700 12px monospace;letter-spacing:1px;color:#8d949b}.grid{display:grid;grid-template-columns:1.25fr .75fr;gap:54px;border-top:3px solid #111;padding-top:38px}h1{font-size:54px;line-height:1.06;letter-spacing:-4px;margin:18px 0}h1 em{font-style:normal;color:var(--red)}p{line-height:1.7;color:#535960}.notice{border-left:4px solid var(--red);padding:18px 22px;background:#fafafa;margin:26px 0 32px}.form{border:1px solid var(--line);padding:28px}.form h2{margin:0 0 6px;font-size:21px}label{display:block;font-weight:700;margin:20px 0 8px}input,select{width:100%;border:1px solid #cfd0ce;padding:13px;background:white;font:inherit}input[type=file]{border-style:dashed}small{display:block;color:var(--mute);line-height:1.5;margin-top:6px}button{margin-top:26px;width:100%;border:0;background:#111;color:white;padding:15px;font-weight:800;font-size:15px;cursor:pointer}.result{margin-top:18px;padding:14px;background:#eef8f1;color:#17643b}.error{background:#fff0f1;color:#a31429}.side{padding-top:15px}.side h2{font-size:20px}.rule{border-top:1px solid var(--line);padding:18px 0}.rule b{display:block;margin-bottom:5px}.rule span{color:var(--mute);font-size:14px;line-height:1.5}@media(max-width:760px){.top{padding:0 20px}.wrap{padding:40px 24px}.grid{grid-template-columns:1fr}h1{font-size:42px}}</style><body><header class=\"top\"><div class=\"mark\"></div><div class=\"brand\">SPECLEDGER</div><div class=\"product\">GOLDSET OPS · REAL DOCUMENT INTAKE</div></header><main class=\"wrap\"><div class=\"eyebrow\">S2/S3 REAL-DOCUMENT INTAKE</div><div class=\"grid\"><section><h1>실제 문서는<br><em>별도 증적</em>으로<br>받습니다.</h1><p>고객사 문서가 아니어도 됩니다. 조직이 사용 권한을 가진 실제 운영·품질·교육·공개 안내 문서를 S2/S3 검수 후보로 등록합니다.</p><div class=\"notice\"><b>중요:</b> 이 페이지의 등록은 Locked Gold 승격이 아닙니다. 원문 해시·원천·권한을 남긴 뒤, 사람 검수로만 최종 평가셋에 편입할 수 있습니다.</div><section class=\"form\"><h2>실문서 후보 등록</h2><small>파일은 외부 LLM으로 전송하지 않습니다.</small><label for=\"file\">원문 파일</label><input id=\"file\" type=\"file\" accept=\".txt,.md,.csv,.pdf,.doc,.docx,.hwp,.hwpx,.xlsx,.xls,.pptx,.jpg,.jpeg,.png,.tiff\"><label for=\"origin\">문서 출처</label><select id=\"origin\"><option value=\"organization_real\">조직 보유 실문서 — S2/S3</option><option value=\"public_real\">공개 실문서 — S3 우선</option></select><label for=\"source\">원천 위치 / 식별 정보</label><input id=\"source\" placeholder=\"예: 품질관리/운영절차/2026 또는 공개기관 URL\"><label for=\"basis\">사용 권한 또는 공개 근거</label><input id=\"basis\" placeholder=\"예: 소유부서 검수용 사용 승인 / 공개 라이선스\"><button id=\"submit\">검토 후보로 등록</button><div id=\"result\"></div></section></section><aside class=\"side\"><div class=\"eyebrow\">INTAKE RULES</div><h2>등록 기준</h2><div class=\"rule\"><b>S3 · 공개·일반</b><span>공식 공지, 공개 매뉴얼, 일반 안내. 기관·버전·공개 위치를 기록합니다.</span></div><div class=\"rule\"><b>S2 · 조직 내부</b><span>운영 절차, 품질 이력, 교육 자료, 변경·장애 후속조치 등. 소유부서의 검수·사용 근거를 기록합니다.</span></div><div class=\"rule\"><b>제외</b><span>고객사 원문, 반출 승인이 없는 문서, 권한이 불명확한 파일은 등록하지 않습니다.</span></div><div class=\"rule\"><b>다음 단계</b><span>관리콘솔에서 등급을 잠정 지정하고, 사람 검수 완료 뒤에만 별도 Locked Gold 승격 절차를 사용합니다.</span></div></aside></div></main><script>const $=x=>document.getElementById(x);function out(t,b=false){const e=$('result');e.textContent=t;e.className=b?'result error':'result'}$('submit').onclick=async()=>{const f=$('file').files[0],s=$('source').value.trim(),b=$('basis').value.trim();if(!f||!s||!b){out('원문 파일·원천 위치·사용 근거를 모두 입력하세요.',true);return}const d=new FormData();d.append('file',f);d.append('document_origin',$('origin').value);d.append('source_reference',s);d.append('authorization_basis',b);try{const r=await fetch('/api/v1/golden/candidates/upload',{method:'POST',body:d,credentials:'same-origin'});if(!r.ok)throw new Error(await r.text());const x=await r.json();out('등록 완료: '+x.doc_id+' · 검토 대기 상태입니다. 관리콘솔에서 등급을 지정하세요.')}catch(e){out('등록 실패: '+e.message,true)}};</script></body></html>"""


@html_router.get("/golden/candidates/manage.html", response_class=HTMLResponse)
def proxy_gold_candidate_manager_html(
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> HTMLResponse:
    """포털 JWT 세션으로만 여는 관리 화면. URL 토큰·API Key 입력을 사용하지 않는다."""
    _console_actor_id(auth)
    return HTMLResponse(content=_render_specledger_gold_console_html())


@html_router.get("/golden/candidates/actual-intake.html", response_class=HTMLResponse)
def proxy_gold_actual_document_intake_html(
    auth: dict = Depends(require_role("admin", "kl_backend")),
) -> HTMLResponse:
    """Portal-JWT protected intake for non-customer, real S2/S3 source documents."""
    _console_actor_id(auth)
    return HTMLResponse(content=_render_actual_document_intake_html())


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
