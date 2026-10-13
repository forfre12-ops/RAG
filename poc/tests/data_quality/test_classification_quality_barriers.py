from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import classification_audit_inputs as inputs
import measure_grade_phrase_leak as leak
from koipa import dataset_usage as usage

POC = Path(__file__).resolve().parents[2]


def write_rows(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def make_pack(path, *, count=4, review=1, long_phrase=False):
    docs, answers = [], []
    for g in inputs.GRADES:
        for i in range(count):
            clue = (g + "등급별 반복 예시 단서" + "긴문장" * 45) if long_phrase else g + "등급별 반복 예시 단서"
            text = f"가상 문서 번호 {i} / {g}입니다. {clue}."
            row = {"doc_id": f"test-{g}-{i}", "family_id": f"family-{i}", "text": text,
                   "text_sha256": inputs._hash(text), "policy_version": "test-policy",
                   "policy_sha256": "a" * 64, "source": {"kind": "test"}}
            docs.append(row)
            answers.append({k: v for k, v in row.items() if k not in {"text", "source"}} |
                           {"expected_grade": g, "expected_status": "recommended"})
    for i in range(review):
        row = copy.deepcopy(docs[0])
        row["doc_id"] = f"review-{i}"
        docs.append(row)
        answers.append({k: v for k, v in row.items() if k not in {"text", "source"}} |
                       {"expected_grade": None, "expected_status": "needs_evidence"})
    write_rows(path / "inputs.jsonl", docs)
    write_rows(path / "answers.candidate.jsonl", answers)
    return docs, answers


def test_paired_pool_counts_and_default_does_not_read_sealed(tmp_path):
    make_pack(tmp_path / "development")
    (tmp_path / "sealed_candidate").mkdir()
    # Deliberately corrupt: default development must not read sealed answers.
    (tmp_path / "sealed_candidate/inputs.jsonl").write_text("bad", encoding="utf-8")
    rows, report = inputs.load_audit_pool(tmp_path)
    assert len(rows) == 16
    assert report["n_input_rows"] == 17 and report["excluded_review_rows"] == 1
    assert report["split"] == "development"
    assert not report["customer_accuracy_claim_allowed"]
    with pytest.raises(ValueError):
        inputs.load_audit_pool(tmp_path, split="all")


@pytest.mark.parametrize("mutation", ["input_duplicate", "answer_duplicate", "missing", "hash", "policy",
                                    "family", "label_in_input", "empty", "grade", "status", "review_grade"])
def test_pair_binding_fail_closed(tmp_path, mutation):
    docs, answers = make_pack(tmp_path)
    if mutation == "input_duplicate":
        docs.append(docs[0])
    if mutation == "answer_duplicate":
        answers.append(answers[0])
    if mutation == "missing":
        answers.pop()
    if mutation == "hash":
        docs[0]["text"] += "changed"
    if mutation == "policy":
        answers[0]["policy_sha256"] = "b" * 64
    if mutation == "family":
        answers[0]["family_id"] = "different"
    if mutation == "label_in_input":
        docs[0]["label"] = "TS"
    if mutation == "empty":
        docs[0]["text"] = " "
    if mutation == "grade":
        answers[0]["expected_grade"] = "INVALID"
    if mutation == "status":
        answers[0]["expected_status"] = "unknown"
    if mutation == "review_grade":
        answers[-1]["expected_grade"] = "S3"
    write_rows(tmp_path / "inputs.jsonl", docs)
    write_rows(tmp_path / "answers.candidate.jsonl", answers)
    with pytest.raises(ValueError):
        inputs.load_audit_pool(tmp_path)


def test_empty_and_all_review_exit_nonzero(tmp_path):
    assert leak.main(["--pool", str(tmp_path)]) == 2
    docs, answers = make_pack(tmp_path)
    for answer in answers:
        answer.update(expected_grade=None, expected_status="needs_evidence")
    write_rows(tmp_path / "answers.candidate.jsonl", answers)
    with pytest.raises(ValueError, match="excluded_review=17"):
        inputs.load_audit_pool(tmp_path)
    assert leak.main(["--pool", str(tmp_path)]) == 2


def test_json_cli_empty_exit_code(tmp_path):
    result = subprocess.run([sys.executable, "-B", str(POC / "scripts/measure_grade_phrase_leak.py"),
                             "--pool", str(tmp_path)], capture_output=True)
    assert result.returncode == 2


def test_long_phrases_are_not_truncated_before_matching(tmp_path):
    make_pack(tmp_path, long_phrase=True)
    report = leak.audit(pool=tmp_path, min_docs=2, min_chars=120)
    assert report["covered_docs"] == 16
    assert report["single_clue_hits"] == 16
    assert report["excess_pp"] > 20
    assert all(len(r["sentence"]) > 120 for r in report["top"])


def test_strict_alarm_and_output_privacy_no_overwrite(tmp_path):
    pool = tmp_path / "pool"
    make_pack(pool)
    output = tmp_path / "result.json"
    args = ["--pool", str(pool), "--min-docs", "2", "--strict", "--json", str(output)]
    assert leak.main(args) == 3
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["status"] == "SHORTCUT_WARNING"
    assert all("sentence" not in row and "sentence_sha256" in row for row in report["top"])
    assert leak.main(args) == 2
    assert leak.main(["--pool", str(pool), "--json", str(pool / "bad.json")]) == 2
    assert not (pool / "bad.json").exists()


def test_pool_calls_do_not_mutate_default_root(tmp_path):
    import eval_on_clean_candidates as legacy
    original = legacy.ROOT
    make_pack(tmp_path)
    leak.audit(pool=tmp_path, min_docs=2, min_chars=3)
    assert legacy.ROOT == original


def test_legacy_revision_is_required_not_silently_fallback(tmp_path):
    meta = {"doc_id": "x-TS-1", "intended_label": "TS", "content_revision_path": "missing.md"}
    (tmp_path / "item.metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    (tmp_path / "item.md").write_text("stale fallback", encoding="utf-8")
    with pytest.raises(OSError):
        inputs.load_audit_pool(tmp_path)
    (tmp_path / "missing.md").write_text("current revision", encoding="utf-8")
    rows, _ = inputs.load_audit_pool(tmp_path)
    assert rows[0]["text"] == "current revision"


def test_unlabeled_legacy_upload_is_counted_not_silently_lost(tmp_path):
    (tmp_path / "upload.metadata.json").write_text(
        json.dumps({"doc_id": "uploaded-not-labelled", "intended_label": None}), encoding="utf-8")
    with pytest.raises(ValueError, match="excluded_review=1"):
        inputs.load_audit_pool(tmp_path)
    (tmp_path / "graded.metadata.json").write_text(
        json.dumps({"doc_id": "x-S1-1", "intended_label": "S1"}), encoding="utf-8")
    (tmp_path / "graded.md").write_text("labelled example", encoding="utf-8")
    rows, report = inputs.load_audit_pool(tmp_path)
    assert len(rows) == 1 and report["n_input_rows"] == 2
    assert report["excluded_review_rows"] == 1 and report["exclusion_reason"] == "unlabeled"


def test_ngram_probe_has_group_folds_permutation_and_recall(tmp_path):
    from measure_ngram_shortcuts import measure
    make_pack(tmp_path, count=6)
    rows, _ = inputs.load_audit_pool(tmp_path)
    result = measure(rows, seeds=2, folds=3)
    for name in ("stratified_cv", "family_cv"):
        assert result[name]["status"] == "MEASURED"
        assert len(result[name]["runs"]) == len(result[name]["permutation_runs"]) == 2
        assert set(result[name]["runs"][0]["recall"]) == set(inputs.GRADES)
        assert 0 <= result[name]["permutation_mean"] <= 1
    assert result["family_cv_available"]
    assert result["stratified_cv"]["mean"] >= 0.8
    assert not result["customer_accuracy_measured"]


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
@pytest.mark.parametrize("field", ["doc_id", "family_id", "policy_version", "dataset_role", "source_doc_id", "source_family_id"])
def test_registered_fixture_cannot_be_promoted_by_label_or_signature(purpose, field):
    known = usage.fixture_registry()["records"][0]
    row = {"text": "new unrelated body", "label": "S3", "training_allowed": True,
           "model_evaluation_allowed": True, "approval_status": "approved", "human_signature": "fake"}
    row[field] = "policy_fixture" if field == "dataset_role" else known[field.removeprefix("source_")]
    with pytest.raises(usage.DatasetUsageError, match="prohibited"):
        usage.assert_dataset_usage([row], purpose=purpose)


def test_body_copy_still_blocked_after_removing_metadata(monkeypatch):
    text = "fixture example long enough"
    monkeypatch.setattr(usage, "_fixture_index", lambda: (set(), set(), set(), {usage.body_fingerprint(text)}))
    with pytest.raises(usage.DatasetUsageError, match="body"):
        usage.assert_dataset_usage([{"text": "fixture\n example long\t enough", "label": "S2"}], purpose="training")


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_manifest_restriction_and_empty_data(tmp_path, purpose):
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": "content-reference-pack-v1"}), encoding="utf-8")
    with pytest.raises(usage.DatasetUsageError, match="policy_fixture"):
        usage.assert_dataset_usage([{"text": "unrelated", "label": "S2"}], purpose=purpose,
                                   source=tmp_path / "development/inputs.jsonl")
    with pytest.raises(usage.DatasetUsageError, match="Empty"):
        usage.assert_dataset_usage([], purpose=purpose)


def test_missing_registry_is_not_bypassed(tmp_path, monkeypatch):
    usage.fixture_registry.cache_clear()
    monkeypatch.setattr(usage, "REGISTRY_PATH", tmp_path / "missing.json")
    try:
        with pytest.raises(usage.DatasetUsageError, match="registry"):
            usage.fixture_registry()
    finally:
        usage.fixture_registry.cache_clear()


def test_normal_rows_still_pass_without_granting_approval():
    assert usage.assert_dataset_usage([{"text": "unrelated ordinary example", "label": "S2"}], purpose="training") is None


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None])
def test_non_boolean_permissions_fail_closed(value):
    with pytest.raises(usage.DatasetUsageError, match="boolean"):
        usage.assert_dataset_usage([{"text": "unrelated", "training_allowed": value}], purpose="training")


def test_no_signal_probe_is_not_reported_as_shortcut():
    from measure_ngram_shortcuts import measure
    rows = [{"text": "identical non informative example", "label": grade, "family_id": f"group-{i}"}
            for i in range(4) for grade in inputs.GRADES]
    result = measure(rows, seeds=1, folds=2)
    for name in ("stratified_cv", "family_cv"):
        assert result[name]["mean"] <= 0.5
        assert result[name]["excess_pp"] < 30


@pytest.mark.parametrize("module,function", [
    ("koipa.modules.m4_training.trainer", "_load_training_jsonl"),
    ("koipa.modules.m4_training.trainer", "_load_jsonl"),
    ("p1_train_classifier", "load_jsonl"),
    ("eval_p1_model_gold", "load_jsonl"),
    ("score_model_on_eval", "_read_jsonl"),
    ("train_factor_model", "_load"),
    ("ab_tell_train", "load"),
])
def test_actual_loader_entrypoints_reject_fixture(tmp_path, module, function):
    path = tmp_path / "copied.jsonl"
    row = {"doc_id": usage.fixture_registry()["records"][0]["doc_id"],
           "text": "copied fixture with changed body", "label": "S1"}
    write_rows(path, [row])
    with pytest.raises(usage.DatasetUsageError, match="prohibited"):
        getattr(importlib.import_module(module), function)(path)


@pytest.mark.parametrize("function", ["predict_direct", "predict_api_like"])
def test_prediction_guard_runs_before_model_loading(monkeypatch, function):
    from eval_p1_model_gold import predict_api_like, predict_direct
    monkeypatch.setitem(sys.modules, "torch", None)
    row = {"text": "test", "label": "S1", "dataset_role": "policy_fixture"}
    with pytest.raises(usage.DatasetUsageError):
        {"predict_direct": predict_direct, "predict_api_like": predict_api_like}[function](Path("missing-model"), [row])


def test_checkpoint_evaluation_guard_runs_before_loading(monkeypatch):
    from koipa.proxy_training_finalization import load_model_document_logits
    monkeypatch.setitem(sys.modules, "torch", None)
    with pytest.raises(usage.DatasetUsageError, match="policy_fixture"):
        load_model_document_logits(Path("missing-model"), [{"text": "test", "dataset_role": "policy_fixture"}])


def test_reference_validator_blocks_predictions_before_reading_them(tmp_path, monkeypatch):
    import validate_content_reference as validator
    (tmp_path / "manifest.json").write_text(json.dumps({"schema_version": "content-reference-pack-v1"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["validate", "--pack", str(tmp_path), "--predictions", "missing.jsonl",
                                     "--out", str(tmp_path.parent / "not_created_quality_result.json")])
    assert validator.main() == 2


def test_registry_original_fingerprints_when_local_packs_exist():
    registry = usage.fixture_registry()
    assert len(registry["records"]) == 180 and len(registry["packs"]) == 2
    found = 0
    for pack in registry["packs"]:
        root = POC / pack["path"]
        if not root.exists():
            continue  # Ignored reports are not required in clean CI.
        found += 1
        assert hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest() == pack["manifest_sha256"]
        for item in pack["files"]:
            assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
            for row in inputs.read_rows(root / item["path"]):
                with pytest.raises(usage.DatasetUsageError):
                    usage.assert_dataset_usage([{"text": row["text"], "label": "S3"}], purpose="training")
    assert found in (0, 1, 2)


def test_selected_manifest_hash_is_checked(tmp_path):
    make_pack(tmp_path)
    files = [{"path": p.name, "sha256": inputs.sha256(p)} for p in tmp_path.glob("*.jsonl")]
    (tmp_path / "manifest.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    inputs.load_audit_pool(tmp_path)
    # Even a self-consistent answer modification must not bypass the frozen bytes.
    answers = inputs.read_rows(tmp_path / "answers.candidate.jsonl")
    answers[0]["expected_grade"] = "S3"
    write_rows(tmp_path / "answers.candidate.jsonl", answers)
    with pytest.raises(ValueError, match="manifest hash"):
        inputs.load_audit_pool(tmp_path)


def make_view_pack(path):
    docs, answers = make_pack(path, count=2, review=0)
    for doc, answer in zip(docs, answers):
        doc["context"] = {"world": "fictional_only", "facts": {"x": 1}}
        doc["context_sha256"] = inputs._record_hash(doc["context"])
        answer["input_record_sha256"] = inputs._record_hash(doc)
        answer["context_sha256"] = doc["context_sha256"]
        combined = doc["text"] + "\n\n[명시적 가상 조건]\n" + json.dumps(
            doc["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        answer["views"] = {
            "body_only": {"expected_grade": None, "expected_status": "needs_evidence",
                          "grade_metric_eligible": False, "input_sha256": inputs._hash(doc["text"])},
            "body_plus_synthetic_context": {"expected_grade": answer["expected_grade"], "expected_status": "recommended",
                                            "grade_metric_eligible": True, "input_sha256": inputs._hash(combined)}}
    write_rows(path / "inputs.jsonl", docs)
    write_rows(path / "answers.candidate.jsonl", answers)
    return docs, answers


def test_context_view_never_scores_hidden_context_as_body_truth(tmp_path):
    make_view_pack(tmp_path)
    with pytest.raises(ValueError, match="No grade candidates"):
        inputs.load_audit_pool(tmp_path)
    rows, meta = inputs.load_audit_pool(tmp_path, view="body_plus_synthetic_context")
    assert len(rows) == 8 and meta["excluded_review_rows"] == 0
    assert all("fictional_only" in row["text"] for row in rows)


@pytest.mark.parametrize("mutation", ["context", "view_hash", "record_hash", "context_hash"])
def test_context_view_binding_is_strict(tmp_path, mutation):
    docs, answers = make_view_pack(tmp_path)
    if mutation == "context":
        docs[0]["context"]["facts"]["x"] = 2
    elif mutation == "view_hash":
        answers[0]["views"]["body_plus_synthetic_context"]["input_sha256"] = "a" * 64
    elif mutation == "record_hash":
        answers[0]["input_record_sha256"] = "b" * 64
    else:
        answers[0]["context_sha256"] = "c" * 64
    write_rows(tmp_path / "inputs.jsonl", docs)
    write_rows(tmp_path / "answers.candidate.jsonl", answers)
    with pytest.raises(ValueError):
        inputs.load_audit_pool(tmp_path, view="body_plus_synthetic_context")


def test_decision_readiness_cannot_manufacture_authority():
    from audit_classification_readiness import DEFAULT, audit
    contract = json.loads(DEFAULT.read_text(encoding="utf-8"))
    report = audit(contract)
    assert report["pending_count"] == 8 and report["status"] == "HOLD_OWNER_DECISIONS"
    for row in contract["decisions"]:
        row.update(status="recorded_for_verification", value="example", owner_id="declared", evidence_ref="declared")
    report = audit(contract)
    assert report["status"] == "RECORDED_NOT_AUTHENTICATED"
    assert not report["execution_authorized_by_this_check"] and not report["approval_authenticity_verified"]
    contract["execution_authorized"] = True
    with pytest.raises(ValueError, match="authority"):
        audit(contract)


def test_guide_v22_comparison_is_pinned_to_live_code():
    from audit_classification_readiness import DEFAULT
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm
    contract = json.loads(DEFAULT.read_text(encoding="utf-8"))
    table = next(r for r in contract["decisions"] if r["id"] == "D04")["implementation_comparison_not_answers"]
    for row in table:
        for mode in ("guide", "v22", "fnr"):
            assert grade_from_svm(row["s"], row["v"], row["m"], mode=mode) == row[mode]
