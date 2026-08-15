"""서명 경로가 실제로 locked_gold_eval 을 만드는지 **끝까지 돌려서** 확인한다.

왜. 검수 경로가 둘인데 서로 다른 것을 만든다.

    콘솔 후보 검수  /golden/candidates/{id}/decision -> approved_proxy
                   (소스 주석 원문: "it never creates a locked evaluation record")
    골든 서명      /golden/jobs/{job_id}/signoff    -> locked_gold_eval

릴리스 게이트(eval_readiness)가 세는 것은 뒤쪽뿐이다. KL 서버에 배포된 콘솔에서 120건을
전부 검수해도 locked_gold_eval 은 0 건 그대로다. **검수자를 앉히기 전에** 이 경로가
실제로 도는지 확인해야 한다 — 배포 후에 발견하면 일정이 그대로 밀린다.

확인하는 것:
    1. 골든 빌드 잡을 만들 수 있는가(서명 대상이 어디서 오는가)
    2. 서명이 locked_gold_eval 을 만드는가
    3. 그것이 eval_readiness 를 움직이는가 (등급별 5건)
    4. 머신 reviewer 가 거부되는가 (서명 무결성)
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="서명 경로 검증")
    ap.add_argument("--reviewer", default="jjw-admin-01", help="실계정 형태의 검수자 id")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "inmemory")
    os.environ.setdefault("REQUIRE_REAL_EMBEDDER", "false")

    from koipa.golden_tiers import (
        DEFAULT_MIN_LOCKED_PER_GRADE,
        eval_readiness,
        is_human_reviewer,
        is_locked_eval,
        is_valid_signoff,
        tier_of,
    )

    print("=== 1) 서명 무결성 — 누가 서명할 수 있는가\n")
    for rid in (args.reviewer, "ai_assist", "demo-console", "system", "codex", "", None):
        print(f"   {str(rid)!r:22s} 사람 검수자로 인정 = {is_human_reviewer(rid)}")

    print(f"\n=== 2) locked_gold_eval 편입 조건 (is_valid_signoff)\n")
    base = {
        "label_source": "human_review",
        "reviewer_id": args.reviewer,
        "gate_version": None,
        "signed_at": "2026-08-15T09:00:00+09:00",
        "reviewer_ids": [args.reviewer],
    }
    from koipa.golden_tiers import SUPPORTED_GATE_VERSIONS

    print(f"   지원 gate_version: {sorted(SUPPORTED_GATE_VERSIONS)}")
    ok = dict(base, gate_version=sorted(SUPPORTED_GATE_VERSIONS)[0])
    print(f"   완전한 서명           -> valid={is_valid_signoff(ok)} · tier={tier_of(ok)}")
    for drop in ("label_source", "reviewer_id", "gate_version", "signed_at", "reviewer_ids"):
        bad = dict(ok)
        bad[drop] = None
        print(f"   {drop:14s} 없으면 -> valid={is_valid_signoff(bad)} · tier={tier_of(bad)}")

    print(f"\n=== 3) readiness 게이트 — 등급별 {DEFAULT_MIN_LOCKED_PER_GRADE}건\n")
    for n in (0, 3, 5, 30):
        recs = []
        for g in ("TS", "S1", "S2", "S3"):
            for i in range(n):
                recs.append(dict(ok, label=g, doc_id=f"{g}-{i}"))
        r = eval_readiness(recs)
        print(f"   등급별 {n:2d}건(총 {len(recs):3d}) -> ready={r.get('ready')} · "
              f"per_grade={r.get('per_grade')}")

    print("\n=== 4) 현재 실제 상태 — locked 이 몇 건인가\n")
    try:
        from koipa.services.golden_build_service import GoldenBuildService

        svc = GoldenBuildService()
        jobs = svc.list_jobs() if hasattr(svc, "list_jobs") else None
        print(f"   골든 빌드 잡: {len(jobs) if jobs is not None else '조회 불가'}")
        if jobs:
            for j in (jobs[:5] if isinstance(jobs, list) else []):
                print(f"     {j}")
    except Exception as exc:  # noqa: BLE001
        print(f"   [golden build service] {type(exc).__name__}: {exc}")

    # 정본 골든 파일에서 tier 를 세어 본다
    import json

    for p in ("datasets/gold_real/classification_gold.jsonl",
              "datasets/gold_real/holdout_business.jsonl"):
        f = Path(p)
        if not f.exists():
            continue
        rows = [json.loads(l) for l in f.read_text("utf-8").splitlines() if l.strip()]
        locked = [r for r in rows if is_locked_eval(r)]
        from collections import Counter

        print(f"   {p}: {len(rows)}건 · locked {len(locked)}건 · "
              f"tier {dict(Counter(tier_of(r) for r in rows))}")

    print("\n결론은 아래 세 줄로 읽는다:")
    print("  · 서명 없이는 tier 가 locked 가 되지 않는다(2번)")
    print("  · 등급별 5건이면 ready 가 열린다(3번)")
    print("  · 지금 locked 이 0 이면 게이트는 아직 닫혀 있다(4번)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
