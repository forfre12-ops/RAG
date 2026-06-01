"""P1 재학습 with 6도메인 균형 rag_corpus_v2.

rag_corpus_v2 720건 (label_match=True) → JSONL 변환 → labeled_oss_v1과 합쳐 재학습.
"""
import json
import os
import sys
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

CORPUS_DIR = Path("datasets/rag_corpus_v2")
OSS_TRAIN  = Path("datasets/labeled_oss_v1/train.jsonl")
OUT_DIR    = Path("datasets/labeled_v2_balanced")
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    # 1. rag_corpus_v2 → JSONL (label_match=True만)
    files   = sorted(CORPUS_DIR.glob("*.json"))
    new_rows = []
    skipped  = 0
    for f in files:
        d = json.loads(f.read_text("utf-8"))
        if not d.get("label_match", False):
            skipped += 1
            continue
        body = d.get("body", "").strip()
        if not body:
            skipped += 1
            continue
        new_rows.append({"text": body, "label": d["target_grade"],
                          "domain": d.get("domain", ""), "source": "rag_corpus_v2"})

    print(f"rag_corpus_v2: {len(new_rows)}건 사용 / {skipped}건 스킵(label_match=False)")

    # 2. OSS train 병합
    oss_rows = [json.loads(l) for l in OSS_TRAIN.read_text("utf-8").splitlines() if l.strip()]
    print(f"OSS train: {len(oss_rows)}건")

    combined = oss_rows + new_rows
    out_train = OUT_DIR / "train.jsonl"
    out_train.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in combined), encoding="utf-8")
    print(f"병합 train: {len(combined)}건 → {out_train}")

    # 3. 재학습 (5 epochs)
    from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier

    spec = TrainSpec(
        train_path=str(out_train),
        val_path="datasets/labeled_oss_v1/val.jsonl",
        test_path="datasets/labeled_oss_v1/test.jsonl",
        epochs=5,
        output_dir="artifacts/classifier_v2_ep5",
    )
    print(f"\n학습 시작: epochs={spec.epochs} train={len(combined)}건")
    report = train_classifier(spec)
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))

    print(f"\n=== P1 재학습 완료 ===")
    print(f"F1-macro: {report.f1_macro:.4f}")
    print(f"FNR-overall: {report.fnr_overall:.4f} ({report.fnr_overall*100:.1f}%)")
    print(f"FNR-TS: {report.fnr_by_grade.get('TS',0)*100:.1f}%")


if __name__ == "__main__":
    main()
