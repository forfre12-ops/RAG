"""Step 3 (doc/32 후속): step2 학습셋 + 비민감 일상문서 S3(mundane_s3)를 병합.
목적: 모델의 S3가 '판례체'에만 갇혀 일상문서를 과분류(80%)하는 문제를 고치기 위해
'평범한 업무문서 = S3'를 학습에 주입. 특허로 TS/S1 갭 메운 것과 대칭의 S3 갭 메우기.
mundane은 업웨이트(--repeat)로 신호 보강. 일부는 in-distribution 점검용 홀드아웃으로 분리.
출력: datasets/labeled_p1_retrain_v4_step3/train.jsonl
"""
from __future__ import annotations
import argparse, json, random
from collections import Counter
from pathlib import Path


def _read(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--step2-train", default="datasets/labeled_p1_retrain_v4_step2/train.jsonl")
    ap.add_argument("--mundane", default="datasets/mundane_s3/raw.jsonl")
    ap.add_argument("--out-train", default="datasets/labeled_p1_retrain_v4_step3/train.jsonl")
    ap.add_argument("--out-mundane-holdout", default="datasets/mundane_s3/holdout.jsonl")
    ap.add_argument("--repeat", type=int, default=3, help="mundane S3 업웨이트 반복")
    ap.add_argument("--holdout-frac", type=float, default=0.1, help="in-distribution 점검용 분리")
    ap.add_argument("--seed", type=int, default=20260603)
    a = ap.parse_args()

    base = _read(Path(a.step2_train))
    mund = [{"text": r["text"], "label": "S3"} for r in _read(Path(a.mundane)) if (r.get("text") or "").strip()]
    rng = random.Random(a.seed); rng.shuffle(mund)
    k = int(len(mund) * a.holdout_frac)
    hold, train_m = mund[:k], mund[k:]

    merged = base + train_m * a.repeat
    rng.shuffle(merged)

    out = Path(a.out_train); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged) + "\n", encoding="utf-8")
    Path(a.out_mundane_holdout).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in hold) + "\n", encoding="utf-8")

    print(f"[step3] base(step2)={len(base)}  mundane={len(mund)} (train {len(train_m)}×{a.repeat} + holdout {len(hold)})")
    print(f"[step3] merged train={len(merged)}  labels={dict(Counter(r['label'] for r in merged))}")
    print(f"[step3] wrote {out}")


if __name__ == "__main__":
    raise SystemExit(main())
