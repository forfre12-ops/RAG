# -*- coding: utf-8 -*-
"""합성 골든 빌드 run-스코프 산출 정직 분석 — eval_cards(FNR 카드) + judge_reliability(불일치).

build_synthetic_golden.py가 만든 golden_runs/<run_id>/ 를 입력으로:
  - 요약: 통과율·등급별 gold 수율·needs_review status 분포
  - eval_cards: 의도등급(=true) 대비 LLM 심판(=pred)의 under-class FNR Wilson 카드
    (표본부족=INCONCLUSIVE, 단일 헤드라인 금지 — 합성 천장 거짓신뢰 방지)
  - judge_reliability: rule(a) vs llm(b) 불일치를 도메인·의도등급별 층화(라벨 미수정)

순수 분석(임베더·LLM 불요·DB 불요). 결과는 run dir에 analysis.json + ascii 요약.
사용: python analyze_golden_run.py [run_dir]   # 생략 시 golden_runs 최신
"""
from __future__ import annotations

import glob
import io
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lloydk.golden_defense import assess_defense_liveness  # noqa: E402
from lloydk.modules.m6_evaluation.eval_cards import GRADE_ORDER, build_eval_cards  # noqa: E402
from lloydk.modules.m6_evaluation.judge_reliability import build_judge_reliability  # noqa: E402


def _load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def domain_leakage(gold: list[dict]) -> dict:
    """도메인→등급 shortcut 누출 지표(silver 학습 적합성). 도메인이 등급을 얼마나 결정하나.

    trivial_domain_baseline: 도메인-다수결 분류기 정확도(높을수록 누출 심함, 1.0=완전종속).
    theil_u: 도메인이 등급 불확실성을 해소하는 비율(0=독립, 1=완전결정). H는 bits.
    """
    import math as _m
    # 라벨을 GRADE_ORDER로 검증(eval_cards·judge_reliability와 동일) — 잡값이 엔트로피/baseline 오염 방지.
    rows = [(r.get("domain") or "?", r.get("label") or r.get("llm_grade")) for r in gold
            if (r.get("label") or r.get("llm_grade")) in GRADE_ORDER]
    n = len(rows)
    if not n:
        return {}
    grades = Counter(g for _, g in rows)

    def H(counter, tot):
        return -sum((c / tot) * _m.log2(c / tot) for c in counter.values() if c)

    Hg = H(grades, n)
    by_dom = defaultdict(Counter)
    for d, g in rows:
        by_dom[d][g] += 1
    Hgd = sum((sum(gc.values()) / n) * H(gc, sum(gc.values())) for gc in by_dom.values())
    correct = sum(max(gc.values()) for gc in by_dom.values())
    return {
        "n": n, "n_domains": len(by_dom),
        "H_grade": round(Hg, 3) + 0.0, "H_grade_given_domain": round(Hgd, 3) + 0.0,  # -0.0 정규화
        "trivial_domain_baseline": round(correct / n, 3),
        # 단일 등급(Hg=0)은 도메인이 그 등급을 자명하게 결정 → theil_u=1.0(baseline=1.0과 정합).
        # 0.0로 두면 '누출 없음'으로 오독됨(baseline은 1.0인데).
        "theil_u": round((Hg - Hgd) / Hg, 3) if Hg else 1.0,
        "pure_domain_rows": sum(sum(gc.values()) for gc in by_dom.values() if len(gc) == 1),
    }


def analyze(run_dir: str) -> dict:
    run_dir = run_dir.rstrip("/\\") + os.sep
    intended = {d["doc_id"]: d.get("intended", "?") for d in _load(run_dir + "candidates.jsonl")}
    records: list[dict] = []
    for pat in ("build_*.jsonl", "uncertain_*.jsonl"):
        for p in glob.glob(run_dir + pat):
            bucket = "gold" if os.path.basename(p).startswith("build_") else "review"
            for r in _load(p):
                r["intended"] = intended.get(r.get("doc_id"), "?")
                r["bucket"] = bucket
                records.append(r)

    n = len(records)
    gold = [r for r in records if r["bucket"] == "gold"]
    summary = {
        "n_records": n,
        "gold_candidate": len(gold),
        "pass_rate": round(len(gold) / n, 3) if n else 0,
        "gold_by_intended": dict(Counter(r["intended"] for r in gold)),
        "review_by_status": dict(Counter(r.get("status") for r in records if r["bucket"] == "review")),
    }

    # eval_cards: 의도등급(true) vs LLM 심판(pred) — under-class FNR 카드(합성 silver 라벨의 FNR-안전성)
    cards = build_eval_cards(
        records,
        slice_of=lambda r: "synthetic",
        true_of=lambda r: r.get("intended"),
        pred_of=lambda r: r.get("llm_grade"),
    )

    # judge_reliability: rule(a) vs llm(b) 불일치 — 병목 방향(룰 침묵 vs LLM 오류) 폭로
    jr = build_judge_reliability(
        records,
        a_of=lambda r: r.get("rule_grade"),
        b_of=lambda r: r.get("llm_grade"),
        source_of=lambda r: r.get("intended"),
        domain_of=lambda r: r.get("domain") or "?",
        a_name="rule", b_name="llm", min_stratum_n=10,
    )

    # defense_liveness: 5개 방어 축이 실제로 발화했나(무음으로 꺼진 방어 폭로) — gold+review 전체.
    defense = assess_defense_liveness(records)

    return {"run_dir": run_dir, "summary": summary,
            "domain_leakage": domain_leakage(gold),
            "defense_liveness": defense,
            "eval_cards": cards.to_dict(), "judge_reliability": jr.to_dict()}


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows cp949 콘솔의 한글·em-dash 크래시 회피
    except Exception:  # noqa: BLE001
        pass
    args = argv if argv is not None else sys.argv[1:]
    if args:
        run_dir = args[0]
    else:
        runs = sorted(glob.glob("poc/datasets/golden_runs/*/") +
                      glob.glob("f:/antigravity/rag/poc/datasets/golden_runs/*/"),
                      key=os.path.getmtime, reverse=True)
        if not runs:
            print("no run dir found", file=sys.stderr)
            return 2
        run_dir = runs[0]

    out = analyze(run_dir)
    with io.open(out["run_dir"] + "analysis.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    s = out["summary"]
    print(f"run={out['run_dir']}")
    print(f"N={s['n_records']} gold={s['gold_candidate']} pass={s['pass_rate']} "
          f"gold_by_intended={s['gold_by_intended']}")
    print(f"review_by_status={s['review_by_status']}")
    dl = out.get("domain_leakage", {})
    if dl:
        print(f"[누출] 도메인→등급 trivial baseline={dl['trivial_domain_baseline']} "
              f"(낮을수록 좋음; 0.856=본생성714 블로커) Theil_U={dl['theil_u']} "
              f"H(grade|domain)={dl['H_grade_given_domain']}/{dl['H_grade']} 도메인수={dl['n_domains']}")
    dfl = out.get("defense_liveness", {})
    if dfl:
        ax = dfl.get("axes", {})
        print(f"[방어 liveness] verdict={dfl['verdict']} (shadow_active={dfl['shadow_active']}, "
              f"차단축 발화={dfl['blocking_axes_active']}/3)")
        print(f"  shadow cov={ax.get('shadow',{}).get('coverage')} fired={ax.get('shadow',{}).get('fired')} | "
              f"disagree={ax.get('agreement',{}).get('fired')} | ts_down={ax.get('ts_downgrade',{}).get('fired')} | "
              f"self_consist fired={ax.get('self_consistency',{}).get('fired')} informative={ax.get('self_consistency',{}).get('informative')}")
        print(f"  → {dfl['recommendation']}")
    o = out["judge_reliability"]["overall"]
    print(f"[judge] rule vs llm: agree={o['agreement_rate']} "
          f"up(llm>rule)={o['upgrade_rate']} down(llm<rule)={o['downgrade_rate']}")
    print("[eval_cards] under-class FNR (의도 vs LLM):")
    for c in out["eval_cards"]["cards"]:
        print(f"  {c['grade']:3s} n={c['n']:3d} fnr={c['underclass_fnr']:.3f} "
              f"ci={c['fnr_ci']} verdict={c['verdict']} {c['cannot_measure']}")
    print("->", out["run_dir"] + "analysis.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
