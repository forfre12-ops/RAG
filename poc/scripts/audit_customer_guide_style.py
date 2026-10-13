"""Paired style sensitivity on 48 existing parents; zero new benchmark documents.

Original and style-balanced diagnostic probes use identical parent/group folds.
Variants never cross a parent fold or alter context, reference grades or policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC/"src"))

import build_customer_guide_batch04 as parent
from customer_guide_style_variants import rebind_claims, transform
from prepare_customer_benchmark import _json, _jsonl, _new_file, _rows, token_audit
from koipa.customer_benchmark import FLAGS, GRADES, presented_text, strict_loads, validate_documents
from koipa.customer_guide_reference import POLICY_SHA256, decide, decode_context
from koipa.policy_facts import require, text_digest, value_digest

PARENT_SHA256 = "dcc8ea80ad4e3ff6fadf21a4debe20212ac015a2892ebd444cdee73917a07f41"
SCHEMA = "customer-guide-style-diagnostic-v0.1"
STYLES = ("plain", "polite")
PROFILES = ("body_only", "body_context")
ARMS = ("original_training", "duplicate_control_training", "style_balanced_training")
SOURCES = ("scripts/customer_guide_style_variants.py", "scripts/audit_customer_guide_style.py")


def source_hashes():
    return {**parent.source_hashes(), **{p: hashlib.sha256((POC/p).read_bytes()).hexdigest() for p in SOURCES}}


def material():
    docs, answers, payload = parent.core_payload()
    details = _rows(payload["answers/evidence.jsonl"].encode())
    by_answer = {a["doc_id"]: a for a in answers}
    by_detail = {e["doc_id"]: e for e in details}
    panel_ids = {r[0]["input"]["doc_id"] for r in parent.build_new_cases()}
    panel = [d for d in docs if d.input.doc_id in panel_ids]
    require(len(panel) == 48 and Counter(by_answer[d.input.doc_id]["reference_grade"] for d in panel) == {g: 12 for g in GRADES}, "style_panel_invalid")
    variants, bindings, variant_docs, checks = [], [], {}, []
    for style in STYLES:
        typed = []
        for d in panel:
            result = transform(d.input.text, style)
            record = d.model_dump()
            record["claims"] = rebind_claims(d.claims, d.input.text, result)
            record["input"]["text"] = result["text"]
            record["input"]["doc_id"] = "doc-"+value_digest({"text": result["text"], "context": record["input"]["context"]})[:24]
            record["input_sha256"] = value_digest(record["input"])
            target = validate_documents([record])[0]
            require(target.input.context == d.input.context, "style_context_changed")
            decision = decide(decode_context(record["input"]["context"]))
            require(value_digest(decision) == value_digest(by_detail[d.input.doc_id]["decision"]), "style_policy_changed")
            key = "style-"+value_digest({"parent": d.input.doc_id, "style": style, "input": target.input_sha256})[:24]
            variants.append({**FLAGS, "variant_id": key, "parent_doc_id": d.input.doc_id, "style": style,
                **target.input.model_dump(), "input_sha256": target.input_sha256,
                "dataset_role": "diagnostic_style_view_not_new_document"})
            bindings.append({**FLAGS, "variant_id": key, "parent_doc_id": d.input.doc_id,
                "parent_body_sha256": text_digest(d.input.text), "parent_answer_sha256": value_digest(by_answer[d.input.doc_id]),
                "context_sha256": value_digest(record["input"]["context"]), "style": style,
                **{k: v for k, v in result.items() if k not in {"text", "style"}},
                "claims": record["claims"], "policy_sha256": POLICY_SHA256,
                "policy_reference_unchanged": True, "numeric_sequence_unchanged": True,
                "title_unchanged": True, "permission_or_customer_grade_certified": False})
            variant_docs[(d.input.doc_id, style)] = target
            typed.append(target)
        math = parent.arithmetic(typed, specs=parent.CASES)
        checks.append({"style": style, "passed": math["new_passed"], "checks": math["new_checks"]})
    summary = {**FLAGS, "status": "paired_style_diagnostic_not_release", "source_documents": len(docs),
        "panel_parents": len(panel), "variant_views": len(variants), "new_benchmark_documents": 0,
        "benchmark_total_unchanged": 164, "remaining_unchanged": 836, "policy_sha256": POLICY_SHA256,
        "changed_views": sum(b["changed"] for b in bindings), "unchanged_control_views": sum(not b["changed"] for b in bindings),
        "style_counts": {s: {"views": sum(b["style"] == s for b in bindings),
            "changed": sum(b["style"] == s and b["changed"] for b in bindings),
            "edited_terminal_words": sum(len(b["edits"]) for b in bindings if b["style"] == s)} for s in STYLES},
        "arithmetic_view_checks": sum(c["passed"] for c in checks), "rebound_claims": sum(len(b["claims"]) for b in bindings),
        "same_parent_fold_required": True, "whole_semantic_equivalence_certified": False,
        "source_selection": "entire frozen batch04, not score-selected examples",
        "limits": ["Sentence-ending whitelist only; request force, headings, technical vocabulary and topics are not normalized.",
                   "Repeated views/seeds are not independent new documents. No customer-model or customer-accuracy claim."]}
    comparison = ["# 기존48건 문체 진단 비교", "", "96개 표현 뷰(실제 변경57/동일39), 신규 문서0. 원문/정답은 보존했다.",
                  "숫자·조건·요청·맥락은 유지하고 승인된 종결어만 바꾼 작성자 진단이다. 전체 의미 동등성 인증이 아니다."]
    for d in panel:
        comparison += ["", "## "+d.input.text.splitlines()[0], "", "원문 ID: "+d.input.doc_id,
                       "조건부 참조: "+by_answer[d.input.doc_id]["reference_grade"], "", "### 원문", "", d.input.text]
        for s in STYLES:
            comparison += ["", "### "+s, "", variant_docs[(d.input.doc_id, s)].input.text]
    out = {"summary.json": _json(summary), "variants/inputs.jsonl": _jsonl(variants),
        "audit/edits_and_bindings.jsonl": _jsonl(bindings), "audit/arithmetic.json": _json(checks),
        "audit/semantic_family_proposals.json": payload["audit/semantic_family_proposals.json"],
        "parents/documents.jsonl": _jsonl([d.model_dump() for d in panel]),
        "parents/answers.candidate.jsonl": _jsonl([by_answer[d.input.doc_id] for d in panel]),
        "parents/evidence.jsonl": _jsonl([by_detail[d.input.doc_id] for d in panel]),
        "COMPARISON.md": "\n".join(comparison)+"\n"}
    return docs, answers, panel, variant_docs, out


def prepare(out, *, parent_pack=None, tokenizer=None):
    require(not out.exists(), "style_output_exists")
    if parent_pack is not None:
        parent.verify(parent_pack)
        require(hashlib.sha256((parent_pack/"manifest.json").read_bytes()).hexdigest() == PARENT_SHA256, "style_parent_pack_mismatch")
    _, _, _, variant_docs, payload = material()
    # Duplicate input IDs across no-op views are legitimate diagnostic controls.
    tokens = token_audit(list(variant_docs.values()), tokenizer)
    payload["audit/tokenizer.json"] = _json(tokens)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        _new_file(out/name, content)
    _new_file(out/"manifest.json", _json({**FLAGS, "schema_version": SCHEMA,
        "dataset_role": "diagnostic_style_views_only", "parent_manifest_sha256": PARENT_SHA256,
        "parent_verified_during_build": parent_pack is not None, "sources_sha256": source_hashes(),
        "files": {p: text_digest(c) for p, c in payload.items()}}))
    return verify(out)


def verify(root):
    root = root.resolve()
    raw = (root/"manifest.json").read_bytes()
    manifest = strict_loads(raw.decode("utf-8"))
    require(manifest["schema_version"] == SCHEMA and manifest["parent_manifest_sha256"] == PARENT_SHA256 and
            manifest["dataset_role"] == "diagnostic_style_views_only", "style_manifest_invalid")
    require(all(type(manifest[k]) is bool and manifest[k] is False for k in FLAGS) and
            type(manifest["parent_verified_during_build"]) is bool, "style_permission_invalid")
    require(manifest["sources_sha256"] == source_hashes(), "style_source_drift")
    files, observed = manifest["files"], {}
    require(type(files) is dict and bool(files), "style_manifest_empty")
    for name, sha in files.items():
        p = (root/name).resolve()
        require(not Path(name).is_absolute() and "\\" not in name and p.is_relative_to(root) and p != root, "style_path_escape")
        observed[name] = p.read_bytes()
        require(hashlib.sha256(observed[name]).hexdigest() == sha, "style_hash_mismatch")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} == set(files)|{"manifest.json"}, "style_unlisted_file")
    _, _, _, variants, expected = material()
    require(set(files) == set(expected)|{"audit/tokenizer.json"}, "style_file_list_mismatch")
    for name, content in expected.items():
        require(observed[name] == content.encode("utf-8"), "style_replay_mismatch")
    tokens = strict_loads(observed["audit/tokenizer.json"].decode("utf-8"))
    if tokens["status"] == "measured":
        wanted = Counter((d.input.doc_id, profile, text_digest(presented_text(d, profile)))
                         for d in variants.values() for profile in PROFILES)
        actual = Counter((v["doc_id"], v["profile"], v["presented_sha256"]) for v in tokens["views"])
        require(actual == wanted, "style_token_input_mismatch")
        for v in tokens["views"]:
            require(type(v["tokens_with_special"]) is int and v["tokens_with_special"] > 0 and
                    type(v["fits_512_tokens"]) is bool and v["fits_512_tokens"] == (v["tokens_with_special"] <= 512), "style_token_count_invalid")
    else:
        require(tokens["status"] == "not_run", "style_token_status_invalid")
    require((root/"manifest.json").read_bytes() == raw, "style_pack_changed")
    return strict_loads(observed["summary.json"].decode("utf-8"))


def default_pipeline(seed):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC
    return make_pipeline(TfidfVectorizer(analyzer="char", ngram_range=(2, 5), min_df=1, max_features=30000),
                         LinearSVC(random_state=seed, max_iter=5000))


def fold_training(docs, labels, train_ids, variants, profile, arm):
    require(arm in ARMS and profile in PROFILES, "style_training_condition_invalid")
    texts, y, weights, origin_ids = [], [], [], []
    for index in train_ids:
        d = docs[index]
        parent_id = d.input.doc_id
        expanded = arm == "style_balanced_training" and (parent_id, "plain") in variants
        views = ([variants[(parent_id, s)] for s in STYLES] if expanded else
                 [d] if arm == "original_training" else [d, d])
        for view in views:
            texts.append(presented_text(view, profile))
            y.append(labels[index])
            weights.append(1/len(views))
            origin_ids.append(parent_id)
    mass = Counter()
    for parent_id, weight in zip(origin_ids, weights, strict=True):
        mass[parent_id] += weight
    require(set(mass) == {docs[i].input.doc_id for i in train_ids} and all(v == 1 for v in mass.values()), "style_parent_weight_mismatch")
    return texts, y, weights, origin_ids


def measure_paired(*, seeds=5, folds=5, pipeline_factory=None):
    import numpy as np
    import sklearn
    from sklearn.model_selection import StratifiedGroupKFold

    require(type(seeds) is int and 1 <= seeds <= 100 and type(folds) is int and 2 <= folds <= 5, "style_cv_parameters_invalid")
    docs, answers, panel, variants, _ = material()
    by_answer = {a["doc_id"]: a["reference_grade"] for a in answers}
    labels = np.array([by_answer[d.input.doc_id] for d in docs])
    mapping = parent.semantic_family_audit(docs)["doc_to_group"]
    groups = np.array([mapping[d.input.doc_id] for d in docs])
    panel_ids = {d.input.doc_id for d in panel}
    factory = default_pipeline if pipeline_factory is None else pipeline_factory
    result_rows, fold_rows = [], []
    for seed in range(seeds):
        splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
        seen = set()
        for fold, (train, test) in enumerate(splitter.split(np.arange(len(docs)), labels, groups)):
            require(not set(groups[train]) & set(groups[test]), "style_group_overlap")
            require(not set(test) & seen, "style_repeated_test_index")
            seen.update(test)
            selected = [i for i in test if docs[i].input.doc_id in panel_ids]
            train_parents, test_parents = [docs[i].input.doc_id for i in train], [docs[i].input.doc_id for i in test]
            fold_rows.append({"seed": seed, "fold": fold, "train_parent_ids": train_parents,
                "test_parent_ids": test_parents, "panel_test_parent_ids": [docs[i].input.doc_id for i in selected],
                "train_groups": sorted(set(groups[train])), "test_groups": sorted(set(groups[test]))})
            for profile in PROFILES:
                for arm in ARMS:
                    texts, y, weights, origins = fold_training(docs, labels, train, variants, profile, arm)
                    require(not set(origins) & set(test_parents), "style_parent_leakage")
                    model = factory(seed)
                    # Per-parent weight stays 1; altered vocabulary is fit on train views only.
                    model.fit(texts, y, linearsvc__sample_weight=weights)
                    predicted = {}
                    for view in ("original", *STYLES):
                        texts_test = [presented_text(docs[i] if view == "original" else variants[(docs[i].input.doc_id, view)], profile) for i in selected]
                        values = list(model.predict(texts_test)) if texts_test else []
                        require(len(values) == len(selected) and all(v in GRADES for v in values), "style_prediction_invalid")
                        predicted[view] = values
                    for offset, index in enumerate(selected):
                        result_rows.append({"seed": seed, "fold": fold, "profile": profile, "arm": arm,
                            "parent_doc_id": docs[index].input.doc_id, "reference_grade": str(labels[index]),
                            "predictions": {view: str(values[offset]) for view, values in predicted.items()}})
        require(len(seen) == len(docs), "style_cv_incomplete")
    expected = seeds*len(panel)*len(PROFILES)*len(ARMS)
    require(len(result_rows) == expected and len({(r["seed"], r["profile"], r["arm"], r["parent_doc_id"]) for r in result_rows}) == expected, "style_pair_coverage_invalid")
    summary = {}
    for profile in PROFILES:
        summary[profile] = {}
        for arm in ARMS:
            rows = [r for r in result_rows if r["profile"] == profile and r["arm"] == arm]
            n = len(rows)
            agreement = {view: sum(r["predictions"][view] == r["reference_grade"] for r in rows)/n for view in ("original", *STYLES)}
            flips = {view: sum(r["predictions"][view] != r["predictions"]["original"] for r in rows) for view in STYLES}
            summary[profile][arm] = {"parent_seed_observations": n, "unique_parents": len(panel),
                "reference_agreement": agreement, "prediction_changes_vs_original": flips,
                "plain_polite_disagreements": sum(r["predictions"]["plain"] != r["predictions"]["polite"] for r in rows),
                "correct_to_wrong": {view: sum(r["predictions"]["original"] == r["reference_grade"] and r["predictions"][view] != r["reference_grade"] for r in rows) for view in STYLES},
                "wrong_to_correct": {view: sum(r["predictions"]["original"] != r["reference_grade"] and r["predictions"][view] == r["reference_grade"] for r in rows) for view in STYLES},
                "grade_flip_counts": {view: {g: sum(r["reference_grade"] == g and r["predictions"][view] != r["predictions"]["original"] for r in rows) for g in GRADES} for view in STYLES}}
    return {**FLAGS, "status": "paired_diagnostic_not_customer_evaluation", "diagnostic_probe_fitted": True,
        "production_model_trained": False, "seeds": list(range(seeds)), "folds": folds, "unique_panel_parents": len(panel),
        "training_parent_population": len(docs), "new_benchmark_documents": 0, "paired_same_parent_folds": True,
        "grouping_sha256": value_digest(parent.semantic_family_audit(docs)), "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__, "python_version": sys.version, "summary": summary, "fold_assignments": fold_rows,
        "paired_predictions": result_rows, "confidence_intervals_computed": False,
        "body_only_labels_identifiable": False, "whole_semantic_equivalence_certified": False,
        "training_design": "Original: one view/parent. Duplicate control: two original copies/parent. Balanced: two styles for panel parents, two original copies for other parents. Classifier weight sums to one per parent; balanced and duplicate-control rows have identical counts. IDF is fit separately within each arm/train fold.",
        "warning": "Only 48 authored parents, repeatedly evaluated across seeds. Sensitivity of a character probe, not customer accuracy, causal proof of all shortcuts or release approval."}


def audit(root, out, *, seeds=5):
    root, out = root.resolve(), out.resolve()
    require(not out.exists() and not out.is_relative_to(root), "style_audit_output_invalid")
    verify(root)
    before = hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest()
    result = measure_paired(seeds=seeds)
    result["diagnostic_pack_sha256"] = before
    result["sources_sha256"] = source_hashes()
    verify(root)
    require(hashlib.sha256((root/"manifest.json").read_bytes()).hexdigest() == before, "style_audit_pack_changed")
    _new_file(out, _json(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("prepare")
    create.add_argument("--out", type=Path, required=True)
    create.add_argument("--parent-pack", type=Path, required=True)
    create.add_argument("--tokenizer", type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--pack", type=Path, required=True)
    measure = commands.add_parser("audit")
    measure.add_argument("--pack", type=Path, required=True)
    measure.add_argument("--out", type=Path, required=True)
    measure.add_argument("--seeds", type=int, default=5)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, parent_pack=args.parent_pack, tokenizer=args.tokenizer)
        elif args.command == "verify":
            result = verify(args.pack)
        else:
            result = audit(args.pack, args.out, seeds=args.seeds)
        print(json.dumps({k: result[k] for k in ("status", "panel_parents", "variant_views", "new_benchmark_documents", "summary") if k in result}, ensure_ascii=False))
        return 0
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError, ImportError):
        print(json.dumps({"status": "failed", "code": "customer_guide_style_audit_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
