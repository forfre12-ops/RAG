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
