"""
P1 PoC — KF-DeBERTa 기반 분류기 학습.

사용:
  python scripts/p1_train_classifier.py \
    --train datasets/labeled/train.jsonl \
    --val   datasets/labeled/val.jsonl \
    --test  datasets/labeled/test.jsonl \
    --epochs 5
"""
import argparse
from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-model", default="kakaobank/kf-deberta-base")
    ap.add_argument("--train", default="datasets/labeled/train.jsonl")
    ap.add_argument("--val", default="datasets/labeled/val.jsonl")
    ap.add_argument("--test", default="datasets/labeled/test.jsonl")
    ap.add_argument("--out", default="artifacts/classifier")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--max-seq-len", type=int, default=512)
    ap.add_argument("--lr", type=float, default=2e-5)
    args = ap.parse_args()

    spec = TrainSpec(
        base_model=args.base_model,
        train_path=args.train, val_path=args.val, test_path=args.test,
        output_dir=args.out, epochs=args.epochs,
        batch_size=args.batch_size, max_seq_len=args.max_seq_len, lr=args.lr,
    )
    report = train_classifier(spec)
    print("=" * 60)
    print(f"model_version: {report.model_version}")
    print(f"accuracy:      {report.accuracy:.4f}")
    print(f"f1_macro:      {report.f1_macro:.4f}")
    print(f"fnr_overall:   {report.fnr_overall:.4f}  <-- KPI")
    for k, v in report.fnr_by_grade.items():
        print(f"  fnr_{k}: {v:.4f}")
    print(report.classification_report)


if __name__ == "__main__":
    main()
