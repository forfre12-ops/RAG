"""기존 labeled_oss_v1/train.jsonl + synthetic_ts_augment 84건 병합."""
import json
import os
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)

BASE_TRAIN = Path("datasets/labeled_oss_v1/train.jsonl")
AUGMENT_DIR = Path("datasets/synthetic_ts_augment")
OUT_PATH = Path("datasets/labeled_oss_ts_augmented/train.jsonl")
OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

rows = []

# 기존 OSS train
for line in BASE_TRAIN.read_text("utf-8").splitlines():
    line = line.strip()
    if line:
        rows.append(json.loads(line))

base_count = len(rows)

# TS 합성 84건 → jsonl 형식으로 변환
aug_count = 0
for f in sorted(AUGMENT_DIR.glob("*.json")):
    d = json.loads(f.read_text("utf-8"))
    rows.append({
        "text": d.get("body", ""),
        "label": "TS",
    })
    aug_count += 1

OUT_PATH.write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in rows),
    encoding="utf-8"
)

print(f"기존 OSS train: {base_count}건")
print(f"TS 합성 추가:   {aug_count}건")
print(f"병합 총계:      {len(rows)}건 → {OUT_PATH}")
