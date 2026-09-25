"""Offline input preflight; never infer, score answers, train or grant a release.

The adapter accepts only the public input projection.  It uses the existing
benchmark renderer without adding labels, rule IDs or authoring metadata.
Unknown evidence and oversized inputs are explicit holds, not zero or truncation.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

from pydantic import ValidationError

from koipa.customer_benchmark import FLAGS, ModelInput, Prediction, presented_text
from koipa.customer_guide_reference import POLICY, POLICY_SHA256, REQUIRED, decode_context
from koipa.policy_facts import FactContractError, require, text_digest, value_digest

SCHEMA = "customer-trial-input-preflight-v1"
MAX_TOKENS = 512
INPUT_KEYS = set(FLAGS) | {"doc_id", "text", "context", "input_sha256"}
CONTEXT_NAMES = {"reader_scope", "impact_description", "management_controls",
                 "release_authorized", "other_risk_present"}
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
RUN_ID = re.compile(r"[a-z][a-z0-9_-]{2,79}\Z")


def _digest(value):
    require(type(value) is str and DIGEST.fullmatch(value) is not None, "trial_digest_invalid")
    return value


def _flags(value):
    require(all(type(value.get(k)) is bool and value[k] is False for k in FLAGS), "trial_permission_invalid")


def source_hashes():
    directory = Path(__file__).resolve().parent
    names = ("customer_trial_input_v1.py", "customer_benchmark.py", "customer_guide_reference.py", "policy_facts.py")
    return {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def validate_input_rows(rows):
    """No answer, evidence, family or style object is accepted by this boundary."""
    require(type(rows) is list and 0 < len(rows) <= 10000, "trial_inputs_empty_or_oversized")
    parsed = []
    for row in rows:
        require(type(row) is dict and set(row) == INPUT_KEYS, "trial_input_fields_invalid")
        _flags(row)
        _digest(row["input_sha256"])
        try:
            item = ModelInput.model_validate({k: row[k] for k in ("doc_id", "text", "context")})
        except (ValidationError, TypeError, ValueError):
            raise FactContractError("trial_input_contract_invalid") from None
        require(value_digest(item.model_dump()) == row["input_sha256"], "trial_input_hash_mismatch")
        names = [c.name for c in item.context]
        require(len(names) == len(set(names)) and set(names) <= CONTEXT_NAMES, "trial_context_names_invalid")
        parsed.append(item)
    require(len({d.doc_id for d in parsed}) == len(parsed), "trial_duplicate_doc_id")
    return sorted(parsed, key=lambda d: d.doc_id)


def _load_tokenizer(path, expected_sha256):
    _digest(expected_sha256)
    data = Path(path).read_bytes()
    require(hashlib.sha256(data).hexdigest() == expected_sha256, "trial_tokenizer_hash_mismatch")
    # Reading bytes once binds the parser to the exact checked artifact.
    from tokenizers import Tokenizer, __version__

    try:
        tokenizer = Tokenizer.from_str(data.decode("utf-8"))
    except Exception:
        # Tokenizers has extension exceptions without a stable public base class.
        raise FactContractError("trial_tokenizer_invalid") from None
    tokenizer.no_padding()
    tokenizer.no_truncation()
    return tokenizer, __version__


def prepare_trial_inputs(rows, *, tokenizer_path, expected_tokenizer_sha256,
                         expected_policy_sha256, profile="body_context"):
    """Deterministic preflight only; context-ready never means approved to infer."""
    _digest(expected_policy_sha256)
    require(expected_policy_sha256 == POLICY_SHA256, "trial_policy_hash_mismatch")
    require(profile in {"body_only", "body_context"}, "trial_profile_invalid")
    documents = validate_input_rows(rows)
    tokenizer, tokenizer_version = _load_tokenizer(tokenizer_path, expected_tokenizer_sha256)
    views = []
    for document in documents:
        context = [c.model_dump() for c in document.context]
        reasons = []
        missing_names = sorted(CONTEXT_NAMES - {c.name for c in document.context})
        missing_facts = []
        if missing_names:
            reasons.append("required_context_group_missing")
        else:
            facts = decode_context(context)
            missing_facts = [key for key in REQUIRED if facts[key] is None]
            if missing_facts:
                reasons.append("required_context_fact_unknown")
        if profile == "body_only":
            reasons.append("body_only_context_not_presented")
        text = presented_text(SimpleNamespace(input=document), profile)
        encoded = tokenizer.encode(text, add_special_tokens=True)
        require(not encoded.overflowing and len(encoded.ids) > 0, "trial_tokenization_truncated_or_empty")
        require(len(encoded.ids) == len(encoded.attention_mask) == len(encoded.type_ids), "trial_tokenization_shape_invalid")
        if len(encoded.ids) > MAX_TOKENS:
            reasons.append("input_exceeds_512_tokens")
        tokens = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask,
                  "token_type_ids": encoded.type_ids}
        views.append({**FLAGS, "doc_id": document.doc_id, "input_sha256": value_digest(document.model_dump()),
            "body_sha256": text_digest(document.text), "context_sha256": value_digest(context),
            "profile": profile, "presented_input_sha256": text_digest(text), "presented_text": text,
            "presented_characters": len(text), "token_limit_with_special": MAX_TOKENS,
            "tokenizer_sha256": expected_tokenizer_sha256, "policy_sha256": expected_policy_sha256,
            "token_payload_sha256": value_digest(tokens), "tokens_with_special": len(encoded.ids),
            "fits_512_tokens": len(encoded.ids) <= MAX_TOKENS,
            "status": "needs_review" if reasons else "input_ready_only", "reason_codes": reasons,
            "missing_context_groups": missing_names, "missing_context_facts": missing_facts,
            "body_only_grade_scoring_allowed": False, "truncation_applied": False,
            "chunking_applied": False, "model_payload": tokens})
    counts = Counter(v["status"] for v in views)
    public = [{**FLAGS, **d.model_dump(), "input_sha256": value_digest(d.model_dump())} for d in documents]
    return {**FLAGS, "schema_version": SCHEMA, "status": "offline_preflight_not_release",
        "profile": profile, "policy_id": POLICY["id"], "policy_version": POLICY["version"],
        "policy_sha256": expected_policy_sha256, "tokenizer_sha256": expected_tokenizer_sha256,
        "tokenizers_version": tokenizer_version, "source_files_sha256": source_hashes(),
        "input_projection_sha256": value_digest(public), "views_sha256": value_digest(views),
        "max_tokens_with_special": MAX_TOKENS, "documents": len(documents),
        "input_ready_only": counts["input_ready_only"], "needs_review": counts["needs_review"],
        "grade_scoring_allowed": False, "model_inference_performed": False, "model_forward_executed": False,
        "model_artifact_verified": False, "semantic_evidence_completeness_certified": False,
        "body_context_is_supplied_facts_not_body_extraction": True,
        "source_inputs": public, "views": views}


def verify_trial_inputs(preflight, rows, *, tokenizer_path, expected_tokenizer_sha256, expected_policy_sha256):
    require(type(preflight) is dict, "trial_preflight_invalid")
    _flags(preflight)
    expected = prepare_trial_inputs(rows, tokenizer_path=tokenizer_path,
        expected_tokenizer_sha256=expected_tokenizer_sha256, expected_policy_sha256=expected_policy_sha256,
        profile=preflight.get("profile"))
    require(value_digest(preflight) == value_digest(expected), "trial_preflight_replay_mismatch")
    return expected


def validate_diagnostic_predictions(preflight, rows, raw_predictions, *, tokenizer_path,
        expected_tokenizer_sha256, expected_policy_sha256, expected_model_sha256, run_id):
    """Bind supplied diagnostic outputs; no predictor invocation or answer access.

    Model identity is a caller-declared contract, not artifact verification.
    Missing responses become ERROR; preflight holds remain needs_review.
    """
    _digest(expected_model_sha256)
    require(type(run_id) is str and RUN_ID.fullmatch(run_id) is not None, "trial_run_id_invalid")
    verified = verify_trial_inputs(preflight, rows, tokenizer_path=tokenizer_path,
        expected_tokenizer_sha256=expected_tokenizer_sha256, expected_policy_sha256=expected_policy_sha256)
    require(type(raw_predictions) is list, "trial_predictions_invalid")
    ready = {v["doc_id"]: v for v in verified["views"] if v["status"] == "input_ready_only"}
    output = {}
    required = set(Prediction.model_fields) | {"tokenizer_sha256", "token_payload_sha256", "reason_code"}
    for raw in raw_predictions:
        require(type(raw) is dict and set(raw) == required, "trial_prediction_fields_invalid")
        try:
            item = Prediction.model_validate({k: raw[k] for k in Prediction.model_fields})
        except (ValueError, TypeError):
            raise FactContractError("trial_prediction_contract_invalid") from None
        require(item.doc_id in ready and item.doc_id not in output, "trial_prediction_ids_invalid")
        view = ready[item.doc_id]
        require(item.model_sha256 == expected_model_sha256 and item.run_id == run_id,
                "trial_prediction_model_identity_mismatch")
        require(all(raw[k] == view[k] for k in ("input_sha256", "presented_input_sha256", "profile",
            "policy_sha256", "tokenizer_sha256", "token_payload_sha256")), "trial_prediction_lineage_mismatch")
        require(item.status != "ok" or (item.predicted_grade is not None and raw["reason_code"] is None),
                "trial_success_grade_or_reason_invalid")
        require(item.status != "error" or item.predicted_grade is None, "trial_error_cannot_have_grade")
        require(item.status == "ok" or (type(raw["reason_code"]) is str and RUN_ID.fullmatch(raw["reason_code"])),
                "trial_non_success_reason_required")
        output[item.doc_id] = {**copy.deepcopy(raw), **FLAGS, "result_origin": "supplied_unscored_diagnostic"}
    result = []
    for view in verified["views"]:
        if view["doc_id"] in output:
            result.append(output[view["doc_id"]])
            continue
        held = view["status"] == "needs_review"
        result.append({**FLAGS, **{k: view[k] for k in ("doc_id", "input_sha256", "presented_input_sha256",
            "profile", "policy_sha256", "tokenizer_sha256", "token_payload_sha256")},
            "model_sha256": expected_model_sha256, "run_id": run_id,
            "status": "needs_review" if held else "error", "predicted_grade": None,
            "reason_code": "preflight_hold" if held else "diagnostic_prediction_missing",
            "preflight_reason_codes": view["reason_codes"],
            "result_origin": "adapter_hold" if held else "adapter_missing_response"})
    counts = Counter(p["status"] for p in result)
    return {**FLAGS, "status": "bound_diagnostic_predictions_not_scored", "denominator": len(result),
        "preflight_sha256": value_digest(verified), "model_sha256": expected_model_sha256,
        "model_identity_scope": "caller_declared_expected_digest_not_artifact_verification",
        "model_artifact_verified": False, "model_inference_performed_here": False, "model_forward_executed": False,
        "grade_scoring_performed": False, "statuses": {k: counts[k] for k in ("ok", "needs_review", "error")},
        "missing_responses": sum(p["result_origin"] == "adapter_missing_response" for p in result),
        "results": result}
