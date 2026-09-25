"""Visibility logic tests with a tiny local tokenizer, never model grading."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import measure_reference_input_fit as fit
from short_reference_probes import FLAGS, build_probes
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


class CharacterTokenizer:
    """Whitespace ignored; one token per other character. Not a model tokenizer."""
    def no_padding(self):
        pass

    def no_truncation(self):
        self.limit, self.stride = None, 0

    def enable_truncation(self, max_length, stride):
        self.limit, self.stride = max_length, stride

    def encode(self, text):
        positions = [(i, i + 1) for i, c in enumerate(text) if not c.isspace()]
        size = self.limit - 2 if self.limit else max(len(positions), 1)
        windows = []
        for start in range(0, max(len(positions), 1), size - self.stride):
            part = positions[start:start + size]
            windows.append(SimpleNamespace(ids=[101, *[ord(text[a]) for a, _ in part], 102],
                offsets=[(0, 0), *part, (0, 0)], tokens=["[CLS]", *[text[a] for a, _ in part], "[SEP]"], overflowing=[]))
            if start + size >= len(positions):
                break
        windows[0].overflowing = windows[1:]
        return windows[0]


@pytest.fixture
def normalizer():
    return fit.load_local_module("_fit_test_normalizer", "src/koipa/modules/m2_preprocess/normalizer.py").normalize


@pytest.fixture
def splitter():
    return fit.load_local_module("_fit_test_chunker", "src/koipa/modules/m2_preprocess/chunker.py").split


@pytest.fixture
def pairs():
    return build_probes()


def test_short_probes_count_no_fixed_answers(pairs):
    inputs, annotations = pairs
    assert len(inputs) == len(annotations) == len({r["input"]["doc_id"] for r in inputs}) == 21
    assert len({r["input"]["text"] for r in inputs}) == 9
    assert len({r["family_id"] for r in annotations}) == 3
    assert sum(r["proposed_grade"] is None for r in annotations) == 3
    assert all(r["expectation_status"] == "design_hypothesis_not_fixed" for r in annotations)
    for row, ann in zip(inputs, annotations, strict=True):
        assert all(row[k] == v for k, v in FLAGS.items())
        fit.validate_pair(row["input"], ann)
        assert set(row["input"]) == {"doc_id", "text", "document_sha256", "context"}
        assert "proposed_grade" not in fit.proposed_view(row["input"])


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_usage_denied(purpose, pairs):
    with pytest.raises(ValueError):
        assert_dataset_usage(pairs[0], purpose=purpose)


@pytest.mark.parametrize("index", range(21))
def test_missing_context_vs_explicit_serialization(index, pairs, normalizer, splitter):
    raw, ann = pairs[0][index]["input"], pairs[1][index]
    absent = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter,
                              profile="train_body_first", max_length=1024)
    present = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter,
                               profile="proposed_body_context_first", max_length=1024)
    assert absent["all_required_input_observed"] is False
    assert present["all_required_input_observed"] is True
    assert present["semantic_grade_verified"] is present["grade_metric_eligible"] is False


@pytest.mark.parametrize("profile", ["train_body_first", "serving_body_truncated", "serving_body_overflow"])
def test_all_profiles_exclude_context(profile, pairs, normalizer, splitter):
    raw, ann = pairs[0][0]["input"], pairs[1][0]
    result = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter, profile=profile, max_length=64, overlap=8)
    assert result["context_serialized"] is False
    assert not result["all_required_input_observed"]
    assert all(not r["visible"] for r in result["requirements"] if r["id"].startswith("C-"))


def test_overflow_observes_late_body_but_truncation_does_not(normalizer, splitter):
    text = "가" * 150 + "나" * 20
    raw = {"doc_id": "late", "text": text, "document_sha256": text_digest(text), "context": {}}
    ann = {"doc_id": "late", "input_sha256": value_digest(raw), "proposed_grade": "S1", "requires_full_body": True,
           "requirements": [{"id": "late", "kind": "body", "start": 150, "end": 170, "sha256": text_digest(text[150:])}]}
    a = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter, profile="serving_body_truncated", max_length=64, overlap=8)
    b = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter, profile="serving_body_overflow", max_length=64, overlap=8)
    assert not a["requirements"][0]["visible"] and b["requirements"][0]["visible"]
    assert not a["all_required_input_observed"] and b["all_required_input_observed"]


def test_chunk_mapping_never_invents_offsets(splitter):
    text = "\n".join("가상 행 " + str(i) + " 자료" for i in range(30))
    chunks = splitter(text, size=40, overlap=5)
    positions = fit.chunk_positions(text, chunks)
    assert all("".join(text[i] for i in pos) == chunk.text for pos, chunk in zip(positions, chunks, strict=True))
    chunks[1].text += "원문에없는값"
    with pytest.raises(FactContractError):
        fit.chunk_positions(text, chunks)


@pytest.mark.parametrize("mutation", ["hash", "offset", "answer", "pointer", "duplicate", "empty", "boolean"])
def test_bad_contract_fails(mutation, pairs):
    raw, ann = copy.deepcopy(pairs[0][0]["input"]), copy.deepcopy(pairs[1][0])
    if mutation == "hash":
        raw["document_sha256"] = "0" * 64
    elif mutation == "offset":
        ann["requirements"][0]["start"] = -1
    elif mutation == "answer":
        raw["proposed_grade"] = "TS"
    elif mutation == "pointer":
        ann["requirements"][-1]["pointer"] = "/missing"
    elif mutation == "duplicate":
        ann["requirements"].append(copy.deepcopy(ann["requirements"][0]))
    elif mutation == "empty":
        ann["requirements"] = []
    else:
        ann["requires_full_body"] = 1
    with pytest.raises(FactContractError):
        fit.validate_pair(raw, ann)


def test_identical_body_different_label_bound():
    rows = [{"doc_id": str(i), "grade": grade, "feature_sha256": "same"} for i, grade in enumerate(("TS", "S1", "S2", "S3"))]
    result = fit.collision_bound(rows)
    assert result["max_correct_on_this_fixed_table"] == 1
    assert result["deterministic_accuracy_ceiling"] == .25
    assert result["rows_in_conflict"] == 4


def test_hold_not_scored_and_zero_denominator_null():
    result = fit.collision_bound([{"doc_id": "hold", "grade": None, "feature_sha256": "x"}])
    assert result["grade_rows"] == 0 and result["deterministic_accuracy_ceiling"] is None


def test_short_body_pair_conflict_ceiling(pairs):
    rows = [{"doc_id": r["input"]["doc_id"], "grade": a["proposed_grade"],
             "feature_sha256": text_digest(r["input"]["text"])} for r, a in zip(*pairs, strict=True)]
    result = fit.collision_bound(rows)
    assert result["grade_rows"] == 18
    assert result["conflicting_input_groups"] == 7
    assert result["max_correct_on_this_fixed_table"] == 11


def test_cannot_write_inside_sources(tmp_path):
    tokenizer = tmp_path / "tokenizer.json"
    with pytest.raises(FactContractError, match="inside_source"):
        fit.run(tmp_path / "output", tmp_path / "ledger", tokenizer)


def test_existing_output_never_changed(tmp_path):
    assert fit.main(["--ledger-pack", "absent", "--tokenizer", "absent/tokenizer.json", "--out", str(tmp_path)]) == 2
    assert list(tmp_path.iterdir()) == []


def test_normalization_deletion_is_not_complete_input(normalizer, splitter):
    text = "12\n\n13\n\n작업 준비"
    raw = {"doc_id": "normalization-loss", "text": text, "document_sha256": text_digest(text), "context": {}}
    ann = {"doc_id": raw["doc_id"], "input_sha256": value_digest(raw), "proposed_grade": "S2", "requires_full_body": True,
           "requirements": [{"id": "body", "kind": "body", "start": text.index("작업"), "end": len(text), "sha256": text_digest("작업 준비")}]}
    result = fit.inspect_view(raw, ann, CharacterTokenizer(), normalizer, splitter, profile="train_body_first")
    assert result["requirements"][0]["visible"] is True
    assert result["normalization_retained_non_whitespace_content"] is False
    assert result["all_required_input_observed"] is False


def test_unknown_token_offset_coverage_is_not_full_observability(normalizer, splitter, pairs):
    class WithUnknown(CharacterTokenizer):
        def encode(self, text):
            enc = super().encode(text)
            enc.tokens[1] = "[UNK]"
            return enc

    raw, ann = pairs[0][0]["input"], pairs[1][0]
    result = fit.inspect_view(raw, ann, WithUnknown(), normalizer, splitter,
                              profile="proposed_body_context_first", max_length=1024)
    assert result["full_token_offset_coverage"] is True and result["unk_tokens"] == 1
    assert result["all_required_input_observed"] is False


@pytest.mark.parametrize("size,overlap", [(True, 0), (512, False), (15, 0), (64, 64), (512, -1)])
def test_invalid_limits_rejected(size, overlap, pairs, normalizer, splitter):
    with pytest.raises(FactContractError, match="limits_invalid"):
        fit.inspect_view(pairs[0][0]["input"], pairs[1][0], CharacterTokenizer(), normalizer, splitter,
                         profile="train_body_first", max_length=size, overlap=overlap)
