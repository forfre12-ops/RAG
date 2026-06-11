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
import re
from collections import defaultdict
from pathlib import Path


def _norm_key(text: str) -> str:
    """[M-dataset-leak] 분할 누수 방지용 정규화 키.

    합성 m1·--extra JSONL에는 동일/유사 텍스트가 빈번해, 정규화 없이 라벨 내 셔플 후
    비율 슬라이스하면 같은 내용이 train/test를 가로질러 누수(평가 낙관 편향)된다.
    공백을 모두 제거한 텍스트를 키로 써서 같은 내용이 한 split에만 가게 한다.
    """
    return re.sub(r"\s+", "", text or "")


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
    total_dropped = 0
    for label, rows in by_label.items():
        # [M-dataset-leak] 분할 전에 정규화 텍스트 키로 dedup — 동일 내용이 train/test
        # 양쪽에 걸려 평가가 낙관적으로 편향되는 것을 차단. 첫 등장 행만 유지.
        seen: set[str] = set()
        unique_rows = []
        for row in rows:
            key = _norm_key(row.get("text", ""))
            if key and key in seen:
                continue
            seen.add(key)
            unique_rows.append(row)
        dropped = len(rows) - len(unique_rows)
        total_dropped += dropped
        rows = unique_rows
        rng.shuffle(rows)
        n = len(rows)
        n_tr = int(n * tr)
        n_va = int(n * va)
        train += rows[:n_tr]
        val += rows[n_tr:n_tr + n_va]
        test += rows[n_tr + n_va:]
        print(f"{label}: total={n} (deduped -{dropped}) tr={n_tr} va={n_va} te={n - n_tr - n_va}")
    if total_dropped:
        print(f"dedup: dropped {total_dropped} duplicate rows (split-leak guard)")

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
