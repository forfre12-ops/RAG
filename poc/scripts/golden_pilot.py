"""골든셋 본셋 파일럿 — M1 합성 → B안 룰 + LLM 이중 라벨 → 합의/정확도/FNR 측정.

목적: 가이드만으로 골든셋을 자력 구축할 수 있는지, 라벨 품질(특히 과소분류/미탐)을 검증.
핵심 관점: **gold = 합성 target 등급**(우리가 그 등급으로 생성). 라벨러(룰/LLM)는 gold 대비 *평가* 대상.

실행(로컬 Ollama Qwen3):
  cd poc
  PILOT_N=3 LLM_PROVIDER=local_openai LOCAL_LLM_BASE_URL=http://localhost:11434/v1 \
    LOCAL_LLM_MODEL=qwen3:14b LOCAL_LLM_API_KEY=ollama PYTHONPATH=src \
    .venv/Scripts/python.exe scripts/golden_pilot.py
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

from koipa.modules.m1_synthesis.generator import SyntheticDocGenerator, SynthRequest  # noqa: E402
from koipa.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: E402
from koipa.modules.m3_labeling.pipeline import LabelingPipeline  # noqa: E402

N = int(os.environ.get("PILOT_N", "3"))
PLAN = [("S1", "finance"), ("S2", "business"), ("S3", "mixed"), ("TS", "semiconductor")]
ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}

gen = SyntheticDocGenerator()
rule = LabelingPipeline()
llm = LLMLabeler()
print(f"provider={gen.llm.name}  PILOT_N={N}  (등급별 {N}건, 총 {N*len(PLAN)}건)")

rows = []
t0 = time.time()
for grade, domain in PLAN:
    for i in range(N):
        try:
            doc = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1, len_min=500, len_max=1200))[0]
            r = rule.label(doc.body)
            rg = r.grade.value if hasattr(r.grade, "value") else str(r.grade)
            lg = llm.label(doc.body).grade
        except Exception as exc:  # noqa: BLE001
            print(f"  [{grade}/{domain} #{i}] ERROR {exc}")
            continue
        agree = rg == lg
        gold_match_rule = rg == grade
        rows.append({
            "target": grade, "domain": domain, "rule": rg, "llm": lg,
            "agree": agree, "rule_correct": gold_match_rule, "llm_correct": lg == grade,
            "svm": r.rule_result.svm if r.rule_result else None,
            "title": doc.title, "len": len(doc.body), "body": doc.body,
        })
        print(f"  [{grade}/{domain} #{i}] gold={grade} rule={rg} llm={lg} "
              f"{'AGREE' if agree else 'DIFF'} svm={r.rule_result.svm if r.rule_result else '-'} | {doc.title[:28]}")

tot = len(rows)
if tot:
    agree = sum(x["agree"] for x in rows)
    rule_acc = sum(x["rule_correct"] for x in rows)
    llm_acc = sum(x["llm_correct"] for x in rows)
    high = [x for x in rows if x["target"] in ("TS", "S1")]
    rule_fnr = sum(ORDER[x["rule"]] > ORDER[x["target"]] for x in high)
    llm_fnr = sum(ORDER[x["llm"]] > ORDER[x["target"]] for x in high)
    print("=" * 64)
    print(f"총 {tot}건 ({time.time()-t0:.0f}s)")
    print(f"이중라벨 일치율    : {agree}/{tot} = {agree/tot:.0%}  (불일치만 검수 → 검수부담 {tot-agree}건)")
    print(f"룰(B안) vs gold    : {rule_acc}/{tot} = {rule_acc/tot:.0%}")
    print(f"LLM vs gold        : {llm_acc}/{tot} = {llm_acc/tot:.0%}")
    print(f"고등급(TS·S1) {len(high)}건 — 미탐(하향): 룰 {rule_fnr} / LLM {llm_fnr}")
    out = Path(__file__).resolve().parents[1] / "datasets" / "gold" / "pilot_candidates.jsonl"
    out.write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in rows), encoding="utf-8")
    print(f"후보 저장: {out}")
else:
    print("생성된 문서 없음")
