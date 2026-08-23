"""holdout/test에서 train에 텍스트 누출된 문서를 제거해 클린본을 emit.

배경: train_subset(610)의 텍스트가 classification_gold/holdout에 doc_id만 다른 채
중복돼 있어, holdout_eval 109건 중 67건(42%)이 학습 누출 상태였음(평가 무효화).
본 스크립트는 train 텍스트해시에 걸리는 문서를 holdout에서 제거하고
<stem>.clean.jsonl 로 저장한다(원본은 보존). check_data_quality.py 의 누출 게이트와 짝.

usage:
  python scripts/clean_holdout_leakage.py
  python scripts/clean_holdout_leakage.py --train datasets/gold_real/train_subset.jsonl \
      --holdout datasets/gold_real/holdout_eval.jsonl,datasets/gold_real/holdout_business.jsonl
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import sys
from collections import Counter
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent


def load(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.startswith("#")]


def text_of(r: dict) -> str:
    return (r.get("text") or (r.get("title", "") + " " + r.get("body", ""))).strip()


def sha(t: str) -> str:
    return hashlib.sha1(t.encode("utf-8", "ignore")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description="holdout 누출 제거")
    ap.add_argument("--train", default="datasets/gold_real/train_subset.jsonl")
    ap.add_argument("--holdout",
                    default="datasets/gold_real/holdout_eval.jsonl,datasets/gold_real/holdout_business.jsonl")
    ap.add_argument("--suffix", default=".clean.jsonl")
    args = ap.parse_args()

    tp = ROOT / args.train
    if not tp.exists():
        print(f"[ERROR] train 없음: {tp}", file=sys.stderr)
        return 2
    train_hashes = {sha(text_of(r)) for r in load(tp)}
    print(f"[train] {tp.name}: {len(train_hashes)} 텍스트해시")

    any_leak = False
    for hpath in [h.strip() for h in args.holdout.split(",") if h.strip()]:
        hp = ROOT / hpath
        if not hp.exists():
            print(f"[SKIP] {hp} 없음", file=sys.stderr)
            continue
        recs = load(hp)
        clean = [r for r in recs if sha(text_of(r)) not in train_hashes]
        leaked = [r for r in recs if sha(text_of(r)) in train_hashes]
        if leaked:
            any_leak = True
        out = hp.with_name(hp.stem + args.suffix)
        out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in clean),
                       encoding="utf-8")
        dist_before = dict(Counter(r.get("label") for r in recs))
        dist_after = dict(Counter(r.get("label") for r in clean))
        print(f"\n[{hp.name}] {len(recs)} → clean {len(clean)} (누출 {len(leaked)} 제거)")
        print(f"  라벨 before: {dist_before}")
        print(f"  라벨 after : {dist_after}")
        print(f"  → {out}")
        # 제거된 doc_id 기록
        if leaked:
            ids_out = hp.with_name(hp.stem + ".leaked_ids.txt")
            ids_out.write_text("\n".join(str(r.get("doc_id")) for r in leaked), encoding="utf-8")
            print(f"  제거 doc_id 목록 → {ids_out}")

    if not any_leak:
        print("\n[OK] 누출 없음 — 클린본은 원본과 동일")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
