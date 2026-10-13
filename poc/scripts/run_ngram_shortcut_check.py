"""임의의 JSONL(들)에 대해 정식 도구 measure_ngram_shortcuts.measure() 를 돌리는 얇은 드라이버.

measure_ngram_shortcuts.py 자체는 재사용 가능한 라이브러리 함수만 제공하고 독립 CLI가 없다
(customer-guide 전용 래퍼만 있음). 이 스크립트는 그 함수를 그대로 불러 쓸 뿐 로직을 새로
짜지 않는다 — family_id 있으면 family-분리 CV 도 함께 낸다(measure() 가 알아서 처리).

사용:
    python scripts/run_ngram_shortcut_check.py --data datasets/labeled_p1_v5_clean/train.jsonl --seeds 5
    python scripts/run_ngram_shortcut_check.py --data a.jsonl --data b.jsonl --label combined --seeds 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_ngram_shortcuts import measure  # noqa: E402

FAMILY_FIELDS = ("family_id", "document_family_id")


def load_rows(paths: list[str]) -> list[dict]:
    rows = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                text, label = d.get("text"), d.get("label")
                if not text or label not in {"TS", "S1", "S2", "S3"}:
                    continue
                fam = next((d[k] for k in FAMILY_FIELDS if d.get(k)), "")
                rows.append({"text": text, "label": label, "family_id": fam})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data", action="append", required=True, help="jsonl 경로, 여러 번 지정 가능(합쳐서 잰다)")
    ap.add_argument("--label", default=None, help="출력용 라벨(예: v5_clean). 기본값: 첫 --data 파일명")
    ap.add_argument("--seeds", type=int, default=5)
    args = ap.parse_args()

    rows = load_rows(args.data)
    name = args.label or Path(args.data[0]).stem
    print(f"[{name}] n={len(rows)} (라벨+본문 있는 것만)")
    if not rows:
        print("데이터 없음")
        return 1

    result = measure(rows, seeds=args.seeds)
    print(f"[{name}] stratified_cv: mean={result['stratified_cv']['mean']:.1%} "
          f"permutation_baseline={result['stratified_cv']['permutation_mean']:.1%} "
          f"excess_pp={result['stratified_cv']['excess_pp']:.1f}pp "
          f"(min={result['stratified_cv']['min']:.1%} max={result['stratified_cv']['max']:.1%}, "
          f"{result['stratified_cv']['folds']}-fold x {args.seeds}seeds)")
    fam = result.get("family_cv", {})
    if fam.get("status") == "MEASURED":
        print(f"[{name}] family_cv(그룹분리): mean={fam['mean']:.1%} "
              f"permutation_baseline={fam['permutation_mean']:.1%} excess_pp={fam['excess_pp']:.1f}pp")
    else:
        print(f"[{name}] family_cv: {fam.get('status', 'NOT_RUN')} (family_id 없음)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
