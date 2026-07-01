# -*- coding: utf-8 -*-
"""합성 골든셋 빌드 러너 — 생성(SyntheticDocGenerator) → 합의게이트(build_golden_set).

파일럿(scratchpad)을 정식화한 CLI. 등급별 도메인 층화 + 과생성 + run-스코프 출력
(정본 classification_gold.jsonl 직접변경 금지 — golden_runs/<run_id>/ 에만 기록).

기본은 로컬 Ollama(생성=qwen3:14b / 심판=gemma3:12b, 생성기와 독립 모델).
[B-1] 섀도 심판(production_suspect 교차검증)도 기본 ON=Qwen(qwen3:14b, 심판 gemma와 독립).
심판이 놓칠 배포 실패 위험을 섀도 불일치로 review에 라우팅한다(옛 기본 None=안전장치 no-op). --no-shadow로 off.
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

# 도메인×등급 교차 — 각 도메인이 모든 등급에 걸쳐 생성되게 공유 풀(도메인→등급 shortcut 차단).
# 본 생성 714 다각감사에서 등급-잠금 도메인풀(TS=기술도메인·S3=public 단독)이 도메인→등급 누출
# (trivial baseline 85.6%·Theil U 0.808)을 만들어 KF-DeBERTa가 민감도 대신 도메인토픽을 학습할
# 위험으로 단독학습 보류 판정 → 교차 재설계. spannable(한 도메인이 등급별 다른 문서를 낼 수 있는)
# 일반 도메인만 사용: finance는 S3 실적공시~TS M&A, tech는 S3 제품발표~TS 핵심알고리즘까지.
# 등급은 생성기 situation 프롬프트가 통제(도메인 무관). 등급-잠금 특화/public 도메인은 배제.
SPAN_DOMAINS = ["finance", "tech", "business", "legal", "hr", "mixed"]
GRADE_DOMAINS = {g: SPAN_DOMAINS for g in ("TS", "S1", "S2", "S3")}


def _provider(base_url: str, model: str, label: str):
    from lloydk.adapters.llm.local_openai_provider import LocalOpenAIProvider
    return LocalOpenAIProvider(base_url=base_url, api_key="ollama", model=model,
                               enable_thinking=False, provider_label=label)


def _resolve_shadow_model(shadow_model, judge_model, log):
    """[B-1] 섀도 심판 모델 확정 — production_suspect(배포 실패 예측) 교차검증용.

    섀도는 주 심판과 **독립**이어야 신호가 있다. 심판과 동일하면 자기합의라
    shadow_grade==llm_grade가 강제돼 production_suspect가 절대 발화하지 못한다(무의미) →
    비활성(경고). 빈 값이면 섀도 없음. 기본은 온프렘 Qwen으로 켜 둔다(제품
    judge_shadow_provider='vllm' 규약과 정합) — 옛 스크립트 기본 None은 안전장치가
    no-op이었다(섀도 없으면 production_suspect 상시 False).
    """
    if not shadow_model:
        return None
    if shadow_model == judge_model:
        log(f"  [shadow] 섀도({shadow_model})가 심판과 동일 — 자기합의라 "
            f"production_suspect 신호 0 → 비활성")
        return None
    return shadow_model


def generate_docs(gen_model, base_url, grades, per_grade, len_min, len_max, log, run_dir):
    """등급별 per_grade건 생성(도메인 순환). candidates.jsonl에 **증분 기록**(장시간 런 손실 방지).

    (docs, intended_by_id, gen_fail, pii_hits) 반환. 생성 1건마다 candidates.jsonl에
    append+flush 하므로 프로세스가 도중 죽어도 그때까지 생성분은 디스크에 남고, --build-from
    으로 재개 가능.
    """
    from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator
    gen = SyntheticDocGenerator(llm=_provider(base_url, gen_model, "ollama-gen"))
    docs, intended_by_id, gen_fail, pii_hits = [], {}, 0, 0
    cand_path = Path(run_dir) / "candidates.jsonl"
    with io.open(cand_path, "w", encoding="utf-8") as cf:
        for g in grades:
            domains = GRADE_DOMAINS.get(g, ["mixed"])
            for i in range(per_grade):
                dom = domains[i % len(domains)]
                try:
                    d = gen.generate_one(SynthRequest(target_grade=g, domain=dom, count=1,
                                                      len_min=len_min, len_max=len_max))
                except Exception as exc:  # noqa: BLE001 — 1건 실패가 장시간 런 전체를 죽이지 않게
                    gen_fail += 1
                    log(f"  생성 실패 {g}/{dom}: {type(exc).__name__}")
                    continue
                if d.parse_error or not d.body or len(d.body.strip()) < 50:
                    gen_fail += 1
                    continue
                if d.pii_violations:
                    pii_hits += 1  # 생성기가 PII 넣으면 카운트(드물지만 감사용)
                doc_id = f"syn-{g}-{dom}-{uuid.uuid4().hex[:6]}"
                doc = {"doc_id": doc_id, "text": d.body, "domain": dom,
                       "source": "synthetic", "intended": g, "title": d.title}
                docs.append(doc)
                intended_by_id[doc_id] = g
                cf.write(json.dumps(doc, ensure_ascii=False) + "\n")
                cf.flush()  # 증분 영속 — 도중 죽어도 보존
            log(f"  생성 {g}: 누적 {len([x for x in docs if x['intended']==g])}건")
    return docs, intended_by_id, gen_fail, pii_hits


def load_candidates(build_from):
    """--build-from: run_dir(또는 candidates.jsonl 경로)에서 생성분 로드(생성 건너뜀·재개)."""
    p = Path(build_from)
    if p.is_dir():
        p = p / "candidates.jsonl"
    if not p.exists():
        return [], {}  # 누락 시 main의 [abort] 가드가 처리(uncaught FileNotFoundError 대신)
    docs = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
    intended_by_id = {d["doc_id"]: d.get("intended", "?") for d in docs}
    return docs, intended_by_id


def build(docs, judge_model, base_url, require_evidence, k_min, k_max, shadow_model, temperature, log):
    from lloydk.golden_builder import LabelPair, build_golden_set, make_label_fn
    from lloydk.modules.m3_labeling.judge import ConsensusJudge
    from lloydk.modules.m3_labeling.llm_labeler import LLMLabeler
    primary = LLMLabeler(provider=_provider(base_url, judge_model, "ollama-judge"))
    shadow = (LLMLabeler(provider=_provider(base_url, shadow_model, "ollama-shadow"))
              if shadow_model else None)
    judge = ConsensusJudge(primary=primary, shadow=shadow, airgap=False,
                           k_min=k_min, k_max=k_max, temperature=temperature)
    label_fn = make_label_fn(judge=judge)
    fails = {"n": 0}

    def safe_label_fn(text):
        try:
            return label_fn(text)
        except Exception as exc:  # noqa: BLE001 — 판정 실패는 review로(gold 금지)·런 보존
            fails["n"] += 1
            log(f"  판정 실패(→review): {type(exc).__name__}")
            # rule=TS vs llm=S3 → ts_downgrade_suspect → needs_review (절대 gold 안 됨)
            return LabelPair(rule_grade="TS", rule_conf=0.0, llm_grade="S3",
                             llm_conf=0.0, has_real_evidence=False)

    result = build_golden_set(docs, label_fn=safe_label_fn, require_evidence=require_evidence)
    return result, fails["n"]


def write_outputs(run_dir, run_id, result, meta):
    """build/uncertain/stats 기록. candidates.jsonl은 generate_docs가 증분 기록(또는 build-from 입력)."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl(name, rows):
        with (run_dir / name).open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

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
    ap.add_argument("--shadow-model", default="qwen3:14b",
                    help="섀도 심판 모델(production_suspect 교차검증; 기본 ON=Qwen). 심판과 동일하면 자동 비활성")
    ap.add_argument("--no-shadow", action="store_true",
                    help="섀도 심판 비활성(비용 절감·교차검증 포기). 기본은 ON")
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
    ap.add_argument("--build-from", default=None,
                    help="생성 건너뛰고 기존 run_dir(또는 candidates.jsonl)에서 빌드만(재개)")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    grades = [g.strip() for g in args.grades.split(",") if g.strip()]
    per_grade = 1 if args.smoke else args.per_grade
    require_evidence = bool(args.require_evidence)

    def log(msg):
        # ascii-safe 진행 로그(cp949 콘솔에서 한글 깨질 수 있어 stderr+flush; 핵심 수치는 stats.json)
        print(msg, file=sys.stderr, flush=True)

    # [B-1] 섀도 심판 기본 ON — production_suspect 교차검증 활성화(옛 기본 None=안전장치 no-op였음).
    # --no-shadow로 끄거나, 섀도==심판이면 자기합의라 자동 비활성.
    shadow_model = None if args.no_shadow else _resolve_shadow_model(
        args.shadow_model, args.judge_model, log)

    # --build-from: 기존 run_dir 재사용(생성 건너뜀). 아니면 새 run_dir 생성 후 증분 생성.
    if args.build_from:
        run_dir = Path(args.build_from if Path(args.build_from).is_dir()
                       else Path(args.build_from).parent)
        run_id = run_dir.name
        log(f"[run {run_id}] BUILD-FROM {run_dir} (생성 건너뜀)")
        docs, intended_by_id = load_candidates(args.build_from)
        gen_fail = pii_hits = 0
        gen_s = 0.0
        log(f"[gen] (재개) candidates {len(docs)}건 로드")
    else:
        run_id = uuid.uuid4().hex[:12]
        run_dir = Path(args.out_dir) / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        log(f"[run {run_id}] grades={grades} per_grade={per_grade} "
            f"gen={args.gen_model} judge={args.judge_model} require_evidence={require_evidence}")
        t0 = time.time()
        docs, intended_by_id, gen_fail, pii_hits = generate_docs(
            args.gen_model, args.base_url, grades, per_grade, args.len_min, args.len_max, log, run_dir)
        gen_s = round(time.time() - t0, 1)
        log(f"[gen] {len(docs)}건 생성(실패 {gen_fail}, pii {pii_hits}) {gen_s}s")

    if not docs:
        log("[abort] 생성/로드 0건 — Ollama 가동·모델·candidates 확인")
        return 2

    t1 = time.time()
    result, judge_fail = build(docs, args.judge_model, args.base_url, require_evidence,
                               args.k_min, args.k_max, shadow_model, args.temperature, log)
    build_s = round(time.time() - t1, 1)

    # 등급별 gold 수율(intended 기준).
    gold_by_intended = {}
    for r in result.gold:
        g = intended_by_id.get(r.doc_id, "?")
        gold_by_intended[g] = gold_by_intended.get(g, 0) + 1

    meta = {
        "run_id": run_id, "grades": grades, "per_grade": per_grade,
        "gen_model": args.gen_model, "judge_model": args.judge_model,
        "shadow_model": shadow_model, "require_evidence": require_evidence,
        "k_min": args.k_min, "k_max": args.k_max, "temperature": args.temperature,
        "generated": len(docs), "gen_fail": gen_fail, "judge_fail": judge_fail, "pii_hits": pii_hits,
        "gen_seconds": gen_s, "build_seconds": build_s,
        "gold_candidate": len(result.gold), "needs_review": len(result.uncertain),
        "pass_rate": round(len(result.gold) / len(docs), 3) if docs else 0,
        "gold_by_intended_grade": gold_by_intended,
        "high_grade_gold": gold_by_intended.get("TS", 0) + gold_by_intended.get("S1", 0),
        "stats": result.stats,
    }
    write_outputs(run_dir, run_id, result, meta)
    log(f"[build] gold={len(result.gold)} review={len(result.uncertain)} "
        f"pass={meta['pass_rate']} high_grade_gold={meta['high_grade_gold']} {build_s}s")
    log(f"[done] -> {run_dir} (stats.json)")
    # stdout엔 run_dir만(파이프라인 친화). 상세는 stats.json.
    print(str(run_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
