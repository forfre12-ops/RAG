"""
P1 분류기 학습기.
- 입력: JSONL (text, label) → labeled/{train,val,test}.jsonl
- 모델: KF-DeBERTa-base 기본 (config에서 변경)
- 출력: model_dir + MLflow 로그 + 평가 리포트(JSON)
- KPI: F1-macro, FNR-overall, FNR per grade
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

import numpy as np
from sklearn.metrics import (
    accuracy_score, precision_recall_fscore_support,
    confusion_matrix, classification_report,
)

from lloydk.schemas.common import Grade

_LABEL_LIST: list[str] = [g.value for g in (Grade.TS, Grade.S1, Grade.S2, Grade.S3)]
_LABEL2ID = {label: i for i, label in enumerate(_LABEL_LIST)}
_ID2LABEL = {i: label for label, i in _LABEL2ID.items()}


@dataclass
class TrainSpec:
    base_model: str = "kakaobank/kf-deberta-base"
    train_path: str = "datasets/labeled/train.jsonl"
    val_path: str = "datasets/labeled/val.jsonl"
    test_path: str = "datasets/labeled/test.jsonl"
    output_dir: str = "artifacts/classifier"
    max_seq_len: int = 512
    batch_size: int = 8
    epochs: int = 5
    lr: float = 2e-5
    weight_decay: float = 0.01
    warmup_ratio: float = 0.1
    class_weighted: bool = True
    seed: int = 42
    experiment_name: str = "kipra-classifier"


@dataclass
class TrainReport:
    model_version: str
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    fnr_overall: float
    fnr_by_grade: dict[str, float]
    confusion_matrix: list[list[int]] = field(default_factory=list)
    classification_report: str = ""


def _load_jsonl(path: str) -> tuple[list[str], list[int]]:
    texts, labels = [], []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        texts.append(row["text"])
        labels.append(_LABEL2ID[row["label"]])
    return texts, labels


def _compute_fnr(cm: np.ndarray) -> tuple[float, dict[str, float]]:
    fnr_by = {}
    fn_total, pos_total = 0, 0
    for i, name in _ID2LABEL.items():
        row_sum = cm[i].sum()
        tp = cm[i, i]
        fn = row_sum - tp
        fnr_by[name] = float(fn / row_sum) if row_sum else 0.0
        fn_total += fn
        pos_total += row_sum
    overall = float(fn_total / pos_total) if pos_total else 0.0
    return overall, fnr_by


def train_classifier(spec: Optional[TrainSpec] = None) -> TrainReport:
    spec = spec or TrainSpec()

    import torch
    from datasets import Dataset
    from transformers import (
        AutoTokenizer, AutoModelForSequenceClassification,
        TrainingArguments, Trainer, DataCollatorWithPadding,
    )
    import mlflow

    mlflow.set_experiment(spec.experiment_name)

    train_x, train_y = _load_jsonl(spec.train_path)
    val_x, val_y = _load_jsonl(spec.val_path)
    test_x, test_y = _load_jsonl(spec.test_path)

    tok = AutoTokenizer.from_pretrained(spec.base_model)

    def tokenize(batch):
        return tok(batch["text"], truncation=True, max_length=spec.max_seq_len)

    ds_train = Dataset.from_dict({"text": train_x, "label": train_y}).map(tokenize, batched=True)
    ds_val = Dataset.from_dict({"text": val_x, "label": val_y}).map(tokenize, batched=True)
    ds_test = Dataset.from_dict({"text": test_x, "label": test_y}).map(tokenize, batched=True)

    model = AutoModelForSequenceClassification.from_pretrained(
        spec.base_model,
        num_labels=len(_LABEL_LIST),
        id2label=_ID2LABEL,
        label2id=_LABEL2ID,
    )

    # class weight (불균형 보정)
    if spec.class_weighted:
        counts = np.bincount(train_y, minlength=len(_LABEL_LIST))
        weights = counts.sum() / (len(_LABEL_LIST) * np.maximum(counts, 1))
        class_weights = torch.tensor(weights, dtype=torch.float32)
    else:
        class_weights = None

    class WeightedTrainer(Trainer):
        def compute_loss(self, model, inputs, return_outputs=False, **kwargs):
            labels = inputs.pop("labels")
            outputs = model(**inputs)
            logits = outputs.logits
            if class_weights is not None:
                loss_fct = torch.nn.CrossEntropyLoss(weight=class_weights.to(logits.device))
            else:
                loss_fct = torch.nn.CrossEntropyLoss()
            loss = loss_fct(logits, labels)
            return (loss, outputs) if return_outputs else loss

    def compute_metrics(eval_pred):
        preds = np.argmax(eval_pred.predictions, axis=-1)
        labels = eval_pred.label_ids
        acc = accuracy_score(labels, preds)
        p, r, f, _ = precision_recall_fscore_support(labels, preds, average="macro", zero_division=0)
        cm = confusion_matrix(labels, preds, labels=list(_ID2LABEL.keys()))
        fnr, fnr_by = _compute_fnr(cm)
        return {
            "accuracy": acc, "precision_macro": p, "recall_macro": r,
            "f1_macro": f, "fnr_overall": fnr,
            **{f"fnr_{name}": v for name, v in fnr_by.items()},
        }

    args = TrainingArguments(
        output_dir=spec.output_dir,
        num_train_epochs=spec.epochs,
        per_device_train_batch_size=spec.batch_size,
        per_device_eval_batch_size=spec.batch_size,
        learning_rate=spec.lr,
        weight_decay=spec.weight_decay,
        warmup_ratio=spec.warmup_ratio,
        eval_strategy="epoch",
        save_strategy="epoch",
        load_best_model_at_end=True,
        metric_for_best_model="fnr_overall",
        greater_is_better=False,
        logging_steps=20,
        seed=spec.seed,
        report_to=["mlflow"],
    )

    # transformers v5+ 호환: Trainer.__init__ 가 `tokenizer` → `processing_class`로 이름
    # 변경됨. v4 에서는 tokenizer 가, v5 에서는 processing_class 가 표준.
    # 두 경로 모두 시도 (런타임 버전 호환).
    try:
        trainer = WeightedTrainer(
            model=model, args=args,
            train_dataset=ds_train, eval_dataset=ds_val,
            processing_class=tok,
            data_collator=DataCollatorWithPadding(tok),
            compute_metrics=compute_metrics,
        )
    except TypeError:  # v4 폴백
        trainer = WeightedTrainer(
            model=model, args=args,
            train_dataset=ds_train, eval_dataset=ds_val,
            tokenizer=tok, data_collator=DataCollatorWithPadding(tok),
            compute_metrics=compute_metrics,
        )

    with mlflow.start_run() as run:
        mlflow.log_params(asdict(spec))
        trainer.train()

        pred_out = trainer.predict(ds_test)
        preds = np.argmax(pred_out.predictions, axis=-1)
        cm = confusion_matrix(test_y, preds, labels=list(_ID2LABEL.keys()))
        fnr, fnr_by = _compute_fnr(cm)
        acc = accuracy_score(test_y, preds)
        p, r, f, _ = precision_recall_fscore_support(test_y, preds, average="macro", zero_division=0)
        report = classification_report(test_y, preds, target_names=_LABEL_LIST, zero_division=0)

        out_dir = Path(spec.output_dir) / f"v-{run.info.run_id[:8]}"
        out_dir.mkdir(parents=True, exist_ok=True)
        trainer.save_model(str(out_dir))
        tok.save_pretrained(str(out_dir))

        result = TrainReport(
            model_version=f"v-{run.info.run_id[:8]}",
            accuracy=float(acc),
            precision_macro=float(p),
            recall_macro=float(r),
            f1_macro=float(f),
            fnr_overall=float(fnr),
            fnr_by_grade=fnr_by,
            confusion_matrix=cm.tolist(),
            classification_report=report,
        )
        (out_dir / "report.json").write_text(
            json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        mlflow.log_metrics({
            "test_accuracy": acc, "test_f1_macro": float(f),
            "test_fnr_overall": float(fnr),
            **{f"test_fnr_{k}": v for k, v in fnr_by.items()},
        })
        mlflow.log_artifact(str(out_dir / "report.json"))
        return result
