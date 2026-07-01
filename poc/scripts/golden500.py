"""골든셋 본셋 500건 — 회사 내부형 4등급×다도메인. 무인 야간 실행용.

golden50 + relabel_ledger 일체형: 생성→룰+LLM(S/V/M 포착)→정합 판단근거 대장.
**증분 저장**(각 문서 완료 시 jsonl append) → 중단돼도 부분 보존. 종료 시 대장·검수시트 빌드.

산출:
  datasets/gold/golden500.jsonl          (전체, 증분)
  datasets/gold/golden500_evidence.md    (정합 판단근거 대장, 곱↔등급 일치)
  datasets/gold/golden500_review_sheet.csv (검수필요 — 전문가 확정란)

실행(로컬 Ollama Qwen3):
  cd poc
  PER_GRADE=125 LLM_PROVIDER=local_openai LOCAL_LLM_BASE_URL=http://localhost:11434/v1 \
    LOCAL_LLM_MODEL=qwen3:14b LOCAL_LLM_API_KEY=ollama PYTHONPATH=src \
    .venv/Scripts/python.exe scripts/golden500.py
"""

from __future__ import annotations

import csv
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
from lloydk.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade  # noqa: E402
from lloydk.modules.m3_labeling.seeds import to_canonical_factor  # noqa: E402

PER_GRADE = int(os.environ.get("PER_GRADE", "125"))
ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}
GRADE_NAME = {"TS": "특급기밀", "S1": "1급비밀", "S2": "2급대외비", "S3": "3급공개"}
FACTOR_KO = {"SECRECY": "비공지성(S)", "VALUE": "경제유용성(V)", "MANAGEMENT": "비밀관리성(M)"}
GRADE_DOMAIN_POOL = {
    "TS": ["semiconductor", "defense", "ma", "security", "ai", "bio"],
    "S1": ["finance", "tech", "ai", "bio", "hr"],
    "S2": ["business", "ma", "finance", "legal", "mixed"],
    "S3": ["mixed", "business", "finance", "tech"],
}

gd = Path(__file__).resolve().parents[1] / "datasets" / "gold"
gd.mkdir(parents=True, exist_ok=True)
JSONL = gd / "golden500.jsonl"

gen = SyntheticDocGenerator()
rule = LabelingPipeline()
llm = LLMLabeler()

# 재개(resume): 기존 jsonl이 있으면 이미 만든 doc_id 스킵.
done_ids = set()
if JSONL.exists():
    for line in JSONL.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                done_ids.add(json.loads(line)["doc_id"])
            except Exception:  # noqa: BLE001
                pass
print(f"provider={gen.llm.name}  PER_GRADE={PER_GRADE}  목표 {PER_GRADE*4}건  (이미 완료 {len(done_ids)}건 스킵)", flush=True)


def rule_keywords(matched):
    out = {"SECRECY": [], "VALUE": [], "MANAGEMENT": []}
    for m in matched:
        fac = to_canonical_factor(m.factor)
        if fac in out and m.keyword not in out[fac]:
            out[fac].append(m.keyword)
    return {k: v[:6] for k, v in out.items()}


t0 = time.time()
made = 0
with open(JSONL, "a", encoding="utf-8") as fout:
    for grade, pool in GRADE_DOMAIN_POOL.items():
        for i in range(PER_GRADE):
            doc_id = f"G500-{grade}-{i:03d}"
            if doc_id in done_ids:
                continue
            domain = pool[i % len(pool)]
            try:
                doc = gen.generate(SynthRequest(target_grade=grade, domain=domain, count=1, len_min=600, len_max=1300))[0]
                res = rule.label(doc.body)
                rg = res.grade.value if hasattr(res.grade, "value") else str(res.grade)
                rr = res.rule_result
                ev = rule_keywords(rr.matched_keywords) if rr else {}
                lr = llm.label(doc.body)
                fs = lr.factor_scores
                ls = int(round(fs.get("secrecy", 0))); lv = int(round(fs.get("value", 0))); lm = int(round(fs.get("management", 0)))
                lprod = ls * lv * lm
                lderived = grade_from_svm(ls, lv, lm)
            except Exception as exc:  # noqa: BLE001
                print(f"  [{doc_id}] ERROR {exc}", flush=True)
                continue
            rec = {
                "doc_id": doc_id, "target": grade, "domain": domain, "title": doc.title,
                "len": len(doc.body), "body": doc.body,
                "rule": rg, "llm": lr.grade, "llm_derived": lderived,
                "llm_s": ls, "llm_v": lv, "llm_m": lm, "llm_prod": lprod,
                "llm_rationale": lr.rationale, "evidence": ev,
                "agree": rg == lderived, "rule_correct": rg == grade,
                "review_status": "auto" if (lderived == grade and rg == grade) else "review",
            }
            fout.write(json.dumps(rec, ensure_ascii=False) + "\n"); fout.flush()
            made += 1
            if made % 10 == 0 or made <= 3:
                el = time.time() - t0
                print(f"  [{doc_id}] gold={grade} rule={rg} llm={lderived} {rec['review_status']} "
                      f"| {made}건 / {el:.0f}s ({el/made:.0f}s·건)", flush=True)

# ── 종료: 정합 대장 + 검수시트 빌드 (전체 jsonl에서) ──
rows = [json.loads(l) for l in JSONL.read_text(encoding="utf-8").splitlines() if l.strip()]
auto = sum(1 for r in rows if r["review_status"] == "auto")
md = ["# 골든셋 500건 — 판단근거 대장 (정합)", "",
      "정본 가이드 등급 = 비공지성 S × 경제유용성 V × 비밀관리성 M (각 0·1·2).",
      "곱 매핑: 8→특급 / 4→1급 / 1·2→2급 / 0→3급공개. (S/V/M은 독립 판독값 → 곱↔등급 정합)",
      f"- 집계: 총 {len(rows)} · 자동확정후보 {auto} · 전문가 검수필요 {len(rows)-auto}", ""]
for r in rows:
    md.append(f"\n## {r['doc_id']} · {GRADE_NAME[r['target']]}({r['target']}) · {r['domain']}")
    md.append(f"**제목**: {r['title']}  (본문 {r['len']}자)")
    md.append(f"**등급 근거**: S={r['llm_s']} × V={r['llm_v']} × M={r['llm_m']} = **{r['llm_prod']} → {r['llm_derived']}**")
    kws = r.get("evidence") or {}
    if any(kws.values()):
        md.append("**룰 매치 키워드(보조)**: " + " / ".join(f"{FACTOR_KO[f]}: {', '.join(k)}" for f, k in kws.items() if k))
    md.append(f"**판독 사유**: {r.get('llm_rationale') or '(없음)'}")
    status = "✅ 자동확정후보" if r["review_status"] == "auto" else f"⚠ 검수필요(룰={r['rule']}/LLM={r['llm_derived']}/목표={r['target']})"
    md.append(f"**검수**: {status}")
(gd / "golden500_evidence.md").write_text("\n".join(md), encoding="utf-8")

with open(gd / "golden500_review_sheet.csv", "w", encoding="utf-8-sig", newline="") as f:
    w = csv.writer(f)
    w.writerow(["문서ID", "도메인", "제목", "제안등급(목표)", "룰", "LLM", "S", "V", "M", "곱", "근거요약", "검수자_확정등급", "검수자_사유", "검수자명"])
    for r in rows:
        if r["review_status"] == "review":
            w.writerow([r["doc_id"], r["domain"], r["title"][:50], f"{r['target']}({GRADE_NAME[r['target']]})",
                        r["rule"], r["llm_derived"], r["llm_s"], r["llm_v"], r["llm_m"], r["llm_prod"],
                        (r.get("llm_rationale") or "")[:90], "", "", ""])

print("=" * 64, flush=True)
print(f"완료: 총 {len(rows)}건 ({time.time()-t0:.0f}s) · 자동확정 {auto} · 검수필요 {len(rows)-auto}", flush=True)
print(f"[저장] {JSONL} + golden500_evidence.md + golden500_review_sheet.csv", flush=True)
