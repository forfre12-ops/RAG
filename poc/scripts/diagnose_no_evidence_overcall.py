#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""룰이 '근거 0' 이라 말하는데 모델이 높게 부르는 자리를 센다 — 과탐의 주 경로.

## 왜 (2026-09-14)

평범한 사내문서 500건(mundane_s3)에서 서빙 과탐이 **86.2%** 였다. 기전을 보니 이렇다.

    rule_grade   S3 483 · S2 17      ← 룰은 96.6% 를 맞게 본다
    model_grade  S2 429 · S3 69 · S1 2
    predicted    모델 그대로
    rule_factors {SECRECY: 0.0, VALUE: 0.0, MANAGEMENT: 0.0}  ← 414건이 근거 0
    경고          "factors aligned to model grade S2 (rule under-detected S/V/M)" 412건
    conf          과탐분 중앙값 0.754  (검수 임계 0.50 보다 높다 → 자동확정)

합의 게이트가 개입하지 않는다. `classify_service.py:918` 이 이렇게 적는다 —

    if not detect_management_marking(text or ""):
        return None  # 룰 무의견(실 근거 0건) — 불일치로 안 침, conf 단독 신뢰

**그 abstain 은 비대칭이다.** 룰이 근거 0 일 때
  · 모델이 **낮게** 부르면 → 미탐 위험 → 관리표시가 있으면 개입한다(2026-08-22 에 고침)
  · 모델이 **높게** 부르면 → 과탐 위험 → **아무 장치가 없다**

코드 주석 자신이 "그때 abstain 은 확신에 찬 과소분류를 그냥 통과시킨다" 고 적었는데,
걱정한 방향은 미탐이었다. 과탐 방향으로도 같은 구멍이다.

## 그런데 게이트를 막는 것이 답이 아니다

막았을 때의 검수율을 재면(아래 실행 결과) mundane 은 11.4% → 83.0% 가 된다.
평범한 문서는 원래 룰 근거가 없다 — 게이트로 막으면 전부 검수로 간다.

**진짜 고칠 자리는 학습 데이터다.** mundane_s3 500건은 "평범한 업무문서 = 비밀 아님(S3)"
를 가르치려고 만든 것인데(gen_mundane_s3.py 헤더) **배포 학습셋 v5_clean 에 0건**이다
(step3 1,350행 · v7_diverse 196건에만 들어갔고 build_p1_v5_clean.py 에 mundane 언급 0건).

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/diagnose_no_evidence_overcall.py \
        --records reports/OVERCLASS_20260914/mundane_s3_500.records.jsonl
    ... --glob "reports/**/*.records.jsonl"
"""
from __future__ import annotations

import argparse
import glob as globmod
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def _rows(path: Path) -> list[dict]:
    out = []
    try:
        fh = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def has_no_rule_evidence(r: dict) -> bool:
    """룰이 요소를 전부 0 으로 봤는가 — '근거가 하나도 없다' 는 룰의 명시적 진술이다."""
    f = r.get("rule_factors")
    if not isinstance(f, dict) or not f:
        return False
    try:
        return all(float(v) == 0.0 for v in f.values())
    except (TypeError, ValueError):
        return False


def analyze(rows: list[dict]) -> dict:
    n = len(rows)
    no_ev = [r for r in rows if has_no_rule_evidence(r)]
    # 룰보다 모델이 **높게** 부른 것 (숫자가 작을수록 높은 등급)
    over = [r for r in no_ev
            if GRADE_ORDER.get(r.get("predicted"), 9) < GRADE_ORDER.get(r.get("rule_grade"), 9)]
    auto = [r for r in over if r.get("status") != "needs_review"]
    cur_review = sum(1 for r in rows if r.get("status") == "needs_review")
    confs = [float(r["confidence"]) for r in auto
             if isinstance(r.get("confidence"), (int, float))]
    confs.sort()
    return {
        "n": n,
        "no_rule_evidence": len(no_ev),
        "model_calls_higher": len(over),
        "auto_confirmed": len(auto),
        "review_rate_now": (cur_review / n) if n else None,
        "review_rate_if_gated": ((cur_review + len(auto)) / n) if n else None,
        "auto_conf_median": confs[len(confs) // 2] if confs else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--records", action="append", default=[])
    ap.add_argument("--glob", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    paths = [POC / p for p in args.records]
    if args.glob:
        paths += [Path(p) for p in globmod.glob(str(POC / args.glob), recursive=True)]
    paths = sorted({p.resolve() for p in paths})
    if not paths:
        print("대상이 없다 — --records 또는 --glob 을 주라")
        return 2

    print("=" * 88)
    print("룰이 '근거 0' 인데 모델이 높게 부르는 자리 — 합의 게이트가 개입하지 않는 구멍")
    print("=" * 88)
    print(f"{'평가면':<26} {'N':>5} {'근거0':>7} {'모델↑':>7} {'자동확정':>8} "
          f"{'현 검수율':>9} {'막으면':>8}")
    print("-" * 88)
    out = []
    for p in paths:
        rows = _rows(p)
        if not rows:
            continue
        a = analyze(rows)
        name = p.stem.replace(".records", "")[:26]
        print(f"{name:<26} {a['n']:>5} {a['no_rule_evidence']:>7} {a['model_calls_higher']:>7} "
              f"{a['auto_confirmed']:>8} {(a['review_rate_now'] or 0) * 100:>8.1f}% "
              f"{(a['review_rate_if_gated'] or 0) * 100:>7.1f}%")
        a["suite"] = name
        a["records"] = p.relative_to(POC).as_posix()
        out.append(a)

    print()
    print("읽는 법")
    print("  · '근거0'     룰이 SECRECY·VALUE·MANAGEMENT 를 전부 0 으로 본 문서")
    print("  · '모델↑'     그 상태에서 모델이 룰보다 높은 등급을 부른 것 = 과탐 위험")
    print("  · '막으면'    그 자동확정분을 검수로 보냈을 때의 검수율")
    print()
    print("⚠ 게이트로 막는 것이 답이 아닐 수 있다 — 평범한 문서는 원래 룰 근거가 없다.")
    print("  진짜 고칠 자리는 학습 데이터다(mundane_s3 500건이 배포 학습셋에 0건).")

    if args.json:
        q = POC / args.json
        q.parent.mkdir(parents=True, exist_ok=True)
        q.write_text(json.dumps({"suites": out}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {q.relative_to(POC)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
