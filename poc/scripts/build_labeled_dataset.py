"""
검수 통과한 합성 문서(.json) + 공개 데이터 라벨 → train/val/test.jsonl 통합 빌더.

사용:
  python scripts/build_labeled_dataset.py \
    --synth-dir datasets/synthetic \
    --extra datasets/external/labels.jsonl \
    --out datasets/labeled --seed 42
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--synth-dir", default="datasets/synthetic/accepted",
                    help="검수 완료 합성 JSON 디렉토리 (Phase 4 이후: synthetic/accepted/)")
    ap.add_argument("--extra", default=None,
                    help="추가 라벨 JSONL ({text,label,...} per line). labeled_v2_balanced/train.jsonl 권장")
    ap.add_argument("--out", default="datasets/labeled")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--ratio", default="0.7,0.15,0.15")
    ap.add_argument(
        "--include-all",
        action="store_true",
        help="status 필드 무관 모든 합성 문서 포함 (검수 통과 가정). p3 합성 결과를 직접 학습에 쓸 때 유용.",
    )
    args = ap.parse_args()

    rng = random.Random(args.seed)
    by_label: dict[str, list[dict]] = defaultdict(list)

    # synthetic (approved 만)
    synth_root = Path(args.synth_dir)
    if synth_root.exists():
        for p in synth_root.rglob("*.json"):
            row = json.loads(p.read_text(encoding="utf-8"))
            if not args.include_all and row.get("status") not in {"approved", "pending"}:
                continue
            text = (row.get("title", "") + "\n" + row.get("body", "")).strip()
            label = row["target_grade"]
            by_label[label].append({
                "doc_id": row["synth_id"],
                "text": text,
                "label": label,
                "source": "synthetic",
                "domain": row.get("domain"),
            })

    # extra labeled jsonl
    if args.extra and Path(args.extra).exists():
        for line in Path(args.extra).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            by_label[row["label"]].append(row)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tr, va, te = (float(x) for x in args.ratio.split(","))
    train, val, test = [], [], []
    for label, rows in by_label.items():
        rng.shuffle(rows)
        n = len(rows)
        n_tr = int(n * tr)
        n_va = int(n * va)
        train += rows[:n_tr]
        val += rows[n_tr:n_tr + n_va]
        test += rows[n_tr + n_va:]
        print(f"{label}: total={n} tr={n_tr} va={n_va} te={n - n_tr - n_va}")

    rng.shuffle(train)
    rng.shuffle(val)
    rng.shuffle(test)
    for name, rows in [("train", train), ("val", val), ("test", test)]:
        path = out / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"  {path}: {len(rows)}")


if __name__ == "__main__":
    main()
