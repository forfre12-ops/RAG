"""Offline checks for UNAPPROVED content-reference candidates, not a classifier.

This module never extracts facts from text, signs answers, trains, or publishes.
Predicate annotations are fictional author assertions, not customer evidence.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from collections import Counter
from pathlib import Path

from evaluation_inputs import normalized_hash, read_rows, sha256

POLICY_ID = "content-protection-reference-v1-draft"
GRADES = ("TS", "S1", "S2", "S3")
PREDICATES = ("core_package", "live_control", "bulk_sensitive", "specific", "internal", "generic")
RULES = dict(zip(PREDICATES, ("CP-TS-01", "CP-TS-02", "CP-TS-03", "CP-S1-01", "CP-S2-01", "CP-S3-01")))
RULE_GRADES = dict(zip(PREDICATES, ("TS", "TS", "TS", "S1", "S2", "S3")))
HOLD_RULES = {"needs_evidence": "CP-HOLD-01", "needs_policy_review": "CP-HOLD-02"}
INPUT_KEYS = {"doc_id", "family_id", "text", "text_sha256", "policy_version", "policy_sha256", "source"}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def resolve(facts: dict, *, conflict: bool = False, conflict_candidates=()) -> dict:
    """Enumerate unknowns; missing information is neither false nor default S3.

    Precedence excludes lower grades, not the existence of lower-level content.
    A proven TS is unaffected by unknown facts which cannot change its grade.
    Unresolved contradictory scope/version claims are always sent to review.
    """
    require(set(facts) == set(PREDICATES), "Invalid predicate set")
    require(all(v is None or type(v) is bool for v in facts.values()), "Facts must be bool or unknown")
    if conflict:
        require(len(set(conflict_candidates)) >= 2 and all(g in GRADES for g in conflict_candidates),
                "Conflicting context requires at least two explicit grade candidates")
        return {"grade": None, "status": "needs_policy_review", "rule_ids": ["CP-HOLD-02"],
                "candidate_grades": sorted(set(conflict_candidates), key=GRADES.index)}
    unknown = [k for k, v in facts.items() if v is None]
    possible = set()
    for values in itertools.product((False, True), repeat=len(unknown)):
        completed = facts | dict(zip(unknown, values))
        possible.add(next((RULE_GRADES[k] for k in PREDICATES if completed[k]), None))
    candidates = sorted((g for g in possible if g is not None), key=GRADES.index)
    if len(possible) != 1 or None in possible:
        return {"grade": None, "status": "needs_evidence", "rule_ids": ["CP-HOLD-01"],
                "candidate_grades": candidates}
    grade = candidates[0]
    return {"grade": grade, "status": "recommended", "candidate_grades": [grade],
            "rule_ids": [RULES[k] for k in PREDICATES if facts[k] is True and RULE_GRADES[k] == grade]}


def validate_pack(inputs: list[dict], answers: list[dict], splits: dict, policy: Path) -> dict:
    """Fail closed on structural/annotation defects; no claim of semantic truth."""
    policy_sha = sha256(policy)
    policy_text = policy.read_text(encoding="utf-8")
    require(POLICY_ID in policy_text and all(r in policy_text for r in (*RULES.values(), *HOLD_RULES.values())),
            "Policy rule registry missing")
    require(len(inputs) == len(answers) == 140, "Pack must have exactly 140 input/answer pairs")
    docs = {r.get("doc_id"): r for r in inputs}
    answer_map = {r.get("doc_id"): r for r in answers}
    require(len(docs) == len(answer_map) == 140 and set(docs) == set(answer_map) and None not in docs,
            "Duplicate/mismatched document IDs")
    require(set(splits) == {"development", "sealed_candidate"}, "Unexpected splits")
    all_ids = splits["development"] + splits["sealed_candidate"]
    require(len(all_ids) == len(set(all_ids)) == 140 and set(all_ids) == set(docs), "Split membership mismatch")
    normalized = {}
    annotations = {}
    spans = 0
    for doc_id, doc in docs.items():
        answer = answer_map[doc_id]
        require(set(doc) == INPUT_KEYS, "Unexpected input field (potential answer leakage)")
        require(isinstance(doc["text"], str) and bool(doc["text"].strip()) and bool(doc["family_id"]), "Empty body/family")
        require(doc["text_sha256"] == text_hash(doc["text"]), "Body SHA-256 mismatch")
        for row in (doc, answer):
            require(row.get("policy_version") == POLICY_ID and row.get("policy_sha256") == policy_sha,
                    "Policy binding mismatch")
        require(answer.get("text_sha256") == doc["text_sha256"] and answer.get("family_id") == doc["family_id"],
                "Answer binding mismatch")
        require(doc["source"].get("kind") == "synthetic_scenario" and doc["source"].get("real_document") is False,
                "Fictional source must be explicit")
        require(answer.get("label_source") == "generator_intended_label" and answer.get("approval_status") == "unapproved"
                and answer.get("review_status") == "pending" and answer.get("human_signature") is None
                and answer.get("gold_eligible") is False and answer.get("training_allowed") is False,
                "Fabricated authority or approval")
        facts = answer["facts"]
        require(set(facts) == set(PREDICATES), "Answer predicate set mismatch")
        evidence = {e["id"]: e for e in answer["evidence"]}
        require(len(evidence) == len(answer["evidence"]) and bool(evidence), "Duplicate/absent evidence")
        for e in evidence.values():
            start, end = e["start"], e["end"]
            require(type(start) is int and type(end) is int and 0 <= start < end <= len(doc["text"]), "Invalid span")
            require(doc["text"][start:end] == e["quote"], "Evidence quote mismatch")
            require(e["origin"] == "synthetic_scenario", "Unverified real-world evidence origin")
            spans += 1
        for fact in facts.values():
            require(fact["state"] in {"known", "unknown"} and
                    ((fact["state"] == "unknown" and fact["value"] is None) or
                     (fact["state"] == "known" and type(fact["value"]) is bool)), "Unknown is not false/zero")
            require(bool(fact["evidence_ids"]) and set(fact["evidence_ids"]) <= set(evidence), "Unbound fact evidence")
            require(fact["origin"] == "synthetic_author_assumption" and bool(fact["scope_note"]), "Unscoped author assumption")
        require(type(answer["context_conflict"]) is bool, "Conflict flag must be boolean")
        expected = resolve({k: f["value"] for k, f in facts.items()}, conflict=answer["context_conflict"],
                           conflict_candidates=answer["conflict_candidates"])
        require(answer["expected_grade"] == expected["grade"] and answer["expected_status"] == expected["status"],
                f"Authored answer contradicts explicit predicates: {doc_id}")
        require(answer["rule_ids"] == expected["rule_ids"] and answer["candidate_grades"] == expected["candidate_grades"],
                "Authored rules/candidate bounds mismatch")
        require(bool(answer["rationale"]) and bool(answer["not_higher_reason"]) and bool(answer["not_lower_reason"]),
                "Missing boundary explanation")
        if expected["status"] == "needs_evidence":
            require(bool(answer["missing_evidence"]), "Missing follow-up evidence request")
        if expected["status"] == "needs_policy_review":
            require(bool(answer["conflict_note"]) and bool(answer["resolution_owner_role"]), "Unowned conflict")
        # Normalization is whitespace-insensitive, not a semantic duplicate proof.
        h = normalized_hash(doc["text"])
        require(h not in normalized, "Duplicate normalized body")
        normalized[h] = doc_id
        key = (doc["policy_sha256"], tuple(facts[k]["value"] for k in PREDICATES), answer["context_conflict"],
               tuple(answer["conflict_candidates"]))
        outcome = (answer["expected_grade"], answer["expected_status"])
        require(key not in annotations or annotations[key] == outcome, "Same conditions have conflicting answers")
        annotations[key] = outcome
    summary = {}
    family_sets = []
    for split, ids in splits.items():
        counts = Counter(answer_map[i]["expected_grade"] or "REVIEW" for i in ids)
        expected_n, each, reviews, families = (90, 20, 10, 25) if split == "development" else (50, 10, 10, 15)
        require(len(ids) == expected_n and counts == Counter({g: each for g in GRADES} | {"REVIEW": reviews}),
                "Split counts/grade balance mismatch")
        family_set = {docs[i]["family_id"] for i in ids}
        require(len(family_set) == families, "Family count mismatch")
        family_sets.append(family_set)
        summary[split] = {"n": len(ids), "counts": dict(counts), "families": len(family_set),
                          "statuses": dict(Counter(answer_map[i]["expected_status"] for i in ids))}
    require(not family_sets[0] & family_sets[1], "Family leakage across partitions")
    for family in set.union(*family_sets):
        members = [a for a in answers if a["family_id"] == family]
        grades = Counter(a["expected_grade"] for a in members)
        statuses = Counter(a["expected_status"] for a in members)
        require(grades == Counter(GRADES) or (grades == Counter({None: 2}) and
                statuses == Counter({"needs_evidence": 1, "needs_policy_review": 1})),
                "Family variants must be four contrastive grades or two review contexts")
    return {"status": "STRUCTURAL_CHECKS_OK", "n": 140, "grade_cases": 120, "review_cases": 20,
            "unique_normalized_bodies": len(normalized), "families": len(set.union(*family_sets)),
            "evidence_spans_checked": spans, "split": summary, "cross_split_body_overlap": 0,
            "cross_split_family_overlap": 0, "policy_sha256": policy_sha, "gold_qualified": 0,
            "limitations": ["Predicate consistency is not independent semantic or human validation.",
                            "Sealed candidate is a partition name, not verified access-controlled blindness."]}


def known_pool_overlap(inputs: list[dict], root: Path, paths: list[str]) -> dict:
    from evaluation_inputs import text_of

    lookup = {normalized_hash(row["text"]): row["doc_id"] for row in inputs}
    families = {row["family_id"] for row in inputs}
    result = []
    for rel in paths:
        path = root / rel
        if not path.is_file():
            result.append({"path": rel, "status": "MISSING"})
            continue
        digest = sha256(path)
        rows = read_rows(path)
        text_matches = sorted({lookup[normalized_hash(text_of(r))] for r in rows if text_of(r) and normalized_hash(text_of(r)) in lookup})
        family_matches = sorted({str(r["family_id"]) for r in rows if r.get("family_id") in families})
        require(sha256(path) == digest, "Training pool changed during scan")
        result.append({"path": rel, "status": "CHECKED", "sha256": digest, "rows": len(rows),
                       "text_overlap_candidates": text_matches, "family_id_overlap": family_matches,
                       "rows_without_body": sum(not text_of(r) for r in rows),
                       "rows_without_family_id": sum(not r.get("family_id") for r in rows)})
    return {"scope": "listed_known_pools_only", "complete_model_training_lineage": False,
            "semantic_family_independence_verified": False, "pools": result,
            "overlap_candidate_ids": sorted({i for r in result for i in r.get("text_overlap_candidates", [])}),
            "note": "Missing family metadata and unlisted/model-pretraining inputs remain unverified."}


def measure_predictions(inputs: list[dict], answers: list[dict], predictions: list[dict]) -> dict:
    """Reuse existing grade metrics; review-only truth never becomes a fake grade.

    Caller must first validate the complete pack. A subset is permitted only by
    explicitly supplying the same subset of inputs and answers to this helper.
    Grade abstentions remain errors in the all-grade denominator and are reported
    outside the legacy grade matrix, which requires four-way predictions.
    """
    from measure_four_metrics import compute

    docs = {r["doc_id"]: r for r in inputs}
    expected = {r["doc_id"]: r for r in answers}
    observed = {r["doc_id"]: r for r in predictions}
    require(len(docs) == len(inputs) == len(expected) == len(answers) == len(observed) == len(predictions)
            and set(docs) == set(expected) == set(observed), "Prediction ID coverage mismatch")
    records, grade_total, abstained, correct = [], 0, 0, 0
    tp = fp = fn = tn = 0
    routing_failures, grade_errors = [], []
    for doc_id, doc in docs.items():
        a, p = expected[doc_id], observed[doc_id]
        for field in ("text_sha256", "policy_version", "policy_sha256"):
            require(p.get(field) == doc[field] == a[field], "Prediction/document/answer binding mismatch")
        require("predicted" in p and "model_grade" in p and p["predicted"] in (*GRADES, None)
                and p["model_grade"] in (*GRADES, None), "Explicit valid prediction/raw grade required")
        require(p.get("status") in {"staging", "needs_review"}, "Explicit staging/needs_review routing required")
        needs_review, reviewed = a["expected_status"] != "recommended", p["status"] == "needs_review"
        tp += int(needs_review and reviewed)
        fp += int(not needs_review and reviewed)
        fn += int(needs_review and not reviewed)
        tn += int(not needs_review and not reviewed)
        if needs_review != reviewed:
            routing_failures.append(doc_id)
        if a["expected_grade"] is not None:
            grade_total += 1
            correct += int(p["predicted"] == a["expected_grade"])
            if p["predicted"] != a["expected_grade"]:
                grade_errors.append(doc_id)
            if p["predicted"] is None:
                abstained += 1
            else:
                records.append({"truth": a["expected_grade"], "predicted": p["predicted"],
                                "model_grade": p["model_grade"], "status": p["status"]})
    def metric(count, total):
        return {"count": count, "n": total, "rate": count / total if total else None}
    def error_metric(truths, bad_predictions):
        total = sum(a["expected_grade"] in truths for a in answers)
        count = sum(a["expected_grade"] in truths and observed[a["doc_id"]]["predicted"] in bad_predictions for a in answers)
        return metric(count, total)
    return {
        "scope": "unapproved_reference_candidate_diagnostic", "customer_accuracy_claim_allowed": False,
        "model_promotion_allowed": False, "n": len(inputs),
        "grade_metrics": compute(records), "grade_matrix_denominator": len(records),
        "grade_accuracy_including_abstentions": metric(correct, grade_total),
        "grade_abstentions": metric(abstained, grade_total),
        "raw_grade_missing": sum(p["model_grade"] is None for p in predictions),
        "raw_metric_note": "Legacy raw metrics fall back to final grade where raw grade is absent; see missing count.",
        "s1_to_s2": error_metric({"S1"}, {"S2"}), "s2_to_s1": error_metric({"S2"}, {"S1"}),
        "s2_to_s3": error_metric({"S2"}, {"S3"}), "ts_s1_to_s3": error_metric({"TS", "S1"}, {"S3"}),
        "s3_overclassification": error_metric({"S3"}, {"TS", "S1", "S2"}),
        "routing": {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
                    "missed_review": metric(fn, tp + fn), "unnecessary_review": metric(fp, fp + tn),
                    "review_capture": metric(tp, tp + fn), "review_rate": metric(tp + fp, len(inputs)),
                    "not_routed_rate": metric(fn + tn, len(inputs)), "human_confirmation_measured": False},
        "grade_error_ids": grade_errors, "routing_error_ids": routing_failures,
    }


def load_pack(pack: Path, policy: Path) -> tuple[list, list, dict, dict]:
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("schema_version") == "content-reference-pack-v1"
            and manifest.get("approval_status") == "unapproved" and manifest.get("human_signature") is None
            and manifest.get("training_allowed") is False, "Invalid pack authority")
    listed = [item["path"] for item in manifest["files"]]
    required = {f"{s}/{f}" for s in ("development", "sealed_candidate")
                for f in ("inputs.jsonl", "answers.candidate.jsonl", "REVIEW_INPUTS.md", "ANSWERS_CANDIDATE.md")}
    required |= {"validation.json", "legacy_label_issues.json", "comparison_training_contract.json", "LEGACY_LABEL_ISSUES.md", "README.md"}
    require(len(listed) == len(set(listed)) and set(listed) == required, "Pack manifest file coverage mismatch")
    for item in manifest["files"]:
        candidate = (pack / item["path"]).resolve()
        require(candidate.is_relative_to(pack.resolve()), "Manifest path escapes pack")
        require(sha256(candidate) == item["sha256"], "Pack file hash mismatch")
    require(manifest["policy_sha256"] == sha256(policy), "Manifest policy binding mismatch")
    inputs, answers, splits = [], [], {}
    for split in ("development", "sealed_candidate"):
        docs = read_rows(pack / split / "inputs.jsonl")
        inputs.extend(docs)
        answers.extend(read_rows(pack / split / "answers.candidate.jsonl"))
        splits[split] = [d["doc_id"] for d in docs]
    require(manifest["splits"] == splits, "Manifest partition order/membership mismatch")
    return inputs, answers, splits, validate_pack(inputs, answers, splits, policy)
