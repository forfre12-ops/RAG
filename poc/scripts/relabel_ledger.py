"""근거대장 정합 재구성 — 저장된 골든셋 본문을 재라벨(LLM S/V/M 포착)해
'S×V×M=곱→등급'이 내부 정합하는 객관 판단근거 대장 생성.

기존 golden50.py 대장의 결함(곱≠등급, rationale↔target 불일치)을 교정:
  - LLM의 비공지성/경제유용성/비밀관리성 수치를 포착 → 곱이 LLM 등급을 재구성
  - 목표(gold)와 일치하면 '자동확정후보', 불일치면 '전문가 검수필요'로 명시
  - 룰 매치 키워드는 보조 근거로 첨부

실행(재생성 X, 재라벨만 ~5분):
  cd poc
  LLM_PROVIDER=local_openai LOCAL_LLM_BASE_URL=http://localhost:11434/v1 \
    LOCAL_LLM_MODEL=qwen3:14b LOCAL_LLM_API_KEY=ollama PYTHONPATH=src \
    .venv/Scripts/python.exe scripts/relabel_ledger.py
"""

from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler  # noqa: E402
from lloydk.modules.m3_labeling.pipeline import LabelingPipeline  # noqa: E402
from lloydk.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402
from lloydk.modules.m3_labeling.seeds import to_canonical_factor  # noqa: E402

GRADE_NAME = {"TS": "특급기밀", "S1": "1급비밀", "S2": "2급대외비", "S3": "3급공개"}
FACTOR_KO = {"SECRECY": "비공지성(S)", "VALUE": "경제유용성(V)", "MANAGEMENT": "비밀관리성(M)"}

gd = Path(__file__).resolve().parents[1] / "datasets" / "gold"
rows = [json.loads(l) for l in (gd / "golden50.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
rule = LabelingPipeline()
llm = LLMLabeler()
print(f"재라벨 대상 {len(rows)}건", flush=True)


def rule_keywords(text):
    res = rule.label(text).rule_result
    out = {"SECRECY": [], "VALUE": [], "MANAGEMENT": []}
    if res:
        for m in res.matched_keywords:
            fac = to_canonical_factor(m.factor)
            if fac in out and m.keyword not in out[fac]:
                out[fac].append(m.keyword)
    return {k: v[:6] for k, v in out.items()}


md = ["# 골든셋 판단근거 대장 (정합 재구성판)", "",
      "정본 가이드 **등급 = 비공지성 S × 경제유용성 V × 비밀관리성 M** (각 0·1·2)",
      "곱 매핑: 8→특급(TS) / 4→1급(S1) / 1·2→2급(S2) / 0→3급공개(S3)", "",
      "- **S/V/M은 독립 판독(LLM)이 매긴 값** → 곱이 LLM 등급을 재구성(내부 정합).",
      "- 목표(생성 의도)와 일치 시 *자동확정후보*, 불일치 시 *전문가 검수필요*.", ""]

t0 = time.time()
auto_ok = review = 0
out_rows = []
for i, r in enumerate(rows):
    body = r.get("body") or r.get("text", "")
    target = r["target"]
    try:
        lr = llm.label(body)
        fs = lr.factor_scores
        s = int(round(fs.get("secrecy", 0))); v = int(round(fs.get("value", 0))); m = int(round(fs.get("management", 0)))
        prod = s * v * m
        derived = grade_from_svm(s, v, m)
        kws = rule_keywords(body)
    except Exception as exc:  # noqa: BLE001
        print(f"  [{r['doc_id']}] ERROR {exc}", flush=True); continue

    consistent = derived == lr.grade        # LLM S/V/M이 LLM 등급을 재구성하는가(정합)
    matches_gold = derived == target        # 목표 등급과 일치하는가
    rule_g = r.get("rule")
    status = ("✅ 자동확정후보" if (matches_gold and rule_g == target)
              else "⚠ 전문가 검수필요")
    auto_ok += int(status.startswith("✅")); review += int(status.startswith("⚠"))

    md.append(f"\n## {r['doc_id']} · {GRADE_NAME[target]}({target}) · {r['domain']}")
    md.append(f"**제목**: {r['title']}  (본문 {len(body)}자)")
    md.append("\n**등급 근거 (독립 판독 S×V×M)**:")
    md.append(f"- 비공지성 S={s} · 경제유용성 V={v} · 비밀관리성 M={m}")
    md.append(f"- → {s}×{v}×{m} = **{prod} → {derived}({GRADE_NAME.get(derived,derived)})**"
              + ("  ✓ 곱↔등급 정합" if consistent else f"  (LLM최종={lr.grade})"))
    md.append(f"- 판독 사유: {lr.rationale or '(없음)'}")
    anyk = any(kws.values())
    if anyk:
        md.append("**룰 매치 키워드(보조)**: " + " / ".join(
            f"{FACTOR_KO[f]}: {', '.join(ks)}" for f, ks in kws.items() if ks))
    md.append("**가이드 조항**: 정본 가이드 §등급=S×V×M (p.11 요소기준 · p.12 산정표)")
    md.append(f"**목표={target} / 룰={rule_g} / LLM판독={derived} → {status}**")
    out_rows.append({**r, "llm_s": s, "llm_v": v, "llm_m": m, "llm_prod": prod, "llm_derived": derived,
                     "review_status": "auto" if status.startswith("✅") else "review"})
    print(f"  [{r['doc_id']}] gold={target} LLM_svm={s}{v}{m}={prod}→{derived} rule={rule_g} {status}", flush=True)

md.insert(6, f"- 집계: 자동확정후보 {auto_ok}건 / 전문가 검수필요 {review}건 (총 {len(out_rows)})")
(gd / "golden50_evidence_v2.md").write_text("\n".join(md), encoding="utf-8")
(gd / "golden50_labeled_v2.jsonl").write_text(
    "\n".join(json.dumps(x, ensure_ascii=False) for x in out_rows), encoding="utf-8")
print("=" * 60, flush=True)
print(f"총 {len(out_rows)}건 ({time.time()-t0:.0f}s) — 자동확정후보 {auto_ok} / 검수필요 {review}", flush=True)
print(f"[저장] {gd/'golden50_evidence_v2.md'} + {gd/'golden50_labeled_v2.jsonl'}", flush=True)
