"""관리 콘솔 로그인 — 아이디+비밀번호 → `koipa_access_token` 쿠키.

[2026-10-09 #9 이미지 분리] 원래 golden.py 안에 있었다(토큰 입력으로 골든셋 검수 화면에
들어가는 용도로 만들어졌기 때문). 그런데 이 로그인 화면·쿠키 발급이 **고객사 관리자
콘솔(admin.html) 전체의 유일한 로그인 경로**이기도 했다 — admin.html 의 `LOGIN_URL`
상수와 401 리다이렉트가 전부 이 경로를 가리킨다(`?next=` 로 로그인 뒤 돌아갈 곳을 바꿀 뿐).
golden.py 를 고객사 이미지에서 통째로 빼면서 그 사실을 실제로 띄워보고 나서 발견했다 —
golden.py 만 빼면 고객사는 관리 콘솔에 **아무도 로그인할 수 없게** 된다(검수 화면뿐 아니라
모델 활성화·재학습 화면까지 전부). 그래서 로그인 발급 로직만 떼어 양쪽 이미지에 항상
남는 모듈로 옮긴다 — golden.py 는 여기서 만드는 `_console_actor_id` 를 가져다 쓴다.

URL 경로는 바꾸지 않았다(`/golden/candidates/login.html` 등 그대로) — admin.html 의
하드코딩된 LOGIN_URL, scripts/setup_console_test_login.py 가 안내하는 주소, golden.py
자신의 manage.html 안 LOGIN_URL JS 상수 전부 고칠 필요가 없게.
"""
from __future__ import annotations


from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from koipa.api._jwt_auth import SigningUnavailable, sign_jwt
from koipa.api._rbac import require_role
from koipa.api.rate_limit import limiter
from koipa.config import settings
from koipa.console_nav import (
    CONSOLE_THEME_CSS,
    HEADER_CSS,
    NAV_CSS,
    REVIEW_SCREEN_EXCLUDE,
    header_html,
)
from koipa.schemas.golden import ReviewerLoginRequest
from koipa.services import reviewer_credentials

router = APIRouter(tags=["console-login"])
html_router = APIRouter(tags=["console-login"])


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


_CONSOLE_ADMIN_ROLES = frozenset({"admin", "kl_backend"})


def _is_console_admin(auth: dict) -> bool:
    roles = set(auth.get("actor_roles") or ())
    if not roles and auth.get("actor_role"):
        roles = {auth["actor_role"]}
    return not roles.isdisjoint(_CONSOLE_ADMIN_ROLES)


def _demo_prefill_block() -> str:
    """명시적으로 설정한 폐쇄망 시연 계정 진입 버튼만 만든다.

    과거에는 무인증 로그인 페이지의 textarea에 JWT를 넣고, 페이지가 열리자마자
    ``document.cookie``로 옮겼다. 시연 편의와 별개로 토큰이 페이지 소스ㆍ브라우저 DOM에
    남고, 주소를 여는 것만으로 세션이 생기는 위험한 경로였다. unsafe 프리필은 여전히
    폐쇄망 시연 전용이지만, 서버가 HttpOnly 쿠키를 발급하고 사용자가 명시적으로 눌러야 한다.
    """
    token = str(getattr(settings, "console_login_prefill_token", "") or "").strip()
    if not token:
        return ""
    return (
        '<div class="note" style="border-left-color:#c47a00;background:#fff8e8">'
        "<b>폐쇄망 시연 설정:</b> 이 서버에는 시연용 접속 수단이 설정돼 있습니다. "
        "외부 공개 환경에서는 사용할 수 없으며, 시연 계정으로 들어가면 모든 작업이 그 계정의 이력으로 남습니다. "
        "접속 토큰 자체는 이 페이지나 브라우저에 표시하지 않습니다.</div>"
        '<button type="button" id="goPrefill">시연 계정으로 계속</button><div class="msg" id="mPrefill"></div>'
    )


def _render_console_login_html() -> str:
    """아이디·비밀번호로 서버가 HttpOnly 세션을 발급하는 진입 화면."""
    return (
        '<!doctype html><html lang="ko"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>한국지식재산보호원 | 관리 콘솔 로그인</title><style>"
        + CONSOLE_THEME_CSS +
        # 로그인만 가운데 인증 패널이라는 역할은 유지하되, 색·글꼴·테두리·포커스는
        # 관리자·골든셋 화면과 같은 CONSOLE_THEME_CSS를 쓴다.
        "body{background:var(--bg-surface)}"
        ".wrap{max-width:720px;margin:32px auto 72px;padding:28px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius-lg);box-shadow:var(--shell-panel-shadow)}"
        ".eyebrow{font:800 11px var(--font-mono);letter-spacing:1px;color:var(--text-dim)}"
        "h1{font-size:clamp(28px,3vw,36px);line-height:1.18;letter-spacing:-.045em;margin:12px 0 10px}"
        "h1 em{font-style:normal;color:var(--text)}p{line-height:1.7;color:var(--text-soft)}"
        "input[type=text],input[type=password]{width:100%;padding:13px;"
        "font:15px inherit;margin-top:10px}"
        "input:focus{border-color:var(--text)}"
        "button{margin-top:18px;width:100%;border:1px solid var(--bg-inverse);border-radius:var(--radius-sm);background:var(--bg-inverse);color:#fff;"
        "padding:15px;font-weight:800;font-size:15px;cursor:pointer}button:hover{opacity:.86}"
        ".msg{margin-top:16px;padding:14px;border-radius:8px;background:#fff1f2;color:#b42318;display:none}"
        ".note{border-left:3px solid var(--text);padding:16px 20px;border-radius:var(--radius-sm);background:var(--bg-surface);margin:24px 0}"
        ".divider{display:flex;align-items:center;gap:14px;margin:28px 0 20px;color:var(--text-dim);font-size:13px}"
        "@media(max-width:760px){.wrap{margin:22px 14px;padding:32px 24px}h1{font-size:32px}}"
        ".divider::before,.divider::after{content:'';flex:1;height:1px;background:var(--border-strong)}"
        + HEADER_CSS + NAV_CSS +
        "</style><body>"
        # 로그인 화면은 아직 쿠키가 없다 - 메뉴를 눌러도 401 이 정상이다.
        # 그래도 '어떤 화면들이 있는지' 를 보여주는 것이 주소를 외우게 하는 것보다 낫다.
        + header_html("관리 콘솔 로그인", exclude=REVIEW_SCREEN_EXCLUDE, show_logout=False)
        + '<main class="wrap"><div class="eyebrow">관리 콘솔 인증</div>'
        "<h1>아이디와 <em>비밀번호</em>로<br>로그인합니다.</h1>"
        "<p>발급받은 아이디와 비밀번호를 입력하십시오. 이 브라우저에만 로그인 상태가 저장됩니다.</p>"
        '<div class="note">누가 등급을 정했는지 기록에 남기기 위해 공유 계정으로는 열 수 없습니다. '
        "로그인한 아이디가 검수 이력에 그대로 기록됩니다.</div>"
        '<input id="u" type="text" placeholder="아이디" autocomplete="username" autofocus>'
        '<input id="p" type="password" placeholder="비밀번호" autocomplete="current-password">'
        '<button id="goPw">로그인</button><div class="msg" id="mPw"></div>'
        + _demo_prefill_block()
        + '</main><script>'
        "var $=function(x){return document.getElementById(x)};"
        "async function loginWithPassword(u,p){"
        "var r=await fetch('/api/v1/golden/candidates/login',{method:'POST',credentials:'same-origin',"
        "headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});"
        "if(!r.ok){$('mPw').style.display='block';"
        "$('mPw').textContent=(r.status===404)"
        "?'이 서버에서는 아이디+비밀번호 로그인이 설정되지 않았습니다. 관리자에게 계정 발급을 요청하십시오.'"
        ":'로그인 실패. 아이디 또는 비밀번호를 확인하십시오.';return false}"
        "return afterLogin()}"
        "$('goPw').onclick=function(){loginWithPassword($('u').value.trim(),$('p').value)};"
        "$('p').addEventListener('keydown',function(e){if(e.key==='Enter')$('goPw').click()});"
        # [2026-08-24] 로그인 뒤 돌아갈 곳. 기본은 검수 화면이지만 `?next=` 가 붙어 오면
        # 그리로 되돌린다 — 시연 화면에서 401 을 만나 여기로 튕겨 온 사람이 검수 화면에
        # 떨어지면 시연 대본이 끊긴다. 열린 리다이렉트를 막으려고 **같은 오리진의
        # 절대경로 한 종류만** 받는다(`/` 로 시작하고 `//` 도 역슬래시도 아닌 것).
        "function dest(session){try{var n=new URLSearchParams(location.search).get('next');"
        "if(n&&n.charAt(0)==='/'&&n.charAt(1)!=='/'&&n.indexOf(String.fromCharCode(92))===-1)return n}catch(e){}"
        "return session&&session.actor_role==='reviewer'?'/api/v1/golden/candidates/manage.html'+(location.hash||''):'/console/admin.html'+(location.hash||'')}"
        "async function afterLogin(){var r=await fetch('/api/v1/golden/candidates/session',{credentials:'same-origin'});var s=r.ok?await r.json():{};location.href=dest(s);return true}"
        "async function loginWithPrefill(){var b=$('goPrefill'),m=$('mPrefill');if(!b)return false;b.disabled=true;"
        "var r=await fetch('/api/v1/golden/candidates/prefill-login',{method:'POST',credentials:'same-origin'});"
        "if(!r.ok){m.style.display='block';m.textContent='시연 계정 로그인에 실패했습니다('+r.status+'). 관리자에게 설정을 확인하십시오.';b.disabled=false;return false}"
        "var s=await fetch('/api/v1/golden/candidates/session',{credentials:'same-origin'});"
        "if(!s.ok){m.style.display='block';m.textContent='시연 계정 권한을 확인하지 못했습니다.';b.disabled=false;return false}location.href=dest(await s.json());return true}"
        "var prefill=$('goPrefill');if(prefill)prefill.onclick=loginWithPrefill;"
        # 이미 쿠키가 살아 있으면 로그인 화면을 거치지 않는다 — 이 화면이 공용 메뉴의
        # 「검증문서 후보 관리」 목적지라, 붙여넣을 것이 없는 사람에게는 한 단계 군더더기다.
        # 쿠키가 없거나 만료면 그대로 아래 폼으로 남는다.
        "(async function(){try{var r=await fetch('/api/v1/golden/candidates/session',"
        "{credentials:'same-origin'});if(r.ok){location.href=dest(await r.json());return}}catch(e){}"
        # 프리필 토큰이 있어도 자동 로그인하지 않는다. 이 페이지를 단순히 열었을 때
        # 관리자 세션이 생기면 안 되며, 시연 계정 버튼을 명시적으로 누르게 한다.
        "})();"
        "</script></body></html>"
    )


@html_router.get("/golden/candidates/login.html", response_class=HTMLResponse)
def proxy_gold_console_login_html() -> HTMLResponse:
    """콘솔 진입 화면 — 무인증. 성공 시 서버가 HttpOnly 세션을 발급한다."""
    return HTMLResponse(content=_render_console_login_html())


@html_router.get("/golden/candidates/manage.html", include_in_schema=False)
def redirect_legacy_gold_console(
    auth: dict = Depends(require_role("admin", "kl_backend", "reviewer")),
) -> Response:
    """관리자는 운영 콘솔, 검수자는 권한이 분리된 검수 문서함으로 보낸다."""
    _console_actor_id(auth)
    if not _is_console_admin(auth):
        # console_login 은 golden.py 보다 먼저 등록돼 이 고정 URL을 먼저 잡는다.
        # 검수자까지 관리자 정적 화면으로 보내면 화면 API는 막혀도 관리자 작업공간을
        # 보게 된다. 실행 시점 import로 순환 의존 없이 원래의 제한된 검수 화면을 돌려준다.
        from koipa.api.golden import proxy_gold_candidate_manager_html

        return proxy_gold_candidate_manager_html(auth)
    return RedirectResponse(
        url="/console/admin.html",
        status_code=307,
        headers={"Cache-Control": "no-store"},
    )


@html_router.post("/golden/candidates/prefill-login", summary="폐쇄망 시연 계정 로그인")
def proxy_gold_console_prefill_login(request: Request) -> Response:
    """명시적으로 요청한 폐쇄망 시연에만 프리필 JWT를 HttpOnly 쿠키로 발급한다.

    프리필이 비어 있으면 이 경로 자체를 제공하지 않는다. 토큰 문자열은 응답 본문ㆍHTMLㆍ
    JavaScript 어디에도 실리지 않는다. 이 경로는 안전한 인증 수단이 아니므로 기존 설정의
    하드닝 배포 차단(``CONSOLE_LOGIN_PREFILL_ALLOW_UNSAFE``)도 그대로 적용된다.
    """
    token = str(getattr(settings, "console_login_prefill_token", "") or "").strip()
    if not token:
        raise HTTPException(status_code=404, detail="demo prefill login is not configured")
    response = JSONResponse({"ok": True, "mode": "demo-prefill"})
    response.set_cookie(
        "koipa_access_token",
        token,
        max_age=12 * 3600,
        path="/",
        samesite="lax",
        httponly=True,
        secure=_request_uses_https(request),
    )
    return response


# 세션 쿠키 수명 — 서버가 직접 서명하는 토큰(아이디+비밀번호 로그인)의 만료.
# CLI 로 발급하는 토큰(scripts/setup_console_test_login.py 기본 30일)과 성격이 달라 따로 둔다.
_PASSWORD_LOGIN_TTL_SECONDS = 60 * 86400


def _request_uses_https(request: Request) -> bool:
    """TLS 역프록시 뒤의 실제 브라우저 요청이면 Secure 쿠키를 쓴다.

    개발·설치 리허설의 loopback HTTP 로그인도 지원해야 하므로 무조건 Secure로 고정하지는
    않는다. 콘솔 프록시는 ``X-Forwarded-Proto``를 설정하므로, 그 경로에서는 브라우저가
    평문 HTTP 연결로 세션 쿠키를 재전송하지 않게 한다.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",", 1)[0].strip().lower()
    return request.url.scheme.lower() == "https" or forwarded == "https"


@html_router.post("/golden/candidates/logout", status_code=204, summary="관리 콘솔 로그아웃")
def proxy_gold_console_logout(request: Request) -> Response:
    """브라우저 세션 쿠키를 만료시킨다.

    비밀번호 로그인은 ``HttpOnly`` 쿠키를 발급하므로 화면 JavaScript가 직접 지울 수 없다.
    로그인 때와 같은 path·SameSite·Secure 속성으로 만료 Set-Cookie를 내려 모든 로그인
    방식(비밀번호, 프리필, 레거시 토큰)의 브라우저 세션을 일관되게 끝낸다.
    """
    response = Response(status_code=204)
    response.delete_cookie(
        "koipa_access_token",
        path="/",
        samesite="lax",
        httponly=True,
        secure=_request_uses_https(request),
    )
    return response


@html_router.post("/golden/candidates/login", summary="검수자 아이디+비밀번호 로그인")
@limiter.limit("10/minute")
def proxy_gold_console_password_login(request: Request, body: ReviewerLoginRequest) -> Response:
    """비밀번호를 확인하고, 맞으면 서버가 직접 세션 토큰을 서명해 쿠키로 심는다.

    실패해도 계정 존재 여부를 구분해 알려주지 않는다(둘 다 같은 401) — 계정 목록을 긁어내는
    통로가 되면 안 된다. settings.console_jwt_private_key_path 가 없으면 이 기능 자체가
    꺼져 있다는 뜻으로 404(기존 "토큰 붙여넣기" 로그인만 켜진 배포에서는 이 경로가 아예 없다).
    """
    roles = reviewer_credentials.verify_password(body.username, body.password)
    if roles is None:
        raise HTTPException(status_code=401, detail="invalid username or password")
    try:
        token = sign_jwt(sub=body.username, roles=roles, ttl_seconds=_PASSWORD_LOGIN_TTL_SECONDS)
    except SigningUnavailable as e:
        raise HTTPException(
            status_code=404,
            detail=f"password login is not configured on this server: {e}",
        ) from e
    resp = JSONResponse({"ok": True, "username": body.username, "roles": list(roles)})
    resp.set_cookie(
        "koipa_access_token", token, max_age=_PASSWORD_LOGIN_TTL_SECONDS,
        # 비밀번호 로그인은 서버가 직접 Set-Cookie 하므로 브라우저 JavaScript가 토큰을
        # 읽을 이유가 없다. HttpOnly로 고정해 XSS가 검수자 세션 JWT를 탈취하지 못하게 한다.
        # HTTPS 콘솔 프록시 경로에서는 Secure도 설정한다. loopback HTTP 설치 리허설은
        # 쿠키를 받지 못하게 되는 것을 피하기 위해 실제/전달된 스킴을 따른다.
        path="/", samesite="lax", httponly=True, secure=_request_uses_https(request),
    )
    return resp


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
    out = {
        "actor_id": _console_actor_id(auth),
        "auth_mode": auth.get("mode"),
        "actor_role": auth.get("actor_role") or "",
    }
    # 목록 화면이 처음 열릴 때 좁혀 볼 검수 배치(설정 GOLDEN_DEFAULT_REVIEW_BATCH). 비면 전체.
    # 후보 풀에는 예전 적재분이 그대로 쌓여 있어(3,598건 = 이번 회차 1,731 + 중복 적재 800 + 옛 후보 1,067) 기본을 전체로
    # 두면 이번에 검수할 문서가 옛 문서에 묻힌다.
    # ⚠ 관리자 세션에만 싣는다. 검수자 세션은 "호출자 정보만" 돌려주는 계약이다(배정으로 좁혀진
    #   검수자에게 배정 밖 회차의 이름이 새면 안 된다 — test_session_reveals_only_the_caller).
    if _is_console_admin(auth):
        out["default_review_batch"] = str(getattr(settings, "golden_default_review_batch", "") or "")
    return out
