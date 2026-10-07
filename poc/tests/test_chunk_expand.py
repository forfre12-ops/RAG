"""FUN-004 chunk 단위 학습 확장 — 오프라인(torch/GPU 불요).

expand_chunks 순수 변환 + TrainSpec 플래그 기본값(문서단위 보존) 검증.
누수차단(train-only)은 trainer 가 train_x 에만 적용하는 배선으로 보장(val/test 미확장).
"""

from __future__ import annotations

import pytest

from koipa.modules.m4_training.chunk_expand import expand_chunks, expand_records_evidence_aware


def test_short_doc_single_chunk_same_label():
    x, y = expand_chunks(["짧은 문서"], ["TS"])
    assert x == ["짧은 문서"] and y == ["TS"]


def test_long_doc_expands_and_inherits_label():
    long = "가나다라마 " * 2000  # ≫ 1536자 → 다중 chunk
    x, y = expand_chunks([long], ["S1"], char_size=1536, overlap=64)
    assert len(x) > 1  # 확장됨
    assert all(lbl == "S1" for lbl in y)  # 모든 chunk 가 문서 라벨 상속
    assert len(x) == len(y)


def test_labels_correspond_across_multiple_docs():
    long = "x" * 4000
    x, y = expand_chunks([long, "짧음"], ["TS", "S3"], char_size=1000)
    assert y[-1] == "S3"  # 마지막 문서(짧음) 1행
    assert set(y[:-1]) == {"TS"}  # 첫 문서의 모든 chunk = TS
    assert y.count("S3") == 1


def test_min_chars_drops_tiny_tail_but_keeps_at_least_one():
    # 'a'(1자)는 min_chars 미만 → kept 비면 원문 1행 유지(표본 유실 방지).
    x, y = expand_chunks(["a"], ["TS"], char_size=1536, min_chars=40)
    assert x == ["a"] and y == ["TS"]


def test_empty_text_kept_as_single_row():
    x, y = expand_chunks(["", "hi"], ["S3", "S2"], min_chars=1)
    assert len(x) == 2 and y == ["S3", "S2"]


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        expand_chunks(["a", "b"], ["TS"])


def test_rows_non_decreasing_and_never_lose_docs():
    docs = ["z" * 5000, "short", "y" * 3000]
    x, y = expand_chunks(docs, ["TS", "S1", "S2"], char_size=1200)
    assert len(x) >= len(docs)  # 문서당 ≥1행(표본 유실 없음)
    assert len(x) == len(y)
    # 각 원 라벨이 최소 1회 이상 존재(문서 유실 없음).
    for lbl in ("TS", "S1", "S2"):
        assert lbl in y


def test_trainspec_flag_defaults_off_preserves_doc_level():
    from koipa.modules.m4_training.trainer import TrainSpec

    s = TrainSpec()
    assert s.chunk_expand is False  # 기본 = 기존 문서단위 학습 보존
    assert s.chunk_overlap == 64 and s.chunk_min_chars == 40 and s.chunk_char_size == 0


def _evidence_record(label="TS"):
    text = "서론 공개 배경 설명. " * 30 + "\n\n" + "핵심 조합은 온도 128도와 압력 0.84의 결합조건이다. " * 20 + "\n\n" + "일반 부록 설명. " * 50
    quote = "핵심 조합은 온도 128도와 압력 0.84의 결합조건이다."
    start = text.index(quote)
    return {
        "doc_id": "high-1",
        "document_family_id": "family-1",
        "text": text,
        "label": label,
        "evidence_card": {
            "factors": {
                "competitive_value": {
                    "spans": [{"start": start, "end": start + len(quote), "quote": quote}]
                }
            }
        },
    }


def test_high_grade_expansion_keeps_evidence_and_neighbors_not_every_chunk():
    record = _evidence_record()
    all_chunks, _ = expand_chunks([record["text"]], [record["label"]], char_size=220, overlap=20)
    rows = expand_records_evidence_aware([record], char_size=220, overlap=20)
    assert 1 <= len(rows) < len(all_chunks)
    assert any(row["chunk_label_strength"] == "evidence" for row in rows)
    assert all(row["source_doc_id"] == "high-1" for row in rows)
    for row in rows:
        assert record["text"][row["chunk_start"]:row["chunk_end"]] in row["text"]


def test_high_grade_without_exact_evidence_fails_closed():
    record = _evidence_record()
    record.pop("evidence_card")
    with pytest.raises(ValueError, match="no exact evidence"):
        expand_records_evidence_aware([record], char_size=220)


def test_lower_grade_keeps_all_chunks_with_document_strength():
    record = _evidence_record(label="S2")
    rows = expand_records_evidence_aware([record], char_size=220, overlap=20)
    assert rows and {row["chunk_label_strength"] for row in rows} == {"document"}
