"""Independent repair contract and fake-pipeline leakage regressions."""

import copy
import json
import random
import string

import pytest

import audit_customer_alias_repair_v1 as audit
import build_customer_guide_alias_repair_v1 as author
from koipa.customer_benchmark import FLAGS, GRADES, presented_text, validate_documents
from koipa.policy_facts import FactContractError, text_digest, value_digest


def rows(payload, path):
    return [json.loads(line) for line in payload[path].splitlines()]


@pytest.fixture(scope="module")
def actual():
    original = audit.original_batch.core_payload()[2]
    repaired = author.core_payload()[2]
    paths = (
        "authoring/documents.jsonl",
        "answers/answers.candidate.jsonl",
        "answers/evidence.jsonl",
    )
    return (
        [rows(original, name) for name in paths]
        + [rows(repaired, name) for name in paths]
        + [rows(repaired, "audit/lineage.jsonl")]
    )


def test_actual_260_independent_contract_and_scope_hold(actual):
    before = copy.deepcopy(actual)
    result = audit.audit_repair(*actual)
    assert actual == before
    assert result["original_documents"] == result["active_documents"] == 260
    assert result["panel_parents"] == 64
    assert result["changed_parents"] == 16 and result["checked_edit_count"] == 18
    assert (
        result["unchanged_panel_parents"] == 48 and result["fixed_old_controls"] == 196
    )
    scopes = [r["scope_review"] for r in result["rows"] if r["changed"]]
    assert len(scopes) == 16 and all(
        s["full_body_bounded_read_performed"] for s in scopes
    )
    assert all(s["target_binding_preservation_certified"] is False for s in scopes)
    enzyme = next(s for s in scopes if s["family_id"] == "family-enzyme-addition-lag")
    assert "기질 배치 M에 한정" in enzyme["remaining_local_scope_evidence"]["quote"]
    assert enzyme["removed_target_bindings"] == {"효소": "E", "기질 배치": "M"}
    assert result["before_markers"]["by_grade"]["TS"]["known_marker_occurrences"] == 17
    assert result["before_markers"]["by_grade"]["TS"]["broad_marker_occurrences"] == 19
    assert result["after_markers"]["by_grade"]["TS"]["known_marker_occurrences"] == 0
    assert (
        result["after_markers"]["by_grade"]["TS"]["broad_marker_occurrences"] == 1
    )  # μL unit
    assert result["broader_authoring_quality_hold_cleared"] is False
    assert all(result[key] is False for key in FLAGS)


@pytest.mark.parametrize(
    "before,after",
    [
        ("분말 R은", "분말은"),
        ("표면 P는", "표면은"),
        ("분말 W가", "분말이"),
        ("효소 E는", "효소는"),
        ("기질 배치 M에", "기질 배치에"),
        ("경로 A으로", "경로로"),
        ("분말 A으로", "분말로"),
        ("표면 A과", "표면과"),
        ("효소 A과", "효소와"),
        ("분말 A를", "분말을"),
    ],
)
def test_independent_particle_rules(before, after):
    text = "고정 제목\n\n" + before + " 2μL를 12분 후 넣지 않습니다.\n"
    got = audit.independent_expected(text)
    assert got["text"] == text.replace(before, after)
    e = got["edits"][0]
    assert text[e["start"] : e["end"]] == before
    assert got["text"][e["target_start"] : e["target_end"]] == after
    assert got["numeric_sequence"] == ["2", "12"]


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "문서\n분말 A와 분말 B는 다릅니다.",
        "문서\n표면 P에게 붙인다.",
        "표면 P는\n제목 변경",
    ],
)
def test_empty_unreviewed_particle_multi_target_title_changes_fail(text):
    with pytest.raises(FactContractError):
        audit.independent_expected(text)


def test_two_noun_roles_and_unit_are_distinct():
    result = audit.independent_expected(
        "관측\n효소 E는 기질 배치 M에 2μL를 넣지 않습니다."
    )
    assert result["text"] == "관측\n효소는 기질 배치에 2μL를 넣지 않습니다."
    assert result["distinct_noun_targets"] == {"효소": "E", "기질 배치": "M"}


@pytest.mark.parametrize(
    "mutation",
    [
        "grade",
        "context",
        "outside_span",
        "particle",
        "claim",
        "policy",
        "factor",
        "premise",
        "lineage_hash",
        "answer_hash",
        "evidence_hash",
        "edits",
        "reason",
        "permission",
        "count",
        "changed",
        "duplicate",
        "empty",
        "old196",
        "parent_draft",
    ],
)
def test_actual_mutations_rejected(actual, mutation):
    data = copy.deepcopy(actual)
    orig, oa, od, active, aa, ad, lineage = data
    item = next(r for r in lineage if r["changed"])
    child = next(r for r in active if r["input"]["doc_id"] == item["child_doc_id"])
    answer = next(r for r in aa if r["doc_id"] == item["child_doc_id"])
    detail = next(r for r in ad if r["doc_id"] == item["child_doc_id"])
    if mutation == "grade":
        answer["reference_grade"] = "S3"
    elif mutation == "context":
        child["input"]["context"] = []
    elif mutation == "outside_span":
        child["input"]["text"] += "추가 조건"
    elif mutation == "particle":
        child["input"]["text"] = child["input"]["text"].replace(
            item["edits"][0]["after"], "구역는", 1
        )
    elif mutation == "claim":
        child["claims"][0]["start"] += 1
    elif mutation == "policy":
        answer["policy_sha256"] = "f" * 64
    elif mutation == "factor":
        detail["independently_invented_factor"] = 0
    elif mutation == "premise":
        answer["other_grade_exclusions"] = {}
    elif mutation == "lineage_hash":
        item["child_body_sha256"] = "f" * 64
    elif mutation == "answer_hash":
        item["child_answer_sha256"] = "f" * 64
    elif mutation == "evidence_hash":
        item["child_evidence_sha256"] = "f" * 64
    elif mutation == "edits":
        item["edits"][0]["start"] += 1
    elif mutation == "reason":
        item["edits"][0]["reason"] = ""
    elif mutation == "permission":
        item["training_allowed"] = True
    elif mutation == "count":
        item["new_document_count"] = True
    elif mutation == "changed":
        item["changed"] = False
    elif mutation == "duplicate":
        lineage.append(copy.deepcopy(lineage[0]))
    elif mutation == "empty":
        lineage.clear()
    elif mutation == "old196":
        panel = {r["parent_doc_id"] for r in lineage}
        next(
            r
            for r in active
            if r["input"]["doc_id"] not in panel
            and r["input"]["doc_id"] != child["input"]["doc_id"]
        )["domain"] = "changed"
    elif mutation == "parent_draft":
        detail["parent_draft_id"] = "new-draft"
    with pytest.raises((FactContractError, ValueError)):
        audit.audit_repair(*data)


@pytest.fixture
def corpus():
    records, answers, active, lineage = [], [], [], []
    for i in range(40):
        rng = random.Random(9982 + i)
        prose = "".join(rng.choices(string.ascii_lowercase, k=250))
        text = f"제목 {i}\n\n{prose}\n" + (
            "분말 R은 12분 뒤 옮기지 않는다." if i < 8 else "고정 관측입니다."
        )
        inp = {"doc_id": "doc-" + text_digest(str(i))[:24], "text": text, "context": []}
        record = {
            **FLAGS,
            "schema_version": "customer-synthetic-draft-v1",
            "document_origin": "synthetic",
            "input": inp,
            "input_sha256": value_digest(inp),
            "family_id": f"test-family-{i // 4}",
            "scenario_id": f"scenario-{i}",
            "template_family_id": f"template-{i}",
            "domain": "test-only",
            "claims": [
                {
                    "name": "claim-one",
                    "claim": text[:4],
                    "quote": text[:4],
                    "start": 0,
                    "end": 4,
                    "sha256": text_digest(text[:4]),
                    "status": "authored_binding_only",
                }
            ],
        }
        grade = GRADES[i % 4]
        answer = {
            "doc_id": inp["doc_id"],
            "input_sha256": record["input_sha256"],
            "policy_id": "test-policy",
            "policy_version": "0.1",
            "policy_sha256": "a" * 64,
            "reference_grade": grade,
            "rule_ids": ["test-rule"],
            "evidence_names": ["claim-one"],
            "other_grade_exclusions": {
                g: "test-only exclusion" for g in GRADES if g != grade
            },
            "status": "authored_candidate",
        }
        child = copy.deepcopy(record)
        if i < 8:
            child["input"]["text"] = text.replace("분말 R은", "분말은")
            child["input"]["doc_id"] = "doc-" + text_digest("child" + str(i))[:24]
            child["input_sha256"] = value_digest(child["input"])
        if i < 16:
            lineage.append(
                {
                    "parent_doc_id": inp["doc_id"],
                    "child_doc_id": child["input"]["doc_id"],
                }
            )
        records.append(record)
        answers.append(answer)
        active.append(child)
    groups = {}
    for start in range(0, 40, 4):
        ids = sorted(r["input"]["doc_id"] for r in records[start : start + 4])
        groups[ids[0]] = ids
    grouping = {
        "documents": 40,
        "documents_sha256": value_digest(
            sorted(records, key=lambda r: r["input"]["doc_id"])
        ),
        "groups": groups,
        "semantic_links": [],
    }
    return records, answers, active, lineage, grouping


def test_fake_pipeline_four_cells_identical_fold_parent_groups(corpus):
    original, _, active, lineage, _ = corpus
    pairs = {r["child_doc_id"]: r["parent_doc_id"] for r in lineage}
    inverse = {}
    for name, raw in (("original", original), ("repaired", active)):
        for d in validate_documents(raw):
            for profile in audit.PROFILES:
                inverse[presented_text(d, profile)] = (
                    pairs.get(d.input.doc_id, d.input.doc_id),
                    name,
                )
    models = []

    class Fake:
        def fit(self, texts, labels):
            self.parents = {inverse[t][0] for t in texts}
            self.texts = texts
            self.predicted = []
            assert len(texts) == len(self.parents) == len(labels)
            models.append(self)

        def predict(self, texts):
            ids = [inverse[t][0] for t in texts]
            assert not set(ids) & self.parents
            self.predicted.append(ids)
            return ["TS" if "분말 R은" in t else "S1" for t in texts]

    before = copy.deepcopy(corpus)
    result = audit.measure(
        *corpus, seeds=2, folds=5, pipeline_factory=lambda seed: Fake()
    )
    assert corpus == before
    assert result["diagnostic_model_fits"] == len(models) == 40
    assert result["cohort_sizes"] == {
        "all_population": 40,
        "panel": 16,
        "changed_panel": 8,
        "unchanged_panel": 8,
        "old_unchanged_controls": 24,
    }
    assert len(result["paired_predictions"]) == 40 * 2 * 2 * 2
    for assignment, chunk in zip(
        result["fold_assignments"],
        [models[i : i + 4] for i in range(0, 40, 4)],
        strict=True,
    ):
        assert not set(assignment["train_groups"]) & set(assignment["test_groups"])
        for model in chunk:
            assert model.parents == set(assignment["train_parent_ids"])
            assert (
                model.predicted[0]
                == model.predicted[1]
                == assignment["test_parent_ids"]
            )
        # Each profile uses same original/repaired training parents. Exact body
        # values are the preregistered views, never held-out views.
        byid = {d.input.doc_id: d for d in validate_documents(original)}
        variants = {
            pairs.get(d.input.doc_id, d.input.doc_id): d
            for d in validate_documents(active)
        }
        for index, model in enumerate(chunk):
            selected = byid if index % 2 == 0 else variants
            profile = audit.PROFILES[index // 2]
            assert model.texts == [
                presented_text(selected[i], profile)
                for i in assignment["train_parent_ids"]
            ]
    for profile in audit.PROFILES:
        for arm in audit.ARMS:
            stats = result["summary"][profile][arm]
            assert stats["panel"]["parent_seed_observations"] == 32
            assert stats["changed_panel"]["test_view_prediction_flips"] == 16
            assert stats["changed_panel"]["correct_to_wrong"] == 4
            assert stats["changed_panel"]["wrong_to_correct"] == 4
            assert stats["unchanged_panel"]["test_view_prediction_flips"] == 0
            assert stats["old_unchanged_controls"]["test_view_prediction_flips"] == 0
    assert (
        result["adoption_allowed"] is False
        and result["authoritative_parent_replaced"] is False
    )


@pytest.mark.parametrize("seeds,folds", [(0, 5), (True, 5), (31, 5), (1, 1), (1, 6)])
def test_invalid_cv_parameters(corpus, seeds, folds):
    with pytest.raises(FactContractError):
        audit.measure(*corpus, seeds=seeds, folds=folds)


@pytest.mark.parametrize(
    "mode",
    ["empty", "lineage", "grade", "group_hash", "group_split", "unverified_text"],
)
def test_invalid_diagnostic_inputs(corpus, mode):
    original, answers, active, lineage, grouping = copy.deepcopy(corpus)
    if mode == "empty":
        original.clear()
    elif mode == "lineage":
        lineage.clear()
    elif mode == "grade":
        answers[0]["reference_grade"] = "HOLD"
    elif mode == "group_hash":
        grouping["documents_sha256"] = "f" * 64
    elif mode == "group_split":
        members = grouping["groups"].pop(next(iter(grouping["groups"])))
        grouping["groups"].update({i: [i] for i in members})
    else:
        active[0]["input"]["text"] += "extra"
        active[0]["input_sha256"] = value_digest(active[0]["input"])
    with pytest.raises((FactContractError, ValueError)):
        audit.measure(original, answers, active, lineage, grouping, seeds=1, folds=2)


@pytest.mark.parametrize("mode", ["short", "invalid"])
def test_invalid_predictor(corpus, mode):
    class Fake:
        def fit(self, *args):
            pass

        def predict(self, texts):
            return [] if mode == "short" else ["HOLD"] * len(texts)

    with pytest.raises(FactContractError):
        audit.measure(*corpus, seeds=1, folds=2, pipeline_factory=lambda _: Fake())


def test_zero_jsonl_and_summary_fail(tmp_path):
    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(FactContractError):
        audit._rows(empty)
    with pytest.raises(FactContractError):
        audit._summary([])


def test_declared_char_pipeline():
    model = audit.default_pipeline(3)
    assert model.steps[0][1].analyzer == "char" and model.steps[0][1].ngram_range == (
        2,
        5,
    )
    assert model.steps[1][1].random_state == 3


@pytest.fixture
def fake_pack(tmp_path, monkeypatch, actual):
    original, _, _, active, _, _, lineage = actual
    original_pack, repair_pack = tmp_path / "original", tmp_path / "repair"
    original_pack.mkdir()
    repair_pack.mkdir()
    names = (
        "authoring/documents.jsonl",
        "answers/answers.candidate.jsonl",
        "answers/evidence.jsonl",
    )
    for root, payload in ((original_pack, actual[:3]), (repair_pack, actual[3:6])):
        for name, content in zip(names, payload, strict=True):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in content),
                encoding="utf-8",
            )
    panel = {r["parent_doc_id"] for r in lineage}
    for index, (name, population) in enumerate(
        zip(
            (
                "parents/documents.jsonl",
                "parents/answers.candidate.jsonl",
                "parents/evidence.jsonl",
            ),
            actual[:3],
            strict=True,
        )
    ):
        subset = [
            r
            for r in population
            if (r["input"]["doc_id"] if index == 0 else r["doc_id"]) in panel
        ]
        path = repair_pack / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r) + "\n" for r in subset), encoding="utf-8")
    path = repair_pack / "audit/lineage.jsonl"
    path.parent.mkdir()
    path.write_text("".join(json.dumps(r) + "\n" for r in lineage), encoding="utf-8")
    (original_pack / "manifest.json").write_text("{}", encoding="utf-8")
    original_sha = audit._sha(original_pack / "manifest.json")
    manifest = {
        **FLAGS,
        "parent_manifest_sha256": original_sha,
        "source_files_sha256": {
            "scripts/audit_customer_alias_repair_v1.py": audit._sha(
                audit.POC / "scripts/audit_customer_alias_repair_v1.py"
            )
        },
        "files": {
            p.relative_to(repair_pack).as_posix(): audit._sha(p)
            for p in repair_pack.rglob("*")
            if p.is_file()
        },
    }
    (repair_pack / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    grouping = tmp_path / "grouping.json"
    grouping.write_text(
        json.dumps({"groups": {str(i): [] for i in range(236)}}), encoding="utf-8"
    )
    monkeypatch.setattr(audit, "ORIGINAL_MANIFEST_SHA256", original_sha)
    monkeypatch.setattr(audit, "GROUPING_FILE_SHA256", audit._sha(grouping))
    calls = []
    monkeypatch.setattr(audit.original_batch, "verify", lambda root: calls.append(root))
    monkeypatch.setattr(audit, "audit_repair", lambda *args: {"changed_parents": 16})
    return (
        original_pack,
        repair_pack,
        grouping,
        tmp_path / "output.json",
        audit._sha(repair_pack / "manifest.json"),
        calls,
    )


def call_run(fake_pack):
    original, repair, grouping, out, pin, _ = fake_pack
    return audit.run(
        original, repair, grouping, out, expected_repair_manifest_sha256=pin
    )


def test_runner_completion_last_and_immutable(fake_pack, monkeypatch):
    original, repair, grouping, out, pin, calls = fake_pack
    before = {
        p: p.read_bytes()
        for folder in (original, repair)
        for p in folder.rglob("*")
        if p.is_file()
    }
    completion = out.with_name(out.name + ".manifest.json")

    def verify(root):
        calls.append(root)
        assert not completion.exists()
        if len(calls) == 3:
            assert out.exists()

    monkeypatch.setattr(audit.original_batch, "verify", verify)
    result = call_run(fake_pack)
    assert result["actual_cv_executed"] is False
    assert len(calls) == 3
    assert all(p.read_bytes() == content for p, content in before.items())
    assert json.loads(completion.read_text())["result_sha256"] == audit._sha(out)
    snapshot = out.read_bytes()
    with pytest.raises(FactContractError):
        call_run(fake_pack)
    assert out.read_bytes() == snapshot


@pytest.mark.parametrize("which", ["original", "repair", "unrelated", "sidecar"])
def test_runner_unsafe_output_rejected(fake_pack, which):
    original, repair, grouping, out, pin, _ = fake_pack
    if which in ("original", "repair"):
        out = (original if which == "original" else repair) / "out.json"
    elif which == "unrelated":
        root = out.parent / "unrelated"
        root.mkdir()
        (root / "manifest.json").write_text("{}")
        out = root / "nested/out.json"
    else:
        out.with_name(out.name + ".manifest.json").write_text("{}")
    with pytest.raises(FactContractError):
        audit.run(original, repair, grouping, out, expected_repair_manifest_sha256=pin)
    assert not out.exists()


@pytest.mark.parametrize(
    "which", ["grouping", "manifest", "payload", "unlisted", "source_pin", "parents"]
)
def test_runner_changed_input_rejected(fake_pack, which):
    original, repair, grouping, out, pin, _ = fake_pack
    manifest_path = repair / "manifest.json"
    if which == "grouping":
        grouping.write_text("{}")
    elif which == "manifest":
        manifest_path.write_text("{}")
    elif which == "payload":
        (repair / "authoring/documents.jsonl").write_text("{}")
    elif which == "unlisted":
        (repair / "extra.txt").write_text("extra")
    else:
        manifest = json.loads(manifest_path.read_text())
        if which == "source_pin":
            manifest["source_files_sha256"][
                "scripts/audit_customer_alias_repair_v1.py"
            ] = "f" * 64
        else:
            path = repair / "parents/documents.jsonl"
            content = path.read_text()
            path.write_text(content + content.splitlines()[0] + "\n")
            manifest["files"]["parents/documents.jsonl"] = audit._sha(path)
        manifest_path.write_text(json.dumps(manifest))
        pin = audit._sha(manifest_path)
    with pytest.raises((FactContractError, ValueError)):
        audit.run(original, repair, grouping, out, expected_repair_manifest_sha256=pin)
    assert not out.exists()


@pytest.mark.parametrize("late", [False, True])
def test_source_drift_never_creates_completion_marker(fake_pack, monkeypatch, late):
    original, repair, grouping, out, pin, calls = fake_pack

    def verify(root):
        calls.append(root)
        if len(calls) == (3 if late else 2):
            grouping.write_text(grouping.read_text() + " ")

    monkeypatch.setattr(audit.original_batch, "verify", verify)
    with pytest.raises(FactContractError):
        call_run(fake_pack)
    assert out.exists() is late
    assert not out.with_name(out.name + ".manifest.json").exists()


def test_cli_pin_failure_nonzero(fake_pack):
    original, repair, grouping, out, _, _ = fake_pack
    assert (
        audit.main(
            [
                "--original-pack",
                str(original),
                "--repair-pack",
                str(repair),
                "--grouping",
                str(grouping),
                "--repair-manifest-sha256",
                "f" * 64,
                "--out",
                str(out),
            ]
        )
        == 2
    )
    assert not out.exists()
