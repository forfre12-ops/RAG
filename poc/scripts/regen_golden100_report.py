"""golden100 분류근거 보고서 재생성 — 픽스처 llm열을 실제 분류기 출력으로 교체.

- rule: 실제 LabelRuleEngine 출력 (등급·S/V/M·매칭 키워드)
- model: 보정 학습모델(bpilot_v2 + temperature.json) 실출력 (등급 + confidence)
- status: 실제 런타임 검수 게이트 (conf < review_confidence_threshold → 검수필요)
- 미탐(과소분류): 모델이 자동확정했는데 정답보다 낮은 등급 → ⚠ 표기 (검수가 잡아야 할 진짜 위험)

기존 HTML의 <style>/<head>를 그대로 재사용해 폼 일관성을 보존한다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

from lloydk.config import settings  # noqa: E402
from lloydk.modules.m3_labeling.rule_engine import LabelRuleEngine  # noqa: E402
from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS, to_canonical_factor  # noqa: E402
from lloydk.modules.m5_inference.pipeline import InferencePipeline  # noqa: E402

# argv[1]=jsonl, argv[2]=출력 html (기본: golden100). golden500도 동일 포맷으로 재사용.
JSONL = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("datasets/gold/golden100_labeled_v2.jsonl")
HTML = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("../doc/result/open/golden100_분류근거_보고서.html")
MODEL_DIR = "artifacts/classifier_bpilot_v2/v-137066a1"

DOM = {
    "energy": "에너지", "pharma": "제약", "fintech": "핀테크", "aerospace": "항공우주",
    "automotive": "자동차", "telecom": "통신", "biotech": "바이오", "semiconductor": "반도체",
    "quantum": "양자컴퓨팅", "manufacturing": "제조", "ai_defense": "국방AI", "medical_device": "의료기기",
    "retail": "유통", "food": "식품", "education": "교육", "healthcare": "헬스케어",
    "media": "미디어", "real_estate": "부동산", "insurance": "보험", "gaming": "게임",
    "supply_chain": "공급망", "consulting": "컨설팅", "hr": "인사", "marketing": "마케팅",
    "finance": "재무", "it": "IT", "product": "제품", "business_dev": "사업개발",
    "procurement": "구매", "operations": "운영", "legal": "법무", "training": "교육훈련",
    "pr": "홍보", "recruitment": "채용", "ir": "IR", "newsletter": "뉴스레터",
    "webinar": "웨비나", "academic": "학술", "faq": "FAQ", "csr": "CSR",
    "catalog": "카탈로그", "company_profile": "기업소개", "manual": "매뉴얼",
    "regulatory": "규정", "ma": "M&A", "security": "보안", "ai": "AI", "defense": "국방",
}
ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def preview_of(body: str) -> str:
    p = body[:200]
    for t in ("[가상기업A]", "[가상기업B]", "[가상기업C]", "[가상기업D]", "[가상기업E]"):
        p = p.replace(t, "")
    p = p.strip()
    return p[:147] + "..." if len(p) > 150 else p


def main() -> int:
    recs = [json.loads(l) for l in JSONL.open(encoding="utf-8") if l.strip()]
    eng = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    ip = InferencePipeline(model_dir=MODEL_DIR)
    if getattr(ip, "_model", None) is None:
        print("[ERR] 모델 로드 실패 — MODEL_DIR 확인", file=sys.stderr)
        return 1
    thresh = float(settings.review_confidence_threshold)
    temp = ip._temperature
    print(f"model loaded, temperature={temp}, review_threshold={thresh}")

    out = []
    for r in recs:
        body = r["body"]
        rr = eng.label(body)
        rule_g = rr.grade
        # 매칭 키워드를 S/V/M 표준요건으로 그룹핑
        ev = {"SECRECY": [], "VALUE": [], "MANAGEMENT": []}
        for mk in rr.matched_keywords:
            cf = to_canonical_factor(mk.factor)
            if cf in ev and mk.keyword not in ev[cf]:
                ev[cf].append(mk.keyword)
        s_lv = int(rr.factor_scores.get("SECRECY", 0))
        v_lv = int(rr.factor_scores.get("VALUE", 0))
        m_lv = int(rr.factor_scores.get("MANAGEMENT", 0))

        res = ip.run(body, return_evidence=False)
        model_g = res.label.value if hasattr(res.label, "value") else str(res.label)
        conf = round(float(res.confidence), 3)

        tgt = r["target"]
        status = "auto" if conf >= thresh else "review"
        under = status == "auto" and ORDER[model_g] > ORDER[tgt]  # 자동확정 미탐(과소분류)
        review_reason = "" if status == "auto" else f"저신뢰 conf {conf:.2f} < {thresh:.2f}"

        out.append({
            "id": r["doc_id"],
            "target": tgt,
            "domain": DOM.get(r.get("domain", ""), r.get("domain", "")),
            "title": r["title"],
            "rule": rule_g,
            "rule_correct": rule_g == tgt,
            "model": model_g,
            "model_correct": model_g == tgt,
            "conf": conf,
            "agree": rule_g == model_g,
            "s_lv": s_lv, "v_lv": v_lv, "m_lv": m_lv,
            "ev_s": ev["SECRECY"], "ev_v": ev["VALUE"], "ev_m": ev["MANAGEMENT"],
            "status": status,
            "under": under,
            "review_reason": review_reason,
            "preview": preview_of(body),
        })

    # 통계 (서버에서 미리 계산해 헤더 요약에 반영)
    auto = sum(1 for o in out if o["status"] == "auto")
    review = len(out) - auto
    under = sum(1 for o in out if o["under"])
    under_ts = sum(1 for o in out if o["under"] and o["target"] in ("TS", "S1"))
    rule_acc = sum(1 for o in out if o["rule_correct"])
    model_acc = sum(1 for o in out if o["model_correct"])
    print(f"자동확정 {auto} 검수 {review} | 미탐 {under}(TS/S1 {under_ts}) | 룰정확 {rule_acc} 모델정확 {model_acc}")

    # head는 기존 golden100 보고서에서 CSS/스타일을 그대로 차용(폼 일관성). 새 파일이면 golden100을 템플릿으로.
    tmpl = HTML if HTML.exists() else Path("../doc/result/open/golden100_분류근거_보고서.html")
    head = tmpl.read_text(encoding="utf-8").split("</head>")[0] + "</head>"
    body_html = build_body(out, temp, thresh, auto, review, under, under_ts, rule_acc, model_acc, total=len(out))
    HTML.write_text(head + "\n" + body_html, encoding="utf-8")
    print(f"wrote {HTML}")
    return 0


def build_body(out, temp, thresh, auto, review, under, under_ts, rule_acc, model_acc, total=100) -> str:
    data_json = json.dumps(out, ensure_ascii=False)
    # JS는 일반 문자열(템플릿 리터럴 포함) — DATA만 주입
    js = r"""
<body>
<header class="app-header">
  <div class="header-inner">
    <div class="header-brand">
      <h1>골든셋 분류 근거 보고서</h1>
      <span class="sub">Golden v2 · 실제 분류기 출력 기반 · __TOTAL__건 · 룰엔진 + 보정 학습모델(T=__TEMP__)</span>
    </div>
    <span class="header-meta">golden100_labeled_v2 · 실엔진 재생성 · 검수율 = 런타임 게이트(conf&lt;__THRESH__)</span>
  </div>
</header>

<div class="container">
  <div class="stats-bar" id="stats-bar"></div>

  <div class="legend">
    <h3>이 보고서 읽는 법</h3>
    <div class="legend-grid" style="grid-template-columns:repeat(2,1fr)">
      <div class="legend-item"><span class="grade-lbl" style="color:#16a34a">실</span><p><b>실제 분류기 출력</b>입니다. 룰=LabelRuleEngine, 모델=보정 학습모델(bpilot_v2, T=__TEMP__)의 실제 등급·confidence. (과거 픽스처 llm열 아님)</p></div>
      <div class="legend-item"><span class="grade-lbl">검</span><p><b>검수필요</b> = 런타임 게이트가 모델 confidence &lt; __THRESH__일 때 자동으로 사람에게 올림. golden100은 경계만 모은 스트레스셋이라 실제 트래픽 검수율은 이보다 낮음.</p></div>
      <div class="legend-item"><span class="grade-lbl mark-n">⚠</span><p><b>미탐(과소분류)</b> = 모델이 정답보다 낮은 등급을 confidence로 자동확정한 경우. 검수가 못 잡는 진짜 위험 — TS/S1 미탐은 표본감사로 보완해야 함.</p></div>
      <div class="legend-item"><span class="grade-lbl">등</span><p>TS 최고기밀 · S1 영업비밀 · S2 대외비 · S3 일반공개. 등급은 비공지성(S)×경제가치(V)×비밀관리(M).</p></div>
    </div>
  </div>

  <div class="filters" id="filter-bar">
    <label>등급</label>
    <button class="filter-btn active" data-f="grade" data-v="all">전체</button>
    <button class="filter-btn" data-f="grade" data-v="TS">TS</button>
    <button class="filter-btn" data-f="grade" data-v="S1">S1</button>
    <button class="filter-btn" data-f="grade" data-v="S2">S2</button>
    <button class="filter-btn" data-f="grade" data-v="S3">S3</button>
    <div class="filter-sep"></div>
    <label>판정</label>
    <button class="filter-btn active" data-f="status" data-v="all">전체</button>
    <button class="filter-btn" data-f="status" data-v="auto">자동확정</button>
    <button class="filter-btn" data-f="status" data-v="review">검수 필요</button>
    <button class="filter-btn" data-f="status" data-v="under">⚠ 미탐</button>
    <div class="filter-sep"></div>
    <input type="text" class="search-box" id="search-box" placeholder="제목 검색...">
  </div>

  <div class="result-count" id="result-count"></div>
  <div class="records-grid" id="records-grid"></div>
</div>

<script id="raw-data" type="application/json">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('raw-data').textContent);
const REVIEW_THRESH = __THRESH__;

let activeGrade='all', activeStatus='all', searchQuery='';

function evWords(arr){ return arr&&arr.length ? arr.join(', ') : '<span class="ev-empty">—</span>'; }

function renderCard(r){
  const statusTxt = r.status==='auto' ? '자동확정' : '⚠ 검수 필요';
  const statusCls = r.status==='auto' ? '' : 'review';
  const rMark = r.rule_correct ? '<span class="mark-y">✓</span>' : '<span class="mark-n">✗</span>';
  const mMark = r.model_correct ? '<span class="mark-y">✓</span>' : '<span class="mark-n">✗</span>';
  const agreeHtml = r.agree ? '<span class="agree-txt">룰·모델 일치</span>' : '<span class="agree-txt n">룰·모델 불일치</span>';
  const prod = r.s_lv * r.v_lv * r.m_lv;
  const underBadge = r.under ? '<span class="agree-txt n" style="border-color:#dc2626;color:#dc2626;font-weight:700">⚠ 미탐(과소분류)</span>' : '';
  const reasonTxt = r.status==='review' ? `<span class="status-txt review">${r.review_reason}</span>` : '';
  return `<div class="card ${r.target.toLowerCase()}" data-grade="${r.target}" data-status="${r.status}" data-under="${r.under?1:0}" data-title="${r.title.toLowerCase()}">
  <div class="card-head">
    <div class="card-head-left">
      <div class="card-meta"><span>${r.id}</span><span class="domain">${r.domain}</span></div>
      <div class="card-title">${r.title}</div>
    </div>
    <div class="card-right">
      <span class="grade-mark">${r.target}</span>
      <span class="status-txt ${statusCls}">${statusTxt}</span>
      ${reasonTxt}
    </div>
  </div>
  <div class="divider"></div>
  <div class="clf-row">
    <span class="clf-item"><span class="clf-lbl">룰</span> &nbsp;<span class="clf-val">${r.rule}</span> ${rMark}</span>
    <span class="clf-item"><span class="clf-lbl">모델</span> &nbsp;<span class="clf-val">${r.model}</span> ${mMark} <span style="font-size:10.5px;color:#71717a">conf ${r.conf.toFixed(2)}</span></span>
    ${agreeHtml}
    ${underBadge}
    <span style="font-size:10px;color:#9ca3af;margin-left:auto">정답: <strong>${r.target}</strong></span>
  </div>
  <div class="divider"></div>
  <div class="svm-section">
    <div class="section-lbl">룰 S × V × M 점수</div>
    <div class="svm-table">
      <div class="svm-cell"><span class="sc-lbl">비공지성 S</span><span class="sc-val">${r.s_lv}</span></div>
      <div class="svm-cell"><span class="sc-lbl">경제가치 V</span><span class="sc-val">${r.v_lv}</span></div>
      <div class="svm-cell"><span class="sc-lbl">비밀관리 M</span><span class="sc-val">${r.m_lv}</span></div>
    </div>
    <div class="svm-result"><span class="svm-eq">${r.s_lv} × ${r.v_lv} × ${r.m_lv} = ${prod}</span> <span>→ 룰 등급</span> <span class="svm-grade-txt">${r.rule}</span></div>
  </div>
  <div class="divider"></div>
  <div class="evidence-section">
    <div class="section-lbl">룰 매칭 키워드</div>
    <div class="ev-row">
      <div class="ev-line"><span class="ev-factor">비공지</span><span class="ev-words">${evWords(r.ev_s)}</span></div>
      <div class="ev-line"><span class="ev-factor">가치</span><span class="ev-words">${evWords(r.ev_v)}</span></div>
      <div class="ev-line"><span class="ev-factor">비밀관리</span><span class="ev-words">${evWords(r.ev_m)}</span></div>
    </div>
  </div>
  <div class="body-section">
    <button class="body-toggle" onclick="toggleBody(this)">▸ 문서 요약</button>
    <div class="body-preview">${r.preview}</div>
  </div>
</div>`;
}

function toggleBody(btn){ const p=btn.nextElementSibling; const open=p.classList.toggle('open'); btn.textContent=open?'▾ 문서 요약 닫기':'▸ 문서 요약'; }

function renderStats(){
  const total=DATA.length;
  const byGrade={TS:0,S1:0,S2:0,S3:0};
  let autoCount=0,reviewCount=0,underCount=0;
  DATA.forEach(r=>{byGrade[r.target]=(byGrade[r.target]||0)+1; if(r.status==='auto')autoCount++; else reviewCount++; if(r.under)underCount++;});
  document.getElementById('stats-bar').innerHTML=`
    <div class="stat-card"><div class="num">${total}</div><div class="lbl">전체</div></div>
    <div class="stat-card"><div class="num">${byGrade.TS}</div><div class="lbl">TS</div></div>
    <div class="stat-card"><div class="num">${byGrade.S1}</div><div class="lbl">S1</div></div>
    <div class="stat-card"><div class="num">${byGrade.S2}</div><div class="lbl">S2</div></div>
    <div class="stat-card"><div class="num">${byGrade.S3}</div><div class="lbl">S3</div></div>
    <div class="stat-card"><div class="num">${autoCount}</div><div class="lbl">자동확정</div></div>
    <div class="stat-card"><div class="num">${reviewCount}</div><div class="lbl">검수 필요</div></div>
    <div class="stat-card" style="border-color:#fca5a5"><div class="num" style="color:#dc2626">${underCount}</div><div class="lbl">⚠ 미탐(과소)</div></div>`;
}

function applyFilters(){
  const q=searchQuery.toLowerCase();
  const filtered=DATA.filter(r=>{
    if(activeGrade!=='all'&&r.target!==activeGrade) return false;
    if(activeStatus==='under'){ if(!r.under) return false; }
    else if(activeStatus!=='all'&&r.status!==activeStatus) return false;
    if(q&&!r.title.toLowerCase().includes(q)) return false;
    return true;
  });
  document.getElementById('result-count').textContent=`${filtered.length}건 표시 중 (전체 ${DATA.length}건)`;
  const grid=document.getElementById('records-grid');
  grid.innerHTML=filtered.length===0 ? '<div class="no-results">조건에 맞는 문서가 없습니다.</div>' : filtered.map(renderCard).join('');
}

document.querySelectorAll('.filter-btn').forEach(btn=>{
  btn.addEventListener('click',function(){
    const f=this.dataset.f, v=this.dataset.v;
    document.querySelectorAll(`.filter-btn[data-f="${f}"]`).forEach(b=>b.classList.remove('active'));
    this.classList.add('active');
    if(f==='grade') activeGrade=v; else if(f==='status') activeStatus=v;
    applyFilters();
  });
});
document.getElementById('search-box').addEventListener('input',function(){ searchQuery=this.value; applyFilters(); });
renderStats(); applyFilters();
</script>
</body>
</html>
"""
    js = (js.replace("__TEMP__", f"{temp:.1f}").replace("__THRESH__", f"{thresh:.2f}")
            .replace("__TOTAL__", str(total)).replace("__DATA__", data_json))
    return js


if __name__ == "__main__":
    raise SystemExit(main())
