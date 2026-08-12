"""골든 빌더 후보 검토본 HTML 렌더 (G4-html).

빌더 출력(build_<id>.jsonl / GoldenRecord.to_dict())을 지재원 관리자 검수용 인터랙티브
HTML로 렌더한다.

주의 — golden100_분류근거_보고서(regen_golden100_report.build_body)는 '평가 리포트'(정답
target 대조·정오답·미탐)다. 빌더 검토본은 정답이 아직 없는 '후보 검수'이므로 target/미탐 대신
**후보 등급(llm)·룰 등급·합의 상태·신뢰도**를 보여준다. 시각 폼(등급/상태 필터·카드)만 같은
계열을 따른다. (순수 렌더 — 무거운 의존 없음, HTML 문자열 반환)
"""
from __future__ import annotations

import base64
import html as _html
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional, Sequence

# 콘솔(static/styles.css)과 동일한 NovaX 토큰 — 골든 화면이 /demo 콘솔과 한 시스템으로 보이게
# 맞춘다(radius 0·동일 폰트스택·동일 등급색). 외부 CSS 링크를 쓰지 않는 이유: 이 HTML 은
# 감리 증적으로 단독 저장·전달될 수 있어 self-contained 여야 한다(정적 마운트 의존 금지).
_TOKENS = """
:root{
  --bg:#ffffff;--bg-surface:#fafafa;--text:#0a0a0a;--text-soft:#525252;--text-dim:#737373;
  --border:rgba(0,0,0,0.08);--border-strong:rgba(0,0,0,0.16);--accent:#0a0a0a;--accent-soft:#f4f4f4;
  --radius:0;
  --font-sans:-apple-system,BlinkMacSystemFont,"Segoe UI","Pretendard","Apple SD Gothic Neo","Malgun Gothic","Noto Sans KR",sans-serif;
  --font-mono:ui-monospace,SFMono-Regular,Menlo,Consolas,"Liberation Mono",monospace;
  --c-ts:#dc2626;--c-s1:#d97706;--c-s2:#0070f3;--c-s3:#16a34a;
}
"""

_DEFAULT_CSS = """<style>""" + _TOKENS + """
*{box-sizing:border-box}
body{font-family:var(--font-sans);margin:0;background:var(--bg);color:var(--text);
     -webkit-font-smoothing:antialiased}
/* ── 콘솔(static/styles.css)과 동일한 nav·브랜드 크롬 ─────────────────────────── */
.nav{position:sticky;top:0;z-index:50;background:rgba(255,255,255,0.72);
     backdrop-filter:saturate(180%) blur(14px);-webkit-backdrop-filter:saturate(180%) blur(14px);
     border-bottom:1px solid var(--border)}
.nav-inner{max-width:1320px;margin:0 auto;padding:14px 32px;display:flex;align-items:center;
           gap:10px;min-width:0}
.brand{display:inline-flex;align-items:center;gap:8px;color:var(--text);text-decoration:none;
       white-space:nowrap;min-width:0;line-height:1}
.brand-mark{width:34px;height:34px;display:grid;place-items:center;flex-shrink:0;background:#fff;
            border-radius:7px;padding:4px;border:1px solid rgba(0,0,0,.08)}
.brand-mark img{max-width:100%;max-height:100%;display:block}
.brand-name{font-weight:600;font-size:15px;letter-spacing:-.02em;color:var(--text)}
.brand-sep{color:var(--text-dim);font-size:14px;margin:0 6px;font-weight:400}
.brand-sub{font-size:12.5px;color:var(--text-dim)}
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
.lede-row{max-width:1320px;margin:0 auto;padding:28px 32px 0}
.h1{font-size:30px;font-weight:600;letter-spacing:-.03em;margin:0 0 10px;line-height:1.2}
.lede{font-size:15px;font-weight:300;color:var(--text-soft);line-height:1.55;margin:0;
      letter-spacing:-.012em;max-width:820px}
.container{max-width:1320px;margin:0 auto;padding:24px 32px 48px}
.stats-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.stat-card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:10px 14px;min-width:80px;text-align:center}
.stat-card .num{font-size:20px;font-weight:700;font-family:var(--font-mono)}
.stat-card .lbl{font-size:11px;color:var(--text-dim)}
.filters{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.filter-btn{border:1px solid var(--border-strong);background:var(--bg);color:var(--text);
            border-radius:var(--radius);padding:5px 10px;font-size:12px;cursor:pointer;font-family:inherit}
.filter-btn:hover{background:var(--accent-soft)}
.filter-btn.active{background:var(--accent);color:#fff;border-color:var(--accent)}
.filter-sep{width:1px;height:20px;background:var(--border-strong);margin:0 4px}
.search-box{border:1px solid var(--border-strong);border-radius:var(--radius);padding:5px 10px;
            font-size:12px;flex:1;min-width:140px;font-family:inherit}
.result-count{font-size:12px;color:var(--text-dim);margin-bottom:8px}
.records-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.card{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px}
.card-meta{font-size:11px;color:var(--text-dim);display:flex;gap:8px;font-family:var(--font-mono)}
.card-title{font-weight:600;font-size:13px;margin:6px 0 8px;display:flex;align-items:center;gap:8px}
.grade-mark{font-weight:700;border-radius:var(--radius);padding:2px 8px;color:#fff;font-size:12px}
.g-TS{background:var(--c-ts)}.g-S1{background:var(--c-s1)}.g-S2{background:var(--c-s2)}.g-S3{background:var(--c-s3)}
.row{font-size:12px;margin:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.status-txt{font-size:11px;border:1px solid var(--border-strong);border-radius:var(--radius);padding:1px 6px}
.status-txt.uncertain{border-color:var(--c-ts);color:var(--c-ts)}
.preview{font-size:11.5px;color:var(--text-soft);margin-top:8px;line-height:1.5}
.no-results{padding:30px;text-align:center;color:var(--text-dim)}
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


def _reviewer_label(profile: Optional[str]) -> str:
    """검수 주체 문구 — 배포처에 따라 달라진다.

    종전엔 "지재원 관리자 검수용"이 템플릿에 박혀 있어, **고객사 폐쇄망에 배포된 화면에서도**
    발주처 검수자에게 '지재원'이 표시됐다(제출문서 `발주처_골든셋_생성갱신_지원` §2 의 설명과
    화면이 어긋나는 지점). 배지와 같은 프로파일 근거로 문구도 맞춘다.
    """
    site = _SITES.get(profile or "", ("", "", ""))[0]
    if site == "고객사":
        return "발주처 검수자"
    if site == "지재원":
        return "지재원 관리자"
    return "검수자"


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


def _nav_html(sub: str, profile: Optional[str], screen: str) -> str:
    """콘솔(static/*.html)과 같은 브랜드 바."""
    logo = _logo_data_uri()
    mark = f'<span class="brand-mark"><img src="{logo}" alt="Koipa"/></span>' if logo else ""
    return (
        '<nav class="nav"><div class="nav-inner">'
        f'<span class="brand">{mark}'
        '<span class="brand-name">한국지식재산보호원</span>'
        '<span class="brand-sep">/</span>'
        f'<span class="brand-sub">{_html.escape(sub)}</span></span>'
        + _site_badge_html(profile, screen)
        + "</div></nav>"
    )


def _display_records(records: Sequence[dict]) -> list[dict]:
    out: list[dict] = []
    for r in records:
        grade = r.get("label") or r.get("llm_grade") or "S3"
        if grade not in _GRADES:
            grade = "S3"
        text = str(r.get("text") or "")
        out.append({
            "id": str(r.get("doc_id", "")),
            "grade": grade,                                   # 후보 등급 (gold=label, uncertain=llm 제안)
            "rule": str(r.get("rule_grade", "")),
            "llm": str(r.get("llm_grade", "")),
            "conf": round(float(r.get("llm_confidence") or 0.0), 3),
            "status": str(r.get("status", "")),
            "is_gold": r.get("review_status") in ("accepted", "gold_candidate"),
            "agree": bool(r.get("agreement")),
            "domain": str(r.get("domain", "") or r.get("source", "")),
            "preview": text[:150].replace("\n", " "),
        })
    return out


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


def render_review_html(
    records: Sequence[dict],
    *,
    title: str = "골든셋 후보 검토본",
    subtitle: str = "",
    css: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    """빌더 후보 레코드(dict)들을 지재원 관리자 검수용 인터랙티브 HTML로 렌더.

    profile = settings.deploy_profile. 주면 콘솔과 같은 배포 주체 배지(지재원/고객사)를
    nav 에 박는다 — 이중배포에서 어느 스택의 후보를 보는지 화면만으로 구분된다.
    """
    data = _display_records(records)
    n_gold = sum(1 for d in data if d["is_gold"])
    head = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)}</title>{css or _DEFAULT_CSS}</head>"
    )
    return head + (
        _BODY_TEMPLATE
        .replace("__NAV__", _nav_html("골든셋 후보 검토본", profile, "검수"))
        .replace("__TITLE__", _html.escape(title))
        .replace("__SUBTITLE__", _html.escape(subtitle))
        .replace("__REVIEWER__", _html.escape(_reviewer_label(profile)))
        .replace("__TOTAL__", str(len(data)))
        .replace("__GOLD__", str(n_gold))
        .replace("__UNCERTAIN__", str(len(data) - n_gold))
        .replace("__DATA__", _embed_json(data))
    )


def render_review_html_from_jsonl(
    paths: Sequence[str | Path],
    *,
    title: str = "골든셋 후보 검토본",
    subtitle: str = "",
    css: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    """build_<id>.jsonl·uncertain_<id>.jsonl 등을 읽어 검토본 HTML로 렌더."""
    recs: list[dict] = []
    for p in paths:
        pp = Path(p)
        if not pp.exists():
            continue
        for line in pp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return render_review_html(recs, title=title, subtitle=subtitle, css=css, profile=profile)


# ── 골든셋 검수 · 화면 서명(signoff) 인터랙티브 렌더 ──────────────────────────────
# render_review(보기 전용)와 달리 각 후보에 승인/등급변경/거부 폼을 붙이고, 제출 시
# POST /golden/jobs/{id}/signoff 로 결정을 보내 locked_gold_eval 로 승격한다. 검수자가
# jsonl 을 손으로 편집하는 대신 화면에서 클릭 서명 — 서명 캡처 UI 갭(뷰어만 존재) 해소.
def _signoff_records(records: Sequence[dict]) -> list[dict]:
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
        })
    return out


def render_signoff_html(
    records: Sequence[dict],
    *,
    job_id: str,
    post_url: str,
    default_reviewer: str = "",
    default_api_key: str = "",
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    """gold 후보를 화면 서명용 인터랙티브 HTML로 렌더(승인/등급변경/거부 → POST signoff)."""
    data = _signoff_records(records)
    head = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)}</title>{css or _DEFAULT_CSS}{_SIGNOFF_CSS}</head>"
    )
    return head + (
        _SIGNOFF_BODY
        .replace("__NAV__", _nav_html("골든셋 검수 · 서명", profile, "서명"))
        .replace("__TITLE__", _html.escape(title))
        .replace("__JOB__", _html.escape(job_id))
        .replace("__POST_URL__", _html.escape(post_url))
        .replace("__REVIEWER_DEFAULT__", _html.escape(default_reviewer or ""))
        .replace("__APIKEY_DEFAULT__", _html.escape(default_api_key or ""))
        .replace("__TOTAL__", str(len(data)))
        .replace("__DATA__", _embed_json(data))
    )


def render_signoff_html_from_jsonl(
    paths: Sequence[str | Path],
    *,
    job_id: str,
    post_url: str,
    default_reviewer: str = "",
    default_api_key: str = "",
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
    profile: Optional[str] = None,
) -> str:
    """build_<id>.jsonl(gold 후보)을 읽어 서명 HTML로 렌더."""
    recs: list[dict] = []
    for p in paths:
        pp = Path(p)
        if not pp.exists():
            continue
        for line in pp.read_text(encoding="utf-8").splitlines():
            if line.strip():
                recs.append(json.loads(line))
    return render_signoff_html(recs, job_id=job_id, post_url=post_url, title=title, css=css,
                               profile=profile, default_reviewer=default_reviewer,
                               default_api_key=default_api_key)


# 토큰 재선언 — render_signoff_html(css=...) 로 커스텀 CSS 를 주입해도 서명 화면이
# 토큰 미정의로 무너지지 않게 self-sufficient 하게 둔다(중복 선언은 무해).
_SIGNOFF_CSS = """<style>""" + _TOKENS + """
.signbar{position:sticky;top:0;z-index:10;background:var(--accent);color:#e4e4e7;padding:12px 24px;
         display:flex;gap:10px;flex-wrap:wrap;align-items:end;border-bottom:1px solid var(--accent)}
.signbar .fld{display:flex;flex-direction:column;font-size:11px;gap:3px}
.signbar input,.signbar select{padding:5px 8px;border:1px solid #3f3f46;border-radius:var(--radius);
                               background:#18181b;color:#f4f4f5;font-size:12px;font-family:inherit}
.signbar .chk{flex-direction:row;align-items:center;gap:4px}
.signbar button{padding:8px 18px;border:0;border-radius:var(--radius);background:var(--c-s3);color:#fff;
                font-weight:700;font-size:13px;cursor:pointer;font-family:inherit}
.signbar button:disabled{background:#52525b;cursor:not-allowed}
.rubric{background:var(--accent-soft);border:1px solid var(--border-strong);border-left:3px solid var(--c-s1);
        border-radius:var(--radius);padding:10px 14px;margin:14px 0;font-size:12px;line-height:1.6}
.rubric b{color:var(--text)}
.scard{background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);padding:14px;margin-bottom:12px}
.scard.decided{border-color:var(--c-s3);box-shadow:0 0 0 1px var(--c-s3) inset}
.scard.rejected{border-color:var(--c-ts);box-shadow:0 0 0 1px var(--c-ts) inset;opacity:.75}
.stext{font-size:12.5px;color:var(--text-soft);white-space:pre-wrap;max-height:220px;overflow:auto;
       background:var(--bg-surface);border:1px solid var(--border);border-radius:var(--radius);
       padding:10px;margin:8px 0;line-height:1.55;font-family:var(--font-mono)}
.decrow{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px}
.decrow label{display:flex;gap:5px;align-items:center;cursor:pointer}
.decrow select{padding:3px 6px;border:1px solid var(--border-strong);border-radius:var(--radius);font-family:inherit}
.note{width:100%;margin-top:8px;padding:6px 8px;border:1px solid var(--border);border-radius:var(--radius);
      font-size:12px;font-family:inherit}
.result{margin:14px 0;padding:12px 16px;border-radius:var(--radius);font-size:13px;display:none}
.result.ok{background:#f0fdf4;border:1px solid var(--c-s3);border-left:3px solid var(--c-s3);color:#166534;display:block}
.result.err{background:#fef2f2;border:1px solid var(--c-ts);border-left:3px solid var(--c-ts);color:#991b1b;display:block}
</style>"""

_SIGNOFF_BODY = r"""
<body>
__NAV__
<div class="lede-row">
  <h1 class="h1">__TITLE__</h1>
  <p class="lede">job __JOB__ · gold 후보 __TOTAL__건 — 승인/등급변경/거부 후 서명하면 <b>locked_gold_eval</b>(사람서명 평가정답)로 승격됩니다.</p>
</div>
<div class="signbar">
  <div class="fld"><label>X-API-Key</label><input id="key" type="password" value="__APIKEY_DEFAULT__" placeholder="settings.api_key"></div>
  <div class="fld"><label>역할(X-Actor-Role)</label><select id="role"><option value="reviewer">reviewer</option><option value="admin">admin</option><option value="kl_backend">kl_backend</option></select></div>
  <div class="fld"><label>검수자 계정(reviewer_id)</label><input id="reviewer" value="__REVIEWER_DEFAULT__" placeholder="실계정 예: hong.gd"></div>
  <div class="fld chk"><input type="checkbox" id="publish" checked><label for="publish">라이브 반영(publish)</label></div>
  <div class="fld"><label>&nbsp;</label><button id="submit">서명 제출</button></div>
</div>
<div class="container">
  <div class="rubric">
    <b>판정</b> S×V×M → <b>8=TS</b>·<b>4=S1</b>·<b>1·2=S2</b>·<b>0=S3</b> (S=0이면 무조건 S3).
    핵심 분기 <b>S1 vs TS = M</b>: S2·V2에서 <b>M=0→S1</b>(관리 미공식화)·<b>M≥1→TS</b>. 확신 없으면 <b>거부</b>(미탐 안전).
    승인=제안등급 유지 / 변경=다른 등급 / 거부=승격 제외. 미결정 후보는 제출에서 빠진다.
  </div>
  <div class="filters">
    <span style="font-size:12px">등급</span>
    <button class="filter-btn active" data-v="all">전체</button>
    <button class="filter-btn" data-v="TS">TS</button>
    <button class="filter-btn" data-v="S1">S1</button>
    <button class="filter-btn" data-v="S2">S2</button>
    <button class="filter-btn" data-v="S3">S3</button>
    <span class="filter-sep"></span>
    <input class="search-box" id="q" placeholder="id·본문 검색...">
    <span id="deccount" style="font-size:12px;color:var(--text-dim)"></span>
  </div>
  <div class="result" id="result"></div>
  <div id="grid"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('data').textContent);
const POST_URL="__POST_URL__";
const DEC={};       // id -> {decision, grade, note}
let g='all',q='';
// 텍스트+속성(name=/data-id=/value=) 양쪽에 쓰이므로 따옴표도 이스케이프(속성 컨텍스트 주입 차단).
function esc(t){const d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML.replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
function gopts(sel){return ['TS','S1','S2','S3'].map(x=>'<option value="'+x+'"'+(x===sel?' selected':'')+'>'+x+'</option>').join('');}
function card(r){
  const d=DEC[r.id]||{};
  const cls=d.decision==='reject'?'scard rejected':(d.decision?'scard decided':'scard');
  return '<div class="'+cls+'" data-id="'+esc(r.id)+'">'
    +'<div class="card-meta"><span class="grade-mark g-'+r.grade+'">'+r.grade+'</span> <span>'+esc(r.id)+'</span> <span>'+esc(r.domain)+'</span> <span style="color:var(--text-dim)">룰 '+esc(r.rule)+' · LLM '+esc(r.llm)+' conf '+r.conf.toFixed(2)+'</span></div>'
    +'<div class="stext">'+esc(r.text)+'</div>'
    +'<div class="decrow">'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="approve"'+(d.decision==='approve'?' checked':'')+'> 승인 ('+r.grade+' 유지)</label>'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="change"'+(d.decision==='change'?' checked':'')+'> 등급변경 <select class="gsel" data-id="'+esc(r.id)+'">'+gopts(d.grade||r.grade)+'</select></label>'
      +'<label><input type="radio" name="dec-'+esc(r.id)+'" value="reject"'+(d.decision==='reject'?' checked':'')+'> 거부</label>'
    +'</div>'
    +'<input class="note" data-id="'+esc(r.id)+'" placeholder="메모(선택)" value="'+esc(d.note||'')+'">'
    +'</div>';
}
function decCount(){const n=Object.keys(DEC).length;document.getElementById('deccount').textContent='결정 '+n+' / 후보 '+DATA.length+'건';}
function render(){
  const ql=q.toLowerCase();
  const f=DATA.filter(r=>{
    if(g!=='all'&&r.grade!==g)return false;
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
    decCount();
  } else if(t.matches('.gsel')){
    const id=t.dataset.id;DEC[id]=DEC[id]||{decision:'change'};DEC[id].grade=t.value;
    const rc=document.querySelector('input[name="dec-'+CSS.escape(id)+'"][value=change]');if(rc)rc.checked=true;DEC[id].decision='change';
  }
});
document.getElementById('grid').addEventListener('input',function(e){
  if(e.target.matches('.note')){const id=e.target.dataset.id;DEC[id]=DEC[id]||{};DEC[id].note=e.target.value;}
});
document.querySelectorAll('.filter-btn').forEach(b=>b.addEventListener('click',function(){
  document.querySelectorAll('.filter-btn').forEach(x=>x.classList.remove('active'));this.classList.add('active');g=this.dataset.v;render();
}));
document.getElementById('q').addEventListener('input',function(){q=this.value;render();});
document.getElementById('submit').addEventListener('click',async function(){
  const key=document.getElementById('key').value.trim();
  const role=document.getElementById('role').value;
  const reviewer=document.getElementById('reviewer').value.trim();
  const publish=document.getElementById('publish').checked;
  const box=document.getElementById('result');
  if(!reviewer){box.className='result err';box.textContent='검수자 계정(reviewer_id)을 입력하세요.';return;}
  const decisions=Object.keys(DEC).filter(id=>DEC[id].decision).map(id=>{
    const o={doc_id:id,decision:DEC[id].decision,note:DEC[id].note||''};
    if(DEC[id].decision==='change')o.grade=DEC[id].grade;return o;
  });
  if(!decisions.length){box.className='result err';box.textContent='결정한 후보가 없습니다.';return;}
  this.disabled=true;this.textContent='제출 중...';
  try{
    const res=await fetch(POST_URL,{method:'POST',headers:{'X-API-Key':key,'X-Actor-Role':role,'Content-Type':'application/json; charset=utf-8'},
      body:JSON.stringify({decisions,actor:{user_id:reviewer,role:role},publish})});
    const j=await res.json();
    if(!res.ok){box.className='result err';box.textContent='실패('+res.status+'): '+(j.detail||JSON.stringify(j));}
    else{const rd=j.readiness||{};
      // locked 0 인데 거부만 있으면 성공(ok) 스타일이 오도 → 경고 스타일.
      box.className = (j.locked>0) ? 'result ok' : (j.rejected>0 ? 'result err' : 'result ok');
      // publish 를 체크했는데 실제 미반영(경로 미설정/승격 0)이면 명시 — 조용한 미리보기 강등 방지.
      var pubTxt = j.published ? '· 라이브 반영됨'
                 : (publish ? '· ⚠ 라이브 반영 요청됨—미반영 '+esc(j.publish_note||'(locked_eval 경로 미설정 또는 승격 0)')
                            : '· 미리보기(라이브 무변경)');
      var rr = (j.rejected_reasons && Object.keys(j.rejected_reasons).length)
                 ? '<br>거부 사유: '+esc(JSON.stringify(j.rejected_reasons)) : '';
      box.innerHTML='서명 완료 — locked <b>'+j.locked+'</b>건 승격 (거부/미서명 '+j.rejected+') · 등급별 '+esc(JSON.stringify(j.locked_by_grade))
        +rr
        +'<br>readiness: ready=<b>'+rd.ready+'</b> per_grade='+esc(JSON.stringify(rd.per_grade))+' '+pubTxt
        +'<br>서명자: '+esc(j.reviewer_id)+(j.overridden?' (클라 값이 인증 신원으로 교정됨)':'');
    }
  }catch(e){box.className='result err';box.textContent='요청 오류: '+e;}
  this.disabled=false;this.textContent='서명 제출';
});
render();
</script>
</body></html>
"""


_BODY_TEMPLATE = r"""
<body>
__NAV__
<div class="lede-row">
  <h1 class="h1">__TITLE__</h1>
  <p class="lede">__SUBTITLE__ · 후보 __TOTAL__건 (gold __GOLD__ / 검수대상 __UNCERTAIN__) — __REVIEWER__ 검수용. <b>정답이 아니라 검토 후보</b>입니다.</p>
</div>
<div class="container">
  <div class="stats-bar" id="stats"></div>
  <div class="filters">
    <span style="font-size:12px">등급</span>
    <button class="filter-btn active" data-f="grade" data-v="all">전체</button>
    <button class="filter-btn" data-f="grade" data-v="TS">TS</button>
    <button class="filter-btn" data-f="grade" data-v="S1">S1</button>
    <button class="filter-btn" data-f="grade" data-v="S2">S2</button>
    <button class="filter-btn" data-f="grade" data-v="S3">S3</button>
    <span class="filter-sep"></span>
    <span style="font-size:12px">상태</span>
    <button class="filter-btn active" data-f="status" data-v="all">전체</button>
    <button class="filter-btn" data-f="status" data-v="gold">gold 후보</button>
    <button class="filter-btn" data-f="status" data-v="uncertain">검수대상</button>
    <span class="filter-sep"></span>
    <input class="search-box" id="q" placeholder="id·요약 검색...">
  </div>
  <div class="result-count" id="cnt"></div>
  <div class="records-grid" id="grid"></div>
</div>
<script id="data" type="application/json">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('data').textContent);
let g='all',s='all',q='';
function esc(t){const d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML;}
function card(r){
  const st=r.is_gold?'<span class="status-txt">'+esc(r.status)+'</span>':'<span class="status-txt uncertain">검수대상 · '+esc(r.status)+'</span>';
  const ag=r.agree?'룰·LLM 일치':'룰·LLM 불일치';
  return '<div class="card">'
    +'<div class="card-meta"><span>'+esc(r.id)+'</span><span>'+esc(r.domain)+'</span></div>'
    +'<div class="card-title"><span class="grade-mark g-'+r.grade+'">'+r.grade+'</span> 후보 등급</div>'
    +'<div class="row">룰 <b>'+esc(r.rule)+'</b> · LLM <b>'+esc(r.llm)+'</b> <span style="color:var(--text-dim)">conf '+r.conf.toFixed(2)+'</span> '+st+'</div>'
    +'<div class="row" style="font-size:11px;color:var(--text-dim)">'+ag+'</div>'
    +'<div class="preview">'+esc(r.preview)+'</div>'
    +'</div>';
}
function stats(){
  const bg={TS:0,S1:0,S2:0,S3:0};let gold=0;
  DATA.forEach(r=>{bg[r.grade]=(bg[r.grade]||0)+1;if(r.is_gold)gold++;});
  document.getElementById('stats').innerHTML=
    '<div class="stat-card"><div class="num">'+DATA.length+'</div><div class="lbl">전체</div></div>'
    +'<div class="stat-card"><div class="num">'+bg.TS+'</div><div class="lbl">TS</div></div>'
    +'<div class="stat-card"><div class="num">'+bg.S1+'</div><div class="lbl">S1</div></div>'
    +'<div class="stat-card"><div class="num">'+bg.S2+'</div><div class="lbl">S2</div></div>'
    +'<div class="stat-card"><div class="num">'+bg.S3+'</div><div class="lbl">S3</div></div>'
    +'<div class="stat-card"><div class="num">'+gold+'</div><div class="lbl">gold 후보</div></div>'
    +'<div class="stat-card"><div class="num">'+(DATA.length-gold)+'</div><div class="lbl">검수대상</div></div>';
}
function apply(){
  const ql=q.toLowerCase();
  const f=DATA.filter(r=>{
    if(g!=='all'&&r.grade!==g)return false;
    if(s==='gold'&&!r.is_gold)return false;
    if(s==='uncertain'&&r.is_gold)return false;
    if(ql&&!((r.id+' '+r.preview).toLowerCase().includes(ql)))return false;
    return true;
  });
  document.getElementById('cnt').textContent=f.length+'건 표시 (전체 '+DATA.length+'건)';
  document.getElementById('grid').innerHTML=f.length?f.map(card).join(''):'<div class="no-results">조건에 맞는 후보가 없습니다.</div>';
}
document.querySelectorAll('.filter-btn').forEach(b=>b.addEventListener('click',function(){
  const f=this.dataset.f,v=this.dataset.v;
  document.querySelectorAll('.filter-btn[data-f="'+f+'"]').forEach(x=>x.classList.remove('active'));
  this.classList.add('active');
  if(f==='grade')g=v;else s=v;apply();
}));
document.getElementById('q').addEventListener('input',function(){q=this.value;apply();});
stats();apply();
</script>
</body></html>
"""
