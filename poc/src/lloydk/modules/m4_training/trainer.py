"""
P1 분류기 학습기.
- 입력: JSONL (text, label) → labeled/{train,val,test}.jsonl
- 모델: KF-DeBERTa-base 기본 (config에서 변경)
- 출력: model_dir + MLflow 로그 + 평가 리포트(JSON)
- KPI: F1-macro, FNR-overall, FNR per grade
"""
from __future__ import annotations
from contextlib import nullcontext
import datetime as _dt
import hashlib
import json
import math
import os
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Literal, Optional

import numpy as np
from sklearn.metrics import (
    accuracy_score, precision_recall_fscore_support,
    confusion_matrix, classification_report,
)

from lloydk.schemas.common import Grade

_LABEL_LIST: list[str] = [g.value for g in (Grade.TS, Grade.S1, Grade.S2, Grade.S3)]
_LABEL2ID = {label: i for i, label in enumerate(_LABEL_LIST)}
_ID2LABEL = {i: label for label, i in _LABEL2ID.items()}

TrainInputMode = Literal["auto", "documents", "pre_chunked"]
_PRE_CHUNK_FIELDS = ("chunk_id", "source_doc_id", "chunk_label_strength")
_CHUNK_SENTINEL_FIELDS = (
    "chunk_id",
    "chunk_index",
    "chunk_start",
    "chunk_end",
    "chunk_label_strength",
)


@dataclass(frozen=True)
class _LocalRunInfo:
    run_id: str


@dataclass(frozen=True)
class _LocalRun:
    info: _LocalRunInfo


def _training_run_context(mlflow_module, spec: "TrainSpec"):
    """Return a tracking context without touching MLflow when it is disabled."""
    if spec.use_mlflow:
        mlflow_module.set_experiment(spec.experiment_name)
        return mlflow_module.start_run()
    return nullcontext(_LocalRun(info=_LocalRunInfo(run_id=uuid.uuid4().hex)))


# 학습셋 run 디렉터리 파일명 ↔ 고전 규약 대응. 앞에 있는 것부터 찾는다.
# train 은 청크가 이미 펼쳐진 train_chunks 를 우선한다(로더가 pre_chunked 를 자동 감지).
_RUN_DIR_ALIASES: dict[str, tuple[str, ...]] = {
    "train.jsonl": ("train_chunks.jsonl", "train_documents.jsonl"),
    "val.jsonl": ("validation_documents.jsonl",),
    "test.jsonl": ("calibration_documents.jsonl",),
}


def _dataset_split(name: str) -> str:
    """기본 학습셋 분할 경로 — settings.training_dataset_dir 에서 파생.

    종전엔 'datasets/labeled/...' 가 하드코딩돼 있었는데 그 디렉터리는 리포에도 배포본에도
    없다. 그래서 hyperparams 없이 호출되는 경로(콘솔 「3 재학습 트리거」, POST /train 기본형)가
    배포 서버에서 항상 FileNotFoundError 로 끝났다(2026-08-08 실서버 실측). 설정에서 끌어와
    학습셋 세대 교체 시 코드를 고치지 않게 한다. import 는 지연 — 이 모듈이 config 로드
    시점에 묶이지 않도록.
    """
    try:
        from lloydk.config import settings  # noqa: PLC0415

        base = str(getattr(settings, "training_dataset_dir", "") or "").strip()
    except Exception:  # noqa: BLE001 - 설정 미가용 환경(단독 스크립트)에서도 동작
        base = ""
    base = base or "datasets/labeled_p1_v5_clean"
    classic = Path(base) / name
    if classic.is_file():
        return str(classic)
    # 학습셋 run 디렉터리(materialize_proxy_training_set 산출)는 파일명 규약이 다르다.
    # 그대로 두면 콘솔 「재학습 트리거」가 FileNotFoundError 로 끝나고, workers/tasks.py 가
    # 그걸 "skipped: retrain topology unavailable" 로 흡수해 **마운트 문제처럼 보인다**
    # (2026-08-08 실측). 세대가 바뀔 때마다 파일명을 손으로 맞추지 않도록 여기서 흡수한다.
    for alt in _RUN_DIR_ALIASES.get(name, ()):  # noqa: SIM118
        candidate = Path(base) / alt
        if candidate.is_file():
            return str(candidate)
    return str(classic)


def _build_progress_callback(run_id: str):
    """학습 진행률을 JobStore(run_id 키)에 기록하는 HF TrainerCallback 을 만든다.

    HF TrainerState 가 주는 값을 그대로 쓴다 — 로그 파싱 없음:
        state.global_step / state.max_steps → progress · current_step · total_steps
        state.epoch      / args.num_train_epochs → current_epoch · total_epochs
        경과시간 × 남은스텝/완료스텝 → estimated_finish_at
    기록 실패(Redis 미가용 등)는 학습을 절대 막지 않는다 — 진행률은 부가 정보다.

    쓰기 빈도: 스텝마다 쓰면 13시간 학습에서 1,280회 + eval 마다 Redis 왕복이 붙는다.
    가치가 없는 부하라 _MIN_INTERVAL_SEC 간격으로 스로틀한다(첫 스텝과 마지막은 항상 기록 —
    화면이 "시작했다"와 "끝났다"를 놓치지 않게).
    """
    from transformers import TrainerCallback  # noqa: PLC0415

    _MIN_INTERVAL_SEC = 20.0

    class _ProgressCallback(TrainerCallback):
        def __init__(self) -> None:
            self._last_write = 0.0
            self._started = time.time()

        def _write(self, state, args, *, force: bool = False) -> None:
            now = time.time()
            if not force and (now - self._last_write) < _MIN_INTERVAL_SEC:
                return
            self._last_write = now
            try:
                import uuid as _uuid  # noqa: PLC0415
                from lloydk.services.job_store import get_default_store  # noqa: PLC0415

                total = int(getattr(state, "max_steps", 0) or 0)
                done = int(getattr(state, "global_step", 0) or 0)
                total_ep = int(getattr(args, "num_train_epochs", 0) or 0) if args else 0
                # HF state.epoch 은 "완료한 에폭 수"라 첫 에폭 동안 0.x 다. 그대로 정수화하면
                # 화면에 "에폭 0 / 1" 로 떠서 시작도 안 한 것처럼 보인다 — 지금 고치는 증상과 같다.
                # 사람이 읽는 값은 1-based("1번째 에폭 진행 중")여야 하므로 +1 하고 총수로 clamp
                # 한다(학습 종료 시 epoch == total 이라 그대로 두면 total+1 이 된다).
                cur_ep = None
                if getattr(state, "epoch", None) is not None:
                    cur_ep = int(state.epoch) + 1
                    if total_ep:
                        cur_ep = min(cur_ep, total_ep)
                fields: dict = {
                    "current_step": done,
                    "total_steps": total or None,
                    "current_epoch": cur_ep,
                    "total_epochs": total_ep or None,
                }
                if total > 0:
                    fields["train_progress"] = round(done / total, 4)
                    if done > 0:
                        elapsed = now - self._started
                        remain = elapsed * (total - done) / done
                        fields["estimated_finish_at"] = (
                            _dt.datetime.now(_dt.timezone.utc)
                            + _dt.timedelta(seconds=remain)
                        ).isoformat()
                get_default_store().update(_uuid.UUID(str(run_id)), **fields)
            except Exception:  # noqa: BLE001 — 진행률 기록 실패가 학습을 막지 않는다
                pass

        def on_train_begin(self, args, state, control, **kw):  # noqa: ANN001,ANN003
            self._started = time.time()
            self._write(state, args, force=True)

        def on_step_end(self, args, state, control, **kw):  # noqa: ANN001,ANN003
            self._write(state, args)

        def on_train_end(self, args, state, control, **kw):  # noqa: ANN001,ANN003
            self._write(state, args, force=True)

    return _ProgressCallback()


def _training_output_dir() -> str:
    """학습 산출물 기본 경로 — settings.training_output_dir.

    종전 'artifacts/classifier' 는 ro 마운트라 학습이 산출물을 쓸 수 없었다(실서버 실측).
    artifacts 의 ro 는 서빙 모델 보호 목적이라 유지하고, 쓰기는 별도 경로로 분리한다.
    """
    try:
        from lloydk.config import settings  # noqa: PLC0415

        out = str(getattr(settings, "training_output_dir", "") or "").strip()
    except Exception:  # noqa: BLE001
        out = ""
    return out or "artifacts_out/classifier"


@dataclass
class TrainSpec:
    base_model: str = "kakaobank/kf-deberta-base"
    train_path: str = field(default_factory=lambda: _dataset_split("train.jsonl"))
    val_path: str = field(default_factory=lambda: _dataset_split("val.jsonl"))
    test_path: str | None = field(default_factory=lambda: _dataset_split("test.jsonl"))
    output_dir: str = field(default_factory=lambda: _training_output_dir())
    # [진행률 배선 2026-08-08] 이 값이 있으면 학습 중 진행률을 JobStore(run_id 키)에 기록한다.
    # 워커(tasks.py)가 TrainingRun 의 run_id 를 넣어 준다 — API hyperparams 로 받는 값이 아니다.
    # 없으면(None) 아무것도 기록하지 않는다: 스크립트 직접 실행·테스트 경로 동작 보존.
    # 종전에는 TrainStatus 의 progress 가 status 에서 유도된 상수(queued 0.0/running 0.5/
    # completed 1.0)뿐이었고 current_epoch·total_epochs·estimated_finish_at 은 스키마에
    # 선언만 되어 있고 채우는 코드가 없었다. 실측 13시간짜리 학습에서 막대가 내내 50% 에
    # 멈춰 있어 "멈춘 건지 도는 건지" 화면으로 구분할 수 없었다.
    progress_run_id: str | None = None
    max_seq_len: int = 512
    # [FUN-004 Chunk 단위 학습] True 면 TRAIN 분할을 chunk 단위로 확장(각 chunk=문서 라벨 상속).
    # 긴 문서의 max_seq_len truncation 으로 잘리던 뒷부분까지 학습 신호로 사용. val/test/holdout 은
    # 문서 단위 유지(누수차단 — chunk_expand 모듈 주석 참조). 기본 False=기존 문서단위 학습 보존.
    chunk_expand: bool = False
    # auto: chunk 계약 필드를 검사해 문서/기생성 chunk를 자동 판별.
    # pre_chunked: materializer의 train_chunks.jsonl 계약을 강제.
    # documents: chunk 표식이 하나라도 있으면 이중 확장 위험으로 거부.
    train_input_mode: TrainInputMode = "auto"
    # chunk char 크기(0=auto=max_seq_len*3, 추론측 chunk_text 와 동일 휴리스틱)·overlap·최소 글자.
    chunk_char_size: int = 0
    chunk_overlap: int = 64
    chunk_min_chars: int = 40
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
    # best 모델 선택 지표.
    # 주의: fnr_high 단독은 '전부 TS' degenerate 예측기에 의해 0으로 게이밍된다
    # (진짜 고등급을 절대 놓치지 않으니 fnr_high=0이지만 무의미한 모델).
    # → 과분류(over_class_rate)와 degenerate penalty를 합한 합성 지표를 기본으로 한다.
    #   fnr_high_balanced = fnr_high + over_class_rate + degenerate_penalty (낮을수록 좋음).
    # 순수 fnr_high를 원하면 명시적으로 "fnr_high"로 설정 가능(권장하지 않음).
    early_stop_metric: str = "fnr_high_balanced"
    seed: int = 42
    experiment_name: str = "kipra-classifier"
    bf16: bool = True           # bf16 가속 (CUDA GPU 필요; GPU 없으면 자동 비활성)
    use_mlflow: bool = True     # MLflow 로깅 (서버 없으면 False 권장)
    logging_steps: int = 20
    # Proxy production path: this trainer emits epoch checkpoint candidates and
    # a hash-bound TRAINING_EXECUTION.json only.  It does not choose a deployable
    # model, evaluate test/frozen data, or fit temperature from validation.
    proxy_candidate_mode: bool = False
    proxy_training_run_dir: str | None = None
    base_model_revision: str | None = None
    training_entrypoint_path: str | None = None


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
    artifact_status: str = "legacy_trained_model"
    claim_scope: str = "legacy_training_report"
    deployable: bool = True
    training_execution_manifest: str | None = None


def _load_jsonl(path: str) -> tuple[list[str], list[int]]:
    texts, labels = [], []
    for row_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if any(field in row for field in _CHUNK_SENTINEL_FIELDS):
            raise ValueError(
                f"{path}:{row_number}: validation/test input must be document-level, "
                "not pre-chunked"
            )
        texts.append(row["text"])
        labels.append(_LABEL2ID[row["label"]])
    return texts, labels


def _parse_sample_weight(value: object, *, path: str, row_number: int) -> float:
    """Parse one externally supplied sample weight using a fail-closed contract.

    상한을 두지 않는다(> 0 · 유한만 요구). 종전엔 (0, 1] 이었는데 그 상한이 **정본 학습셋을
    거부**했다 — datasets/labeled_p1_v5_clean/train.jsonl 2,042행 중 91행(4.5%)이 1.078·1.133
    이고, 그 학습셋이 배포 모델 artifacts/classifier_p1_v5_clean/v-fe4b386b 를 만들었다.
    가중이 1 을 넘는 것은 버그가 아니라 설계다 — build_p1_v5_clean.assign_weights 가
    'tier 신뢰도 × 클래스 희소도'로 계산하며 희소도 상한이 2x 라 최대 1.5 가 나온다.
    구 gold ×3 **물리 복제를 대체**하는 장치이므로(manifest no_physical_replication) 1 로 자르면
    희소 클래스(S1/TS) 업웨이팅이 사라진다.
    실측 2026-08-08(실서버): 이 상한 때문에 재학습이 교정 병합 단계에서 항상 실패했다
    (ValueError: sample_weight must be finite and in (0, 1] · 실제 값 1.133).
    쓰레기 값(0·음수·NaN·inf·bool·비수치)은 계속 거부한다 — fail-closed 취지는 유지.
    """
    if isinstance(value, bool):
        raise ValueError(f"{path}:{row_number}: sample_weight must be a positive number")
    try:
        weight = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{path}:{row_number}: sample_weight must be a positive number"
        ) from exc
    if not math.isfinite(weight) or weight <= 0.0:
        raise ValueError(f"{path}:{row_number}: sample_weight must be finite and > 0")
    return weight


def _load_training_jsonl(
    path: str,
    *,
    input_mode: TrainInputMode = "auto",
) -> tuple[list[str], list[int], list[float], Literal["documents", "pre_chunked"]]:
    """Load training rows, validate weights, and detect materialized chunks.

    Missing ``sample_weight`` remains backwards compatible at 1.0.  Partial or
    mixed chunk metadata is rejected because silently treating those rows as
    documents could expand an already materialized chunk a second time.
    """
    if input_mode not in {"auto", "documents", "pre_chunked"}:
        raise ValueError(f"unsupported train_input_mode: {input_mode!r}")

    rows: list[dict] = []
    source = Path(path)
    for row_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            raise ValueError(f"{path}:{row_number}: training row must be a JSON object")
        parsed["_input_row_number"] = row_number
        rows.append(parsed)

    chunk_marked = [any(field in row for field in _CHUNK_SENTINEL_FIELDS) for row in rows]
    chunk_complete = [
        all(str(row.get(field) or "").strip() for field in _PRE_CHUNK_FIELDS)
        for row in rows
    ]
    if input_mode == "auto":
        if any(chunk_marked):
            if not rows or not all(chunk_complete):
                raise ValueError(
                    f"{path}: mixed or incomplete pre-chunked rows; "
                    f"required fields={_PRE_CHUNK_FIELDS}"
                )
            detected_mode: Literal["documents", "pre_chunked"] = "pre_chunked"
        else:
            detected_mode = "documents"
    elif input_mode == "pre_chunked":
        if not rows or not all(chunk_complete):
            raise ValueError(
                f"{path}: pre_chunked mode requires every row to contain "
                f"{_PRE_CHUNK_FIELDS}"
            )
        detected_mode = "pre_chunked"
    else:
        if any(chunk_marked):
            raise ValueError(f"{path}: documents mode rejects pre-chunked row markers")
        detected_mode = "documents"

    texts: list[str] = []
    labels: list[int] = []
    sample_weights: list[float] = []
    chunk_ids: set[str] = set()
    for row in rows:
        row_number = int(row.pop("_input_row_number"))
        texts.append(row["text"])
        labels.append(_LABEL2ID[row["label"]])
        raw_weight = row["sample_weight"] if "sample_weight" in row else 1.0
        sample_weights.append(
            _parse_sample_weight(raw_weight, path=path, row_number=row_number)
        )
        if detected_mode == "pre_chunked":
            chunk_id = str(row["chunk_id"]).strip()
            if chunk_id in chunk_ids:
                raise ValueError(f"{path}:{row_number}: duplicate chunk_id {chunk_id!r}")
            chunk_ids.add(chunk_id)
    return texts, labels, sample_weights, detected_mode


def _prepare_training_rows(
    spec: TrainSpec,
) -> tuple[list[str], list[int], list[float], Literal["documents", "pre_chunked"]]:
    """Prepare train-only rows without requiring transformers or a model load."""
    train_x, train_y, sample_weights, detected_mode = _load_training_jsonl(
        spec.train_path,
        input_mode=spec.train_input_mode,
    )
    if not spec.chunk_expand:
        return train_x, train_y, sample_weights, detected_mode
    if detected_mode == "pre_chunked":
        raise ValueError(
            "chunk_expand=True cannot be used with pre-chunked training input; "
            "use train_input_mode='pre_chunked' and chunk_expand=False"
        )

    from lloydk.modules.m4_training.chunk_expand import expand_chunks

    char_size = spec.chunk_char_size or (spec.max_seq_len * 3)
    metadata = list(zip(train_y, sample_weights))
    expanded_x, expanded_metadata = expand_chunks(
        train_x,
        metadata,
        char_size=char_size,
        overlap=spec.chunk_overlap,
        min_chars=spec.chunk_min_chars,
    )
    expanded_y = [label for label, _ in expanded_metadata]
    expanded_weights = [weight for _, weight in expanded_metadata]
    return expanded_x, expanded_y, expanded_weights, detected_mode


def _weighted_cross_entropy(
    logits,
    labels,
    *,
    class_weights=None,
    sample_weights=None,
):
    """Combine class and per-sample weights with stable weighted-mean CE.

    With no sample weights this is exactly PyTorch's legacy weighted CE.  With
    sample weights, each row's numerator and normalization mass are scaled by
    that weight, so a 0.5 neighbor has half the influence of a 1.0 evidence row.
    """
    import torch.nn.functional as functional

    if sample_weights is None:
        effective_class_weights = (
            class_weights.to(device=logits.device, dtype=logits.dtype)
            if class_weights is not None
            else None
        )
        return functional.cross_entropy(logits, labels, weight=effective_class_weights)

    weights = sample_weights.to(device=logits.device, dtype=logits.dtype).reshape(-1)
    if weights.numel() != labels.numel():
        raise ValueError("sample_weights length must match labels")
    effective_class_weights = (
        class_weights.to(device=logits.device, dtype=logits.dtype)
        if class_weights is not None
        else None
    )
    per_row = functional.cross_entropy(
        logits,
        labels,
        weight=effective_class_weights,
        reduction="none",
    )
    normalization = weights
    if effective_class_weights is not None:
        normalization = normalization * effective_class_weights[labels]
    denominator = normalization.sum()
    # External values were validated once by _load_training_jsonl.  Avoid a
    # device-to-host synchronization on every GPU batch here.
    return (per_row * weights).sum() / denominator


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


def _compute_over_class_rate(cm: np.ndarray, high_ids: list[int]) -> float:
    """고등급 과분류율 = 진짜 저등급(비-고등급) 표본을 고등급으로 오예측한 비율.

    fnr_high 단독 최소화는 '전부 TS' 같은 degenerate 예측기에 의해 0으로 게이밍된다
    (진짜 고등급을 절대 놓치지 않으니까). 이를 막기 위한 짝지표:
    저등급 진짜 표본 중 몇 %를 고등급으로 끌어올렸는가. degenerate '전부-TS'면
    저등급 전부가 TS로 가므로 이 값이 1.0에 수렴 → 합성 지표가 폭증해 선택 거부.
    """
    high = set(high_ids)
    n = cm.shape[0]
    low_ids = [i for i in range(n) if i not in high]
    over, pos = 0, 0
    for i in low_ids:
        pos += int(cm[i].sum())
        over += int(sum(cm[i, j] for j in range(n) if j in high))
    return float(over / pos) if pos else 0.0


def _compute_degenerate_penalty(cm: np.ndarray) -> float:
    """단일클래스 degenerate 예측기 가드.

    예측이 사실상 한 클래스에 몰려 있으면(예: 전부 TS) 큰 패널티(1.0)를 반환.
    confusion matrix의 열 합(= 각 등급으로 예측된 표본 수)에서 한 열이 전체의
    >=99%를 차지하면 degenerate로 간주. 합성 best-metric에 가산되어 선택을 막는다.
    """
    total = int(cm.sum())
    if total <= 0:
        return 0.0
    col_sums = cm.sum(axis=0)
    return 1.0 if int(col_sums.max()) >= 0.99 * total else 0.0


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_write_new(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Hard-link publication is atomic and fails if the single-assignment
        # destination already exists (unlike POSIX rename, which can replace).
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError(
                f"proxy training execution artifact already exists: {path}"
            ) from exc
    finally:
        if temporary.exists():
            temporary.unlink()


def _proxy_materialization_audit(spec: TrainSpec) -> dict[str, object] | None:
    if not spec.proxy_candidate_mode:
        if spec.proxy_training_run_dir is not None:
            raise ValueError(
                "proxy_training_run_dir requires proxy_candidate_mode=True"
            )
        return None
    if not spec.proxy_training_run_dir:
        raise ValueError("proxy candidate mode requires proxy_training_run_dir")
    if spec.test_path is not None:
        raise ValueError(
            "proxy candidate mode forbids test_path; frozen/calibration data must not be test input"
        )
    if spec.train_input_mode != "pre_chunked" or spec.chunk_expand:
        raise ValueError(
            "proxy candidate mode requires pre_chunked train input without chunk_expand"
        )
    if not spec.training_entrypoint_path:
        raise ValueError("proxy candidate mode requires training_entrypoint_path")
    from lloydk.proxy_training_finalization import (  # noqa: PLC0415
        verify_materialized_training_run,
    )

    bound = verify_materialized_training_run(Path(spec.proxy_training_run_dir))
    artifacts = bound["artifacts"]
    expected_train = Path(str(artifacts["train_chunks"]["path"])).resolve()
    expected_validation = Path(
        str(artifacts["validation_documents"]["path"])
    ).resolve()
    if Path(spec.train_path).resolve() != expected_train:
        raise ValueError(
            "proxy train_path must be the attested training run train_chunks.jsonl"
        )
    if Path(spec.val_path).resolve() != expected_validation:
        raise ValueError(
            "proxy val_path must be the attested training run validation_documents.jsonl"
        )
    output = Path(spec.output_dir)
    if output.exists() and not output.is_dir():
        raise ValueError(f"proxy checkpoint root is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise ValueError(
            f"proxy checkpoint root must be new or empty: {output}"
        )
    return {key: value for key, value in bound.items() if key != "document_rows"}


def _hash_model_state_dict(model) -> str:
    """Hash exact initial parameter bytes before any optimizer step."""
    import torch  # noqa: PLC0415

    digest = hashlib.sha256()
    for name, tensor in sorted(model.state_dict().items()):
        detached = tensor.detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(detached.dtype).encode("ascii"))
        digest.update(b"\0")
        digest.update(json.dumps(list(detached.shape), separators=(",", ":")).encode("ascii"))
        digest.update(b"\0")
        # uint8 view supports bf16 and every other dense numeric dtype without
        # a lossy numpy dtype conversion.
        digest.update(detached.view(-1).view(torch.uint8).numpy().tobytes())
        digest.update(b"\n")
    return digest.hexdigest()


def _attest_base_model(model, tokenizer, spec: TrainSpec) -> dict[str, object]:
    config = getattr(model, "config", None)
    resolved_revision = getattr(config, "_commit_hash", None)
    if not resolved_revision:
        resolved_revision = getattr(tokenizer, "init_kwargs", {}).get("_commit_hash")
    base_path = Path(spec.base_model)
    if base_path.exists():
        revision_kind = "local_exact_state"
        resolved_revision = str(resolved_revision or "local")
    else:
        revision_kind = "huggingface_commit"
        requested = spec.base_model_revision
        if requested and not re.fullmatch(r"[0-9a-f]{40,64}", requested):
            raise ValueError(
                "proxy base_model_revision must be an immutable 40-64 hex commit"
            )
        if requested and re.fullmatch(r"[0-9a-f]{40,64}", requested):
            if resolved_revision and str(resolved_revision) != requested:
                raise ValueError(
                    "resolved base-model commit does not match requested immutable revision"
                )
            resolved_revision = str(resolved_revision or requested)
        if not isinstance(resolved_revision, str) or not re.fullmatch(
            r"[0-9a-f]{40,64}", resolved_revision
        ):
            raise ValueError(
                "proxy candidate mode requires an immutable Hugging Face commit revision; "
                "pass --base-model-revision <40-hex-commit>"
            )
    config_json = (
        config.to_json_string(use_diff=False)
        if config is not None and hasattr(config, "to_json_string")
        else json.dumps(getattr(config, "to_dict", lambda: {})(), sort_keys=True)
    )
    tokenizer_contract = {
        "class": type(tokenizer).__name__,
        "is_fast": bool(getattr(tokenizer, "is_fast", False)),
        "vocab": tokenizer.get_vocab(),
        "special_tokens_map": getattr(tokenizer, "special_tokens_map", {}),
    }
    tokenizer_bytes = json.dumps(
        tokenizer_contract,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "identifier": spec.base_model,
        "requested_revision": spec.base_model_revision,
        "resolved_revision": resolved_revision,
        "revision_kind": revision_kind,
        "initial_state_dict_sha256": _hash_model_state_dict(model),
        "config_sha256": _sha256_bytes(config_json.encode("utf-8")),
        "tokenizer_contract_sha256": _sha256_bytes(tokenizer_bytes),
        "tokenizer_is_fast": bool(getattr(tokenizer, "is_fast", False)),
    }


def _write_proxy_training_execution(
    *,
    spec: TrainSpec,
    materialization_start: dict[str, object],
    base_model_attestation: dict[str, object],
    run_id: str,
) -> Path:
    from lloydk.proxy_model_comparison import hash_model_directory  # noqa: PLC0415
    from lloydk.proxy_training_finalization import (  # noqa: PLC0415
        verify_materialized_training_run,
    )

    checkpoint_root = Path(spec.output_dir)
    checkpoints = sorted(
        (
            path
            for path in checkpoint_root.iterdir()
            if path.is_dir() and re.fullmatch(r"checkpoint-[0-9]+", path.name)
        ),
        key=lambda path: int(path.name.split("-")[1]),
    )
    if not checkpoints:
        raise ValueError("proxy training produced no checkpoint-* candidates")
    if os.name != "nt":
        for checkpoint in checkpoints:
            for child in checkpoint.rglob("*"):
                child.chmod(0o2750 if child.is_dir() else 0o640)
            checkpoint.chmod(0o2750)
    checkpoint_attestations = [
        {"name": path.name, "artifact": hash_model_directory(path)}
        for path in checkpoints
    ]
    checkpoint_set_bytes = json.dumps(
        checkpoint_attestations,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    materialization_end_bound = verify_materialized_training_run(
        Path(str(spec.proxy_training_run_dir))
    )
    materialization_end = {
        key: value
        for key, value in materialization_end_bound.items()
        if key != "document_rows"
    }
    if materialization_end != materialization_start:
        raise ValueError("materialized training run changed during proxy training")
    trainer_source = Path(__file__).resolve()
    source: dict[str, object] = {
        "trainer_path": str(trainer_source),
        "trainer_sha256": _sha256_file(trainer_source),
    }
    if spec.training_entrypoint_path:
        entrypoint = Path(spec.training_entrypoint_path).resolve()
        if not entrypoint.is_file():
            raise ValueError(f"training entrypoint is missing: {entrypoint}")
        source.update(
            {
                "entrypoint_path": str(entrypoint),
                "entrypoint_sha256": _sha256_file(entrypoint),
            }
        )
    manifest = {
        "schema_version": "proxy-training-execution-v1",
        "status": "checkpoint_candidates_complete",
        "run_id": run_id,
        "claim_scope": "proxy_training_checkpoint_candidates_only_not_customer_accuracy",
        "deployable": False,
        "finalizer_required": True,
        "materialized_training_run": materialization_start,
        "inputs": {
            "train_chunks_sha256": materialization_start["artifacts"]["train_chunks"][
                "sha256"
            ],
            "validation_documents_sha256": materialization_start["artifacts"][
                "validation_documents"
            ]["sha256"],
            "calibration_documents_used": False,
            "test_or_frozen_documents_used": False,
        },
        "base_model": base_model_attestation,
        "training_spec": asdict(spec),
        "seed": spec.seed,
        "validation_policy": {
            "during_training": "diagnostic_epoch_metrics_only",
            "deployable_checkpoint_selected": False,
            "temperature_fitted": False,
            "val_logits_exported": False,
        },
        "checkpoint_root": str(checkpoint_root.resolve()),
        "checkpoint_set_sha256": _sha256_bytes(checkpoint_set_bytes),
        "checkpoints": checkpoint_attestations,
        "source": source,
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    manifest_path = checkpoint_root / "TRAINING_EXECUTION.json"
    _atomic_write_new(manifest_path, manifest_bytes)
    complete = {
        "schema_version": "proxy-training-execution-v1",
        "status": "complete",
        "run_id": run_id,
        "manifest_sha256": _sha256_bytes(manifest_bytes),
        "checkpoint_set_sha256": manifest["checkpoint_set_sha256"],
    }
    _atomic_write_new(
        checkpoint_root / "TRAINING_CANDIDATES_COMPLETE",
        (json.dumps(complete, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    if os.name != "nt":
        manifest_path.chmod(0o640)
        (checkpoint_root / "TRAINING_CANDIDATES_COMPLETE").chmod(0o640)
        checkpoint_root.chmod(0o2750)
    return manifest_path


def train_classifier(spec: Optional[TrainSpec] = None) -> TrainReport:
    spec = spec or TrainSpec()
    proxy_materialization = _proxy_materialization_audit(spec)

    import torch
    from datasets import Dataset
    from transformers import (
        AutoTokenizer, AutoModelForSequenceClassification,
        TrainingArguments, Trainer, DataCollatorWithPadding, set_seed,
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

    # Model heads can be initialized during from_pretrained, before Trainer has
    # a chance to apply TrainingArguments.seed.  Seed here so the attested base
    # state and the actual optimization start are reproducible.
    set_seed(spec.seed)

    train_x, train_y, train_sample_weights, detected_train_mode = _prepare_training_rows(spec)
    val_x, val_y = _load_jsonl(spec.val_path)
    if spec.proxy_candidate_mode:
        test_x: list[str] = []
        test_y: list[int] = []
    else:
        if spec.test_path is None:
            raise ValueError("legacy training mode requires test_path")
        test_x, test_y = _load_jsonl(spec.test_path)

    # [FUN-004] chunk 단위 학습: TRAIN 만 chunk 확장(val/test 는 문서 단위 유지 = 누수차단).
    # _prepare_training_rows가 기생성 train_chunks의 이중 확장을 fail-closed로 차단한다.
    if spec.chunk_expand:
        _csz = spec.chunk_char_size or (spec.max_seq_len * 3)
        print(f"  [chunk-expand] prepared {len(train_x)} chunk rows "
              f"(char_size={_csz}); val/test 문서단위 유지(누수차단)")
    elif detected_train_mode == "pre_chunked":
        print(f"  [pre-chunked] consuming {len(train_x)} weighted train chunk rows; "
              "val/test 문서단위 유지(누수차단)")

    revision_kwargs = (
        {"revision": spec.base_model_revision} if spec.base_model_revision else {}
    )
    tok = AutoTokenizer.from_pretrained(spec.base_model, **revision_kwargs)

    def tokenize(batch):
        return tok(batch["text"], truncation=True, max_length=spec.max_seq_len)

    def make_dataset(texts, labels, sample_weights):
        return Dataset.from_dict({
            "text": texts,
            "label": labels,
            "sample_weight": sample_weights,
        }).map(tokenize, batched=True, remove_columns=["text"])

    ds_train = make_dataset(train_x, train_y, train_sample_weights)
    # 평가는 기존과 동일하게 문서단위·비가중으로 집계한다.
    ds_val = make_dataset(val_x, val_y, [1.0] * len(val_y))
    ds_test = (
        None
        if spec.proxy_candidate_mode
        else make_dataset(test_x, test_y, [1.0] * len(test_y))
    )

    model = AutoModelForSequenceClassification.from_pretrained(
        spec.base_model,
        num_labels=len(_LABEL_LIST),
        id2label=_ID2LABEL,
        label2id=_LABEL2ID,
        **revision_kwargs,
    )
    base_model_attestation = (
        _attest_base_model(model, tok, spec) if spec.proxy_candidate_mode else None
    )

    # 고등급 id (TS/S1 등) — 미탐 비대칭 가중·fnr_high 계산 공통 기준
    high_ids = [_LABEL2ID[c] for c in spec.high_grade_codes if c in _LABEL2ID]

    # class weight (불균형 보정) + FNR 비대칭 cost
    if spec.class_weighted:
        counts = np.bincount(
            train_y,
            weights=np.asarray(train_sample_weights, dtype=np.float64),
            minlength=len(_LABEL_LIST),
        )
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
            sample_weights = inputs.pop("sample_weight", None)
            outputs = model(**inputs)
            logits = outputs.logits
            loss = _weighted_cross_entropy(
                logits,
                labels,
                class_weights=class_weights,
                sample_weights=sample_weights,
            )
            return (loss, outputs) if return_outputs else loss

    base_data_collator = DataCollatorWithPadding(tok)

    def weighted_data_collator(features):
        model_features = []
        weights = []
        for feature in features:
            model_feature = dict(feature)
            weights.append(float(model_feature.pop("sample_weight", 1.0)))
            model_features.append(model_feature)
        batch = base_data_collator(model_features)
        batch["sample_weight"] = torch.tensor(weights, dtype=torch.float32)
        return batch

    def compute_metrics(eval_pred):
        preds = np.argmax(eval_pred.predictions, axis=-1)
        labels = eval_pred.label_ids
        acc = accuracy_score(labels, preds)
        p, r, f, _ = precision_recall_fscore_support(labels, preds, average="macro", zero_division=0)
        cm = confusion_matrix(labels, preds, labels=list(_ID2LABEL.keys()))
        fnr, fnr_by = _compute_fnr(cm)
        fnr_high = _compute_fnr_high(cm, high_ids)
        over_class = _compute_over_class_rate(cm, high_ids)
        degenerate = _compute_degenerate_penalty(cm)
        # 합성 best-metric (낮을수록 좋음): 미탐(fnr_high)을 최소화하되 과분류와
        # degenerate '전부-한클래스'를 강하게 패널티해 게이밍을 막는다.
        fnr_high_balanced = float(fnr_high + over_class + degenerate)
        return {
            "accuracy": acc, "precision_macro": p, "recall_macro": r,
            "f1_macro": f, "fnr_overall": fnr, "fnr_high": fnr_high,
            "over_class_rate": over_class, "degenerate_penalty": degenerate,
            "fnr_high_balanced": fnr_high_balanced,
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
        # Proxy mode deliberately retains every epoch checkpoint as a candidate.
        # The serving-faithful finalizer, not this 512-token diagnostic loop,
        # chooses the restricted proxy candidate on validation_documents.
        load_best_model_at_end=not spec.proxy_candidate_mode,
        metric_for_best_model=spec.early_stop_metric,
        greater_is_better=False,
        logging_steps=spec.logging_steps,
        seed=spec.seed,
        bf16=use_bf16,
        report_to=report_to,
        dataloader_num_workers=0,   # Windows deadlock 방지
        dataloader_pin_memory=False,
        # sample_weight는 model.forward 인자가 아니므로 Trainer의 자동 column
        # 제거를 끄고, 위 collator/compute_loss에서만 소비한다.
        remove_unused_columns=False,
    )

    # transformers v5+ 호환: Trainer.__init__ 가 `tokenizer` → `processing_class`로 이름
    # 변경됨. v4 에서는 tokenizer 가, v5 에서는 processing_class 가 표준.
    # 두 경로 모두 시도 (런타임 버전 호환).
    try:
        trainer = WeightedTrainer(
            model=model, args=args,
            train_dataset=ds_train, eval_dataset=ds_val,
            processing_class=tok,
            data_collator=weighted_data_collator,
            compute_metrics=compute_metrics,
        )
    except TypeError:  # v4 폴백
        trainer = WeightedTrainer(
            model=model, args=args,
            train_dataset=ds_train, eval_dataset=ds_val,
            tokenizer=tok, data_collator=weighted_data_collator,
            compute_metrics=compute_metrics,
        )

    # [진행률 배선] HF TrainerState 가 스키마에 선언만 돼 있던 값들을 그대로 갖고 있다
    # (global_step/max_steps/epoch). 로그(tqdm)를 파싱하지 않고 여기서 직접 읽어 JobStore 에
    # 쓴다 — tqdm 출력 형식은 transformers 버전에 따라 바뀌므로 파싱은 조용히 깨진다.
    # add_callback 으로 붙이는 이유: 위 v4/v5 생성자 분기 두 곳을 건드리지 않기 위해서다.
    if spec.progress_run_id:
        trainer.add_callback(_build_progress_callback(spec.progress_run_id))

    with _training_run_context(mlflow, spec) as run:
        if spec.use_mlflow:
            mlflow.log_params(asdict(spec))
        trainer.train()

        if spec.proxy_candidate_mode:
            if proxy_materialization is None or base_model_attestation is None:
                raise AssertionError("proxy training attestations were not initialized")
            execution_manifest = _write_proxy_training_execution(
                spec=spec,
                materialization_start=proxy_materialization,
                base_model_attestation=base_model_attestation,
                run_id=str(run.info.run_id),
            )
            if spec.use_mlflow:
                mlflow.log_artifact(str(execution_manifest))
            # This is intentionally not a model-quality report.  No test/frozen
            # split was loaded and no validation logits or temperature artifact
            # was produced.  Only the finalizer can publish a deployment candidate.
            return TrainReport(
                model_version=f"proxy-candidates-{run.info.run_id[:8]}",
                accuracy=0.0,
                precision_macro=0.0,
                recall_macro=0.0,
                f1_macro=0.0,
                fnr_overall=0.0,
                fnr_by_grade={},
                fnr_high=0.0,
                confusion_matrix=[],
                classification_report=(
                    "NOT_EVALUATED: checkpoint candidates only; serving-faithful "
                    "selection/calibration finalizer required"
                ),
                artifact_status="proxy_checkpoint_candidates_only",
                claim_scope="proxy_training_only_not_customer_accuracy",
                deployable=False,
                training_execution_manifest=str(execution_manifest),
            )

        if ds_test is None:  # pragma: no cover - proxy branch returned above
            raise AssertionError("legacy test dataset is missing")
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

        # [보정 배선] 검증셋(val split) per-row raw logits + 정답 label_idx를 모델
        # 아티팩트 dir에 ADDITIVELY 저장 → calibrate_classifier.py가 더미가 아닌
        # *실제* logits로 temperature를 적합해 temperature.json을 같은 dir에 남길 수
        # 있게 한다(서빙 m5 pipeline이 자동 로드). 기존엔 logits dump가 없어 보정이
        # 더미만 산출(배포금지)됐다. 포맷: 한 줄당 {"logits":[...], "label_idx":int}
        # — calibrate_classifier.py가 읽는 logits·label_idx 키와 동일.
        # 무거운 재학습 없이 이미 로드된 best 모델로 val을 1회 predict만 한다.
        # 베스트에포트(비치명적): 실패해도 학습 산출물·리포트는 그대로 보존.
        try:
            val_pred = trainer.predict(ds_val)
            val_logits = val_pred.predictions
            val_labels = val_pred.label_ids
            _cal_logits: list[list[float]] = []
            _cal_labels: list[int] = []
            with (out_dir / "val_logits.jsonl").open("w", encoding="utf-8") as _vf:
                for _lg, _yl in zip(val_logits, val_labels):
                    _row = [float(x) for x in np.asarray(_lg).ravel()]
                    _cal_logits.append(_row)
                    _cal_labels.append(int(_yl))
                    _vf.write(json.dumps({
                        "logits": _row,
                        "label_idx": int(_yl),
                    }, ensure_ascii=False) + "\n")
            # [train→calibrate 자동연결] 학습 직후 같은 val logits 로 temperature.json 을
            # out_dir 에 산출 → 서빙(m5 pipeline)이 자동 로드(MEMORY: 미보정 서빙 시 OOD 과신).
            # 수동 2-step(calibrate_classifier.py)을 운영자가 잊어도 보정이 적용된다. 자동 '활성'
            # 과는 분리(등록만) — 베스트에포트(실패해도 학습 산출물 보존).
            try:
                from lloydk.modules.m6_evaluation.temperature import (  # noqa: PLC0415
                    fit_temperature_report,
                )
                if _cal_logits and _cal_labels:
                    _rep = fit_temperature_report(_cal_logits, _cal_labels)
                    _rep["source"] = "trainer-auto"
                    (out_dir / "temperature.json").write_text(
                        json.dumps(_rep, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
            except Exception:  # noqa: BLE001 — 자동 보정 실패는 학습/dump 를 막지 않음
                pass
        except Exception:  # noqa: BLE001 — 보정 dump 실패는 학습을 막지 않음
            pass

        # [드리프트 배선] 학습 표본 임베딩으로 train centroid 저장 → run_drift_check 활성화.
        # 기존: centroid 미저장이라 drift_tick이 영원히 skip(드리프트 감지 무력). 베스트에포트(비치명적).
        # [스코프 결정 2026-08-08] settings.drift_detection_enabled 기본 OFF 로 건너뛴다 —
        # 드리프트 감지는 요건이 아니고(RTM 무행), 이 단계 비용이 실측 30분 10초였다(69분 재학습 중).
        # 고객사 야간 무인 배치에 매번 얹히는 시간이라 기본으로 지불할 이유가 없다. 근거는 config.py
        # drift_detection_enabled 주석 참조. 켜면 종전 동작 그대로 복원된다.
        try:
            from lloydk.config import settings as _settings  # noqa: PLC0415
            _drift_on = bool(getattr(_settings, "drift_detection_enabled", False))
        except Exception:  # noqa: BLE001
            _drift_on = False
        if _drift_on:
            try:
                from lloydk.adapters.embedding import build_embedder  # noqa: PLC0415
                from lloydk.services.drift_monitor import save_train_centroid  # noqa: PLC0415
                _vecs = build_embedder().embed(train_x[:200]).vectors
                if _vecs:
                    save_train_centroid(_vecs)
            except Exception:  # noqa: BLE001
                pass

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
        if spec.use_mlflow:
            mlflow.log_metrics({
                "test_accuracy": acc, "test_f1_macro": float(f),
                "test_fnr_overall": float(fnr), "test_fnr_high": float(fnr_high),
                **{f"test_fnr_{k}": v for k, v in fnr_by.items()},
            })
            mlflow.log_artifact(str(out_dir / "report.json"))
        return result
