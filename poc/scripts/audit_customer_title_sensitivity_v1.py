"""Matched held-out title interventions on 196 development manuscripts.

Only original bodies train the diagnostic probe. Removing a title removes
information; this is not a semantic-equivalence or customer-accuracy test.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_batch05 as batch05
from koipa.customer_benchmark import FLAGS, GRADES, duplicate_audit, strict_loads, validate_answers, validate_documents
from koipa.policy_facts import require, text_digest, value_digest

SCHEMA = "customer-title-sensitivity-v1"
PACK_MANIFEST_SHA256 = "433fcac27cf8ae4955c5eb956c6793306ad2d595457bcdfff5424258793dd7b2"
GROUPING_FILE_SHA256 = "a4069c1cf96232e8ef8a13e05b322ea7405b4faf43c43e1de7318aaf67704afd"
VIEWS = ("original", "title_removed", "neutral_title")
NEUTRAL_TITLE = "문서"
SOURCE_FILES = ("scripts/audit_customer_title_sensitivity_v1.py",)


def project_views(text):
    """Replace only the first-line title span; preserve the entire suffix."""
    require(type(text) is str and "\n" in text, "title_body_structure_invalid")
    end = text.index("\n")
    if end and text[end - 1] == "\r":
        end -= 1
    title, suffix = text[:end], text[end:]
    require(bool(title.strip()) and bool(suffix.strip()), "title_body_structure_invalid")
    return {"original": text, "title_removed": suffix, "neutral_title": NEUTRAL_TITLE + suffix}


def validate_grouping(docs, grouping):
    require(type(grouping) is dict and type(grouping.get("groups")) is dict and bool(grouping["groups"]), "title_groups_empty_or_invalid")
    by_id = {d.input.doc_id: d for d in docs}
    require(type(grouping.get("documents")) is int and grouping["documents"] == len(docs) and
            grouping.get("documents_sha256") == value_digest([d.model_dump() for d in sorted(docs, key=lambda d: d.input.doc_id)]), "title_group_document_binding")
    mapping = {}
    for key, members in grouping["groups"].items():
        require(type(key) is str and type(members) is list and bool(members) and
                all(type(i) is str and i in by_id for i in members), "title_group_members_invalid")
        require(len(set(members)) == len(members) and members == sorted(members) and key == members[0], "title_group_canonical_invalid")
        require(not set(members) & set(mapping), "title_group_duplicate_member")
        mapping.update(dict.fromkeys(members, key))
    require(set(mapping) == set(by_id), "title_group_coverage_invalid")
    baseline = duplicate_audit(docs)
    require(all(len({mapping[i] for i in members}) == 1 for members in baseline["groups"].values()), "title_declared_group_split")
    for link in grouping.get("semantic_links", []):
        require(type(link) is dict and type(link.get("members")) is list and len(link["members"]) >= 2 and
                all(i in by_id for i in link["members"]), "title_semantic_link_invalid")
        require(link.get("input_hashes") == {i: by_id[i].input_sha256 for i in link["members"]}, "title_semantic_link_hash")
        require(len({mapping[i] for i in link["members"]}) == 1, "title_semantic_group_split")
    return mapping


def default_pipeline(seed):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC
    return make_pipeline(TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=1, max_features=30000),
                         LinearSVC(random_state=seed, max_iter=5000))


def summarize(rows):
    require(type(rows) is list and bool(rows), "title_summary_empty")
    total = len(rows)
    result = {"parent_seed_observations": total,
              "unique_parents": len({row["parent_doc_id"] for row in rows}),
              "reference_agreement": {view: sum(r["predictions"][view] == r["reference_grade"] for r in rows) / total for view in VIEWS},
              "by_view": {}}
    for view in VIEWS[1:]:
        flips = sum(r["predictions"][view] != r["predictions"]["original"] for r in rows)
        result["by_view"][view] = {
            "prediction_changes_vs_original": flips, "prediction_change_rate": flips / total,
            "correct_to_wrong": sum(r["predictions"]["original"] == r["reference_grade"] and r["predictions"][view] != r["reference_grade"] for r in rows),
            "wrong_to_correct": sum(r["predictions"]["original"] != r["reference_grade"] and r["predictions"][view] == r["reference_grade"] for r in rows),
            "grade_counts": {g: {
                "parent_seed_observations": sum(r["reference_grade"] == g for r in rows),
                "prediction_changes": sum(r["reference_grade"] == g and r["predictions"][view] != r["predictions"]["original"] for r in rows),
                "reference_matches": sum(r["reference_grade"] == g and r["predictions"][view] == g for r in rows),
            } for g in GRADES}}
    return result


def measure(raw_documents, raw_answers, grouping, *, seeds=5, folds=5, pipeline_factory=None):
    import numpy as np
    import sklearn
    from sklearn.model_selection import StratifiedGroupKFold

    require(type(seeds) is int and 1 <= seeds <= 30 and type(folds) is int and 2 <= folds <= 5, "title_cv_parameters_invalid")
    before = value_digest([raw_documents, raw_answers, grouping])
    docs = sorted(validate_documents(raw_documents), key=lambda d: d.input.doc_id)
    answers = validate_answers(docs, raw_answers)
    require(len({a.reference_grade for a in answers}) == 4 and min(Counter(a.reference_grade for a in answers).values()) >= folds, "title_class_population_invalid")
    mapping = validate_grouping(docs, grouping)
    require(len(set(mapping.values())) >= folds, "title_group_population_invalid")
    by_answer = {a.doc_id: a for a in answers}
    labels = np.array([by_answer[d.input.doc_id].reference_grade for d in docs])
    groups = np.array([mapping[d.input.doc_id] for d in docs])
    views = [project_views(d.input.text) for d in docs]
    view_bindings = [{"parent_doc_id": d.input.doc_id, "input_sha256": d.input_sha256,
                      "reference_answer_sha256": value_digest(by_answer[d.input.doc_id].model_dump()),
                      "title_removed_span": {"start": 0, "end": len(d.input.text) - len(views[i]["title_removed"])},
                      "unchanged_suffix_sha256": text_digest(views[i]["title_removed"]),
                      "view_sha256": {view: text_digest(views[i][view]) for view in VIEWS}}
                     for i, d in enumerate(docs)]
    factory = default_pipeline if pipeline_factory is None else pipeline_factory
    predictions, assignments = [], []
    for seed in range(seeds):
        seen = set()
        splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
        for fold, (train, test) in enumerate(splitter.split(np.arange(len(docs)), labels, groups)):
            require(len(train) > 0 and len(test) > 0 and not set(train) & set(test), "title_cv_empty_or_overlap")
            require(not set(groups[train]) & set(groups[test]) and not seen & set(test), "title_group_or_repeat_leakage")
            require(set(labels[train]) == set(GRADES), "title_training_class_missing")
            seen.update(test)
            train_ids = [docs[i].input.doc_id for i in train]
            test_ids = [docs[i].input.doc_id for i in test]
            assignments.append({"seed": seed, "fold": fold, "train_parent_ids": train_ids,
                                "test_parent_ids": test_ids, "train_groups": sorted(set(groups[train])),
                                "test_groups": sorted(set(groups[test]))})
            model = factory(seed)
            # Fit originals only. No transformed held-out text enters TF-IDF fit.
            model.fit([views[i]["original"] for i in train], labels[train].tolist())
            values = {}
            for view in VIEWS:
                actual = list(model.predict([views[i][view] for i in test]))
                require(len(actual) == len(test) and all(type(v) in (str, np.str_) and str(v) in GRADES for v in actual), "title_predictions_invalid")
                values[view] = list(map(str, actual))
            for offset, i in enumerate(test):
                predictions.append({"seed": seed, "fold": fold, "parent_doc_id": docs[i].input.doc_id,
                                    "reference_grade": str(labels[i]), "group_id": str(groups[i]),
                                    "predictions": {view: values[view][offset] for view in VIEWS}})
        require(seen == set(range(len(docs))), "title_seed_coverage_invalid")
    require(len(predictions) == seeds * len(docs) and
            len({(r["seed"], r["parent_doc_id"]) for r in predictions}) == len(predictions), "title_pair_coverage_invalid")
    require(value_digest([raw_documents, raw_answers, grouping]) == before, "title_original_mutation")
    return {**FLAGS, "schema_version": SCHEMA, "status": "paired_title_diagnostic_not_customer_evaluation",
            "source_documents": len(docs), "source_groups": len(set(mapping.values())),
            "seeds": list(range(seeds)), "folds": folds, "diagnostic_model_fits": seeds * folds,
            "new_benchmark_documents": 0, "diagnostic_probe_fitted": True, "production_model_trained": False,
            "summary": summarize(predictions), "summary_by_seed": {str(s): summarize([r for r in predictions if r["seed"] == s]) for s in range(seeds)},
            "fold_assignments": assignments, "paired_predictions": predictions, "view_bindings": view_bindings,
            "source_collection_sha256": before, "grouping_sha256": value_digest(grouping),
            "numpy_version": np.__version__, "sklearn_version": sklearn.__version__, "python_version": sys.version,
            "neutral_title": NEUTRAL_TITLE, "training_profile": "original_body_only", "context_presented": False,
            "body_only_grade_scoring_allowed": False, "reference_agreement_is_customer_accuracy": False,
            "semantic_equivalence_certified": False, "unique_parent_manuscripts": len(docs),
            "statistical_independence_certified": False, "confidence_intervals_computed": False,
            "warning": "Repeated seeds/views are not new independent documents. Title removal/replacement changes information; differences show sensitivity of this character probe, not all bias causes or production classification quality."}


def _rows(raw):
    rows = [strict_loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    require(bool(rows), "title_zero_cases")
    return rows


def _output_guard(pack, grouping_path, out):
    require(not out.exists() and not out.is_relative_to(pack), "title_output_invalid")
    require(not any((ancestor / "manifest.json").is_file() for ancestor in out.parents), "title_output_inside_frozen_ancestor")
    for source in (pack / "manifest.json", grouping_path):
        for ancestor in source.parents:
            if (ancestor / "manifest.json").is_file():
                require(not out.is_relative_to(ancestor), "title_output_inside_frozen_source")


def run(pack, grouping_path, out, *, seeds=5, pipeline_factory=None):
    pack, grouping_path, out = Path(pack).resolve(), Path(grouping_path).resolve(), Path(out).resolve()
    _output_guard(pack, grouping_path, out)
    batch05.verify(pack)
    snapshots = {pack / "manifest.json": (pack / "manifest.json").read_bytes(), grouping_path: grouping_path.read_bytes()}
    require(hashlib.sha256(snapshots[pack / "manifest.json"]).hexdigest() == PACK_MANIFEST_SHA256, "title_wrong_parent_pack")
    require(hashlib.sha256(snapshots[grouping_path]).hexdigest() == GROUPING_FILE_SHA256, "title_wrong_grouping_file")
    for name in ("authoring/documents.jsonl", "answers/answers.candidate.jsonl"):
        snapshots[pack / name] = (pack / name).read_bytes()
    docs = _rows(snapshots[pack / "authoring/documents.jsonl"])
    answers = _rows(snapshots[pack / "answers/answers.candidate.jsonl"])
    grouping = strict_loads(snapshots[grouping_path].decode("utf-8"))
    require(len(docs) == 196 and len(grouping["groups"]) == 178, "title_fixed_population_invalid")
    sources = {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in SOURCE_FILES}
    result = measure(docs, answers, grouping, seeds=seeds, pipeline_factory=pipeline_factory)
    result["provenance"] = {"source_pack_verified_before_and_after": True,
                            "parent_manifest_sha256": PACK_MANIFEST_SHA256, "grouping_file_sha256": GROUPING_FILE_SHA256,
                            "sources_sha256": sources}
    batch05.verify(pack)
    require(all(path.read_bytes() == raw for path, raw in snapshots.items()), "title_source_changed_during_run")
    require(sources == {name: hashlib.sha256((POC / name).read_bytes()).hexdigest() for name in SOURCE_FILES}, "title_code_changed_during_run")
    content = json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as target:
        target.write(content)
    require(all(path.read_bytes() == raw for path, raw in snapshots.items()), "title_source_changed_during_output")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, required=True)
    parser.add_argument("--grouping", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        result = run(args.pack, args.grouping, args.out, seeds=args.seeds)
        print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_title_sensitivity_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
