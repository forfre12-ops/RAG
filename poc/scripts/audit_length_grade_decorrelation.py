"""길이-등급 독립성 검사 — length_only_accuracy(실측) vs 라벨섞음평균(N회) vs 차이.

2026-10-10 결정("길이·표 유무를 등급과 무관하게 배정하면 지름길이 안 생긴다")이
실제 생성물(56건 파일럿: 실측 0.2679 · 라벨섞음평균 0.2408 · 차이 +2.7%p)에서 성립했던 것과
같은 방법으로, 본 생성(1,001건)에서도 성립하는지 재확인한다. 가상 시뮬레이션이 아니라
실제 문서의 실측 body_len을 쓴다.

정본 함수 koipa.dataset_leakage.length_only_accuracy 를 그대로 import해 쓴다(재구현 금지 —
[[counting-tools-hand-picked-scope-2026-09-08]]). 판정 임계값은 찍지 않는다 — 9/12 교훈
([[permutation-baseline-not-majority-2026-09-12]])에 따라 실측·섞음·차이 세 값만 내고
판단은 사람이 한다(절대 임계 0.55를 재도입하지 않기 위함, [[length-grade-decorrelation-fixes-datagen-tension-2026-10-10]]).

입력 파일은 [{"grade": "TS"|"S1"|"S2"|"S3", "body_len": int, ...}, ...] 형태의 JSON 배열이면
된다(지금은 full1001_20261010/audit_results.json의 필드를 그대로 재사용 — 새로 세지 않음).

사용:
    poc/.venv/Scripts/python.exe -X utf8 scripts/audit_length_grade_decorrelation.py \
        [--records reports/full1001_20261010/audit_results.json] [--n-perms 300] [--seed 20261011]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.dataset_leakage import length_only_accuracy  # noqa: E402

GRADES = ("TS", "S1", "S2", "S3")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--records",
        default=str(POC / "reports" / "full1001_20261010" / "audit_results.json"),
    )
    ap.add_argument("--n-perms", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261011)
    args = ap.parse_args()

    with open(args.records, encoding="utf-8") as f:
        records = json.load(f)

    rows = [
        (r["body_len"], r["grade"])
        for r in records
        if r.get("grade") in GRADES and r.get("body_len")
    ]
    skipped = len(records) - len(rows)
    lengths = [r[0] for r in rows]
    grades = [r[1] for r in rows]

    real = length_only_accuracy(rows)

    rng = np.random.default_rng(args.seed)
    perm_accs = []
    for _ in range(args.n_perms):
        shuffled = list(grades)
        rng.shuffle(shuffled)
        perm_accs.append(length_only_accuracy(list(zip(lengths, shuffled))))
    perm_mean = float(np.mean(perm_accs))
    diff = real - perm_mean

    print(f"입력: {args.records}")
    print(f"문서 {len(rows)}건 사용 (등급/길이 결측 제외 {skipped}건)")
    print(f"등급 분포: {dict(Counter(grades))}")
    print(
        f"[길이만으로 등급 맞히기 · 최근접이웃] "
        f"실측 {real:.4f} · 라벨섞음평균({args.n_perms}회) {perm_mean:.4f} · "
        f"차이 {diff:+.4f} ({diff * 100:+.2f}%p)"
    )

    table_rows = [
        (1 if r.get("table_actual") else 0, r["grade"])
        for r in records
        if r.get("grade") in GRADES
    ]
    table_rate_by_grade = {}
    if table_rows:
        for g in GRADES:
            vals = [t for t, gg in table_rows if gg == g]
            if vals:
                table_rate_by_grade[g] = round(sum(vals) / len(vals), 4)
        print(f"등급별 표 포함 비율(등급과 무관해야 함, 참고): {table_rate_by_grade}")

    out = {
        "input": str(args.records),
        "n_docs": len(rows),
        "n_skipped": skipped,
        "grade_counts": dict(Counter(grades)),
        "length_only_accuracy_real": real,
        "length_only_accuracy_permuted_mean": perm_mean,
        "n_perms": args.n_perms,
        "seed": args.seed,
        "diff": diff,
        "diff_pp": diff * 100,
        "table_inclusion_rate_by_grade": table_rate_by_grade or None,
    }
    out_path = Path(args.records).parent / "length_grade_decorrelation_audit.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장 -> {out_path}")


if __name__ == "__main__":
    main()
