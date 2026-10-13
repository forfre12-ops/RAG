"""Offline authoring, split and scoring contracts for a proposed synthetic 800/200 trial.

No customer approval, truth certification, model call or permission promotion.
Typed evidence validation verifies bindings, not the semantics of authored claims.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError

from koipa.dataset_usage import body_fingerprint, fixture_registry
from koipa.policy_facts import ContractModel, FactContractError, require, text_digest, value_digest

GRADES = ("TS", "S1", "S2", "S3")
Grade = Literal["TS", "S1", "S2", "S3"]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Key = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{2,79}$")]
Text = Annotated[str, Field(min_length=1, max_length=20000, pattern=r"\S")]
FLAGS = {"training_allowed": False, "model_evaluation_allowed": False,
         "gold_eligible": False, "customer_accuracy_measured": False, "human_signoff_created": False}
TARGET = {"train": {g: 200 for g in GRADES}, "evaluation": {g: 50 for g in GRADES}}


class ContextFact(ContractModel):
    name: Literal["scope_complete", "release_authorized", "current_revision", "core_asset",
                  "reader_scope", "management_controls", "impact_description", "other_risk_present"]
    value: bool | int | str | list[str] | None
    origin: Literal["synthetic_assumption"]


class ModelInput(ContractModel):
    doc_id: Annotated[str, Field(pattern=r"^doc-[0-9a-f]{24}$")]
    text: Annotated[str, Field(min_length=120, max_length=20000)]
    context: list[ContextFact]


class AuthoredClaim(ContractModel):
    name: Key
    claim: Text
    quote: Text
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]
    sha256: Digest
    status: Literal["authored_binding_only"]


class Document(ContractModel):
    schema_version: Literal["customer-synthetic-draft-v1"]
    document_origin: Literal["synthetic"]
    input: ModelInput
    input_sha256: Digest
    family_id: Key
    scenario_id: Key
    template_family_id: Key
    domain: Text
    claims: Annotated[list[AuthoredClaim], Field(min_length=1)]
    training_allowed: Literal[False]
    model_evaluation_allowed: Literal[False]
    gold_eligible: Literal[False]
    customer_accuracy_measured: Literal[False]
    human_signoff_created: Literal[False]


class Answer(ContractModel):
    doc_id: Annotated[str, Field(pattern=r"^doc-[0-9a-f]{24}$")]
    input_sha256: Digest
    policy_id: Key
    policy_version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+(?:\.[0-9]+)?$")]
    policy_sha256: Digest
    reference_grade: Grade
    rule_ids: Annotated[list[Key], Field(min_length=1)]
    evidence_names: Annotated[list[Key], Field(min_length=1)]
    other_grade_exclusions: dict[Grade, Text]
    status: Literal["authored_candidate"]


class Prediction(ContractModel):
    doc_id: Annotated[str, Field(pattern=r"^doc-[0-9a-f]{24}$")]
    input_sha256: Digest
    presented_input_sha256: Digest
    policy_sha256: Digest
    model_sha256: Digest
    run_id: Key
    profile: Literal["body_only", "body_context"]
    status: Literal["ok", "needs_review", "error"]
    predicted_grade: Grade | None


def _parse(cls, raw):
    # Pydantic Literal[False] alone accepts integer 0; reject this explicitly.
    value = raw.model_dump() if isinstance(raw, cls) else raw
    if cls is Document and isinstance(value, dict):
        require(all(type(value.get(k)) is bool and value[k] is False for k in FLAGS), "benchmark_permission_flag_invalid")
    try:
        return cls.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise FactContractError("benchmark_contract_invalid") from None


def validate_documents(raw_records):
    require(isinstance(raw_records, (list, tuple)) and 0 < len(raw_records) <= 10000, "benchmark_documents_empty_or_oversized")
    records = [_parse(Document, r) for r in raw_records]
    require(len({d.input.doc_id for d in records}) == len(records), "benchmark_duplicate_doc_id")
    for d in records:
        require(value_digest(d.input.model_dump()) == d.input_sha256, "benchmark_input_hash_mismatch")
        require(len({c.name for c in d.input.context}) == len(d.input.context), "benchmark_duplicate_context")
        require(len({c.name for c in d.claims}) == len(d.claims), "benchmark_duplicate_claim")
        for c in d.claims:
            require(c.start < c.end <= len(d.input.text) and d.input.text[c.start:c.end] == c.quote and
                    text_digest(c.quote) == c.sha256, "benchmark_claim_binding_invalid")
    return records


def validate_answers(documents, raw_answers):
    require(isinstance(raw_answers, (list, tuple)) and len(raw_answers) == len(documents), "benchmark_answer_coverage_invalid")
    answers = [_parse(Answer, a) for a in raw_answers]
    by_id = {d.input.doc_id: d for d in documents}
    require(len({a.doc_id for a in answers}) == len(answers) and set(by_id) == {a.doc_id for a in answers}, "benchmark_answer_ids_invalid")
    require(len({(a.policy_id, a.policy_version, a.policy_sha256) for a in answers}) == 1, "benchmark_mixed_policies")
    for a in answers:
        d = by_id[a.doc_id]
        require(a.input_sha256 == d.input_sha256, "benchmark_answer_input_mismatch")
        require(set(a.evidence_names) <= {c.name for c in d.claims} and
                len(set(a.evidence_names)) == len(a.evidence_names), "benchmark_answer_evidence_invalid")
        require(len(set(a.rule_ids)) == len(a.rule_ids), "benchmark_duplicate_rule")
        require(set(a.other_grade_exclusions) == set(GRADES) - {a.reference_grade}, "benchmark_exclusions_invalid")
    return answers


def normalized(text, *, mask_numbers=False):
    value = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()
    return re.sub(r"[0-9]+(?:[.,][0-9]+)*", "#", value) if mask_numbers else value


def _groups(docs, duplicate_pairs=()):
    parent = {d.input.doc_id: d.input.doc_id for d in docs}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def join(a, b):
        a, b = root(a), root(b)
        if a != b:
            parent[max(a, b)] = min(a, b)

    seen = {}
    for d in docs:
        for key in ("family_id", "scenario_id", "template_family_id"):
            identity = (key, getattr(d, key))
            if identity in seen:
                join(d.input.doc_id, seen[identity])
            seen[identity] = d.input.doc_id
    for a, b in duplicate_pairs:
        join(a, b)
    result = defaultdict(list)
    for k in parent:
        result[root(k)].append(k)
    return {k: sorted(v) for k, v in sorted(result.items())}


def duplicate_audit(documents, *, threshold=0.85):
    require(type(threshold) in (float, int) and 0 < threshold <= 1, "benchmark_near_threshold_invalid")
    docs = sorted(documents, key=lambda d: d.input.doc_id)
    normal = [normalized(d.input.text) for d in docs]
    masked = [normalized(d.input.text, mask_numbers=True) for d in docs]
    shingles = [{s[i:i+5] for i in range(len(s)-4)} for s in masked]
    exact, number_only, near, edges = [], [], [], []
    for i in range(len(docs)):
        for j in range(i):
            pair = [docs[j].input.doc_id, docs[i].input.doc_id]
            if normal[i] == normal[j]:
                exact.append(pair)
                edges.append(pair)
            elif masked[i] == masked[j]:
                number_only.append(pair)
                edges.append(pair)
            elif min(len(shingles[i]), len(shingles[j])) >= threshold * max(len(shingles[i]), len(shingles[j])):
                common = len(shingles[i] & shingles[j])
                similarity = common / max(1, len(shingles[i]) + len(shingles[j]) - common)
                if similarity >= threshold:
                    near.append({"ids": pair, "jaccard": similarity})
                    edges.append(pair)
    registry_hashes = {h for r in fixture_registry()["records"] for h in r["body_fingerprints"]}
    blocked = [d.input.doc_id for d in docs if body_fingerprint(d.input.text) in registry_hashes]
    return {"normalization": "NFKC+whitespace removal+casefold; near view additionally masks numbers",
            "near_method": "character 5-gram set Jaccard", "near_threshold": threshold,
            "exact_pairs": exact, "number_only_pairs": number_only, "near_pairs": near,
            "known_fixture_body_matches": blocked, "groups": _groups(docs, edges),
            "semantic_duplicates_excluded": False}


def audit_external_pool(documents, paths):
    paths = sorted({Path(p).resolve() for p in paths})
    require(bool(paths), "benchmark_external_pool_empty")
    target = defaultdict(list)
    masked = defaultdict(list)
    for d in documents:
        target[text_digest(normalized(d.input.text))].append(d.input.doc_id)
        masked[text_digest(normalized(d.input.text, mask_numbers=True))].append(d.input.doc_id)
    matches, failures, files = [], [], []
    checked_rows, without_text = 0, 0
    for path in paths:
        data = path.read_bytes()
        files.append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
        try:
            lines = data.decode("utf-8-sig").splitlines()
        except UnicodeError:
            failures.append({"path": str(path), "line": None, "code": "not_utf8"})
            continue
        for number, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                row = strict_loads(line)
                require(isinstance(row, dict), "row_not_object")
            except (ValueError, TypeError):
                failures.append({"path": str(path), "line": number, "code": "invalid_json_object"})
                continue
            text = next((row[k] for k in ("text", "body", "content", "desc") if isinstance(row.get(k), str) and row[k].strip()), None)
            if text is None:
                without_text += 1
                continue
            checked_rows += 1
            exact = target.get(text_digest(normalized(text)), [])
            similar = masked.get(text_digest(normalized(text, mask_numbers=True)), [])
            if exact or similar:
                matches.append({"path": str(path), "line": number, "exact_doc_ids": exact, "number_mask_doc_ids": similar})
    return {"files": files, "text_rows_checked": checked_rows, "rows_without_supported_text": without_text,
            "parse_failures": failures, "matches": matches,
            "coverage_complete": not failures and without_text == 0 and checked_rows > 0,
            "scope": "supplied files, top-level text/body/content/desc; exact and number-normalized only",
            "full_semantic_or_fuzzy_pool_scan": False}


def propose_split(raw_documents, raw_answers, *, train_per_grade=200, evaluation_per_grade=50, seed=20260915):
    """Whole connected groups, exact four-grade quotas. Not a permission grant."""
    require(type(train_per_grade) is int and type(evaluation_per_grade) is int and
            train_per_grade > 0 and evaluation_per_grade > 0 and type(seed) is int, "benchmark_split_size_invalid")
    docs = validate_documents(raw_documents)
    answers = validate_answers(docs, raw_answers)
    require(Counter(a.reference_grade for a in answers) == {g: train_per_grade + evaluation_per_grade for g in GRADES}, "benchmark_grade_quota_invalid")
    audit = duplicate_audit(docs)
    require(not audit["exact_pairs"] and not audit["known_fixture_body_matches"], "benchmark_duplicate_or_fixture_blocked")
    labels = {a.doc_id: a.reference_grade for a in answers}
    groups = audit["groups"]
    keys = sorted(groups)
    # Optional scientific dependency only for constrained selection; no model fitting.
    import numpy as np
    import scipy
    from scipy.optimize import Bounds, LinearConstraint, milp

    matrix = np.array([[sum(labels[i] == g for i in groups[k]) for k in keys] for g in GRADES], dtype=float)
    cost = np.array([int(text_digest(f"{seed}:{k}")[:12], 16) / 16**12 for k in keys])
    result = milp(c=cost, integrality=np.ones(len(keys)), bounds=Bounds(np.zeros(len(keys)), np.ones(len(keys))),
                  constraints=LinearConstraint(matrix, evaluation_per_grade, evaluation_per_grade),
                  options={"time_limit": 30, "mip_rel_gap": 0.0})
    require(result.success and result.x is not None, "benchmark_group_split_infeasible_or_timeout")
    evaluation = {i for k, chosen in zip(keys, result.x, strict=True) if chosen > 0.5 for i in groups[k]}
    partitions = {d.input.doc_id: "evaluation" if d.input.doc_id in evaluation else "train" for d in docs}
    for part, count in (("train", train_per_grade), ("evaluation", evaluation_per_grade)):
        require(Counter(labels[i] for i, p in partitions.items() if p == part) == {g: count for g in GRADES}, "benchmark_solver_quota_mismatch")
    require(all(len({partitions[i] for i in members}) == 1 for members in groups.values()), "benchmark_split_group_overlap")
    return {**FLAGS, "status": "split_proposal_only", "seed": seed, "solver": "scipy.optimize.milp",
            "scipy_version": scipy.__version__, "sampling": "seeded constrained selection, not uniform independent sampling",
            "partitions": dict(sorted(partitions.items())), "groups": groups,
            "train_counts": {g: train_per_grade for g in GRADES}, "evaluation_counts": {g: evaluation_per_grade for g in GRADES},
            "documents_sha256": value_digest([d.model_dump() for d in sorted(docs, key=lambda d: d.input.doc_id)]),
            "answers_sha256": value_digest([a.model_dump() for a in sorted(answers, key=lambda a: a.doc_id)]),
            "duplicate_audit": audit, "customer_size_contract_met": train_per_grade == 200 and evaluation_per_grade == 50}


def presented_text(doc, profile):
    require(profile in {"body_only", "body_context"}, "benchmark_input_profile_invalid")
    if profile == "body_only":
        return doc.input.text
    return doc.input.text + "\n\n[가상 맥락]\n" + json.dumps([c.model_dump() for c in doc.input.context], ensure_ascii=False, sort_keys=True, allow_nan=False)


def score_predictions(raw_documents, raw_answers, raw_predictions, *, expected_count=200):
    """Score supplied outputs; missing/review/error never disappear from denominators."""
    docs = validate_documents(raw_documents)
    answers = validate_answers(docs, raw_answers)
    require(type(expected_count) is int and expected_count > 0 and len(docs) == expected_count, "benchmark_score_count_invalid")
    require(isinstance(raw_predictions, (list, tuple)), "benchmark_predictions_invalid")
    preds = [_parse(Prediction, p) for p in raw_predictions]
    by_doc, gold = {d.input.doc_id: d for d in docs}, {a.doc_id: a for a in answers}
    require(len({p.doc_id for p in preds}) == len(preds) and {p.doc_id for p in preds} <= set(gold), "benchmark_prediction_ids_invalid")
    run_keys = {(p.run_id, p.model_sha256, p.profile, p.policy_sha256) for p in preds}
    require(len(run_keys) <= 1, "benchmark_mixed_prediction_runs")
    for p in preds:
        require(p.input_sha256 == by_doc[p.doc_id].input_sha256 and p.policy_sha256 == gold[p.doc_id].policy_sha256 and
                p.presented_input_sha256 == text_digest(presented_text(by_doc[p.doc_id], p.profile)), "benchmark_prediction_binding_invalid")
        require(p.status != "ok" or p.predicted_grade is not None, "benchmark_success_grade_missing")
        require(p.status != "error" or p.predicted_grade is None, "benchmark_error_cannot_have_grade")
    by_pred = {p.doc_id: p for p in preds}
    columns = (*GRADES, "HOLD", "ERROR", "MISSING")
    matrix = {g: {k: 0 for k in columns} for g in GRADES}
    raw_correct = resolved_correct = 0
    statuses = Counter()
    cases = []
    for a in answers:
        p = by_pred.get(a.doc_id)
        status = p.status if p else "missing"
        grade = p.predicted_grade if p else None
        outcome = grade if grade is not None else {"needs_review": "HOLD", "error": "ERROR", "missing": "MISSING"}[status]
        matrix[a.reference_grade][outcome] += 1
        correct = grade == a.reference_grade
        raw_correct += correct
        resolved_correct += correct and status == "ok"
        statuses[status] += 1
        cases.append({"doc_id": a.doc_id, "expected_grade": a.reference_grade, "predicted_grade": grade,
                      "status": status, "grade_agrees": correct, "resolved_grade_agrees": correct and status == "ok"})
    support = Counter(a.reference_grade for a in answers)
    per_grade = {}
    for g in GRADES:
        tp = matrix[g][g]
        predicted = sum(matrix[t][g] for t in GRADES)
        recall = tp / support[g] if support[g] else None
        precision = tp / predicted if predicted else None
        f1 = 2 * tp / (support[g] + predicted) if support[g] + predicted else None
        per_grade[g] = {"support": support[g], "recall_all_gold": recall, "precision": precision, "f1": f1}
    return {**FLAGS, "status": "diagnostic_scoring_only", "denominator": expected_count,
            "customer_size_contract_met": expected_count == 200 and dict(support) == {g: 50 for g in GRADES},
            "grade_agreement_all_gold": raw_correct / expected_count,
            "resolved_grade_agreement_all_gold": resolved_correct / expected_count,
            "review_rate_all_gold": statuses["needs_review"] / expected_count,
            "statuses": {s: statuses[s] for s in ("ok", "needs_review", "error", "missing")},
            "run_complete": bool(preds) and not statuses["missing"] and not statuses["error"],
            "confusion_matrix": matrix, "per_grade": per_grade,
            "macro_recall": sum(per_grade[g]["recall_all_gold"] for g in GRADES) / 4 if all(support[g] for g in GRADES) else None,
            "s1_s2_confusions": matrix["S1"]["S2"] + matrix["S2"]["S1"],
            "s2_to_s3": matrix["S2"]["S3"], "ts_s1_to_s3": matrix["TS"]["S3"] + matrix["S1"]["S3"],
            "s3_overclassified": sum(matrix["S3"][g] for g in GRADES[:3]),
            "high_grade_unresolved": sum(r["expected_grade"] in {"TS", "S1"} and r["status"] != "ok" for r in cases),
            "cases": cases, "inference_performed_here": False, "model_artifact_verified_here": False,
            "warning": "Authored answer agreement only. No grade truth, independence, customer approval or production readiness is certified."}


def strict_loads(text):
    def pairs(items):
        out = {}
        for k, v in items:
            require(k not in out, "benchmark_duplicate_json_key")
            out[k] = v
        return out

    def constant(_):
        raise FactContractError("benchmark_nonfinite_json")

    result = json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    value_digest(result)  # Also rejects overflow literals such as 1e999.
    return result
