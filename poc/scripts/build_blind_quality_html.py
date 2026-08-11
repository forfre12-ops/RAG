"""Render the blinded quality pack as a local, editable HTML review form."""

from __future__ import annotations

import json
from pathlib import Path


QUESTIONS = (
    "문서성",
    "사실·수치·통제 일관성",
    "등급 근거 확인 가능성",
    "학습·평가 후보 가치",
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "datasets/proxy_gold/blind_quality_pilot/review_pack.v1.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    if len(rows) != 60:
        raise ValueError(f"expected 60 blinded records, got {len(rows)}")
    payload = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    out = root / "datasets/proxy_gold/blind_quality_pilot/review_form.v1.html"
    if out.exists():
        raise FileExistsError(f"refusing to overwrite: {out}")
    labels = json.dumps(QUESTIONS, ensure_ascii=False)
    page = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>블라인드 문서 품질 평가</title>
<style>
body{{margin:0;background:#f5f7fb;color:#172033;font:15px/1.65 system-ui,'Malgun Gothic',sans-serif}} main{{max-width:1100px;margin:auto;padding:24px}}
h1{{margin:0 0 6px}} .note{{color:#526075;margin:0 0 18px}} .bar{{position:sticky;top:0;background:#ffffffed;border:1px solid #dce3ef;border-radius:12px;padding:12px 16px;display:flex;gap:12px;align-items:center;z-index:2;box-shadow:0 2px 8px #22314a12}} button{{border:0;border-radius:8px;background:#1d4ed8;color:white;padding:9px 13px;font-weight:700;cursor:pointer}} button.alt{{background:#475569}} #progress{{margin-left:auto;font-weight:700}}
.card{{background:#fff;border:1px solid #dce3ef;border-radius:12px;margin:18px 0;padding:20px}} .meta{{font-weight:800;color:#1d4ed8}} .doc{{white-space:pre-wrap;background:#f8fafc;border-radius:8px;padding:15px;margin:14px 0;max-height:460px;overflow:auto;border:1px solid #eef2f7}} .scores{{display:grid;grid-template-columns:repeat(4,minmax(140px,1fr));gap:10px}} label{{font-size:13px;font-weight:700;color:#334155}} select,textarea{{width:100%;box-sizing:border-box;margin-top:4px;border:1px solid #cbd5e1;border-radius:7px;padding:8px;background:white;font:inherit}} textarea{{min-height:72px;resize:vertical}} .complete{{border-left:5px solid #16a34a}} @media(max-width:760px){{.scores{{grid-template-columns:1fr 1fr}}}}
</style></head><body><main><h1>블라인드 문서 품질 평가</h1><p class="note">출처·등급·작성방식은 숨겨져 있습니다. 각 항목을 1~5점으로 평가하고 의견을 남기세요. 입력 내용은 이 브라우저에 자동 저장됩니다.</p><div class="bar"><button id="export">평가 결과 JSON 내려받기</button><button class="alt" id="clear">현재 브라우저 입력 지우기</button><span id="progress"></span></div><section id="cards"></section></main>
<script>
const rows={payload}, questions={labels}, storeKey='blind-quality-review-v1';
let saved=JSON.parse(localStorage.getItem(storeKey)||'{{}}'); const cards=document.querySelector('#cards');
function esc(s){{const d=document.createElement('div');d.textContent=s;return d.innerHTML}}
function render(){{cards.innerHTML=rows.map(r=>{{const v=saved[r.review_id]||{{scores:{{}},comment:''}};const choices=q=>`<label>${{q}}<select data-id="${{r.review_id}}" data-q="${{q}}"><option value="">선택</option>${{[1,2,3,4,5].map(n=>`<option value="${{n}}" ${{String(v.scores[q]||'')===String(n)?'selected':''}}>${{n}}점</option>`).join('')}}</select></label>`;return `<article class="card ${{questions.every(q=>v.scores[q])?'complete':''}}"><div class="meta">${{r.review_id}} · ${{esc(r.document_type||'문서')}}</div><div class="doc">${{esc(r.text)}}</div><div class="scores">${{questions.map(choices).join('')}}</div><label>자유 의견<textarea data-comment="${{r.review_id}}" placeholder="사실 불일치, 근거 부족, 반복 문구 등">${{esc(v.comment||'')}}</textarea></label></article>`}}).join(''); bind(); progress()}}
function save(){{localStorage.setItem(storeKey,JSON.stringify(saved));render()}} function bind(){{document.querySelectorAll('select').forEach(e=>e.onchange=()=>{{const x=saved[e.dataset.id]||{{scores:{{}},comment:''}};x.scores[e.dataset.q]=e.value;saved[e.dataset.id]=x;save()}});document.querySelectorAll('textarea').forEach(e=>e.oninput=()=>{{const x=saved[e.dataset.comment]||{{scores:{{}},comment:''}};x.comment=e.value;saved[e.dataset.comment]=x;localStorage.setItem(storeKey,JSON.stringify(saved));progress()}})}}
function progress(){{const done=rows.filter(r=>questions.every(q=>saved[r.review_id]?.scores?.[q])).length;document.querySelector('#progress').textContent=`완료: ${{done}} / ${{rows.length}}`}}
document.querySelector('#export').onclick=()=>{{const result=rows.map(r=>({{review_id:r.review_id,scores:saved[r.review_id]?.scores||{{}},comment:saved[r.review_id]?.comment||''}}));const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify({{schema:'blind-quality-review-v1',completed_at:new Date().toISOString(),reviews:result}},null,2)],{{type:'application/json'}}));a.download='blind_quality_review_results.json';a.click();URL.revokeObjectURL(a.href)}};
document.querySelector('#clear').onclick=()=>{{if(confirm('이 브라우저에 저장된 모든 평가를 지울까요?')){{saved={{}};localStorage.removeItem(storeKey);render()}}}};render();
</script></body></html>"""
    out.write_text(page, encoding="utf-8")
    print(json.dumps({"out": str(out), "records": len(rows)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
