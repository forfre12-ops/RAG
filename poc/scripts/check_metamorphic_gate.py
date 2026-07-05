"""Release-time metamorphic gate — fail-closed on forward regression OR missing/insufficient evidence.

The deploy gate treats a metamorphic report as opt-in: an absent report -> visible skip
(metamorphic_eval_skipped=True, passed=True). That is correct for the *activation* gate
(auto-activate veto is locked-eval's job), but it means metamorphic can silently never-run
at release and the gate still goes green.

This standalone gate is the release-path enforcement: it REQUIRES a measurable metamorphic
report (>= min_n forward pairs) and blocks on any forward regression. A missing OR
insufficient report FAILs here -- you must actually run metamorphic on the release model.
That is what closes the "never generated -> silently green" fake-green gap.

Produce the report reproducibly (committed paraphrase fixture, no live LLM) with:
  make metamorphic-gate   # from-samples classify -> report -> this gate

ASCII-only output: this may print to a cp949/Windows console at the customer site.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_report(path: Path) -> dict | None:
    """Load a metamorphic report, unwrapping the producer's nested {"report": to_dict}.

    gen_metamorphic_pairs.py writes {"model_dir":..., "report": {forward,...}}; the gate
    reads top-level "forward". Mirrors training_service._maybe_metamorphic_report so a
    nested report is not silently seen as forward=None -> n=0 -> measurable=False.
    """
    if not path.exists():
        return None
    obj = json.loads(path.read_text(encoding="utf-8"))
    inner = obj.get("report")
    if isinstance(inner, dict) and "forward" in inner:
        return inner
    return obj


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", default="reports/metamorphic_pairs.json")
    ap.add_argument("--min-n", type=int, default=5,
                    help="forward 쌍 표본 하한(미만=측정 불가 -> FAIL). deploy_gate_metamorphic_min_n 기본과 정합.")
    ap.add_argument("--forward-ceiling", type=float, default=0.0,
                    help="forward 위반율 Wilson CI 하한 허용 상한(초과 시 회귀).")
    args = ap.parse_args()

    _src = Path(__file__).resolve().parent.parent / "src"
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
    from lloydk.modules.m6_evaluation.deploy_gate import summarize_metamorphic  # noqa: PLC0415

    report = _load_report(Path(args.report))
    if report is None:
        print(f"[metamorphic-gate] FAIL: missing report: {args.report}")
        print("  -> generate it (make metamorphic-gate) before release; a missing report is not a pass.")
        return 1

    summ = summarize_metamorphic(report, forward_ceiling=args.forward_ceiling, min_n=args.min_n)
    if not summ["measurable"]:
        print(f"[metamorphic-gate] FAIL: not measurable (forward n={summ['n']} < min_n={args.min_n}).")
        print("  -> insufficient metamorphic pairs; the gate must not pass on an unrun/empty eval.")
        return 1
    if summ["regression"]:
        ids = [f.get("anchor_id") for f in summ["failures"][:5]]
        print(
            f"[metamorphic-gate] BLOCKED: forward regression "
            f"(violations={summ['violations']}/{summ['n']}, ci_low={summ['ci_low']:.4f} "
            f"> ceiling {args.forward_ceiling:.3f})."
        )
        print(f"  style-only paraphrase downgraded these high-grade anchors (under-classification): {ids}")
        return 1

    print(
        f"[metamorphic-gate] PASS: no forward regression "
        f"(violations={summ['violations']}/{summ['n']}, ci_low={summ['ci_low']:.4f})."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
