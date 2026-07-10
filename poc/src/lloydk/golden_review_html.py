"""골든 빌더 후보 검토본 HTML 렌더 (G4-html).

빌더 출력(build_<id>.jsonl / GoldenRecord.to_dict())을 지재원 관리자 검수용 인터랙티브
HTML로 렌더한다.

주의 — golden100_분류근거_보고서(regen_golden100_report.build_body)는 '평가 리포트'(정답
target 대조·정오답·미탐)다. 빌더 검토본은 정답이 아직 없는 '후보 검수'이므로 target/미탐 대신
**후보 등급(llm)·룰 등급·합의 상태·신뢰도**를 보여준다. 시각 폼(등급/상태 필터·카드)만 같은
계열을 따른다. (순수 렌더 — 무거운 의존 없음, HTML 문자열 반환)
"""
from __future__ import annotations

import html as _html
import json
from pathlib import Path
from typing import Optional, Sequence

_DEFAULT_CSS = """<style>
*{box-sizing:border-box}body{font-family:'Malgun Gothic',system-ui,sans-serif;margin:0;background:#f4f4f5;color:#18181b}
.app-header{background:#1e293b;color:#fff;padding:18px 24px}.app-header h1{margin:0;font-size:18px}
.app-header .sub{font-size:12px;color:#94a3b8;margin-top:4px}
.container{max-width:1100px;margin:0 auto;padding:20px}
.stats-bar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.stat-card{background:#fff;border:1px solid #e4e4e7;border-radius:8px;padding:10px 14px;min-width:80px;text-align:center}
.stat-card .num{font-size:20px;font-weight:700}.stat-card .lbl{font-size:11px;color:#71717a}
.filters{display:flex;gap:6px;align-items:center;flex-wrap:wrap;margin-bottom:14px}
.filter-btn{border:1px solid #d4d4d8;background:#fff;border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer}
.filter-btn.active{background:#1e293b;color:#fff;border-color:#1e293b}
.filter-sep{width:1px;height:20px;background:#d4d4d8;margin:0 4px}
.search-box{border:1px solid #d4d4d8;border-radius:6px;padding:5px 10px;font-size:12px;flex:1;min-width:140px}
.result-count{font-size:12px;color:#71717a;margin-bottom:8px}
.records-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:12px}
.card{background:#fff;border:1px solid #e4e4e7;border-radius:8px;padding:14px}
.card-meta{font-size:11px;color:#71717a;display:flex;gap:8px}
.card-title{font-weight:600;font-size:13px;margin:6px 0 8px;display:flex;align-items:center;gap:8px}
.grade-mark{font-weight:700;border-radius:4px;padding:2px 8px;color:#fff;font-size:12px}
.g-TS{background:#dc2626}.g-S1{background:#ea580c}.g-S2{background:#ca8a04}.g-S3{background:#16a34a}
.row{font-size:12px;margin:6px 0;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.status-txt{font-size:11px;border:1px solid #d4d4d8;border-radius:4px;padding:1px 6px}
.status-txt.uncertain{border-color:#dc2626;color:#dc2626}
.preview{font-size:11.5px;color:#52525b;margin-top:8px;line-height:1.5}
.no-results{padding:30px;text-align:center;color:#9ca3af}
</style>"""

_GRADES = ("TS", "S1", "S2", "S3")


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


def render_review_html(
    records: Sequence[dict],
    *,
    title: str = "골든셋 후보 검토본",
    subtitle: str = "",
    css: Optional[str] = None,
) -> str:
    """빌더 후보 레코드(dict)들을 지재원 관리자 검수용 인터랙티브 HTML로 렌더."""
    data = _display_records(records)
    n_gold = sum(1 for d in data if d["is_gold"])
    head = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)}</title>{css or _DEFAULT_CSS}</head>"
    )
    return head + (
        _BODY_TEMPLATE
        .replace("__TITLE__", _html.escape(title))
        .replace("__SUBTITLE__", _html.escape(subtitle))
        .replace("__TOTAL__", str(len(data)))
        .replace("__GOLD__", str(n_gold))
        .replace("__UNCERTAIN__", str(len(data) - n_gold))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
    )


def render_review_html_from_jsonl(
    paths: Sequence[str | Path],
    *,
    title: str = "골든셋 후보 검토본",
    subtitle: str = "",
    css: Optional[str] = None,
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
    return render_review_html(recs, title=title, subtitle=subtitle, css=css)


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
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
) -> str:
    """gold 후보를 화면 서명용 인터랙티브 HTML로 렌더(승인/등급변경/거부 → POST signoff)."""
    data = _signoff_records(records)
    head = (
        "<!doctype html><html lang=\"ko\"><head><meta charset=\"utf-8\">"
        f"<title>{_html.escape(title)}</title>{css or _DEFAULT_CSS}{_SIGNOFF_CSS}</head>"
    )
    return head + (
        _SIGNOFF_BODY
        .replace("__TITLE__", _html.escape(title))
        .replace("__JOB__", _html.escape(job_id))
        .replace("__POST_URL__", _html.escape(post_url))
        .replace("__TOTAL__", str(len(data)))
        .replace("__DATA__", json.dumps(data, ensure_ascii=False))
    )


def render_signoff_html_from_jsonl(
    paths: Sequence[str | Path],
    *,
    job_id: str,
    post_url: str,
    title: str = "골든셋 검수 · 서명",
    css: Optional[str] = None,
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
    return render_signoff_html(recs, job_id=job_id, post_url=post_url, title=title, css=css)


_SIGNOFF_CSS = """<style>
.signbar{position:sticky;top:0;z-index:10;background:#0f172a;color:#e2e8f0;padding:12px 24px;display:flex;gap:10px;flex-wrap:wrap;align-items:end}
.signbar .fld{display:flex;flex-direction:column;font-size:11px;gap:3px}
.signbar input,.signbar select{padding:5px 8px;border:1px solid #334155;border-radius:6px;background:#1e293b;color:#e2e8f0;font-size:12px}
.signbar .chk{flex-direction:row;align-items:center;gap:4px}
.signbar button{padding:8px 18px;border:0;border-radius:6px;background:#16a34a;color:#fff;font-weight:700;font-size:13px;cursor:pointer}
.signbar button:disabled{background:#475569;cursor:not-allowed}
.rubric{background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:10px 14px;margin:14px 0;font-size:12px;line-height:1.6}
.rubric b{color:#92400e}
.scard{background:#fff;border:1px solid #e4e4e7;border-radius:8px;padding:14px;margin-bottom:12px}
.scard.decided{border-color:#16a34a;box-shadow:0 0 0 1px #16a34a inset}
.scard.rejected{border-color:#dc2626;box-shadow:0 0 0 1px #dc2626 inset;opacity:.75}
.stext{font-size:12.5px;color:#27272a;white-space:pre-wrap;max-height:220px;overflow:auto;background:#fafafa;border:1px solid #f0f0f0;border-radius:6px;padding:10px;margin:8px 0;line-height:1.55}
.decrow{display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px}
.decrow label{display:flex;gap:5px;align-items:center;cursor:pointer}
.decrow select{padding:3px 6px;border:1px solid #d4d4d8;border-radius:4px}
.note{width:100%;margin-top:8px;padding:6px 8px;border:1px solid #e4e4e7;border-radius:6px;font-size:12px}
.result{margin:14px 0;padding:12px 16px;border-radius:8px;font-size:13px;display:none}
.result.ok{background:#f0fdf4;border:1px solid #86efac;color:#166534;display:block}
.result.err{background:#fef2f2;border:1px solid #fca5a5;color:#991b1b;display:block}
</style>"""

_SIGNOFF_BODY = r"""
<body>
<header class="app-header"><h1>__TITLE__</h1>
<div class="sub">job __JOB__ · gold 후보 __TOTAL__건 · 지재원 관리자 골든셋 검수 — 승인/등급변경/거부 후 서명하면 locked_gold_eval(사람서명 평가정답)로 승격</div></header>
<div class="signbar">
  <div class="fld"><label>X-API-Key</label><input id="key" type="password" placeholder="settings.api_key"></div>
  <div class="fld"><label>역할(X-Actor-Role)</label><select id="role"><option value="reviewer">reviewer</option><option value="admin">admin</option><option value="kl_backend">kl_backend</option></select></div>
  <div class="fld"><label>검수자 계정(reviewer_id)</label><input id="reviewer" placeholder="실계정 예: hong.gd"></div>
  <div class="fld chk"><input type="checkbox" id="publish"><label for="publish">라이브 반영(publish)</label></div>
  <div class="fld chk"><input type="checkbox" id="dual"><label for="dual">TS/S1 이중서명</label></div>
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
    <span id="deccount" style="font-size:12px;color:#71717a"></span>
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
function esc(t){const d=document.createElement('div');d.textContent=t==null?'':t;return d.innerHTML;}
function gopts(sel){return ['TS','S1','S2','S3'].map(x=>'<option value="'+x+'"'+(x===sel?' selected':'')+'>'+x+'</option>').join('');}
function card(r){
  const d=DEC[r.id]||{};
  const cls=d.decision==='reject'?'scard rejected':(d.decision?'scard decided':'scard');
  return '<div class="'+cls+'" data-id="'+esc(r.id)+'">'
    +'<div class="card-meta"><span class="grade-mark g-'+r.grade+'">'+r.grade+'</span> <span>'+esc(r.id)+'</span> <span>'+esc(r.domain)+'</span> <span style="color:#71717a">룰 '+esc(r.rule)+' · LLM '+esc(r.llm)+' conf '+r.conf.toFixed(2)+'</span></div>'
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
  const dual=document.getElementById('dual').checked;
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
      body:JSON.stringify({decisions,actor:{user_id:reviewer,role:role},publish,dual_for_upper:dual})});
    const j=await res.json();
    if(!res.ok){box.className='result err';box.textContent='실패('+res.status+'): '+(j.detail||JSON.stringify(j));}
    else{const rd=j.readiness||{};
      box.className='result ok';
      box.innerHTML='서명 완료 — locked <b>'+j.locked+'</b>건 승격 (거부/미서명 '+j.rejected+') · 등급별 '+JSON.stringify(j.locked_by_grade)
        +'<br>readiness: ready=<b>'+rd.ready+'</b> per_grade='+JSON.stringify(rd.per_grade)+' '+(j.published?'· 라이브 반영됨':'· 미리보기(라이브 무변경)')
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
<header class="app-header"><h1>__TITLE__</h1>
<div class="sub">__SUBTITLE__ · 후보 __TOTAL__건 (gold __GOLD__ / 검수대상 __UNCERTAIN__) · 지재원 관리자 검수용 — 정답이 아니라 검토 후보</div></header>
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
    +'<div class="row">룰 <b>'+esc(r.rule)+'</b> · LLM <b>'+esc(r.llm)+'</b> <span style="color:#71717a">conf '+r.conf.toFixed(2)+'</span> '+st+'</div>'
    +'<div class="row" style="font-size:11px;color:#71717a">'+ag+'</div>'
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
