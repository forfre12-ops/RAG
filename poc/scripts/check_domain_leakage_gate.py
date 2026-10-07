# -*- coding: utf-8 -*-
"""[P0#2] 도메인→등급 누출(shortcut) 산출 게이트 — 합성 빌드 gold를 차단선으로 승격.

analyze_golden_run.domain_leakage가 계산하는 누출 지표(trivial_domain_baseline·theil_u)를
"분석만" 하고 끝내던 것을, CI/운영에서 **hard 게이트**로 소비한다. 누출이 임계를 넘으면
exit 2(빌드 산출 거부) — KF-DeBERTa가 민감도 대신 도메인토픽/길이를 학습하는 shortcut의
재발(교차설계 회귀)을 막는다.

실측 앵커(참고): 등급-잠금 도메인풀 = baseline 0.856·theil_u 0.808(누출 심함, 학습 보류).
교차 재설계(SPAN_DOMAINS) = baseline 0.411·theil_u 0.072(건전). 기본 임계는 둘을 분리하되
건전 값에 여유를 둔다(baseline 0.55·theil_u 0.35).

빌드 스크립트(build_synthetic_golden.py)를 수정하지 않는 **독립 게이트** — 산출 후 CI/운영에서
run 디렉토리(build_*.jsonl=gold_candidate=학습 silver)를 대상으로 실행한다.

사용:
  python scripts/check_domain_leakage_gate.py poc/datasets/golden_runs/<run_id>
  python scripts/check_domain_leakage_gate.py --max-baseline 0.55 --max-theil-u 0.35 <run_dir_or_jsonl>
"""
from __future__ import annotations

DEFAULT_MAX_BASELINE = 0.55   # 도메인-다수결 정확도 상한(초과=도메인이 등급을 과하게 결정)
DEFAULT_MAX_THEIL_U = 0.35    # 도메인→등급 종속도 상한(0=독립·1=완전결정)


def domain_leakage_verdict(
    leakage: dict,
    *,
    max_baseline: float = DEFAULT_MAX_BASELINE,
    max_theil_u: float = DEFAULT_MAX_THEIL_U,
) -> dict:
    """누출 지표 dict → 게이트 판정(순수). baseline·theil_u 중 하나라도 임계 초과면 blocked.

    표본 없음(n=0/빈 dict)은 blocked=False(측정 불가는 차단 아님 — fail-open; 게이트는 실제
    누출을 잡는 것이지 데이터 부재를 벌하지 않는다). 무음 통과 방지 위해 reason=no_data 노출.
    """
    if not leakage or not leakage.get("n"):
        return {"blocked": False, "reason": "no_data", "leakage": leakage or {}}
    baseline = float(leakage.get("trivial_domain_baseline", 0.0))
    theil_u = float(leakage.get("theil_u", 0.0))
    reasons = []
    if baseline > max_baseline:
        reasons.append(f"trivial_domain_baseline {baseline:.3f} > {max_baseline:.3f}")
    if theil_u > max_theil_u:
        reasons.append(f"theil_u {theil_u:.3f} > {max_theil_u:.3f}")
    return {
        "blocked": bool(reasons),
        "reason": "; ".join(reasons) if reasons else "ok",
        "leakage": leakage,
        "max_baseline": max_baseline,
        "max_theil_u": max_theil_u,
    }


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    ap = argparse.ArgumentParser(
        description="도메인→등급 누출 산출 게이트(초과 시 exit 2)"
    )
    ap.add_argument("run", help="golden_runs/<run_id> 디렉토리 또는 gold jsonl 경로")
    ap.add_argument("--max-baseline", type=float, default=DEFAULT_MAX_BASELINE)
    ap.add_argument("--max-theil-u", type=float, default=DEFAULT_MAX_THEIL_U)
    args = ap.parse_args(argv)

    # 런타임 import — 테스트 collect가 (동시 편집 중인) analyze_golden_run에 의존하지 않게 격리.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from analyze_golden_run import _load, domain_leakage  # noqa: PLC0415

    run = Path(args.run)
    records: list[dict] = []
    if run.is_dir():
        for f in sorted(run.glob("build_*.jsonl")):  # gold_candidate = 학습 silver
            records += _load(str(f))
    else:
        records = _load(str(run))

    leakage = domain_leakage(records)
    verdict = domain_leakage_verdict(
        leakage, max_baseline=args.max_baseline, max_theil_u=args.max_theil_u
    )
    # 판정 노출(무음 금지). ascii-safe(cp949 콘솔 안전).
    print(json.dumps(verdict, ensure_ascii=False))
    if verdict["blocked"]:
        # ascii-safe(CI 로그·cp949 콘솔 안전). 상세 판정은 stdout JSON 참조.
        print(f"[BLOCK] domain->grade leakage gate FAILED: {verdict['reason']}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
