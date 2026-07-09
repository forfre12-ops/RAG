"""골든셋 50건 파일럿 — 회사 내부형 문서(법률코퍼스 배제) × 4등급 × 다도메인.

산출:
  1) datasets/gold/golden50.jsonl       — 전체 레코드(본문+라벨+근거)
  2) datasets/gold/golden50_evidence.md — 판단근거 대장(③): 문서별 S/V/M·매치문구·가이드조항
  + 요약(이중라벨 일치율·룰/LLM 정확도·고등급 FNR)

목적(④): 각 등급이 *정본 가이드 기준*에 어떤 문구로 매핑됐는지 추적 가능 → 고객 객관 검증.

실행(로컬 Ollama Qwen3):
  cd poc
  PER_GRADE=13 LLM_PROVIDER=local_openai LOCAL_LLM_BASE_URL=http://localhost:11434/v1 \
    LOCAL_LLM_MODEL=qwen3:14b LOCAL_LLM_API_KEY=ollama PYTHONPATH=src \
    .venv/Scripts/python.exe scripts/golden50.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lloydk.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest  # noqa: E402
from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: E402
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline  # noqa: E402
from lloydk.modules.m3_labeling.seeds import to_canonical_factor  # noqa: E402

PER_GRADE = int(os.environ.get("PER_GRADE", "13"))
ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
GRADE_NAME = {"TS": "특급기밀", "S1": "1급비밀", "S2": "2급대외비", "S3": "3급공개"}
FACTOR_KO = {"SECRECY": "비공지성(S)", "VALUE": "경제유용성(V)", "MANAGEMENT": "비밀관리성(M)"}

# 회사 내부형 문서 도메인 풀(법률 판례·심결 제외). 등급별로 현실적 도메인 배분.
GRADE_DOMAIN_POOL = {
    "TS": ["semiconductor", "defense", "ma", "security", "ai", "bio"],
    "S1": ["finance", "tech", "ai", "bio", "hr"],
    "S2": ["business", "ma", "finance", "legal", "mixed"],
    "S3": ["mixed", "business", "finance", "tech"],
}

gen = SyntheticDocGenerator()
rule = LabelingPipeline()
llm = LLMLabeler()
print(f"provider={gen.llm.name}  PER_GRADE={PER_GRADE}  총 {PER_GRADE*4}건 목표", flush=True)


def evidence_by_factor(matched) -> dict[str, list[str]]:
    """매치 키워드를 정본 3요건(S/V/M)으로 그룹핑 → 근거 문구 리스트."""
    out: dict[str, list[str]] = {"SECRECY": [], "VALUE": [], "MANAGEMENT": []}
    for m in matched:
        fac = to_canonical_factor(m.factor)
        if fac in out and m.keyword not in out[fac]:
            out[fac].append(m.keyword)
    return {k: v[:6] for k, v in out.items()}  # 요소별 최대 6개


rows = []
t0 = time.time()
for grade, pool in GRADE_DOMAIN_POOL.items():
    for i in range(PER_GRADE):
        domain = pool[i % len(pool)]
        try:
            doc = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1, len_min=600, len_max=1300))[0]
            res = rule.label(doc.body)
            rg = res.grade.value if hasattr(res.grade, "value") else str(res.grade)
            rr = res.rule_result
            svm = rr.svm if rr else None
            fs = rr.factor_scores if rr else {}
            s_lv, v_lv, m_lv = int(fs.get("SECRECY", 0)), int(fs.get("VALUE", 0)), int(fs.get("MANAGEMENT", 0))
            ev = evidence_by_factor(rr.matched_keywords) if rr else {}
            llm_res = llm.label(doc.body)
            lg = llm_res.grade
        except Exception as exc:  # noqa: BLE001
            print(f"  [{grade}/{domain} #{i}] ERROR {exc}", flush=True)
            continue
        rows.append({
            "doc_id": f"G50-{grade}-{i:02d}", "target": grade, "domain": domain,
            "title": doc.title, "len": len(doc.body), "body": doc.body,
            "rule": rg, "llm": lg, "agree": rg == lg,
            "rule_correct": rg == grade, "llm_correct": lg == grade,
            "svm": svm, "s_lv": s_lv, "v_lv": v_lv, "m_lv": m_lv,
            "evidence": ev, "llm_rationale": llm_res.rationale,
        })
        print(f"  [{grade}/{domain} #{i}] gold={grade} rule={rg} llm={lg} "
              f"{'AGREE' if rg == lg else 'DIFF'} S{s_lv}V{v_lv}M{m_lv}={svm} | {doc.title[:26]}", flush=True)

tot = len(rows)
gd = Path(__file__).resolve().parents[1] / "datasets" / "gold"
gd.mkdir(parents=True, exist_ok=True)
if not tot:
    print("생성된 문서 없음", flush=True); sys.exit(1)

# 1) 전체 레코드
(gd / "golden50.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")

# 2) 판단근거 대장 (③)
md = ["# 골든셋 50건 — 판단근거 대장", "",
      "각 문서가 **정본 가이드 B안(등급 = 비공지성 S × 경제유용성 V × 비밀관리성 M)** 의",
      "어떤 기준·문구로 해당 등급에 매핑됐는지 추적. (S/V/M ∈ {0,1,2}, 곱=등급)", "",
      "- 곱 매핑: 8→특급 / 4→1급 / 1·2→2급 / 0→3급공개", ""]
for x in rows:
    md.append(f"\n## {x['doc_id']} · {GRADE_NAME[x['target']]}({x['target']}) · {x['domain']}")
    md.append(f"**제목**: {x['title']}  (본문 {x['len']}자)")
    md.append(f"**등급 산정**: 비공지성 S={x['s_lv']} × 경제유용성 V={x['v_lv']} × 비밀관리성 M={x['m_lv']} "
              f"= **{x['svm']} → {x['target']}({GRADE_NAME[x['target']]})**")
    md.append("\n**매치 근거(문구→요건)**:")
    any_ev = False
    for fac, kws in x["evidence"].items():
        if kws:
            any_ev = True
            md.append(f"- {FACTOR_KO[fac]}: {', '.join(kws)}")
    if not any_ev:
        md.append("- (룰 키워드 매치 없음 — 의미기반/LLM 판단 의존)")
    md.append(f"\n**LLM 판단근거**: {x['llm_rationale'] or '(없음)'}")
    md.append("**가이드 조항**: 정본 가이드 §등급=S×V×M (p.12 산정표)")
    chk = "✅ 일치" if x["rule_correct"] else f"⚠ 룰={x['rule']} (검수대상)"
    md.append(f"**검증**: 목표={x['target']} / 룰={x['rule']} / LLM={x['llm']} → {chk}")
(gd / "golden50_evidence.md").write_text("\n".join(md), encoding="utf-8")

# 요약
agree = sum(x["agree"] for x in rows)
racc = sum(x["rule_correct"] for x in rows)
lacc = sum(x["llm_correct"] for x in rows)
high = [x for x in rows if x["target"] in ("TS", "S1")]
rfnr = sum(ORDER[x["rule"]] > ORDER[x["target"]] for x in high)
lfnr = sum(ORDER[x["llm"]] > ORDER[x["target"]] for x in high)
print("=" * 66, flush=True)
print(f"총 {tot}건 ({time.time()-t0:.0f}s)", flush=True)
print(f"이중라벨 일치율 : {agree}/{tot} = {agree/tot:.0%}  (불일치 {tot-agree}건만 검수)", flush=True)
print(f"룰(B안) vs gold : {racc}/{tot} = {racc/tot:.0%}", flush=True)
print(f"LLM vs gold     : {lacc}/{tot} = {lacc/tot:.0%}", flush=True)
print(f"고등급(TS·S1) {len(high)}건 — 미탐: 룰 {rfnr} / LLM {lfnr}", flush=True)
print(f"[저장] {gd/'golden50.jsonl'}  +  {gd/'golden50_evidence.md'}", flush=True)
