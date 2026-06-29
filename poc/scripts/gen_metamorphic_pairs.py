"""D 실행 글루 — 앵커 → (Qwen) 패러프레이즈/사실제거 생성 → 서빙 분류 → 메타모픽 회귀 리포트.

paraphrase_gen(생성 오케스트레이션, 결정적 사실보존 게이트) + 실 LLM(build_provider) +
predict_via_serving(분류) + metamorphic.build_metamorphic_report(채점, 실패만 정보)를 잇는다.

규율(metamorphic.py):
  - 통과는 품질 증거 아님 — **실패(위반)만 정보**. quality_score 없음.
  - forward(미탐 FN) + reverse(과분류 FP) + over_class(공개→비밀) **대칭**을 같이 본다.
  - 라벨 상속은 결정적 사실보존 게이트로만(룰/심판 미사용 — 순환 차단).

사용(로컬 Qwen, Ollama):
  python scripts/gen_metamorphic_pairs.py --provider ollama --max-anchors 8 \
    --model-dir artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9 \
    --report reports/metamorphic_pairs.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

HIGH_GRADES = ("TS", "S1")
LOWEST = "S3"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_retrain_v4_clean/v-dd3abab9")
    ap.add_argument("--provider", default="", help="LLM provider(빈칸=settings.llm_provider). 예: ollama")
    ap.add_argument("--max-anchors", type=int, default=8, help="생성할 고등급 앵커 수(LLM 비용 제어)")
    ap.add_argument("--max-public", type=int, default=20, help="과분류 베이스라인용 공개(S3) 앵커 수")
    ap.add_argument("--temperature", type=float, default=0.3)
    ap.add_argument("--report", default="reports/metamorphic_pairs.md")
    ap.add_argument("--dump-pairs", default="reports/metamorphic_pairs.samples.jsonl")
    args = ap.parse_args()

    from lloydk.adapters.llm import build_provider
    from lloydk.modules.m5_inference.pipeline import InferencePipeline
    from lloydk.modules.m6_evaluation.anchor_corpus import load_anchor_corpus
    from lloydk.modules.m6_evaluation.metamorphic import build_metamorphic_report
    from lloydk.modules.m6_evaluation.paraphrase_gen import generate_minimal_pairs
    from lloydk.modules.m6_evaluation.serving_eval import predict_via_serving

    records = load_anchor_corpus()
    # 고등급(TS/S1) + 필수토큰 보유만 생성 대상(보존 게이트가 의미 있는 것). 결정적 정렬 후 절단.
    hi = sorted([r for r in records if r.anchor_grade in HIGH_GRADES and r.required_tokens],
                key=lambda r: r.anchor_id)[:args.max_anchors]
    pub = sorted([r for r in records if r.anchor_grade == LOWEST],
                 key=lambda r: r.anchor_id)[:args.max_public]
    if not hi:
        print("고등급+토큰 앵커 0건 — 생성 불가", file=sys.stderr)
        return 2

    provider = build_provider(args.provider or None)

    def generate_fn(prompt: str, system: str | None = None) -> str:
        return provider.generate(prompt, system=system, max_tokens=1200,
                                 temperature=args.temperature).text

    gen = generate_minimal_pairs(hi, generate_fn=generate_fn, do_reverse=True)

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        model_dir = _HERE.parent / model_dir
    pipe = InferencePipeline(model_dir=str(model_dir))

    def classify(samples):
        """채택된 GeneratedSample 들을 서빙으로 분류 → pred 부착(리스트 of dict)."""
        admitted = [s for s in samples if s.admitted]
        rows = [{"text": s.generated_text, "label": s.anchor_grade} for s in admitted]
        preds = predict_via_serving(rows, pipeline=pipe, labels=["TS", "S1", "S2", "S3"])
        return [(s, p["pred"]) for s, p in zip(admitted, preds)]

    fwd = classify(gen["forward"])
    rev = classify(gen["reverse"])

    forward_pairs = [{"anchor_id": s.anchor_id, "anchor_grade": s.anchor_grade,
                      "paraphrase_pred": pred} for s, pred in fwd]
    reverse_pairs = [{"anchor_grade": s.anchor_grade, "fact_removed_pred": pred}
                     for s, pred in rev]

    # 공개(S3) 과분류 베이스라인 — 분류만.
    pub_rows = [{"text": r.text, "label": r.anchor_grade} for r in pub]
    pub_preds = predict_via_serving(pub_rows, pipeline=pipe, labels=["TS", "S1", "S2", "S3"]) if pub else []
    public_records = [{"pred": p["pred"]} for p in pub_preds]

    report = build_metamorphic_report(
        forward_pairs, reverse_pairs=reverse_pairs, public_records=public_records,
    )
    rd = report.to_dict()

    # 생성 표본 덤프(감사 — 무엇을 생성·채택/탈락했는지).
    dump = Path(args.dump_pairs)
    if not dump.is_absolute():
        dump = _HERE.parent / dump
    dump.parent.mkdir(parents=True, exist_ok=True)
    with dump.open("w", encoding="utf-8") as f:
        for s in gen["forward"] + gen["reverse"]:
            f.write(json.dumps({
                "anchor_id": s.anchor_id, "anchor_grade": s.anchor_grade, "kind": s.kind,
                "admitted": s.admitted, "reason": s.reason,
                "required_tokens": s.required_tokens,
                "generated_text": s.generated_text[:500],
            }, ensure_ascii=False) + "\n")

    admit_fwd = sum(1 for s in gen["forward"] if s.admitted)
    admit_rev = sum(1 for s in gen["reverse"] if s.admitted)
    rev_weak = gen.get("reverse_skipped_weak_tokens", 0)
    rev_caveat = (
        f"⚠️ **reverse 측정 보류**: 적격 토큰(본문중간 근거 evidence@N) 보유 앵커 0 — "
        f"{rev_weak}건이 제목/도메인어 약토큰이라 제외. 약토큰으로 reverse를 돌리면 사실을 빼도 "
        f"본문에 잔존해 **거짓 100% 위반**이 난다(적대검증 확인). required_tokens 강화 전까지 reverse N≈0."
        if len(gen["reverse"]) == 0 else
        f"reverse 적격 {len(gen['reverse'])}건(약토큰 제외 {rev_weak})."
    )
    md = [
        "# 메타모픽 최소쌍 회귀 — D (실패만 정보, 통과는 품질 아님)",
        "",
        f"- model_dir: `{args.model_dir}` · provider: `{provider.name}/{getattr(provider,'model','?')}`",
        f"- 생성 앵커: 고등급 {len(hi)} · 공개(S3) {len(pub)}",
        f"- 사실보존 게이트 채택: forward {admit_fwd}/{len(gen['forward'])} · "
        f"reverse {admit_rev}/{len(gen['reverse'])} (토큰없음 스킵 {gen['skipped_no_tokens']})",
        "",
        "> **실패(위반)만 정보.** 통과(violations=0)는 품질 증거 아님(전부 TS로 찍어도 forward 공허통과).",
        "> 대칭으로 본다: forward=미탐(FN)·reverse=과분류(FP)·over_class=공개를 비밀로.",
        "",
        f"> {rev_caveat}",
        "",
        "| 검사 | N | 위반 | 위반율 | 95%CI | 의미 |",
        "|---|---:|---:|---:|---|---|",
    ]
    for key, mean in (("forward", "미탐(고등급↓)"), ("reverse", "과분류(사실제거후 안내려감)"),
                      ("over_class", "공개→비밀")):
        st = rd[key]
        md.append(f"| {st['name']} | {st['n']} | {st['violations']} | {st['violation_rate']:.3f} | "
                  f"[{st['ci'][0]:.3f}, {st['ci'][1]:.3f}] | {mean} |")
    if rd["forward_failures"]:
        md += ["", "## forward 위반(고등급 미탐 — 위험 신호)", ""]
        for ff in rd["forward_failures"]:
            md.append(f"- `{ff['anchor_id']}` 앵커={ff['anchor_grade']} → 패러프레이즈예측={ff['paraphrase_pred']}")

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps({
        "model_dir": args.model_dir, "provider": provider.name,
        "n_high_anchors": len(hi), "n_public": len(pub),
        "admitted": {"forward": admit_fwd, "reverse": admit_rev,
                     "skipped_no_tokens": gen["skipped_no_tokens"],
                     "reverse_skipped_weak_tokens": rev_weak},
        "reverse_validity": "measurable" if len(gen["reverse"]) else "unmeasurable_weak_tokens",
        "report": rd,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "forward": rd["forward"], "reverse": rd["reverse"], "over_class": rd["over_class"],
        "admitted": {"forward": admit_fwd, "reverse": admit_rev},
        "report": str(out), "samples": str(dump),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
