"""누출 게이트용 train 텍스트해시 매니페스트 생성.

대용량/미추적 학습풀(train_subset.jsonl 등은 gitignore)을 CI에 커밋하지 않고도
holdout↔train 텍스트해시 누출검사를 실제로 수행(fake-green 차단)하게 만든다. 해시는
check_data_quality 의 _text·_sha1 을 그대로 재사용하므로 검사 측과 100% 패리티다.

사용:
  python scripts/build_leakage_manifest.py \
      --train-pool datasets/gold_real/train_subset.jsonl \
      --out datasets/gold_real/train_subset.hashes.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 검사 스크립트와 동일한 _text·_sha1 을 재사용(해시 패리티) — scripts/ 를 경로에 등록.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from check_data_quality import _sha1, _text, load_jsonl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="누출 게이트용 train 텍스트해시 매니페스트 생성")
    ap.add_argument("--train-pool", required=True, help="학습 풀 JSONL")
    ap.add_argument("--out", required=True, help="출력 매니페스트 경로(.txt)")
    args = ap.parse_args()

    tp = Path(args.train_pool)
    if not tp.exists():
        print(f"train-pool 없음: {tp}", file=sys.stderr)
        return 1

    records = load_jsonl(tp)
    hashes = sorted({_sha1(_text(r)) for r in records})
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = (
        f"# leakage manifest — {tp.name} 정규화 텍스트해시(레코드 {len(records)} → 고유 {len(hashes)}건).\n"
        f"# check_data_quality._text/_sha1 로 생성(검사 측과 패리티). 원본 학습풀은 gitignore(대용량).\n"
        f"# 재생성: make leakage-manifest\n"
    )
    out.write_text(header + "\n".join(hashes) + "\n", encoding="utf-8")
    print(f"wrote {len(hashes)} unique hashes (from {len(records)} records) -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
