"""Matched fold coverage and immutable title projections, without model downloads."""
import copy
import hashlib
import json
import random
import string

import pytest

import audit_customer_title_sensitivity_v1 as title
from koipa.customer_benchmark import FLAGS, GRADES, validate_documents
from koipa.policy_facts import FactContractError, text_digest, value_digest


def sample(index):
    rng = random.Random(8890 + index)
    body = f"제목 {index}\n\n" + "".join(rng.choices(string.ascii_letters, k=250)) + "\n두 번째 단락은 그대로 남깁니다.\n"
    inp = {"doc_id": "doc-" + text_digest(str(index))[:24], "text": body, "context": []}
    record = {**FLAGS, "schema_version": "customer-synthetic-draft-v1", "document_origin": "synthetic", "input": inp,
              "input_sha256": value_digest(inp), "family_id": f"family-{index // 4}", "scenario_id": f"scenario-{index}",
              "template_family_id": f"template-{index}", "domain": "test-only",
              "claims": [{"name": "claim-one", "claim": "test binding", "quote": body[:4], "start": 0, "end": 4,
                          "sha256": text_digest(body[:4]), "status": "authored_binding_only"}]}
    grade = GRADES[index % 4]
    answer = {"doc_id": inp["doc_id"], "input_sha256": record["input_sha256"], "policy_id": "test-policy", "policy_version": "0.1",
              "policy_sha256": "a" * 64, "reference_grade": grade, "rule_ids": ["test-rule"], "evidence_names": ["claim-one"],
              "other_grade_exclusions": {g: "test-only exclusion" for g in GRADES if g != grade}, "status": "authored_candidate"}
    return record, answer


@pytest.fixture
def corpus():
    pairs = [sample(i) for i in range(20)]
    records, answers = [p[0] for p in pairs], [p[1] for p in pairs]
    groups = {}
    for start in range(0, 20, 4):
        ids = sorted(r["input"]["doc_id"] for r in records[start:start + 4])
        groups[ids[0]] = ids
    grouping = {"documents": 20, "documents_sha256": value_digest(sorted(records, key=lambda r: r["input"]["doc_id"])),
                "groups": groups, "semantic_links": []}
    return records, answers, grouping


@pytest.mark.parametrize("original", ["원제목\n\n원본문.\n다음 줄.", "원제목\r\n\r\n원본문.\r\n", "원제목\n원본문.\n", "문서\n\n동일 제목 대조."])
def test_projection_preserves_entire_suffix(original):
    before = original.encode()
    views = title.project_views(original)
    assert views["original"].encode() == before
    assert original.endswith(views["title_removed"])
    assert views["neutral_title"] == "문서" + views["title_removed"]
    assert views["title_removed"].startswith(("\n", "\r\n"))
    assert original.encode() == before


@pytest.mark.parametrize("invalid", [None, "", "한 줄만", "\n본문", " \n본문", "제목\n\n "])
def test_invalid_projection_fails(invalid):
    with pytest.raises(FactContractError):
        title.project_views(invalid)


def test_fake_pipeline_fit_originals_only_and_heldout_all_views(corpus):
    records, answers, grouping = corpus
    all_views = {r["input"]["doc_id"]: title.project_views(r["input"]["text"]) for r in records}
    inverse = {text: (doc_id, view) for doc_id, views in all_views.items() for view, text in views.items()}
    fitted = []
    class Fake:
        def __init__(self, seed):
            self.seed = seed
            self.predicted_parents = []
        def fit(self, texts, labels):
            self.parents = {inverse[t][0] for t in texts}
            assert all(inverse[t][1] == "original" for t in texts)
            assert len(texts) == len(labels)
            fitted.append(self)
            return self
        def predict(self, texts):
            ids = [inverse[t][0] for t in texts]
            assert not set(ids) & self.parents
            assert len(ids) == len(set(ids))
            self.predicted_parents.append(ids)
            return ["S1" if inverse[t][1] == "neutral_title" else "TS" for t in texts]
    before = copy.deepcopy(corpus)
    result = title.measure(*corpus, seeds=2, folds=5, pipeline_factory=Fake)
    assert corpus == before
    assert len(fitted) == result["diagnostic_model_fits"] == 10
    assert result["summary"]["parent_seed_observations"] == 40
    assert result["summary"]["unique_parents"] == 20
    assert result["summary"]["by_view"]["neutral_title"]["prediction_changes_vs_original"] == 40
    assert result["summary"]["by_view"]["neutral_title"]["correct_to_wrong"] == 10
    assert result["summary"]["by_view"]["neutral_title"]["wrong_to_correct"] == 10
    assert not result["statistical_independence_certified"]
    for fitted_model, assignment in zip(fitted, result["fold_assignments"], strict=True):
        assert fitted_model.predicted_parents[0] == fitted_model.predicted_parents[1] == fitted_model.predicted_parents[2]
        assert set(assignment["train_parent_ids"]) == fitted_model.parents
        assert not set(assignment["train_groups"]) & set(assignment["test_groups"])
    for seed in (0, 1):
        selected = [r["parent_doc_id"] for r in result["paired_predictions"] if r["seed"] == seed]
        assert set(selected) == set(all_views) and len(selected) == 20
    assert all(result[k] is False for k in FLAGS)
    assert result["new_benchmark_documents"] == 0


@pytest.mark.parametrize("seeds,folds", [(0, 5), (True, 5), (31, 5), (1, 1), (1, 6), (1, True)])
def test_invalid_cv_parameters(corpus, seeds, folds):
    with pytest.raises(FactContractError):
        title.measure(*corpus, seeds=seeds, folds=folds)


@pytest.mark.parametrize("mode", ["short", "grade", "boolean"])
def test_invalid_predictions_fail(corpus, mode):
    class Fake:
        def fit(self, *args):
            return self
        def predict(self, rows):
            return [] if mode == "short" else ["HOLD" if mode == "grade" else True] * len(rows)
    with pytest.raises(FactContractError, match="predictions_invalid"):
        title.measure(*corpus, seeds=1, folds=2, pipeline_factory=lambda seed: Fake())


@pytest.mark.parametrize("change", ["empty", "missing", "extra", "duplicate", "count", "hash", "order", "declared_split", "semantic_hash", "semantic_split"])
def test_group_binding_and_transitivity_fail_closed(corpus, change):
    records, _, grouping = copy.deepcopy(corpus)
    docs = validate_documents(records)
    keys = list(grouping["groups"])
    if change == "empty":
        grouping["groups"] = {}
    elif change == "missing":
        del grouping["groups"][keys[0]]
    elif change == "extra":
        grouping["groups"][keys[0]].append("doc-" + "f" * 24)
    elif change == "duplicate":
        grouping["groups"][keys[0]].append(grouping["groups"][keys[0]][0])
    elif change == "count":
        grouping["documents"] = 19
    elif change == "hash":
        grouping["documents_sha256"] = "f" * 64
    elif change == "order":
        grouping["groups"][keys[0]].reverse()
    elif change == "declared_split":
        members = grouping["groups"].pop(keys[0])
        grouping["groups"].update({i: [i] for i in members})
    else:
        ids = [grouping["groups"][keys[0]][0], grouping["groups"][keys[1]][0]]
        by_id = {d.input.doc_id: d for d in docs}
        hashes = {i: by_id[i].input_sha256 for i in ids}
        if change == "semantic_hash":
            hashes[ids[0]] = "f" * 64
        grouping["semantic_links"] = [{"members": ids, "input_hashes": hashes}]
    with pytest.raises(FactContractError):
        title.validate_grouping(docs, grouping)


def test_empty_source_or_summary_fails(corpus):
    with pytest.raises(FactContractError):
        title.measure([], [], corpus[2])
    with pytest.raises(FactContractError):
        title.summarize([])
    with pytest.raises(FactContractError):
        title._rows(b"\n\n")


def test_default_pipeline_is_declared_char_probe():
    model = title.default_pipeline(3)
    tfidf, classifier = model.steps[0][1], model.steps[1][1]
    assert tfidf.analyzer == "char" and tfidf.ngram_range == (2, 5)
    assert classifier.random_state == 3


@pytest.fixture
def fake_pack(tmp_path, monkeypatch):
    pack = tmp_path / "source"
    pack.mkdir()
    (pack / "manifest.json").write_text("{}", encoding="utf-8")
    (pack / "authoring").mkdir()
    (pack / "answers").mkdir()
    (pack / "authoring/documents.jsonl").write_text("{}\n" * 196, encoding="utf-8")
    (pack / "answers/answers.candidate.jsonl").write_text("{}\n" * 196, encoding="utf-8")
    grouping = tmp_path / "groups.json"
    grouping.write_text(json.dumps({"groups": {str(i): [] for i in range(178)}}), encoding="utf-8")
    monkeypatch.setattr(title, "PACK_MANIFEST_SHA256", hashlib.sha256((pack / "manifest.json").read_bytes()).hexdigest())
    monkeypatch.setattr(title, "GROUPING_FILE_SHA256", hashlib.sha256(grouping.read_bytes()).hexdigest())
    calls = []
    monkeypatch.setattr(title.batch05, "verify", lambda p: calls.append(str(p)))
    monkeypatch.setattr(title, "measure", lambda *args, **kwargs: {"status": "test-only", "summary": {}})
    return pack, grouping, tmp_path / "out.json", calls


def test_runner_revalidates_and_never_overwrites(fake_pack):
    pack, grouping, out, calls = fake_pack
    before = {p: p.read_bytes() for p in pack.rglob("*") if p.is_file()}
    result = title.run(pack, grouping, out)
    assert result["provenance"]["source_pack_verified_before_and_after"]
    assert len(calls) == 2
    assert all(p.read_bytes() == raw for p, raw in before.items())
    initial = out.read_bytes()
    with pytest.raises(FactContractError):
        title.run(pack, grouping, out)
    assert out.read_bytes() == initial


def test_output_cannot_enter_pack_or_grouping_frozen_ancestor(fake_pack):
    pack, grouping, out, _ = fake_pack
    with pytest.raises(FactContractError, match="output"):
        title.run(pack, grouping, pack / "new.json")
    (grouping.parent / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FactContractError, match="frozen"):
        title.run(pack, grouping, out)


def test_output_cannot_enter_unrelated_frozen_pack(fake_pack):
    pack, grouping, out, _ = fake_pack
    unrelated = out.parent / "unrelated-pack"
    unrelated.mkdir()
    (unrelated / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FactContractError, match="frozen_ancestor"):
        title.run(pack, grouping, unrelated / "new.json")
    assert not (unrelated / "new.json").exists()


@pytest.mark.parametrize("which", ["pack", "grouping"])
def test_pinned_source_hashes_rejected(fake_pack, which):
    pack, grouping, out, _ = fake_pack
    path = pack / "manifest.json" if which == "pack" else grouping
    path.write_text(path.read_text() + " ", encoding="utf-8")
    with pytest.raises(FactContractError):
        title.run(pack, grouping, out)
    assert not out.exists()


def test_input_changed_during_measure_fails_before_output(fake_pack, monkeypatch):
    pack, grouping, out, _ = fake_pack
    def changed(*args, **kwargs):
        grouping.write_text(grouping.read_text() + " ", encoding="utf-8")
        return {}
    monkeypatch.setattr(title, "measure", changed)
    with pytest.raises(FactContractError, match="source_changed"):
        title.run(pack, grouping, out)
    assert not out.exists()


def test_cli_failure_is_nonzero(fake_pack):
    pack, grouping, out, _ = fake_pack
    grouping.write_text("{}", encoding="utf-8")
    assert title.main(["--pack", str(pack), "--grouping", str(grouping), "--out", str(out)]) == 2
    assert not out.exists()
