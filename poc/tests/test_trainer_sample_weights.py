from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.modules.m4_training.trainer import (
    TrainSpec,
    _load_jsonl,
    _load_training_jsonl,
    _prepare_training_rows,
    _training_run_context,
    _weighted_cross_entropy,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def _chunk_row(chunk_id: str, *, weight: object = 1.0) -> dict:
    return {
        "text": f"학습 chunk {chunk_id}",
        "label": "TS",
        "chunk_id": chunk_id,
        "source_doc_id": "doc-1",
        "chunk_label_strength": "evidence",
        "sample_weight": weight,
    }


def test_training_loader_defaults_missing_weight_and_accepts_valid_weight(tmp_path: Path):
    path = tmp_path / "documents.jsonl"
    _write_jsonl(
        path,
        [
            {"text": "일반 문서", "label": "S3"},
            {"text": "가중 문서", "label": "S2", "sample_weight": 0.5},
        ],
    )

    texts, labels, weights, mode = _load_training_jsonl(str(path))

    assert texts == ["일반 문서", "가중 문서"]
    assert len(labels) == 2
    assert weights == [1.0, 0.5]
    assert mode == "documents"


@pytest.mark.parametrize("bad_weight", [0, -0.1, float("nan"), float("inf"), True, None])
def test_training_loader_rejects_invalid_sample_weight(tmp_path: Path, bad_weight: object):
    path = tmp_path / "bad.jsonl"
    _write_jsonl(path, [{"text": "문서", "label": "S3", "sample_weight": bad_weight}])

    with pytest.raises(ValueError, match="sample_weight"):
        _load_training_jsonl(str(path))


@pytest.mark.parametrize("good_weight", [1.0, 1.078, 1.133, 1.5])
def test_training_loader_accepts_upweighted_sample_weight(tmp_path: Path, good_weight: float):
    """1 을 넘는 가중은 정상이다 — 희소 클래스 업웨이팅이 물리 복제를 대체한다.

    정본 datasets/labeled_p1_v5_clean/train.jsonl 은 2,042행 중 91행(4.5%)이 1.078·1.133 이고
    그 학습셋이 배포 모델 v-fe4b386b 를 만들었다. build_p1_v5_clean.assign_weights 의
    희소도 상한이 2x 라 이론 최대는 1.5. 상한을 1 로 두면 정본 학습셋 자체가 거부된다.
    """
    path = tmp_path / "good.jsonl"
    _write_jsonl(path, [{"text": "문서", "label": "S3", "sample_weight": good_weight}])

    _texts, _labels, weights, _mode = _load_training_jsonl(str(path))
    assert weights == [good_weight]


def test_canonical_training_set_weights_are_accepted():
    """정본 학습셋을 로더가 통째로 받아들이는가 — 회귀 방어(실서버 재학습 차단 재발 방지)."""
    path = Path("datasets/labeled_p1_v5_clean/train.jsonl")
    if not path.exists():
        pytest.skip("정본 학습셋 미존재(축소 체크아웃)")
    _texts, _labels, weights, _mode = _load_training_jsonl(str(path))
    assert weights, "가중이 비어 있다"
    assert max(weights) > 1.0, "정본에 1 초과 가중이 있어야 한다(희소 업웨이팅)"


def test_pre_chunked_input_cannot_be_expanded_twice(tmp_path: Path):
    path = tmp_path / "train_chunks.jsonl"
    _write_jsonl(path, [_chunk_row("doc-1:chunk-0000", weight=0.5)])
    spec = TrainSpec(
        train_path=str(path),
        train_input_mode="pre_chunked",
        chunk_expand=True,
    )

    with pytest.raises(ValueError, match="cannot be used with pre-chunked"):
        _prepare_training_rows(spec)


def test_auto_mode_rejects_partial_chunk_contract(tmp_path: Path):
    path = tmp_path / "partial.jsonl"
    _write_jsonl(path, [{"text": "chunk", "label": "S1", "chunk_id": "c-1"}])

    with pytest.raises(ValueError, match="incomplete pre-chunked"):
        _load_training_jsonl(str(path), input_mode="auto")


def test_evaluation_loader_rejects_chunk_level_rows(tmp_path: Path):
    path = tmp_path / "validation.jsonl"
    _write_jsonl(path, [_chunk_row("doc-1:chunk-0000")])

    with pytest.raises(ValueError, match="must be document-level"):
        _load_jsonl(str(path))


def test_legacy_high_grade_document_can_still_use_legacy_expansion(tmp_path: Path):
    path = tmp_path / "legacy.jsonl"
    _write_jsonl(
        path,
        [{"text": "증거 카드 없는 기존 TS 문서 " * 100, "label": "TS"}],
    )
    spec = TrainSpec(
        train_path=str(path),
        train_input_mode="documents",
        chunk_expand=True,
        chunk_char_size=120,
        chunk_overlap=10,
    )

    texts, labels, weights, mode = _prepare_training_rows(spec)

    assert mode == "documents"
    assert len(texts) > 1
    assert len(texts) == len(labels) == len(weights)
    assert set(weights) == {1.0}


def test_weighted_cross_entropy_combines_class_and_sample_weights():
    torch = pytest.importorskip("torch")
    functional = pytest.importorskip("torch.nn.functional")
    logits = torch.tensor([[2.0, 0.1], [0.2, 1.7], [0.5, 1.0]], dtype=torch.float64)
    labels = torch.tensor([0, 1, 0])
    class_weights = torch.tensor([1.5, 0.75], dtype=torch.float64)
    sample_weights = torch.tensor([1.0, 0.5, 1.0], dtype=torch.float64)

    actual = _weighted_cross_entropy(
        logits,
        labels,
        class_weights=class_weights,
        sample_weights=sample_weights,
    )
    per_row = functional.cross_entropy(
        logits,
        labels,
        weight=class_weights,
        reduction="none",
    )
    expected = (per_row * sample_weights).sum() / (
        class_weights[labels] * sample_weights
    ).sum()

    assert torch.allclose(actual, expected)


def test_all_one_sample_weights_preserve_legacy_class_weighted_ce():
    torch = pytest.importorskip("torch")
    functional = pytest.importorskip("torch.nn.functional")
    logits = torch.tensor([[1.2, 0.2], [0.1, 1.1]], dtype=torch.float32)
    labels = torch.tensor([0, 1])
    class_weights = torch.tensor([2.0, 0.5])

    legacy = functional.cross_entropy(logits, labels, weight=class_weights)
    weighted = _weighted_cross_entropy(
        logits,
        labels,
        class_weights=class_weights,
        sample_weights=torch.ones(2),
    )

    assert torch.allclose(weighted, legacy)


def test_no_mlflow_context_never_touches_tracking_backend():
    class ForbiddenMlflow:
        def set_experiment(self, *_args, **_kwargs):
            raise AssertionError("MLflow must stay untouched")

        def start_run(self, *_args, **_kwargs):
            raise AssertionError("MLflow must stay untouched")

    with _training_run_context(
        ForbiddenMlflow(), TrainSpec(use_mlflow=False)
    ) as run:
        assert len(run.info.run_id) == 32
        int(run.info.run_id, 16)
