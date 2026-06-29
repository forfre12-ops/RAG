# -*- coding: utf-8 -*-
"""합성 골든셋 빌드 러너 — 생성(SyntheticDocGenerator) → 합의게이트(build_golden_set).

파일럿(scratchpad)을 정식화한 CLI. 등급별 도메인 층화 + 과생성 + run-스코프 출력
(정본 classification_gold.jsonl 직접변경 금지 — golden_runs/<run_id>/ 에만 기록).

기본은 로컬 Ollama(생성=qwen3:14b / 심판=gemma3:12b, 생성기와 독립 모델).
합성 빌드라 require_evidence=False가 기본(룰 시드 부재 no_evidence 탈락 완화·레버3).
운영 게이트(require_evidence=True)와 구분 — 본 러너 산출은 silver(학습 후보)일 뿐
평가정답(locked_gold_eval)이 아니다(사람 서명이 진실).

파일럿 실측(8건): 통과율 합성모드 ~37.5%, 고등급(TS/S1) 수율 구조적으로 낮음
→ 고등급은 앵커 권장. 출력은 콘솔 인코딩 회피 위해 stats.json + ascii 요약.

사용:
  python build_synthetic_golden.py --per-grade 5 --grades TS,S1,S2,S3
  python build_synthetic_golden.py --smoke            # 등급별 1건(빠른 검증)
"""
from __future__ import annotations

import argparse
import io
import json
import sys
import time
import uuid
from pathlib import Path

# 등급별 도메인 — 다양성 위해 등급 내에서 순환(고등급은 전용 특화 도메인).
GRADE_DOMAINS = {
    "TS": ["ma", "security", "defense", "semiconductor", "bio", "ai"],
    "S1": ["finance", "tech", "business", "hr"],
    "S2": ["business", "finance", "legal", "mixed"],
    "S3": ["public"],  # 명확한 공개 도메인 — gemma 과분류 완화(A2). 내부문서형 mixed/business 제거
}


def _provider(base_url: str, model: str, label: str):
    from lloydk.adapters.llm.local_openai_provider import LocalOpenAIProvider
    return LocalOpenAIProvider(base_url=base_url, api_key="ollama", model=model,
                               enable_thinking=False, provider_label=label)


def generate_docs(gen_model, base_url, grades, per_grade, len_min, len_max, log):
    """등급별 per_grade건 생성(도메인 순환). (docs, intended_by_id, gen_fail, pii_hits) 반환."""
    from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator
    gen = SyntheticDocGenerator(llm=_provider(base_url, gen_model, "ollama-gen"))
    docs, intended_by_id, gen_fail, pii_hits = [], {}, 0, 0
    for g in grades:
        domains = GRADE_DOMAINS.get(g, ["mixed"])
        for i in range(per_grade):
            dom = domains[i % len(domains)]
            d = gen.generate_one(SynthRequest(target_grade=g, domain=dom, count=1,
                                              len_min=len_min, len_max=len_max))
            if d.parse_error or not d.body or len(d.body.strip()) < 50:
                gen_fail += 1
                continue
            if d.pii_violations:
                pii_hits += 1  # 생성기가 PII 넣으면 카운트(드물지만 감사용)
            doc_id = f"syn-{g}-{dom}-{uuid.uuid4().hex[:6]}"
            docs.append({"doc_id": doc_id, "text": d.body, "domain": dom,
                         "source": "synthetic", "intended": g, "title": d.title})
            intended_by_id[doc_id] = g
        log(f"  생성 {g}: 누적 {len([x for x in docs if x['intended']==g])}건")
    return docs, intended_by_id, gen_fail, pii_hits


def build(docs, judge_model, base_url, require_evidence, k_min, k_max, shadow_model, temperature):
    from lloydk.golden_builder import build_golden_set, make_label_fn
    from lloydk.modules.m3_labeling.judge import ConsensusJudge
    from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler
    primary = LLMLabeler(provider=_provider(base_url, judge_model, "ollama-judge"))
    shadow = (LLMLabeler(provider=_provider(base_url, shadow_model, "ollama-shadow"))
              if shadow_model else None)
    judge = ConsensusJudge(primary=primary, shadow=shadow, airgap=False,
                           k_min=k_min, k_max=k_max, temperature=temperature)
    label_fn = make_label_fn(judge=judge)
    return build_golden_set(docs, label_fn=label_fn, require_evidence=require_evidence)


def write_outputs(out_root, run_id, docs, result, meta):
    run_dir = Path(out_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl(name, rows):
        with (run_dir / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    _jsonl("candidates.jsonl", docs)                          # 생성 원본(감사·재사용)
    _jsonl(f"build_{run_id}.jsonl", [r.to_dict() for r in result.gold])      # gold_candidate
    _jsonl(f"uncertain_{run_id}.jsonl", [r.to_dict() for r in result.uncertain])  # 검수대상
    with (run_dir / "stats.json").open("w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return run_dir


def main(argv=None):
    ap = argparse.ArgumentParser(description="합성 골든셋 빌드 러너(생성→합의게이트, run-스코프)")
    ap.add_argument("--per-grade", type=int, default=5, help="등급당 생성 건수(도메인 순환)")
    ap.add_argument("--grades", default="TS,S1,S2,S3")
    ap.add_argument("--gen-model", default="qwen3:14b", help="생성 모델(Ollama)")
    ap.add_argument("--judge-model", default="gemma3:12b", help="심판 모델(생성기와 독립)")
    ap.add_argument("--shadow-model", default=None, help="섀도 모델(production_suspect 교차검증; 없으면 생략)")
    ap.add_argument("--base-url", default="http://localhost:11434/v1")
    ap.add_argument("--out-dir", default="poc/datasets/golden_runs")
    ap.add_argument("--require-evidence", action="store_true",
                    help="운영 게이트(룰 시드 근거 요구). 기본=합성모드(False)")
    ap.add_argument("--k-min", type=int, default=2)
    ap.add_argument("--k-max", type=int, default=3)
    ap.add_argument("--len-min", type=int, default=500)
    ap.add_argument("--len-max", type=int, default=1200)
    ap.add_argument("--temperature", type=float, default=0.6)
    ap.add_argument("--smoke", action="store_true", help="등급별 1건 빠른 검증")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    grades = [g.strip() for g in args.grades.split(",") if g.strip()]
    per_grade = 1 if args.smoke else args.per_grade
    require_evidence = bool(args.require_evidence)
    run_id = uuid.uuid4().hex[:12]

    def log(msg):
        # ascii-safe 진행 로그(cp949 콘솔에서 한글 깨질 수 있어 stderr+flush; 핵심 수치는 stats.json)
        print(msg, file=sys.stderr, flush=True)

    log(f"[run {run_id}] grades={grades} per_grade={per_grade} "
        f"gen={args.gen_model} judge={args.judge_model} require_evidence={require_evidence}")

    t0 = time.time()
    docs, intended_by_id, gen_fail, pii_hits = generate_docs(
        args.gen_model, args.base_url, grades, per_grade, args.len_min, args.len_max, log)
    gen_s = round(time.time() - t0, 1)
    log(f"[gen] {len(docs)}건 생성(실패 {gen_fail}, pii {pii_hits}) {gen_s}s")

    if not docs:
        log("[abort] 생성 0건 — Ollama 가동/모델 확인")
        return 2

    t1 = time.time()
    result = build(docs, args.judge_model, args.base_url, require_evidence,
                   args.k_min, args.k_max, args.shadow_model, args.temperature)
    build_s = round(time.time() - t1, 1)

    # 등급별 gold 수율(intended 기준).
    gold_by_intended = {}
    for r in result.gold:
        g = intended_by_id.get(r.doc_id, "?")
        gold_by_intended[g] = gold_by_intended.get(g, 0) + 1

    meta = {
        "run_id": run_id, "grades": grades, "per_grade": per_grade,
        "gen_model": args.gen_model, "judge_model": args.judge_model,
        "shadow_model": args.shadow_model, "require_evidence": require_evidence,
        "k_min": args.k_min, "k_max": args.k_max, "temperature": args.temperature,
        "generated": len(docs), "gen_fail": gen_fail, "pii_hits": pii_hits,
        "gen_seconds": gen_s, "build_seconds": build_s,
        "gold_candidate": len(result.gold), "needs_review": len(result.uncertain),
        "pass_rate": round(len(result.gold) / len(docs), 3) if docs else 0,
        "gold_by_intended_grade": gold_by_intended,
        "high_grade_gold": gold_by_intended.get("TS", 0) + gold_by_intended.get("S1", 0),
        "stats": result.stats,
    }
    run_dir = write_outputs(args.out_dir, run_id, docs, result, meta)
    log(f"[build] gold={len(result.gold)} review={len(result.uncertain)} "
        f"pass={meta['pass_rate']} high_grade_gold={meta['high_grade_gold']} {build_s}s")
    log(f"[done] -> {run_dir} (stats.json)")
    # stdout엔 run_dir만(파이프라인 친화). 상세는 stats.json.
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
