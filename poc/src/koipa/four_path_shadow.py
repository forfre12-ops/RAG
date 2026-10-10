"""Shadow-only comparison of serving, factor, regulation, and policy paths.

This module deliberately does not change a classification, policy, feature flag,
or database row.  It is for measuring the four inputs that would be needed
before any future automation decision:

* the current :class:`ClassifyService` final serving result;
* the independent S/V/M factor-model proposal;
* the member-regulation evidence lookup (and its local-LLM selector, if
  already enabled by the operator); and
* a versioned policy-shadow proposal backed by a supplied fact packet.

The current service persists classifications for real UUID document ids.  A
comparison request therefore always uses an opaque, non-UUID id derived from
the input.  The original id is used only for the read-only regulation lookup.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from koipa.policy_facts import FactContext, FactContractError, text_digest
from koipa.policy_shadow import Policy, evaluate_shadow
from koipa.services.review_reasons import causal_review_reason, gate_hits


SCHEMA_VERSION = "four-path-shadow-v1"
SUMMARY_SCHEMA_VERSION = "four-path-shadow-summary-v1"


def shadow_request_doc_id(source_doc_id: str, text: str) -> str:
    """Return a deterministic id which can never be parsed as a UUID.

    ``ClassifyService`` skips DB persistence and verified-label lookup for a
    non-UUID id.  This lets an evaluation run exercise final serving gates
    without modifying a real document's classification history.
    """
    material = f"{source_doc_id}\0{text}".encode("utf-8")
    return "shadow-eval-" + hashlib.sha256(material).hexdigest()[:24]


def _to_plain(value: Any) -> Any:
    """Return a JSON-compatible object without serializing arbitrary objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): _to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(k): _to_plain(v) for k, v in vars(value).items() if not str(k).startswith("_")}
    return str(value)


def _grade(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _factors(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    raw = _to_plain(value)
    if isinstance(raw, dict):
        return {key: raw[key] for key in ("secrecy", "value", "management", "scores") if key in raw}
    return None


def _evidence_spans(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Keep locations and tags, never raw document text, in the report."""
    out: list[dict[str, Any]] = []
    for value in values or []:
        raw = _to_plain(value)
        if not isinstance(raw, dict):
            continue
        item = {key: raw[key] for key in ("start", "end", "weight", "tag") if key in raw}
        if "text" in raw and isinstance(raw["text"], str):
            item["text_sha256"] = text_digest(raw["text"])
            item["text_chars"] = len(raw["text"])
        out.append(item)
    return out


def _regulation_items(values: Iterable[Any] | None) -> list[dict[str, Any]]:
    """Return citation identities only; policy text is not duplicated in JSONL."""
    out: list[dict[str, Any]] = []
    for value in values or []:
        raw = _to_plain(value)
        if not isinstance(raw, dict):
            continue
        sentences = raw.get("sentences") or []
        hashes = raw.get("sentence_sha256")
        if not isinstance(hashes, list):
            hashes = [text_digest(str(sentence)) for sentence in sentences]
        out.append({
            "regulation_id": raw.get("rgltn_id", raw.get("regulation_id")),
            "regulation_name": raw.get("rgltn_nm", raw.get("regulation_name")),
                "version_label": raw.get("ver_lbl_nm", raw.get("version_label")),
            "clause_id": raw.get("clause_id"),
            "article_no": raw.get("article_no"),
            "title": raw.get("title"),
            "is_grade_list": bool(raw.get("is_grade_list", False)),
            "sentence_sha256": [str(value) for value in hashes],
            "sentence_count": int(raw.get("sentence_count", len(sentences))),
        })
    return out


def _canonical_regulation_path(value: Any) -> dict[str, Any]:
    """Prevent injected/alternate regulation adapters from leaking clause text."""
    raw = _to_plain(value)
    if not isinstance(raw, dict):
        return {"state": "unavailable", "reason": "invalid_regulation_result"}
    result = dict(raw)
    if "items" in result:
        result["items"] = _regulation_items(result["items"] if isinstance(result["items"], list) else [])
    return result


def _error_state(error: BaseException) -> dict[str, str]:
    """Expose a stable error class/code without leaking document content."""
    if isinstance(error, FactContractError):
        return {"state": "invalid_input", "error_code": str(error)}
    return {"state": "unavailable", "error_type": type(error).__name__}


def _serving_result(service: Any, *, source_doc_id: str, text: str, metadata: dict[str, Any] | None) -> dict[str, Any]:
    from koipa.schemas.classify import ClassifyRequest  # lazy: light CLI validation stays usable

    request_doc_id = shadow_request_doc_id(source_doc_id, text)
    try:
        response = service.classify(ClassifyRequest(
            doc_id=request_doc_id,
            content=text,
            metadata=copy.deepcopy(metadata),
            return_evidence=True,
        ))
    except Exception as exc:  # noqa: BLE001 - an error is an evaluation result, never a dropped row
        return {"state": "error", "request_doc_id": request_doc_id, "error_type": type(exc).__name__}

    warnings = [str(item) for item in (getattr(response, "warnings", None) or [])]
    status = str(getattr(response, "status", "unknown"))
    assessment = _to_plain(getattr(response, "automation_assessment", None))
    return {
        "state": "ok",
        "request_doc_id": request_doc_id,
        "label": _grade(getattr(response, "label", None)),
        "confidence": float(getattr(response, "confidence", 0.0) or 0.0),
        "scores": _to_plain(getattr(response, "scores", {}) or {}),
        "status": status,
        "model_version": str(getattr(response, "model_version", "unknown")),
        "rule_grade": _grade(getattr(response, "rule_grade", None)),
        "model_grade": _grade(getattr(response, "model_grade", None)),
        "decision_path": getattr(response, "decision_path", None),
        "factors": _factors(getattr(response, "evaluation_factors", None)),
        "factors_source": getattr(response, "factors_source", None),
        "rule_factors": _factors(getattr(response, "rule_evaluation_factors", None)),
        "evidence_spans": _evidence_spans(getattr(response, "evidence", None)),
        "warnings": warnings,
        "triggered_gates": gate_hits(warnings),
        "causal_gate": causal_review_reason(warnings, status),
        "automation_assessment": assessment if isinstance(assessment, dict) else None,
    }


def configured_factor_shadow(text: str, metadata: dict[str, Any] | None, serving_label: str | None) -> dict[str, Any]:
    """Run the independent factor model only when its operator configuration exists."""
    from koipa.config import settings  # noqa: PLC0415

    model_dir = str(getattr(settings, "factor_model_dir", "") or "")
    if not getattr(settings, "factor_shadow_enabled", False) or not model_dir:
        return {"state": "not_configured"}
    try:
        from koipa.modules.m5_inference.factor_model import (  # noqa: PLC0415
            apply_serving_gate,
            get_factor_inference,
            shadow_compare,
        )
        from koipa.modules.m5_inference.pipeline import _source_prior_is_public  # noqa: PLC0415

        inference = get_factor_inference(
            model_dir,
            base=str(getattr(settings, "factor_model_base", "kakaobank/kf-deberta-base")),
            max_len=int(getattr(settings, "factor_model_max_len", 768)),
        )
        output = inference.predict(text)
        if output is None:
            return {
                "state": "unavailable",
                "reason": "factor_model_unavailable",
                "load_error": getattr(inference, "load_error", None),
            }
        codes, probabilities = output
        source_type = metadata.get("source_type") if isinstance(metadata, dict) else None
        prediction = apply_serving_gate(
            codes,
            probabilities,
            metadata=metadata,
            tau=float(getattr(settings, "factor_tau", 0.99)),
            kappa=float(getattr(settings, "factor_kappa", 0.99)),
            source_is_public=_source_prior_is_public(source_type),
        )
        result = shadow_compare(serving_label or "unknown", prediction)
        return {"state": "ok", **_to_plain(result)}
    except Exception as exc:  # noqa: BLE001 - preserve baseline result if shadow cannot run
        return _error_state(exc)


def configured_regulation_lookup(source_doc_id: str) -> dict[str, Any]:
    """Read regulation evidence without enabling any flag or changing an index."""
    from koipa.config import settings  # noqa: PLC0415

    if not getattr(settings, "regulation_reference_enabled", False):
        return {"state": "not_configured", "reason": "regulation_reference_disabled"}
    try:
        from koipa.services.regulation_evidence_service import get_regulation_evidence_service  # noqa: PLC0415

        result = get_regulation_evidence_service().find_for_document(source_doc_id)
        return {
            "state": "ok",
            "indexed": getattr(result, "indexed", None),
            "reason": getattr(result, "reason", None),
            "llm_selection_enabled": bool(getattr(settings, "regulation_llm_select_enabled", False)),
            "items": _regulation_items(getattr(result, "items", None)),
        }
    except LookupError:
        return {"state": "unavailable", "reason": "document_not_found"}
    except Exception as exc:  # noqa: BLE001 - regulation evidence must not erase serving data
        return _error_state(exc)


def evaluate_policy_row(row: dict[str, Any], policy: Policy | None) -> dict[str, Any]:
    """Evaluate a supplied typed-fact packet as policy shadow, never as authority."""
    if policy is None:
        return {"state": "not_configured"}
    packet = row.get("policy_packet")
    context_raw = row.get("policy_context")
    if not isinstance(packet, dict) or not isinstance(context_raw, dict):
        return {"state": "missing_input", "missing": [
            name for name, value in (("policy_packet", packet), ("policy_context", context_raw))
            if not isinstance(value, dict)
        ]}
    try:
        context = FactContext.model_validate(context_raw)
        proposal = evaluate_shadow(policy, copy.deepcopy(packet), context=context)
        return {
            "state": "ok",
            "proposal": proposal,
            "would_require_review": bool(proposal.get("would_require_review", True)),
            "missing_evidence": list(proposal.get("missing_evidence", [])),
        }
    except Exception as exc:  # noqa: BLE001 - stable invalid-input record is more useful than a missing row
        return _error_state(exc)


@dataclass(frozen=True)
class ComparisonDependencies:
    """Injection points keep the contract testable without DB, GPU, or an LLM."""

    factor_shadow: Callable[[str, dict[str, Any] | None, str | None], dict[str, Any]] = configured_factor_shadow
    regulation_lookup: Callable[[str], dict[str, Any]] = configured_regulation_lookup
    policy_evaluate: Callable[[dict[str, Any], Policy | None], dict[str, Any]] = evaluate_policy_row


def compare_row(
    row: dict[str, Any],
    *,
    line_number: int,
    service: Any,
    policy: Policy | None = None,
    dependencies: ComparisonDependencies = ComparisonDependencies(),
) -> dict[str, Any]:
    """Compare one input without mutating it or allowing a new final decision."""
    original = copy.deepcopy(row)
    source_doc_id = row.get("doc_id")
    text = row.get("text", row.get("content"))
    metadata = row.get("metadata")
    if not isinstance(source_doc_id, str) or not source_doc_id.strip():
        raise ValueError(f"line {line_number}: doc_id must be a non-empty string")
    if not isinstance(text, str):
        raise ValueError(f"line {line_number}: text/content must be a string")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError(f"line {line_number}: metadata must be an object when supplied")

    serving = _serving_result(service, source_doc_id=source_doc_id, text=text, metadata=metadata)
    label = serving.get("label") if serving.get("state") == "ok" else None
    factor = dependencies.factor_shadow(text, metadata, label)
    regulation = _canonical_regulation_path(dependencies.regulation_lookup(source_doc_id))
    policy_path = dependencies.policy_evaluate(row, policy)
    policy_missing = policy_path.get("missing_evidence", []) if policy_path.get("state") == "ok" else []
    missing_evidence = sorted(set(str(item) for item in policy_missing))

    if row != original:  # defensive contract: evaluation has no authority to rewrite input facts
        raise RuntimeError("comparison_mutated_input")
    return {
        "schema_version": SCHEMA_VERSION,
        "record_number": line_number,
        "doc_id": source_doc_id,
        "document_sha256": text_digest(text),
        "input_metadata_present": metadata is not None,
        "final_serving_status": serving,
        "factor_shadow": factor,
        "regulation_evidence": regulation,
        "policy_shadow_proposal": policy_path,
        "model_candidate": label,
        "causal_gate": serving.get("causal_gate"),
        "triggered_gates": serving.get("triggered_gates", []),
        "evidence_spans": serving.get("evidence_spans", []),
        "missing_evidence": missing_evidence,
        "automation_allowed": False,
        "finalized": False,
    }


def summarize(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build a compact sidecar summary; it intentionally contains no source text."""
    rows = list(records)
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "record_count": len(rows),
        "serving_states": dict(sorted(Counter(str(r["final_serving_status"].get("state")) for r in rows).items())),
        "serving_statuses": dict(sorted(Counter(str(r["final_serving_status"].get("status")) for r in rows).items())),
        "factor_states": dict(sorted(Counter(str(r["factor_shadow"].get("state")) for r in rows).items())),
        "regulation_states": dict(sorted(Counter(str(r["regulation_evidence"].get("state")) for r in rows).items())),
        "policy_states": dict(sorted(Counter(str(r["policy_shadow_proposal"].get("state")) for r in rows).items())),
        "causal_gates": dict(sorted(Counter(str(r.get("causal_gate")) for r in rows if r.get("causal_gate") is not None).items())),
        "automation_allowed_count": 0,
        "finalized_count": 0,
    }


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"line {line_number}: invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"line {line_number}: each JSONL row must be an object")
        rows.append(value)
    return rows


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    """Write atomically and refuse to overwrite a previous evidence report."""
    if path.exists():
        raise FileExistsError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        with tmp.open("x", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        tmp.replace(path)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
