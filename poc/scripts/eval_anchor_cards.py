"""C 실행 글루 — 외부 사실 앵커 코퍼스 → 실서빙 예측 → 출처×등급 평가 카드.

코어 build_eval_cards(C)는 순수 채점함수다(픽스처로만 테스트). 이 드라이버가 **실데이터**를
물린다: anchor_corpus가 NKT/홀드아웃을 조립 → predict_via_serving이 실서빙 경로(보정·집계·
게이트·escalation)로 예측 → build_eval_cards가 (출처×등급) Wilson CI 카드를 낸다.

설계 규율 그대로:
  - **단일 헤드라인 숫자 없음.** 출처×등급 칸을 나란히 본다(F1 0.92 같은 거짓신뢰 금지).
  - 표본 부족/CI 과대 칸은 PASS 아니라 **INCONCLUSIVE**. 앵커가 프록시(특허·판례)라
    PASS보다 INCONCLUSIVE가 많이 나오는 게 정상(천장 인정).
  - 표본 캡은 **로그로 명시**(조용한 절단 금지) — 캡이 평가범위를 가린 듯 보이지 않게.

사용:
  python scripts/eval_anchor_cards.py \
    --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
    --cap-per-cell 200 \
    --report reports/anchor_eval_cards.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default="artifacts/classifier_p1_v5_clean/v-fe4b386b")
    ap.add_argument("--cap-per-cell", type=int, default=0,
                    help="(출처×등급) 칸당 최대 표본(CPU 비용 제어). 0=전수. 절단은 로그로 명시.")
    ap.add_argument("--min-n", type=int, default=30, help="칸당 최소 표본(미달=INCONCLUSIVE)")
    ap.add_argument("--report", default="reports/anchor_eval_cards.md")
    ap.add_argument("--tau", default="", help="escalation τ override(빈칸=settings 기본). 0=argmax")
    ap.add_argument("--constructed-floor", action="append", default=[], metavar="RUN_DIR",
                    help="constructed_floor run 디렉터리(또는 admitted.jsonl)를 자체 slice로 편입"
                         "(opt-in·반복 가능). 라벨=floor(≥등급): FNR verdict만 의미, recall은 참고용.")
    ap.add_argument("--patent-timeaxis-prepub", action="append", default=[], metavar="DIR_OR_JSONL",
                    help="특허 시간축 prepub_floor_eval.jsonl(또는 그 run 디렉터리)를 자체 slice로 편입"
                         "(opt-in·반복 가능). 라벨=S2 floor(≥등급): FNR verdict만 의미, recall은 참고용.")
    args = ap.parse_args()

    from lloydk.config import settings
    from lloydk.modules.m6_evaluation.anchor_corpus import (
        DEFAULT_ANCHOR_SOURCES,
        constructed_floor_source,
        patent_timeaxis_prepub_source,
    )
    from lloydk.modules.m6_evaluation.anchor_eval import run_anchor_cards

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        model_dir = _HERE.parent / model_dir

    # constructed_floor·patent_timeaxis는 opt-in — 지정 시에만 기본 앵커에 자체 slice로 덧붙인다
    # (기본 경로 무변경). 둘 다 floor 라벨 = FNR verdict만 의미·recall 참고용.
    extra = []
    if args.constructed_floor:
        cf = [constructed_floor_source(p) for p in args.constructed_floor]
        extra += cf
        print(f"[constructed-floor] {len(cf)}개 run 편입(자체 slice, floor 라벨) — "
              f"FNR verdict만 의미·recall 참고용", file=sys.stderr)
    if args.patent_timeaxis_prepub:
        px = [patent_timeaxis_prepub_source(p) for p in args.patent_timeaxis_prepub]
        extra += px
        print(f"[patent-timeaxis] {len(px)}개 prepub 편입(자체 slice, S2 floor) — "
              f"FNR verdict만 의미·recall 참고용", file=sys.stderr)
    sources = (list(DEFAULT_ANCHOR_SOURCES) + extra) if extra else None

    saved_tau = settings.classifier_escalation_tau
    if args.tau.strip():
        v = float(args.tau)
        settings.classifier_escalation_tau = None if v <= 0 else v
    try:
        out_json = run_anchor_cards(
            model_dir=str(model_dir),
            sources=sources,
            cap_per_cell_n=args.cap_per_cell,
            min_n=args.min_n,
        )
    finally:
        settings.classifier_escalation_tau = saved_tau

    if out_json["corpus_summary"]["total"] == 0:
        print("앵커 코퍼스 0건 — datasets/ 경로 확인", file=sys.stderr)
        return 2

    summary = out_json["corpus_summary"]
    dropped = out_json["dropped_by_cap"]
    n_failed = out_json["serving_failed"]
    rep_dict = out_json["report"]
    out_json["model_dir"] = args.model_dir
    out_json["tau"] = settings.classifier_escalation_tau if not args.tau.strip() else args.tau
    if dropped:
        print(f"[cap] {args.cap_per_cell}/칸 적용 — {sum(dropped.values())}건 절단(전수 평가 아님): "
              f"{dropped}", file=sys.stderr)
    n_eval = out_json["evaluated"]

    # ── 마크다운(사람용) — 단일 숫자 금지, 카드 나열 ────────────────────────────────
    md = [
        "# 앵커 평가 카드 — 출처×등급 (C 실행 글루, 실서빙 경로)",
        "",
        f"- model_dir: `{args.model_dir}`",
        f"- 앵커 코퍼스: {summary['total']}건 / 평가 {n_eval}건"
        + (f" (칸당 캡 {args.cap_per_cell}, 절단 {sum(dropped.values())}건)" if dropped else " (전수)"),
        f"- 서빙 실패(미탐 보수집계): {n_failed}건",
        "",
        "> **단일 헤드라인 없음**(설계상 금지). 칸마다 under-class FNR Wilson 95% CI + 판정.",
        "> 앵커=외부사실 그라운딩(NKT 공식분류·큐레이트 홀드아웃). 프록시 천장이라 INCONCLUSIVE 정상.",
        "",
        f"판정 집계: PASS {rep_dict['counts'].get('PASS',0)} · "
        f"FAIL {rep_dict['counts'].get('FAIL',0)} · "
        f"INCONCLUSIVE {rep_dict['counts'].get('INCONCLUSIVE',0)}  (N/A=최저등급 제외)",
        "",
        "| 출처(slice) | 등급 | N | under-FNR | FNR 95%CI | recall | 판정 | 사유 |",
        "|---|---|---:|---:|---|---:|---|---|",
    ]
    for c in rep_dict["cards"]:
        ci = c["fnr_ci"]
        md.append(
            f"| {c['slice']} | {c['grade']} | {c['n']} | {c['underclass_fnr']:.3f} | "
            f"[{ci[0]:.3f}, {ci[1]:.3f}] | {c['recall']:.3f} | {c['verdict']} | {c['cannot_measure']} |"
        )

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(out_json, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({
        "evaluated": n_eval,
        "serving_failed": n_failed,
        "counts": rep_dict["counts"],
        "cards": [{"slice": c["slice"], "grade": c["grade"], "n": c["n"],
                   "verdict": c["verdict"], "underclass_fnr": c["underclass_fnr"]}
                  for c in rep_dict["cards"]],
        "report": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
