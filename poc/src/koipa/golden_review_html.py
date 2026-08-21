"""골든셋 검수 · 서명 화면 HTML 렌더 (G4-html).

빌더 출력(build_<id>.jsonl / GoldenRecord.to_dict())을 검수자가 클릭 서명하는 인터랙티브
HTML로 렌더한다. gold 후보에는 승인/등급변경/거부 폼을 붙이고, 룰·LLM 합의 미달 후보는
같은 목록에 보기 전용으로 섞는다.

주소는 둘이지만 화면은 하나다 — `review.html` 과 `signoff.html` 이 모두 이 렌더러를 쓴다
(2026-08-18 통합). 같은 잡의 같은 후보를 보는데 화면이 둘이라 검수자가 같은 목록을 두 번 봤다.
[2026-08-19] 옛 검토본 렌더러(render_review_html · _BODY_TEMPLATE, 미리보기 150자)는
HTTP 경로에서 쓰이지 않은 지 오래라 삭제했다. review.html **주소는 유지**한다 —
build_offline_bundle·demo_e2e_golden·register_review_signoff_job·OPERATION.md 가 참조한다.

주의 — golden100_분류근거_보고서(regen_golden100_report.build_body)는 '평가 리포트'(정답
target 대조·정오답·미탐)다. 이 화면은 정답이 아직 없는 '후보 검수'이므로 target/미탐 대신
**후보 등급(llm)·룰 등급·합의 상태·신뢰도**를 보여준다.
(순수 렌더 — 무거운 의존 없음, HTML 문자열 반환)
"""
from __future__ import annotations

import base64
import html as _html
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence
from koipa.console_nav import BRAND_NAME, HEADER_CSS, NAV_CSS, header_html, nav_bar_html
from koipa.console_doc import DOC_CSS, DOC_RENDER_JS
from koipa.console_shell import SHELL_CSS, SHELL_MEDIA_CSS

# 콘솔(static/styles.css)과 동일한 NovaX 토큰 — 골든 화면이 /demo 콘솔과 한 시스템으로 보이게
# 맞춘다(radius 0·동일 폰트스택·동일 등급색). 외부 CSS 링크를 쓰지 않는 이유: 이 HTML 은
# 감리 증적으로 단독 저장·전달될 수 있어 self-contained 여야 한다(정적 마운트 의존 금지).
_TOKENS = """
:root{--bg:#ffffff;--bg-surface:#f7f7f5;--text:#111111;--text-soft:#555555;--text-dim:#8f9498;--border:#e1e1de;--border-strong:#cfcfcb;--accent:#111111;--accent-soft:#f7f7f5;--radius:0;--font-sans:Arial,"Noto Sans KR",sans-serif;--font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;--c-ts:#e72d44;--c-s1:#d97706;--c-s2:#0070f3;--c-s3:#16a34a;--ink:#111111;--red:#e72d44;--paper:#fff;--soft:#f7f7f5;--line:#e1e1de;--mute:#8f9498}
"""

_DEFAULT_CSS = """<style>""" + _TOKENS + """
*{box-sizing:border-box}
body{font-family:var(--font-sans);margin:0;background:var(--bg);color:var(--text);
     -webkit-font-smoothing:antialiased}
/* 브랜드 크롬 — 헤더는 header.top 이고 .brand/.brand-mark 만 쓴다.
   [2026-08-19] 옛 sticky nav(.nav·.nav-inner)와 .brand-name/.brand-sep/.brand-sub 는
   _nav_html 이 더 이상 내보내지 않아 삭제했다(렌더 결과 실측 0회). */
.brand{display:inline-flex;align-items:center;gap:8px;color:var(--text);text-decoration:none;
       white-space:nowrap;min-width:0;line-height:1}
.brand-mark{width:34px;height:34px;display:grid;place-items:center;flex-shrink:0;background:#fff;
            border-radius:7px;padding:4px;border:1px solid rgba(0,0,0,.08)}
.brand-mark img{max-width:100%;max-height:100%;display:block}
/* 배포 주체 배지 — 콘솔 deploy_badge.js 와 동일 폼(골든 화면은 서버 렌더라 값도 서버가 넣는다) */
.site-badge{display:inline-flex;align-items:center;gap:0;font-size:11.5px;font-weight:600;
            line-height:1;white-space:nowrap}
.site-badge>span{padding:4px 8px;border:1px solid transparent}
.site-badge .site-name{color:#fff;letter-spacing:-.01em}
.site-badge .site-role{background:#fff;color:var(--text-soft);border-color:var(--border-strong);border-left:0;font-weight:500}
.site-badge .site-kind{background:var(--accent-soft);color:var(--text);border-color:var(--border-strong);border-left:0}
.site-badge.jjw .site-name{background:#0a0a0a}
.site-badge.cust .site-name{background:#0070f3}
.site-badge.pilot .site-name{background:#d97706}
.site-badge.dev .site-name{background:#737373}
.site-badge.unknown .site-name{background:#fff;color:var(--text-dim);border-color:var(--border-strong)}
@media (max-width:720px){.site-badge .site-role{display:none}}
.filters{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.filter-btn{border:1px solid var(--border-strong);background:var(--bg);color:var(--text);
            border-radius:var(--radius);padding:5px 10px;font-size:12px;cursor:pointer;font-family:inherit}
.filter-btn:hover{background:var(--accent-soft)}
.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.filter-btn:focus-visible,.search-box:focus-visible{outline:2px solid var(--c-s2);outline-offset:2px}
.filter-sep{width:1px;height:20px;background:var(--border-strong);margin:0 4px}
.search-box{border:1px solid var(--border-strong);border-radius:var(--radius);padding:5px 10px;
            font-size:12px;flex:1;min-width:140px;font-family:inherit}
.card-meta{font-size:11px;color:var(--text-dim);display:flex;gap:8px;font-family:var(--font-mono)}
.grade-mark{font-weight:700;border-radius:var(--radius);padding:2px 8px;color:#fff;font-size:12px}
.g-TS{background:var(--c-ts)}.g-S1{background:var(--c-s1)}.g-S2{background:var(--c-s2)}.g-S3{background:var(--c-s3)}
.no-results{padding:30px;text-align:center;color:var(--text-dim)}
""" + NAV_CSS + """
</style>"""

_GRADES = ("TS", "S1", "S2", "S3")

# ── 콘솔과 동일한 nav 크롬 조립 ────────────────────────────────────────────────
# 로고는 base64 인라인 — 이 HTML 은 /api/v1/golden/... 에서 서빙되므로 콘솔의 상대경로
# (./koipa_logo_mark.png)가 안 맞고, 감리 증적으로 단독 저장될 때도 깨지면 안 된다
# (프로젝트 규칙: 새 HTML 은 로고 base64 인라인).
_LOGO_PATH = Path(__file__).with_name("api") / "static" / "koipa_logo_mark.png"


@lru_cache(maxsize=1)
def _logo_data_uri() -> str:
    """로고 data URI. 파일이 없으면 빈 문자열 — 마크는 생략되고 나머지는 정상 렌더."""
    try:
        return "data:image/png;base64," + base64.b64encode(_LOGO_PATH.read_bytes()).decode("ascii")
    except OSError:
        return ""


# deploy_profile → 배포 주체. static/deploy_badge.js 의 표와 동일하게 유지할 것.
_SITES = {
    "full-train": ("지재원", "모델 공장", "jjw"),
    "onprem-local": ("고객사", "폐쇄망 운영", "cust"),
    "lite-cloud": ("오픈망 파일럿", "경량", "pilot"),
    "lite-noapi": ("로컬·개발", "dryrun", "dev"),
}


def _site_badge_html(profile: Optional[str], screen: str) -> str:
    """배포 주체·화면 배지. profile 미지정/미등록이면 단정하지 않고 원시값을 회색으로 표기."""
    if not profile:
        return ""
    site, role, cls = _SITES.get(profile, (profile, "", "unknown"))
    parts = [f'<span class="site-name">{_html.escape(site)}</span>']
    if role:
        parts.append(f'<span class="site-role">{_html.escape(role)}</span>')
    parts.append(f'<span class="site-kind">{_html.escape(screen)}</span>')
    return (
        f'<span class="site-badge {cls}" title="deploy_profile={_html.escape(profile)}">'
        + "".join(parts)
        + "</span>"
    )


def _sibling_link_html(url: str, label: str) -> str:
    """같은 job 의 상대 화면으로 가는 링크.

    전역 네비에는 넣을 수 없다 — review/signoff 는 job_id 와 ?t= HMAC 토큰이 있어야 열리고
    (golden.py:741·762), 고정 링크로 걸면 403 이다. 그런데 **같은 job 안에서는** 서버가
    형제 주소를 서명해 줄 수 있다. 검수자가 검토본과 서명 화면을 오가려고 주소를 다시
    받아야 하던 것을 없앤다.
    """
    if not url:
        return ""
    safe = _html.escape(url, quote=True)
    return f'<a class="cnav-link" href="{safe}">{_html.escape(label)} \u2197</a>'


def _nav_html(sub: str, profile: Optional[str], screen: str, sibling: str = "") -> str:
    """콘솔 5면이 공유하는 상단 바 — 마크업·CSS 는 console_nav 한 곳에 있다.

    [2026-08-20] 종전에는 이 함수가 헤더를 직접 조립했고, 후보 관리·로그인·거버넌스·
    등급 시연이 각자 다른 헤더를 갖고 있었다. 사용자 지시로 **이 화면의 모양을 기준**으로
    다섯 화면을 합치면서, 값의 출처를 console_nav.header_html 로 옮겼다.

    current="signoff" 를 넘기는 이유: 메뉴의 「골든셋 검수」가 이 화면이다. 다만 그 메뉴의
    링크는 잡 목록으로 간다 — 이 화면은 job_id 와 ?t= 토큰이 있어야 열려서 고정 링크를
    걸 수 없기 때문이다(golden.py:701·727 에서 403).
    """
    return header_html(
        sub, "signoff", trailing=sibling + _site_badge_html(profile, screen)
    )


def _embed_json(data: object) -> str:
    """<script> 블록 임베드용 JSON 직렬화 — </script> breakout·HTML 컨텍스트 탈출 차단.

    json.dumps 는 <, >, & 를 이스케이프하지 않으므로, 후보 문서 본문(text)·doc_id 에 리터럴
    </script> 가 있으면 <script id="data" type="application/json"> 블록이 그 지점에서 조기
    종료돼 뒤 문자열이 실행 컨텍스트로 새는 저장형 XSS 가 된다(2026-07 적대 리뷰). <,>,& 를
    유니코드 이스케이프로 치환 — JSON.parse 가 원문을 무손실 복원하므로 데이터는 그대로다.
    """
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


# ── 골든셋 검수 · 화면 서명(signoff) 인터랙티브 렌더 ──────────────────────────────
# 각 후보에 승인/등급변경/거부 폼을 붙이고, 제출 시
# POST /golden/jobs/{id}/signoff 로 결정을 보내 locked_gold_eval 로 승격한다. 검수자가
# jsonl 을 손으로 편집하는 대신 화면에서 클릭 서명 — 서명 캡처 UI 갭(뷰어만 존재) 해소.
def _signoff_records(records: Sequence[dict], *, signable: bool = True) -> list[dict]:
    out: list[dict] = []
    for r in records:
        grade = r.get("label") or r.get("llm_grade") or "S3"
        if grade not in _GRADES:
            grade = "S3"
        out.append({
            "id": str(r.get("doc_id", "")),
            "grade": grade,
            "rule": str(r.get("rule_grade", "")),
            "llm": str(r.get("llm_grade", "")),
            "conf": round(float(r.get("llm_confidence") or 0.0), 3),
            "domain": str(r.get("domain", "") or r.get("source", "")),
            "text": str(r.get("text") or ""),
            # 서명 가능 여부는 **어느 파일에서 왔는가** 로 정한다.
            #
            # ⚠ 레코드의 review_status 로 판정하면 안 된다(2026-08-18 실측으로 잡힌 오류).
            #   apply_signoff 는 gold_path 의 doc_id 만 서명 대상으로 삼는다 —
            #   review_status 는 보지 않는다. 실제 전달본 120건은 전부 'pending' 이라
            #   값으로 판정했더니 **전건이 보기 전용이 되어 아무것도 서명할 수 없었다.**
            "signable": signable,
        })
    return out


def render_signoff_html(
    records: Sequence[dict],
    *,
    job_id: str,
    post_url: str,
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
    profile: Optional[str] = None,
    review_url: str = "",
    min_per_grade: int = 0,
    pending: Optional[Sequence[dict]] = None,
) -> str:
    """gold 후보를 화면 서명용 인터랙티브 HTML로 렌더(승인/등급변경/거부 → POST signoff).

    pending = 합의 미달(uncertain) 후보. 서명 대상이 아니므로 결정 폼 없이 보기 전용으로
    같은 목록에 섞는다 — 종전 검토본(review.html)이 하던 일이다.

    [C2 2026-08-17] 검수자 이름·API Key 를 받는 인자를 없앴다. 신원은 로그인 쿠키(JWT sub)
    에서만 온다 — 화면이 채워 줄 수 있는 값이 아니다.
    """
    data = _signoff_records(records) + _signoff_records(pending or [], signable=False)
    head = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        # [2026-08-20] 브라우저 탭 제목도 다섯 화면이 같은 꼴이어야 한다 —
        # `한국지식재산보호원 | 화면이름`. 종전에는 이 화면만 기관명이 없었다.
        f"<title>{BRAND_NAME} | {_html.escape(title)}</title>"
        f"{css or _DEFAULT_CSS}{_SIGNOFF_CSS}</head>"
    )
    return head + (
        _SIGNOFF_BODY
        .replace("__NAV__", _nav_html("골든셋 검수 · 서명", profile, "서명",
                                      _sibling_link_html(review_url, "검토본")))
        .replace("__TITLE__", _html.escape(title))
        .replace("__JOB__", _html.escape(job_id))
        .replace("__POST_URL__", _html.escape(post_url))
        .replace("__TOTAL__", str(len(data)))
        .replace("__MINPG__", str(int(min_per_grade or 0)))
        .replace("__DOC_RENDER_JS__", DOC_RENDER_JS)
        .replace("__DATA__", _embed_json(data))
    )


def render_signoff_html_from_jsonl(
    paths: Sequence[str | Path],
    *,
    job_id: str,
    post_url: str,
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
    profile: Optional[str] = None,
    review_url: str = "",
    min_per_grade: int = 0,
) -> str:
    """build_<id>.jsonl(gold 후보)을 읽어 서명 HTML로 렌더.

    paths[0] = 서명 대상(gold) · paths[1:] = 보기 전용(uncertain).
    이 순서가 곧 계약이다 — apply_signoff 가 gold_path 만 서명 대상으로 삼기 때문이다.
    """
    def _read(path) -> list[dict]:
        pp = Path(path)
        if not pp.exists():
            return []
        return [json.loads(ln) for ln in pp.read_text(encoding="utf-8").splitlines() if ln.strip()]

    paths = list(paths)
    gold = _read(paths[0]) if paths else []
    pending: list[dict] = []
    for extra in paths[1:]:
        pending.extend(_read(extra))
    return render_signoff_html(gold, job_id=job_id, post_url=post_url, title=title, css=css,
                               profile=profile, review_url=review_url,
                               min_per_grade=min_per_grade, pending=pending)


# 토큰 재선언 — render_signoff_html(css=...) 로 커스텀 CSS 를 주입해도 서명 화면이
# 토큰 미정의로 무너지지 않게 self-sufficient 하게 둔다(중복 선언은 무해).
_SIGNOFF_CSS = """<style>""" + _TOKENS + SHELL_CSS + DOC_CSS + """
/* [2026-08-20] 브랜드 바 CSS 는 console_nav.HEADER_CSS 로 옮겼다(아래 NAV_CSS 앞에서
   붙는다). 정적 화면(admin.html·index.html)이 같은 문자열을 손으로 박아 쓰기 때문에,
   여기서 값을 따로 갖고 있으면 다섯 화면이 다시 갈라진다. */

/* [껍데기 정렬 2026-08-18] 사이드바·히어로 — manage 와 같은 자리·같은 톤.
   골격 규칙(frame/side/main/hero/section/list/btn)은 console_shell.SHELL_CSS 에 있다.
   여기 있는 것은 이 화면에만 필요한 나머지다. */
.side .workname{font-size:18px;font-weight:900;margin:14px 0 6px}
.side .workdesc{font-size:12.5px;color:#82898f;line-height:1.5}
.side .branch{margin-top:12px;font:700 11.5px ui-monospace,monospace;color:#92999f}
.sidenav{margin-top:34px;display:flex;flex-direction:column;gap:2px}
.sidenav a{display:flex;gap:12px;align-items:center;padding:9px 10px;font-size:13.5px;color:#6d757b;text-decoration:none;border-left:2px solid transparent}
.sidenav a span{font:700 11px ui-monospace,monospace;color:#a9b0b5}
.sidenav a.active{color:#111;font-weight:800;border-left-color:var(--red);background:#fafaf8}
.side .ledger{margin-top:auto;padding-top:26px;border-top:1px solid var(--line);font-size:12px;color:#828a90}
.side .ledger b{display:block;margin:8px 0 4px;font-size:14px;color:#111}
.hero h1{font-size:34px;line-height:1.24;margin:14px 0 16px;font-weight:900}
.hero h1 em{font-style:normal;color:var(--red)}
.hero p{color:#5f676d;font-size:14px;line-height:1.7;margin:0;max-width:640px}
.gate strong{display:block;font-size:20px;font-weight:900;margin:8px 0 10px}
.gate p{font-size:12.5px;color:#6f777d;line-height:1.6;margin:0 0 12px}
.gate .gchk{display:flex;gap:8px;align-items:center;font-size:12.5px;color:#444}
.gate .actions{margin-top:14px}
.gate .actions .btn{width:100%}
/* 카드 안 문서 보기 — 후보 관리 화면과 같은 모양 */
.docwrap{margin-top:12px;border-top:1px solid var(--line);padding-top:10px}
.viewbar{display:flex;gap:6px;align-items:center;margin-bottom:6px;flex-wrap:wrap}
.viewbar .btn.sm{padding:4px 9px;font-size:11.5px}
.viewbar .vbtn.active{background:#111;color:#fff;border-color:#111}
.viewnote{font-size:11.5px;color:#8a9299}
.scard .docbody{max-height:320px}

.sechead{display:inline-block;margin:0;font-size:21px}
.secdesc{margin:8px 0 0;color:#727c84;font-size:13px}
.main .rubric{margin-top:22px}
.main .filters{margin-top:18px}

.who{font-size:13px;font-weight:700;color:#fff;padding:5px 0;display:inline-block}
.scard.pending{opacity:.72;background:#fafaf9}
.pendnote{margin-top:10px;padding:8px 11px;font-size:12.5px;line-height:1.55;color:#78350f;background:#fffbeb;border:1px solid #fcd34d;border-left:3px solid #f59e0b;border-radius:2px}
.pg-lack{color:#b45309;font-weight:700}
.pg-note{color:var(--text-dim)}
.preflight{display:none;margin:12px 0;padding:10px 14px;font-size:13px;border-radius:2px;line-height:1.6}
.preflight.block{background:#fef2f2;border:1px solid #fca5a5;border-left:3px solid #dc2626;color:#991b1b}
.preflight.warn{background:#fffbeb;border:1px solid #fcd34d;border-left:3px solid #f59e0b;color:#78350f}
.preflight.okv{background:#f6f6f4;border:1px solid #dededb;border-left:3px solid #6b7280;color:#3f3f46}
.preflight b{font-weight:700}
.restored{display:none;margin:12px 0;padding:10px 14px;font-size:13px;background:#fffbeb;
  border:1px solid #fcd34d;border-left:3px solid #f59e0b;border-radius:2px;color:#78350f}
.restored button{margin-left:10px;font-size:12px;padding:3px 10px;cursor:pointer;
  border:1px solid #d6d3d1;background:#fff;border-radius:2px}
.rubric{background:var(--accent-soft);border:1px solid var(--border-strong);border-left:3px solid var(--c-s1);
        border-radius:var(--radius);padding:10px 14px;margin:14px 0;font-size:12px;line-height:1.6}
.rubric b{color:var(--text)}
.scard{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:12px}
.scard.decided{border-color:var(--c-s3);box-shadow:0 0 0 1px var(--c-s3) inset}
.scard.rejected{border-color:var(--c-ts);box-shadow:0 0 0 1px var(--c-ts) inset;opacity:.75}
.decrow{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px}
.decrow label{display:flex;gap:5px;align-items:center;cursor:pointer}
.decrow select{padding:3px 6px;border:1px solid var(--border-strong);border-radius:var(--radius);font-family:inherit}
.note{width:100%;margin-top:8px;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius);
      font-size:12px;font-family:inherit}
.result{margin:14px 0;padding:12px 16px;border-radius:var(--radius);font-size:13px;display:none}
.result.ok{background:#f0fdf4;border:1px solid var(--c-s3);border-left:3px solid var(--c-s3);color:#166534;display:block}
.result.err{background:#fef2f2;border:1px solid var(--c-ts);border-left:3px solid var(--c-ts);color:#991b1b;display:block}
""" + HEADER_CSS + NAV_CSS + SHELL_MEDIA_CSS + """
</style>"""

_SIGNOFF_BODY = r"""
<body>
__NAV__
<div class="frame">
  <aside class="side">
    <div class="cap">CURRENT WORKSPACE</div>
    <div class="workname">koipa-ai</div>
    <div class="workdesc">Koipa AI Engine for KOIPA Trade-Secret System (PoC)</div>
    <div class="branch">goldset/signoff</div>
    <nav class="sidenav">
      <a href="#s-overview" class="active"><span>01</span>개요</a>
      <a href="#s-list"><span>02</span>후보 검수</a>
    </nav>
    <div class="ledger">
      <div class="cap">SIGN-OFF IDENTITY</div>
      <b id="who" class="who">확인 중…</b>
      <div>이 계정으로 기록됩니다</div>
    </div>
  </aside>
  <main class="main">
    <section id="s-overview" class="hero">
      <div>
        <div class="eyebrow">GOLDEN SET SIGN-OFF</div>
        <h1>검수한 것만<br><em>평가 정답</em>이 됩니다.</h1>
        <p>job __JOB__ · 후보 __TOTAL__건. 승인·등급변경·거부를 고르고 서명하면
           <b>locked_gold_eval</b>(사람 서명 평가정답)로 승격됩니다. 고르지 않은 후보는 제출에서 빠집니다.</p>
      </div>
      <aside class="gate">
        <div class="glabel">서명 진행</div>
        <strong id="deccount">–</strong>
        <p>등급별 최소 건수를 채워야 배포 게이트가 열립니다. 라이브에 반영하려면 아래를 체크하고 제출하십시오.</p>
        <label class="gchk"><input type="checkbox" id="publish"> 라이브 반영(publish)</label>
        <div class="actions"><button id="submit" class="btn black">서명 제출</button></div>
      </aside>
    </section>
    <div class="preflight" id="preflight" role="status" aria-live="polite"></div>
    <div class="restored" id="restored" role="status" aria-live="polite"></div>
    <div class="result" id="result" role="status" aria-live="polite"></div>
    <section id="s-list" class="section">
      <div class="sectionTop">
        <div><span class="secNum">02</span><h2 class="sechead">후보 검수</h2>
        <p class="secdesc">문서 전문을 읽고 결정하십시오. 결정은 이 브라우저에 남아 창을 닫아도 사라지지 않습니다.</p></div>
      </div>
  <div class="rubric">
    <!-- [2026-08-21] 곱=4 를 S1 이라 가르치고 있었다. 실제 판정식은 TS 다 —
         rule_engine.SVM_GRADE_MAP = {8:TS, 4:TS, 2:S2, 1:S2, 0:S3}.
         바로 아랫줄('S2·V2에서 M=0→S1·M≥1→TS')은 코드와 맞아서, 한 상자 안에서
         두 문장이 서로 모순이었다. 검수자가 표대로 계산하면 TS 를 S1 로 내려 서명한다
         — 미탐 방향이라 그냥 두면 안 된다. -->
    <b>판정</b> S×V×M → <b>8·4=TS</b>·<b>1·2=S2</b>·<b>0=S3</b> (S=0이면 무조건 S3).
    핵심 분기 <b>S1 vs TS = M</b>: S2·V2에서 <b>M=0→S1</b>(관리 미공식화)·<b>M≥1→TS</b>. 확신 없으면 <b>거부</b>(미탐 안전).
    승인=제안등급 유지 / 변경=다른 등급 / 거부=승격 제외. 미결정 후보는 제출에서 빠진다.
  </div>
  <div class="filters">
    <span style="font-size:12px">등급</span>
    <button class="filter-btn active" data-f="grade" data-v="all">전체</button>
    <button class="filter-btn" data-f="grade" data-v="TS">TS</button>
    <button class="filter-btn" data-f="grade" data-v="S1">S1</button>
    <button class="filter-btn" data-f="grade" data-v="S2">S2</button>
    <button class="filter-btn" data-f="grade" data-v="S3">S3</button>
    <span class="filter-sep"></span>
    <span style="font-size:12px">진행</span>
    <button class="filter-btn active" data-f="state" data-v="all">전체</button>
    <button class="filter-btn" data-f="state" data-v="todo" id="fTodo">미결정</button>
    <button class="filter-btn" data-f="state" data-v="done">결정함</button>
    <span class="filter-sep"></span>
    <input class="search-box" id="q" placeholder="id·본문 검색..." aria-label="문서 ID 또는 본문 검색">
    <span id="decbreak" style="font-size:12px;color:var(--text-dim)"></span>
  </div>
  <div id="grid"></div>
    </section>
  </main>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
__DOC_RENDER_JS__
// 카드마다 '읽기 좋게 / 원문 그대로' — 후보 관리 화면과 같은 규율.
// 판단 근거는 원문이 정본이라, 렌더링은 서식만 입히고 내용을 바꾸지 않는다.
document.addEventListener('click',function(e){
  var b=e.target.closest&&e.target.closest('.vbtn'); if(!b) return;
  var id=b.dataset.id, want=b.dataset.view;
  document.querySelectorAll('.vbtn[data-id="'+CSS.escape(id)+'"]').forEach(function(x){
    x.classList.toggle('active', x.dataset.view===want);
    x.setAttribute('aria-pressed', x.dataset.view===want ? 'true' : 'false'); });
  document.querySelectorAll('[data-doc][data-id="'+CSS.escape(id)+'"]').forEach(function(x){
    x.style.display = x.dataset.doc===want ? '' : 'none'; });
});
const DATA=JSON.parse(document.getElementById('data').textContent);
const POST_URL="__POST_URL__";
// 배포 게이트가 요구하는 등급별 최소 서명 수(settings.deploy_gate_min_locked_per_grade).
// 화면에 박지 않고 서버 값을 받는다 — 둘이 어긋나면 '다 했는데 안 열린다' 가 된다.
const MIN_PG=__MINPG__;
const BYID={};DATA.forEach(function(r){BYID[r.id]=r;});
const DEC={};       // id -> {decision, grade, note}
let g='all',st='all',q='';

// 중간 결정 보존 — DEC 는 메모리에만 있어서 탭을 닫거나 새로고침하면 사라졌다.
// 120건짜리 회차에서 60건 하다 창을 닫으면 60건을 다시 눌러야 한다는 뜻이다.
// 잡 단위로 브라우저에 남긴다(서버로 보내지 않는다 — 제출 전 결정은 아직 서명이 아니다).
const DEC_KEY='koipa.signoff.'+POST_URL;
function decSave(){
  try{ localStorage.setItem(DEC_KEY, JSON.stringify(DEC)); }
  catch(e){ /* 사생활 보호 모드 등 — 저장 못 해도 검수는 계속돼야 한다 */ }
}
function decRestore(){
  var raw=null;
  try{ raw=localStorage.getItem(DEC_KEY); }catch(e){ return 0; }
  if(!raw) return 0;
  var n=0;
  try{
    var o=JSON.parse(raw);
    Object.keys(o||{}).forEach(function(k){ if(o[k]&&o[k].decision){ DEC[k]=o[k]; n++; } });
  }catch(e){ return 0; }
  return n;
}
function decClear(){
  Object.keys(DEC).forEach(function(k){ delete DEC[k]; });
  try{ localStorage.removeItem(DEC_KEY); }catch(e){}
  document.getElementById('restored').style.display='none';
  render();
}
// 텍스트+속성(name=/data-id=/value=) 양쪽에 쓰이므로 따옴표도 이스케이프(속성 컨텍스트 주입 차단).
// 같은 문서를 다시 그릴 때 mdToHtml 을 다시 돌리지 않는다.
// 실측(후보 120건·각 2,100자): render() 1회 32.4ms 이고 그 대부분이 이 변환이다.
// 필터를 누르거나 검색어를 칠 때마다 120번씩 다시 돌던 것을 문서당 1번으로 줄인다.
const MDC={};
function mdOnce(r){ return MDC[r.id] || (MDC[r.id] = mdToHtml(r.text)); }
function esc(t){const d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML.replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function gopts(sel){return ['TS','S1','S2','S3'].map(x=>'<option value="'+x+'"'+(x===sel?' selected':'')+'>'+x+'</option>').join('');}
function card(r){
  if(!r.signable) return pendingCard(r);
  const d=DEC[r.id]||{};
  const cls=d.decision==='reject'?'scard rejected':(d.decision?'scard decided':'scard');
  return '<div class="'+cls+'" data-id="'+esc(r.id)+'">'
    +'<div class="card-meta"><span class="grade-mark g-'+r.grade+'">'+r.grade+'</span> <span>'+esc(r.id)+'</span> <span>'+esc(r.domain)+'</span> <span style="color:var(--text-dim)">룰 '+esc(r.rule)+' · LLM '+esc(r.llm)+' conf '+r.conf.toFixed(2)+'</span></div>'
    +'<div class="docwrap"><div class="viewbar">'
      +'<button type="button" class="btn sm vbtn active" aria-pressed="true" data-view="md" data-id="'+esc(r.id)+'">읽기 좋게</button>'
      +'<button type="button" class="btn sm vbtn" aria-pressed="false" data-view="raw" data-id="'+esc(r.id)+'">원문 그대로</button>'
      +'<span class="viewnote">판단 근거는 원문 기준입니다. 읽기 좋게 보기는 서식만 입힌 같은 내용입니다.</span>'
    +'</div>'
    +'<div class="docbody md" data-doc="md" data-id="'+esc(r.id)+'">'+mdOnce(r)+'</div>'
    +'<pre class="docbody" data-doc="raw" data-id="'+esc(r.id)+'" style="display:none">'+esc(r.text)+'</pre>'
    +'</div>'
    +'<div class="decrow" role="group" aria-label="'+esc(r.id)+' 등급 결정">'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="approve"'+(d.decision==='approve'?' checked':'')+'> 승인 ('+r.grade+' 유지)</label>'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="change"'+(d.decision==='change'?' checked':'')+'> 등급변경 <select class="gsel" data-id="'+esc(r.id)+'">'+gopts(d.grade||r.grade)+'</select></label>'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="reject"'+(d.decision==='reject'?' checked':'')+'> 거부</label>'
    +'</div>'
    +'<input class="note" data-id="'+esc(r.id)+'" placeholder="메모(선택)" value="'+esc(d.note||'')+'">'
    +'</div>';
}
// 서명 대상이 아닌 후보 — 같은 목록에 두되 결정 폼을 주지 않는다.
// 폼이 있으면 "왜 눌러도 안 되나" 가 되고, 빼 놓으면 "왜 안 보이나" 가 된다. 보여주되 잠근다.
function pendingCard(r){
  return '<div class="scard pending" data-id="'+esc(r.id)+'">'
    +'<div class="card-meta"><span class="grade-mark g-'+r.grade+'">'+r.grade+'</span> '
    +'<span>'+esc(r.id)+'</span> <span>'+esc(r.domain)+'</span> '
    +'<span style="color:var(--text-dim)">룰 '+esc(r.rule)+' · LLM '+esc(r.llm)
    +' conf '+r.conf.toFixed(2)+'</span></div>'
    +'<div class="docbody md">'+mdOnce(r)+'</div>'
    +'<div class="pendnote">서명 대상이 아닙니다 — 룰과 LLM 이 등급에 합의하지 못한 후보입니다. '
    +'여기서는 내용만 확인하고, 확정은 후보 관리 화면에서 합니다.</div>'
    +'</div>';
}
// 결정 건수는 **서명 대상만** 센다 — 보기 전용(합의 미달) 후보는 결정 폼이 아예 없어
// 결정될 수 없다. 화면 숫자와 제출 완료 문구가 따로 세면 어긋나므로 규칙을 여기 한 곳에 둔다.
function decidedIds(){
  return Object.keys(DEC).filter(function(id){
    return DEC[id]&&DEC[id].decision&&!(BYID[id]&&BYID[id].signable===false);
  });
}
function signableCount(){ return DATA.filter(function(x){return x.signable;}).length; }
function decCount(){
  // 승격 예정만 센다 — 거부는 정답지에서 빠지고, 등급변경은 **바꾼 등급**으로 들어간다.
  // 이 셈이 서버의 locked_by_grade 와 같은 규칙이라, 화면 숫자가 결과와 어긋나지 않는다.
  var by={TS:0,S1:0,S2:0,S3:0}, ids=decidedIds();
  ids.forEach(function(id){
    var d=DEC[id];
    if(d.decision==='reject') return;
    var r=BYID[id]; if(!r) return;
    var gr=(d.decision==='change')?(d.grade||r.grade):r.grade;
    if(by[gr]!==undefined) by[gr]++;
  });
  var parts=['TS','S1','S2','S3'].map(function(k){
    var lack=MIN_PG>0&&by[k]<MIN_PG;
    return '<span'+(lack?' class="pg-lack"':'')+'>'+k+' '+by[k]+'</span>';
  }).join(' · ');
  var tail=(MIN_PG>0)?' <span class="pg-note">(등급별 '+MIN_PG+'건 필요)</span>':'';
  var signable=signableCount(), pend=DATA.length-signable;
  // gate 의 큰 글씨(.gate strong = 20px/900, 폭 300px)에는 **진행 숫자만** 넣는다.
  // 등급별 내역까지 밀어 넣으면 제목 자리에 여러 줄로 쏟아진다 — 그것은 목록 옆으로 내린다.
  document.getElementById('deccount').textContent=ids.length+' / '+signable;
  // 「미결정」 버튼에 잔여를 달아 둔다 — 얼마나 남았는지 보려고 세지 않아도 되게.
  var todo=document.getElementById('fTodo');
  if(todo) todo.textContent='미결정 '+Math.max(0, signable-ids.length);
  document.getElementById('decbreak').innerHTML=
    '서명 대상 '+signable+'건'
    +(pend?' <span class="pg-note">(합의 미달 '+pend+'건 보기 전용)</span>':'')
    +' &nbsp; 승격 예정 '+parts+tail;
}
function render(){
  const ql=q.toLowerCase();
  const f=DATA.filter(r=>{
    if(g!=='all'&&r.grade!==g)return false;
    if(st!=='all'){
      // 보기 전용(합의 미달) 후보는 결정될 수 없으니 '미결정' 에도 넣지 않는다 —
      // 넣으면 아무리 눌러도 줄지 않는 잔여가 생긴다.
      const decided=!!(DEC[r.id]&&DEC[r.id].decision);
      if(st==='todo'&&(decided||!r.signable))return false;
      if(st==='done'&&!decided)return false;
    }
    if(ql&&!((r.id+' '+r.text).toLowerCase().includes(ql)))return false;
    return true;
  });
  document.getElementById('grid').innerHTML=f.length?f.map(card).join(''):'<div class="no-results">조건에 맞는 후보가 없습니다.</div>';
  decCount();
}
document.getElementById('grid').addEventListener('change',function(e){
  const t=e.target;
  if(t.matches('input[type=radio]')){
    const id=t.name.slice(4);
    DEC[id]=DEC[id]||{};DEC[id].decision=t.value;
    if(t.value==='change'){const s=document.querySelector('.gsel[data-id="'+CSS.escape(id)+'"]');DEC[id].grade=s?s.value:null;}
    else delete DEC[id].grade;
    const cd=t.closest('.scard');cd.className=t.value==='reject'?'scard rejected':'scard decided';
    decCount();decSave();
  } else if(t.matches('.gsel')){
    const id=t.dataset.id;DEC[id]=DEC[id]||{decision:'change'};DEC[id].grade=t.value;
    const rc=document.querySelector('input[name="dec-'+CSS.escape(id)+'"][value=change]');if(rc)rc.checked=true;DEC[id].decision='change';decSave();
  }
});
document.getElementById('grid').addEventListener('input',function(e){
  if(e.target.matches('.note')){const id=e.target.dataset.id;DEC[id]=DEC[id]||{};DEC[id].note=e.target.value;decSave();}
});
// 필터는 두 그룹(등급·진행)이라 그룹 안에서만 active 를 옮긴다.
// 120건짜리 회차를 나눠 하면 「결정 N건 복원」 뒤에 남은 것을 찾아 스크롤해야 했다 —
// 「미결정」 하나로 목록을 좁힌다.
document.querySelectorAll('.filter-btn').forEach(b=>b.addEventListener('click',function(){
  const grp=this.dataset.f;
  document.querySelectorAll('.filter-btn[data-f="'+grp+'"]').forEach(x=>x.classList.remove('active'));
  this.classList.add('active');
  if(grp==='grade') g=this.dataset.v; else st=this.dataset.v;
  render();
}));
// 한 글자마다 카드 120장을 새로 만들면 타이핑이 끊긴다(실측 render() 1회 32.4ms).
// 입력이 멎은 뒤에 한 번만 그린다.
var qTimer;
document.getElementById('q').addEventListener('input',function(){
  const v=this.value;
  clearTimeout(qTimer);
  qTimer=setTimeout(function(){ q=v; render(); }, 150);
});
// [C2 2026-08-17] 서명자는 **로그인 쿠키(JWT)의 sub** 다. 화면이 이름을 받지 않는다.
var WHO='',WHOROLE='reviewer';
const LOGIN_URL='/api/v1/golden/candidates/login.html';
function needLogin(msg){
  // 링크(?t=)는 화면을 여는 열쇠일 뿐 신원이 아니다. 서명하려면 로그인 쿠키가 있어야 한다.
  // 갈 곳을 안 알려주면 검수자는 '열리는데 왜 못 누르나' 에서 멈춘다.
  const el=document.getElementById('who');
  el.innerHTML=esc(msg)+' — <a href="'+LOGIN_URL+'" target="_blank" rel="noopener" '
    +'style="color:#fff;text-decoration:underline">로그인</a> 후 이 페이지를 새로고침하세요';
  document.getElementById('submit').disabled=true;
}
(async function(){
  const el=document.getElementById('who');
  try{
    const r=await fetch('/api/v1/golden/candidates/session',{credentials:'same-origin'});
    if(r.status===401||r.status===403){needLogin('로그인 필요('+r.status+')');return;}
    if(!r.ok){needLogin('신원 확인 실패('+r.status+')');return;}
    const j=await r.json();
    WHO=j.actor_id||'';WHOROLE=j.actor_role||'reviewer';
    if(!WHO){needLogin('신원 없음');return;}
    el.textContent=WHO+' · '+WHOROLE;
    loadPreflight();
  }catch(e){needLogin('신원 확인 실패');}
})();

// [E2] 서명 전 점검 — 종전에는 실패가 **제출한 뒤에야** 드러났다.
// blocking 은 실제로 POST 를 실패시키는 것만이다. 경고로 버튼을 잠그지 않는다.
var PF_BLOCKED=false;
async function loadPreflight(){
  const box=document.getElementById('preflight');
  try{
    const r=await fetch(POST_URL+'/preflight',{credentials:'same-origin'});
    if(!r.ok) return;                       // 점검이 안 되는 것으로 검수를 막지 않는다
    const j=await r.json();
    PF_BLOCKED=!j.ok;
    const c=j.candidates||{};
    let cls='okv', html='';
    if((j.blocking||[]).length){
      cls='block';
      html='<b>제출할 수 없습니다.</b><br>'+j.blocking.map(function(b){
        return '· '+esc(b.message)+(b.detail?' <span style="opacity:.75">'+esc(b.detail)+'</span>':'');
      }).join('<br>');
      document.getElementById('submit').disabled=true;
    }else{
      html='<b>서버 기준</b> 후보 '+(c.total||0)+'건 · 승격 '+(c.already_locked||0)
        +'건 · 거부 '+(c.already_rejected||0)+'건 · <b>남은 '+(c.remaining||0)+'건</b>';
      if((j.warnings||[]).length){
        cls='warn';
        html+='<br>'+j.warnings.map(function(w){
          return '⚠ '+esc(w.message)+(w.detail?' <span style="opacity:.75">'+esc(w.detail)+'</span>':'');
        }).join('<br>');
      }
    }
    box.className='preflight '+cls; box.innerHTML=html; box.style.display='block';
  }catch(e){ /* 점검 실패는 조용히 넘긴다 — 검수 자체를 막을 이유가 없다 */ }
}
document.getElementById('submit').addEventListener('click',async function(){
  const publish=document.getElementById('publish').checked;
  const box=document.getElementById('result');
  if(!WHO){box.className='result err';box.textContent='로그인이 필요합니다. 콘솔 로그인 후 다시 여세요.';return;}
  if(PF_BLOCKED){box.className='result err';box.textContent='서명 전 점검에서 막힌 항목이 있습니다. 위 안내를 확인하세요.';return;}
  // 서명 대상만 보낸다 — 화면 숫자(decCount)와 같은 규칙을 쓴다.
  const decisions=decidedIds().map(id=>{
    const o={doc_id:id,decision:DEC[id].decision,note:DEC[id].note||''};
    if(DEC[id].decision==='change')o.grade=DEC[id].grade;return o;
  });
  if(!decisions.length){box.className='result err';box.textContent='결정한 후보가 없습니다.';return;}
  this.disabled=true;this.textContent='제출 중...';
  try{
    const res=await fetch(POST_URL,{method:'POST',credentials:'same-origin',
      headers:{'Content-Type':'application/json; charset=utf-8'},
      body:JSON.stringify({decisions,actor:{user_id:WHO,role:WHOROLE},publish})});
    const j=await res.json();
    if(!res.ok){box.className='result err';box.textContent='실패('+res.status+'): '+(j.detail||JSON.stringify(j));}
    else{const rd=j.readiness||{};
      // locked 0 인데 거부만 있으면 성공(ok) 스타일이 오도 → 경고 스타일.
      box.className = (j.locked>0) ? 'result ok' : (j.rejected>0 ? 'result err' : 'result ok');
      // publish 를 체크했는데 실제 미반영(경로 미설정/승격 0)이면 명시 — 조용한 미리보기 강등 방지.
      var pubTxt = j.published ? '· 라이브 반영됨'
                 : (publish ? '· ⚠ 라이브 반영 요청됨—미반영 '+esc(j.publish_note||'(locked_eval 경로 미설정 또는 승격 0)')
                            // 결정은 화면에 남아 있다(DEC 를 비우지 않고 다시 그리지도 않는다).
                            // 그 사실을 말해 주지 않으면 처음부터 다시 하려고 한다.
                            : '· <b>미리보기</b>(라이브 무변경) — 반영하려면 위 <b>[라이브 반영]</b>을 '
                              + '체크하고 <b>다시 제출</b>하세요. <b>결정은 그대로 남아 있습니다.</b>');
      var rr = (j.rejected_reasons && Object.keys(j.rejected_reasons).length)
                 ? '<br>거부 사유: '+esc(JSON.stringify(j.rejected_reasons)) : '';
      // 보기 전용(합의 미달) 후보는 결정될 수 없다 — 서명 대상 기준으로 센다.
      var undecided=signableCount()-decidedIds().length;
      box.innerHTML='서명 완료 — locked <b>'+j.locked+'</b>건 승격 · 거부 '+j.rejected+'건 · 이번에 결정하지 않은 후보 '+undecided+'건 · 등급별 '+esc(JSON.stringify(j.locked_by_grade))
        +rr
        +'<br>readiness: ready=<b>'+rd.ready+'</b> per_grade='+esc(JSON.stringify(rd.per_grade))+' '+pubTxt
        +'<br>서명자: '+esc(j.reviewer_id)+(j.overridden?' (클라 값이 인증 신원으로 교정됨)':'');
    }
  }catch(e){box.className='result err';box.textContent='요청 오류: '+e;}
  this.disabled=false;this.textContent='서명 제출';
  loadPreflight();
});
(function(){
  var n=decRestore();
  if(!n) return;
  var el=document.getElementById('restored');
  el.innerHTML='이 잡에서 하던 <b>결정 '+n+'건</b>을 이 브라우저에서 복원했습니다. '
    +'이어서 하시면 됩니다. <button type="button" id="decclear">지우고 새로 시작</button>';
  el.style.display='block';
  document.getElementById('decclear').addEventListener('click',decClear);
})();
render();
</script>
</body></html>
"""
