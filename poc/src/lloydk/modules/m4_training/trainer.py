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
    # FNR 비대칭 cost — 고등급(TS/S1) 표본의 손실 가중치를 이 배수만큼 추가 증폭.
    # 1.0 = 기존 균형 가중치 그대로(동작 변화 없음). 2.0~3.0 권장(미탐을 더 강하게 벌점).
    # 효과: 고등급 오분류 비용↑ → recall↑(FNR↓), 단 저등급 과분류↑ trade.
    fnr_cost_multiplier: float = 1.0
    # 고등급 코드(미탐 핵심 대상). class weight 증폭 + fnr_high 지표 계산 기준.
    high_grade_codes: tuple[str, ...] = ("TS", "S1")
    # best 모델 선택 지표. 기본을 fnr_overall → fnr_high(고등급 미탐)로 변경:
    # 실제 KPI(고등급을 저등급으로 놓치지 않기)와 최적화 타깃을 일치시킨다.
    early_stop_metric: str = "fnr_high"
    seed: int = 42
    experiment_name: str = "kipra-classifier"
    bf16: bool = True           # bf16 가속 (CUDA GPU 필요; GPU 없으면 자동 비활성)
    use_mlflow: bool = True     # MLflow 로깅 (서버 없으면 False 권장)
    logging_steps: int = 20


@dataclass
class TrainReport:
    model_version: str
    accuracy: float
    precision_macro: float
    recall_macro: float
    f1_macro: float
    fnr_overall: float
    fnr_by_grade: dict[str, float]
    fnr_high: float = 0.0   # 고등급(TS/S1) 미탐율 — 핵심 KPI
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


def _compute_fnr_high(cm: np.ndarray, high_ids: list[int]) -> float:
    """고등급 미탐율 = 진짜 TS/S1을 '덜 심각한' 등급(id가 더 큰 쪽)으로 예측한 비율.

    _LABEL_LIST = [TS, S1, S2, S3] 이므로 id가 클수록 덜 심각. 실제 보안 KPI와 일치.
    """
    fn_under, pos = 0, 0
    n = cm.shape[0]
    for i in high_ids:
        pos += int(cm[i].sum())
        fn_under += int(sum(cm[i, j] for j in range(n) if j > i))
    return float(fn_under / pos) if pos else 0.0


def train_classifier(spec: Optional[TrainSpec] = None) -> TrainReport:
    spec = spec or TrainSpec()

    import torch
    from datasets import Dataset
    from transformers import (
        AutoTokenizer, AutoModelForSequenceClassification,
        TrainingArguments, Trainer, DataCollatorWithPadding,
    )
    import mlflow

    # B1-4 (2026-05-30 후속 정리): safetensors auto-conversion 백그라운드 thread 가
    # HF Hub `discussions` 비활성 레포(kakaobank 등)에서 403 Forbidden 트레이스를
    # stderr 에 쏟는 노이즈 제거. 학습 자체에는 영향 없는 무해 경고.
    import logging as _logging  # noqa: PLC0415
    for _name in (
        "transformers.safetensors_conversion",
        "huggingface_hub.utils._http",
    ):
        _logging.getLogger(_name).setLevel(_logging.CRITICAL)

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

    # 고등급 id (TS/S1 등) — 미탐 비대칭 가중·fnr_high 계산 공통 기준
    high_ids = [_LABEL2ID[c] for c in spec.high_grade_codes if c in _LABEL2ID]

    # class weight (불균형 보정) + FNR 비대칭 cost
    if spec.class_weighted:
        counts = np.bincount(train_y, minlength=len(_LABEL_LIST))
        weights = counts.sum() / (len(_LABEL_LIST) * np.maximum(counts, 1))
        # 고등급 표본의 손실을 추가 증폭 → 미탐(고등급을 저등급으로) 비용 ↑
        if spec.fnr_cost_multiplier != 1.0:
            for hid in high_ids:
                weights[hid] *= spec.fnr_cost_multiplier
        class_weights = torch.tensor(weights, dtype=torch.float32)
    elif spec.fnr_cost_multiplier != 1.0:
        # 균형 가중치를 끈 경우라도 비대칭 cost는 적용 (고등급만 배수, 나머지 1.0)
        weights = np.ones(len(_LABEL_LIST), dtype=np.float32)
        for hid in high_ids:
            weights[hid] *= spec.fnr_cost_multiplier
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
        fnr_high = _compute_fnr_high(cm, high_ids)
        return {
            "accuracy": acc, "precision_macro": p, "recall_macro": r,
            "f1_macro": f, "fnr_overall": fnr, "fnr_high": fnr_high,
            **{f"fnr_{name}": v for name, v in fnr_by.items()},
        }

    use_bf16 = spec.bf16 and torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    report_to = ["mlflow"] if spec.use_mlflow else []

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
        metric_for_best_model=spec.early_stop_metric,
        greater_is_better=False,
        logging_steps=spec.logging_steps,
        seed=spec.seed,
        bf16=use_bf16,
        report_to=report_to,
        dataloader_num_workers=0,   # Windows deadlock 방지
        dataloader_pin_memory=False,
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
        fnr_high = _compute_fnr_high(cm, high_ids)
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
            fnr_high=float(fnr_high),
            confusion_matrix=cm.tolist(),
            classification_report=report,
        )
        (out_dir / "report.json").write_text(
            json.dumps(asdict(result), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        mlflow.log_metrics({
            "test_accuracy": acc, "test_f1_macro": float(f),
            "test_fnr_overall": float(fnr), "test_fnr_high": float(fnr_high),
            **{f"test_fnr_{k}": v for k, v in fnr_by.items()},
        })
        mlflow.log_artifact(str(out_dir / "report.json"))
        return result
