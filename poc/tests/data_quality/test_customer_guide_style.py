"""Paired style diagnostics: no parent leakage, no invented facts or releases."""
from __future__ import annotations

import copy
import json
import re
from collections import Counter

import pytest

import audit_customer_guide_style as style
from customer_guide_style_variants import PAIRS, rebind_claims, transform
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest


@pytest.fixture(scope="module")
def material():
    return style.material()


@pytest.mark.parametrize("plain,polite", PAIRS)
def test_every_whitelisted_pair_is_bidirectional(plain, polite):
    assert transform("제목\n\n"+plain+".", "polite")["text"] == "제목\n\n"+polite+"."
    assert transform("제목\n\n"+polite+".", "plain")["text"] == "제목\n\n"+plain+"."


@pytest.mark.parametrize("word", ["0.07mm다", "15N입니다", "100mm였습니다", "16곳이었다", "55%다", "5N/cm다"])
def test_numeric_copula_changes_no_number_or_unit(word):
    source = "제목\n\n"+word+"."
    for target in style.STYLES:
        result = transform(source, target)
        assert re.findall(r"\d+(?:\.\d+)?", source) == re.findall(r"\d+(?:\.\d+)?", result["text"])


@pytest.mark.parametrize("ending", ["판단되겠지요", "알아보자고요", "추측된다"])
def test_unreviewed_ending_not_blindly_rewritten(ending):
    with pytest.raises(FactContractError, match="unreviewed_ending"):
        transform("제목\n\n"+ending+".", "polite")


def test_requests_fragments_numbers_conditions_and_negation_preserved():
    source = "제목\n\n1. 확인해 주세요.\n조건: 결과표. 12개를 넘으면 적용하지 않는다. 원인은 아니다."
    value = transform(source, "polite")
    assert "1. 확인해 주세요.\n조건: 결과표. 12개를 넘으면 적용하지 않습니다." in value["text"]
    assert "원인은 아닙니다." in value["text"]
    assert len(value["preserved_requests"]) == 1
    assert value["whole_semantic_equivalence_certified"] is False


def test_source_claim_cutting_an_edit_is_rejected():
    source = "제목\n\n결과를 확인한다."
    variant = transform(source, "polite")
    offset = source.index("확인")+1
    claims = [{"start": offset, "end": offset+1, "quote": source[offset:offset+1]}]
    with pytest.raises(FactContractError, match="claim_cuts_edit"):
        rebind_claims(claims, source, variant)


def test_no_new_documents_no_false_semantic_authority(material):
    docs, answers, panel, variants, payload = material
    summary = json.loads(payload["summary.json"])
    assert len(docs) == len(answers) == 164 and len(panel) == 48 and len(variants) == 96
    assert summary["changed_views"] == 57 and summary["unchanged_control_views"] == 39
    assert summary["new_benchmark_documents"] == 0 and summary["benchmark_total_unchanged"] == 164
    assert summary["arithmetic_view_checks"] == 96 and summary["rebound_claims"] == 192
    assert summary["whole_semantic_equivalence_certified"] is False
    assert summary["training_allowed"] is summary["model_evaluation_allowed"] is summary["gold_eligible"] is False


@pytest.mark.parametrize("index", range(48))
def test_each_parent_rebinds_claims_and_preserves_every_unedited_span(material, index):
    _, _, panel, variants, payload = material
    d = panel[index]
    bindings = style._rows(payload["audit/edits_and_bindings.jsonl"].encode())
    for target in style.STYLES:
        v = variants[(d.input.doc_id, target)]
        b = next(r for r in bindings if r["parent_doc_id"] == d.input.doc_id and r["style"] == target)
        assert d.input.context == v.input.context
        assert d.input.text.splitlines()[0] == v.input.text.splitlines()[0]
        old_cursor = new_cursor = 0
        for edit in b["edits"]:
            assert d.input.text[old_cursor:edit["source_start"]] == v.input.text[new_cursor:edit["target_start"]]
            assert d.input.text[edit["source_start"]:edit["source_end"]] == edit["before"]
            assert v.input.text[edit["target_start"]:edit["target_end"]] == edit["after"]
            old_cursor, new_cursor = edit["source_end"], edit["target_end"]
        assert d.input.text[old_cursor:] == v.input.text[new_cursor:]
        for claim in v.claims:
            assert v.input.text[claim.start:claim.end] == claim.quote and text_digest(claim.quote) == claim.sha256
        assert b["policy_reference_unchanged"] is True


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_variants_do_not_gain_release_permission(material, purpose):
    variants = style._rows(material[4]["variants/inputs.jsonl"].encode())
    with pytest.raises(ValueError):
        assert_dataset_usage(variants, purpose=purpose)


@pytest.mark.parametrize("profile", style.PROFILES)
def test_augmented_and_duplicate_control_have_same_rows_and_parent_mass(material, profile):
    docs, answers, _, variants, _ = material
    labels = [next(a["reference_grade"] for a in answers if a["doc_id"] == d.input.doc_id) for d in docs]
    selection = list(range(30))
    observed = {}
    for arm in style.ARMS:
        texts, y, weights, origins = style.fold_training(docs, labels, selection, variants, profile, arm)
        mass = Counter()
        for origin, w in zip(origins, weights, strict=True):
            mass[origin] += w
        assert all(v == 1 for v in mass.values()) and len(mass) == 30
        assert len(texts) == len(y) == len(weights) == len(origins)
        observed[arm] = (texts, y, weights, origins)
    assert len(observed["original_training"][0]) == 30
    assert len(observed["duplicate_control_training"][0]) == len(observed["style_balanced_training"][0]) == 60
    assert observed["duplicate_control_training"][1:] == observed["style_balanced_training"][1:]


class StubProbe:
    def __init__(self, fits):
        self.fits = fits

    def fit(self, texts, labels, **kwargs):
        self.fits.append((list(texts), list(labels), kwargs["linearsvc__sample_weight"]))
        return self

    def predict(self, texts):
        return ["S3" if "니다." in text else "S1" for text in texts]


def test_pair_folds_cover_once_and_never_split_related_parents(material):
    fits = []
    result = style.measure_paired(seeds=1, folds=2, pipeline_factory=lambda seed: StubProbe(fits))
    assert len(fits) == 12 and len(result["paired_predictions"]) == 48*2*3
    assignment = result["fold_assignments"]
    counts = Counter()
    doc_map = {d.input.doc_id: d for d in material[0]}
    all_variants = material[3]
    for f in assignment:
        assert not set(f["train_parent_ids"]) & set(f["test_parent_ids"])
        assert not set(f["train_groups"]) & set(f["test_groups"])
        counts.update(f["test_parent_ids"])
        # All six fits for a fold must exclude every held-out parent's views.
        forbidden = {style.presented_text(doc_map[pid], p) for pid in f["test_parent_ids"] for p in style.PROFILES}
        forbidden |= {style.presented_text(v, p) for (pid, _), v in all_variants.items() if pid in f["test_parent_ids"] for p in style.PROFILES}
        for texts, _, _ in fits[f["fold"]*6:(f["fold"]+1)*6]:
            assert not set(texts) & forbidden
    assert len(counts) == 164 and all(v == 1 for v in counts.values())
    assert result["new_benchmark_documents"] == 0 and result["confidence_intervals_computed"] is False
    for profile in style.PROFILES:
        for arm in style.ARMS:
            assert result["summary"][profile][arm]["parent_seed_observations"] == 48


@pytest.mark.parametrize("seeds,folds", [(0, 5), (True, 5), (101, 5), (1, 1), (1, 6)])
def test_bad_cv_parameters_fail(seeds, folds):
    with pytest.raises(FactContractError):
        style.measure_paired(seeds=seeds, folds=folds)


def test_bad_prediction_not_dropped_from_denominator():
    class Invalid(StubProbe):
        def predict(self, texts):
            return []
    with pytest.raises(FactContractError, match="prediction_invalid"):
        style.measure_paired(seeds=1, folds=2, pipeline_factory=lambda seed: Invalid([]))


def test_pack_roundtrip_and_no_overwrite(tmp_path):
    root = tmp_path/"pack"
    result = style.prepare(root)
    assert style.verify(root) == result
    with pytest.raises(FactContractError):
        style.prepare(root)
    assert style.main(["verify", "--pack", str(tmp_path/"missing")]) == 2


@pytest.mark.parametrize("kind", ["empty", "body", "context", "edit", "source", "permission", "path", "extra", "token"])
def test_rehashed_pack_changes_rejected(tmp_path, kind):
    root = tmp_path/"pack"
    style.prepare(root)
    path = root/"manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    member = None
    if kind == "empty":
        member, content = "variants/inputs.jsonl", ""
    elif kind in {"body", "context"}:
        member = "variants/inputs.jsonl"
        rows = style._rows((root/member).read_bytes())
        if kind == "body":
            rows[0]["text"] += "사실 추가"
        else:
            rows[0]["context"] = []
        content = style._jsonl(rows)
    elif kind == "edit":
        member = "audit/edits_and_bindings.jsonl"
        rows = style._rows((root/member).read_bytes())
        next(r for r in rows if r["edits"])["edits"][0]["before"] = "조작"
        content = style._jsonl(rows)
    elif kind == "token":
        member, content = "audit/tokenizer.json", style._json({"status": "measured", "views": []})
    elif kind == "source":
        manifest["sources_sha256"] = {}
    elif kind == "permission":
        manifest["training_allowed"] = 0
    elif kind == "path":
        manifest["files"]["../escape"] = "0"*64
    elif kind == "extra":
        (root/"extra.txt").write_text("x", encoding="utf-8")
    if member:
        (root/member).write_text(content, encoding="utf-8", newline="\n")
        manifest["files"][member] = text_digest(content)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        style.verify(root)


def test_wrong_parent_fails_before_any_output(tmp_path, monkeypatch):
    root = tmp_path/"parent"
    root.mkdir()
    (root/"manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(style.parent, "verify", lambda p: {})
    with pytest.raises(FactContractError, match="parent_pack_mismatch"):
        style.prepare(tmp_path/"out", parent_pack=root)
    assert not (tmp_path/"out").exists()


def test_audit_output_is_new_outside_packet(tmp_path, monkeypatch):
    root = tmp_path/"pack"
    style.prepare(root)
    monkeypatch.setattr(style, "measure_paired", lambda **kw: {**style.FLAGS, "status": "diagnostic"})
    result = style.audit(root, tmp_path/"result.json")
    assert result["training_allowed"] is False and result["diagnostic_pack_sha256"]
    with pytest.raises(FactContractError):
        style.audit(root, root/"inside.json")
    with pytest.raises(FactContractError):
        style.audit(root, tmp_path/"result.json")


def test_source_objects_not_mutated(material):
    docs_before = copy.deepcopy([d.model_dump() for d in material[0]])
    style.material()
    assert [d.model_dump() for d in material[0]] == docs_before
