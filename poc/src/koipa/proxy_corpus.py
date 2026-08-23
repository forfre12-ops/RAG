"""Contract checks for a high-fidelity proxy corpus.

The proxy program deliberately has two non-interchangeable populations:

* ``synthetic`` matched counterfactuals form the balanced four-grade primary
  regression/calibration set.
* ``public_real`` documents teach real document shape and form a separate S3
  false-positive/overclassification challenge.

They may be used together for training, but they must not be merged into one
balanced evaluation metric: origin would identify S3. Their provenance must
remain visible and a document family must stay on one side of a train/holdout
split. This module contains no I/O so collection and generation tools can use
the same checks.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Iterable, Literal, Mapping, Sequence

from koipa.hygiene import text_hash
from koipa.modules.m3_labeling.rule_engine import grade_from_svm


GRADE_CODES = frozenset({"TS", "S1", "S2", "S3"})
DIRECT_AUTHORED_TRAINING_BUCKET = "direct_authored_training_candidate"
DIRECT_AUTHORED_EVALUATION_BUCKET = "direct_authored_evaluation_candidate"
DIRECT_AUTHORED_TRAINING_GATE_VERSION = "direct_authored_quality_v1"
IntendedUse = Literal["training", "evaluation"]
INTENDED_USES = frozenset({"training", "evaluation"})
CATALOG_SPLIT_INTENDED_USES: dict[str, IntendedUse] = {
    "train_pool_only": "training",
    "frozen_proxy_eval_only": "evaluation",
}
CATALOG_SPLIT_PERMISSIONS = {
    "train_pool_only": {
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
    },
    "frozen_proxy_eval_only": {
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
    },
}
PUBLIC_REAL = "public_real"
SYNTHETIC = "synthetic"
PUBLIC_DOCUMENT = "public_document"
CONFIDENTIAL_SIMULATION = "confidential_simulation"
AIHUB_71813_SOURCE_ID = "aihub-71813-multimodal-information-retrieval"
AIHUB_71813_PERMISSION_VALIDATOR = "aihub-71813-receipt-contract-v1"
AIHUB_71813_REQUIRED_RESTRICTIONS = frozenset(
    {
        "model_training_only",
        "no_redistribution",
        "no_third_party_access",
        "no_foreign_transfer_without_separate_agreement",
        "no_reidentification",
        "report_personal_data_and_delete_dataset",
        "no_dataset_sale_without_separate_agreement",
        "nia_attribution_required",
    }
)
_SHA256_HEX = re.compile(r"[0-9a-f]{64}\Z")

# A high-grade proxy must be long enough to include document context rather than
# only a grade-revealing sentence.  Document-specific pipelines may raise these
# floors, but should not silently lower them.
MIN_TEXT_CHARS = {"TS": 1200, "S1": 1200, "S2": 700, "S3": 300}
REQUIRED_HIGH_GRADE_EVIDENCE = frozenset(
    {"nonpublicity", "competitive_value", "access_controls"}
)
DEFAULT_TARGET_COUNTS = {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
LEGACY_MIXED_TARGET_ORIGINS = {
    "TS": SYNTHETIC,
    "S1": SYNTHETIC,
    "S2": SYNTHETIC,
    "S3": PUBLIC_REAL,
}
MATCHED_SYNTHETIC_TARGET_ORIGINS = {grade: SYNTHETIC for grade in DEFAULT_TARGET_COUNTS}
# Keep the library-level default compatible for callers that already assemble
# the historical mixed corpus. The primary frozen CLI selects the matched
# profile explicitly; callers can do the same through ``expected_origins``.
DEFAULT_TARGET_ORIGINS = LEGACY_MIXED_TARGET_ORIGINS
ORIGIN_EXPECTATION_PROFILES = {
    "legacy-mixed-v1": LEGACY_MIXED_TARGET_ORIGINS,
    "matched-synthetic-v1": MATCHED_SYNTHETIC_TARGET_ORIGINS,
    "public-s3-hybrid-v2": LEGACY_MIXED_TARGET_ORIGINS,
}
DEFAULT_MIN_FAMILIES = {"TS": 40, "S1": 50, "S2": 50, "S3": 75}
DEFAULT_MAX_FAMILY_SHARE = 0.05
QUALITY_POLICY_VERSION = "proxy-quality-v1"
_DIRECT_GRADE_MARKER = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:TS|S[1-3])(?:\s*(?:등급|급))?(?![A-Za-z0-9])"
    r"|특급\s*(?:기밀|비밀)|[1-3]\s*급\s*(?:비밀|기밀|대외비)"
    r"|(?:보안|비밀|기밀)\s*등급\s*[:：]?\s*(?:TS|S[1-3]|특급|[1-3]급)"
    r"|대외비|극비"
)
_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
_NUMBER_RE = re.compile(
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)?(?:\s*(?:%|원|일|주|개월|시간|분|초|건|명|회|개|배|℃|°C|ms|MPa))?"
)
_BLOCKED_MODEL_PARTS = ("noop", "unknown", "fake", "mock", "test")
_DOCUMENT_QUALITY_CHECKS = (
    "structure_appropriate",
    "timeline_consistent",
    "quantitative_consistent",
    "non_repetitive",
)


@dataclass(frozen=True)
class ProxyQualityPolicy:
    """Measurable document-quality floor derived from real public documents."""

    min_hangul_letter_ratio: float
    max_alnum_char_share: float
    min_unique_char4_ratio: float
    min_unique_token_ratio: float
    min_blocks: int = 0
    min_long_blocks: int = 0
    min_numeric_facts: int = 0
    max_duplicate_long_block_ratio: float = 1.0


PUBLIC_QUALITY_POLICY = ProxyQualityPolicy(0.45, 0.12, 0.18, 0.12)
SYNTHETIC_QUALITY_POLICY = ProxyQualityPolicy(
    0.55,
    0.10,
    0.45,
    0.20,
    min_blocks=5,
    min_long_blocks=3,
    min_numeric_facts=3,
    max_duplicate_long_block_ratio=0.20,
)


@dataclass(frozen=True)
class ProxyRecordCheck:
    """Validation result for one candidate; errors make it ineligible."""

    doc_id: str
    errors: tuple[str, ...]
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {
            "doc_id": self.doc_id,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class ProxyAssembly:
    """Result of selecting an exact, family-diverse proxy-gold candidate set."""

    ready: bool
    selected: tuple[dict, ...]
    stats: dict

    def to_dict(self) -> dict:
        return {"ready": self.ready, "stats": self.stats}


def record_text(record: Mapping[str, object]) -> str:
    """Read canonical text, with title/body support for collection-stage rows."""
    text = str(record.get("text") or "").strip()
    if text:
        return text
    title = str(record.get("title") or "").strip()
    body = str(record.get("body") or "").strip()
    return "\n\n".join(part for part in (title, body) if part)


def _required_text(record: Mapping[str, object], key: str, errors: list[str]) -> str:
    value = str(record.get(key) or "").strip()
    if not value:
        errors.append(f"missing:{key}")
    return value


def _require_intended_use(intended_use: str) -> None:
    if intended_use not in INTENDED_USES:
        raise ValueError("intended_use must be 'training' or 'evaluation'")


def proxy_record_intended_use(record: Mapping[str, object]) -> IntendedUse:
    """Resolve a generated proxy row's immutable catalog use boundary.

    Generation and judging process both train-only and frozen-evaluation
    catalogs.  Callers must not rely on ``validate_proxy_record``'s default and
    accidentally reinterpret one population as the other.
    """
    split_role = str(record.get("catalog_split_role") or "").strip()
    intended_use = CATALOG_SPLIT_INTENDED_USES.get(split_role)
    if intended_use is None:
        allowed = ", ".join(sorted(CATALOG_SPLIT_INTENDED_USES))
        raise ValueError(
            f"catalog_split_role must be one of [{allowed}]; found {split_role!r}"
        )
    expected = CATALOG_SPLIT_PERMISSIONS[split_role]
    mismatched = [
        field for field, value in expected.items() if record.get(field) is not value
    ]
    if mismatched:
        raise ValueError(
            f"catalog permission mismatch for {split_role}: {','.join(mismatched)}"
        )
    return intended_use


def _validate_aihub_71813_contract(
    record: Mapping[str, object], errors: list[str]
) -> None:
    """Require the output contract of the offline, receipt-gated intake path."""
    required_values = {
        "source_license": "EXPLICIT-ML-TRAINING",
        "source_use_scope": "model_training_only",
        "permission_validator": AIHUB_71813_PERMISSION_VALIDATOR,
        "permission_contract_status": "validated",
        "aihub_dataset_id": "71813",
    }
    for field, expected in required_values.items():
        if record.get(field) != expected:
            errors.append(f"aihub_71813:invalid:{field}")
    for field in (
        "evaluation_use_permitted",
        "golden_set_use_permitted",
        "redistribution_permitted",
        "third_party_access_permitted",
        "foreign_transfer_permitted",
        "dataset_sale_permitted",
    ):
        if record.get(field) is not False:
            errors.append(f"aihub_71813:requires_false:{field}")
    if record.get("attribution_required") is not True:
        errors.append("aihub_71813:attribution_not_required")
    attribution = str(record.get("attribution_text") or "")
    if "한국지능정보사회진흥원" not in attribution or "NIA" not in attribution:
        errors.append("aihub_71813:invalid_nia_attribution")
    restrictions = record.get("usage_restrictions")
    if not isinstance(restrictions, (list, tuple, set, frozenset)):
        errors.append("aihub_71813:invalid_usage_restrictions")
    else:
        missing = sorted(AIHUB_71813_REQUIRED_RESTRICTIONS - set(restrictions))
        if missing:
            errors.append("aihub_71813:missing_usage_restrictions:" + ",".join(missing))
    for field in (
        "approval_receipt_sha256",
        "approval_evidence_sha256",
        "approval_contract_sha256",
    ):
        if not _SHA256_HEX.fullmatch(str(record.get(field) or "")):
            errors.append(f"aihub_71813:invalid_sha256:{field}")


def measure_text_quality(text: str) -> dict[str, float | int]:
    """Measure language, repetition, structure, and factual-density signals."""
    letters = [char for char in text if char.isalpha()]
    hangul = sum("가" <= char <= "힣" for char in letters)
    alnum = [char.casefold() for char in text if char.isalnum()]
    alnum_counts = Counter(alnum)
    compact = re.sub(r"\s+", "", text)
    char4 = [compact[index : index + 4] for index in range(max(0, len(compact) - 3))]
    tokens = [match.group(0).casefold() for match in _TOKEN_RE.finditer(text)]
    blocks = [part.strip() for part in re.split(r"\n\s*\n|\r?\n", text) if part.strip()]
    long_blocks = [re.sub(r"\s+", " ", part) for part in blocks if len(part) >= 40]
    duplicate_long_blocks = len(long_blocks) - len(set(long_blocks))
    numeric_facts = {
        match.group(0).replace(" ", "") for match in _NUMBER_RE.finditer(text)
    }
    return {
        "chars": len(text),
        "hangul_letter_ratio": hangul / len(letters) if letters else 0.0,
        "max_alnum_char_share": max(alnum_counts.values(), default=0) / len(alnum)
        if alnum
        else 1.0,
        "unique_char4_ratio": len(set(char4)) / len(char4) if char4 else 0.0,
        "unique_token_ratio": len(set(tokens)) / len(tokens) if tokens else 0.0,
        "blocks": len(blocks),
        "long_blocks": len(long_blocks),
        "numeric_facts": len(numeric_facts),
        "duplicate_long_block_ratio": (
            duplicate_long_blocks / len(long_blocks) if long_blocks else 0.0
        ),
        "direct_grade_markers": len(_DIRECT_GRADE_MARKER.findall(text)),
    }


def _quality_errors(text: str, *, origin: str) -> list[str]:
    policy = SYNTHETIC_QUALITY_POLICY if origin == SYNTHETIC else PUBLIC_QUALITY_POLICY
    metrics = measure_text_quality(text)
    errors: list[str] = []
    minimums = {
        "hangul_letter_ratio": policy.min_hangul_letter_ratio,
        "unique_char4_ratio": policy.min_unique_char4_ratio,
        "unique_token_ratio": policy.min_unique_token_ratio,
        "blocks": policy.min_blocks,
        "long_blocks": policy.min_long_blocks,
        "numeric_facts": policy.min_numeric_facts,
    }
    for name, minimum in minimums.items():
        if float(metrics[name]) < minimum:
            errors.append(f"quality:{name}:{metrics[name]}<{minimum}")
    maximums = {
        "max_alnum_char_share": policy.max_alnum_char_share,
        "duplicate_long_block_ratio": policy.max_duplicate_long_block_ratio,
    }
    for name, maximum in maximums.items():
        if float(metrics[name]) > maximum:
            errors.append(f"quality:{name}:{metrics[name]}>{maximum}")
    if origin == SYNTHETIC and int(metrics["direct_grade_markers"]) > 0:
        errors.append(f"quality:direct_grade_marker:{metrics['direct_grade_markers']}")
    return errors


def _model_identity(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _generator_identity(record: Mapping[str, object]) -> tuple[str, str] | None:
    lineage = record.get("generation_lineage")
    if isinstance(lineage, Mapping):
        provider = lineage.get("provider") or lineage.get("generator_provider")
        model = lineage.get("model") or lineage.get("generator_model")
        if provider and model:
            return str(provider), str(model)
    if isinstance(lineage, (list, tuple)):
        for entry in lineage:
            if isinstance(entry, str) and entry.startswith("generator:"):
                parts = entry.split(":", 2)
                if len(parts) == 3:
                    return parts[1], parts[2]
    return None


def _validate_generation_identity(
    record: Mapping[str, object], errors: list[str]
) -> None:
    identity = _generator_identity(record)
    if identity is None:
        errors.append("missing:generation_identity")
        return
    provider, model = identity
    if any(part in _model_identity(provider) for part in _BLOCKED_MODEL_PARTS):
        errors.append(f"blocked:generation_provider:{provider}")
    if any(part in _model_identity(model) for part in _BLOCKED_MODEL_PARTS):
        errors.append(f"blocked:generation_model:{model}")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _validate_evidence_card(
    record: Mapping[str, object], text: str, grade: str, errors: list[str]
) -> None:
    evidence = record.get("evidence_card")
    if (
        not isinstance(evidence, Mapping)
        or evidence.get("schema") != "proxy-evidence-v1"
    ):
        errors.append("invalid:evidence_card_schema")
        return
    if evidence.get("text_sha256") != _sha256(text):
        errors.append("invalid:evidence_text_sha256")
    factors = evidence.get("factors")
    if not isinstance(factors, Mapping):
        errors.append("missing:evidence_factors")
        return
    valid_spans: set[tuple[int, int]] = set()
    text_backed_factors: set[str] = set()
    for factor_name in REQUIRED_HIGH_GRADE_EVIDENCE:
        factor = factors.get(factor_name)
        if not isinstance(factor, Mapping):
            errors.append(f"missing:evidence_factor:{factor_name}")
            continue
        basis = str(factor.get("basis") or "")
        spans = factor.get("spans")
        if basis == "context":
            if not factor.get("context_ref") or not factor.get("catalog_sha256"):
                errors.append(f"invalid:evidence_context:{factor_name}")
            continue
        if basis != "text" or not isinstance(spans, list) or not spans:
            errors.append(f"missing:evidence_spans:{factor_name}")
            continue
        text_backed_factors.add(factor_name)
        for span in spans:
            if not isinstance(span, Mapping):
                errors.append(f"invalid:evidence_span:{factor_name}")
                continue
            try:
                start, end = int(span["start"]), int(span["end"])
            except (KeyError, TypeError, ValueError):
                errors.append(f"invalid:evidence_offset:{factor_name}")
                continue
            quote = str(span.get("quote") or "")
            if not (0 <= start < end <= len(text)) or text[start:end] != quote:
                errors.append(f"invalid:evidence_exact_match:{factor_name}")
                continue
            if not 12 <= len(quote) <= 240:
                errors.append(f"invalid:evidence_span_length:{factor_name}")
            if span.get("quote_sha256") != _sha256(quote):
                errors.append(f"invalid:evidence_quote_sha256:{factor_name}")
            if _DIRECT_GRADE_MARKER.search(quote):
                errors.append(f"invalid:evidence_grade_marker:{factor_name}")
            valid_spans.add((start, end))
    required_spans = 2 if grade in {"TS", "S1"} else 1
    if len(valid_spans) < required_spans:
        errors.append(
            f"insufficient:evidence_spans:{len(valid_spans)}<{required_spans}"
        )
    total_span_chars = sum(end - start for start, end in valid_spans)
    if grade in {"TS", "S1"} and total_span_chars < 40:
        errors.append(f"insufficient:evidence_span_chars:{total_span_chars}<40")
    if "competitive_value" not in text_backed_factors:
        errors.append("missing:text_backed_competitive_value")


def _validate_document_quality_audit(
    record: Mapping[str, object],
    evidence: Mapping[str, object],
    errors: list[str],
) -> None:
    """Recompute the proxy-only quality gate from per-sample raw audits."""
    try:
        sample_count = int(evidence.get("primary_sample_count") or 0)
    except (TypeError, ValueError):
        sample_count = 0
    if sample_count < 1 or evidence.get("primary_quality_required") is not True:
        errors.append("adjudication:document_quality_not_required")

    raw_samples = evidence.get("primary_quality_samples")
    samples = raw_samples if isinstance(raw_samples, list) else []
    if len(samples) != sample_count:
        errors.append("adjudication:invalid_document_quality_samples")

    text = record_text(record)
    derived_votes: dict[str, Counter[bool]] = {
        check: Counter() for check in _DOCUMENT_QUALITY_CHECKS
    }
    derived_coverage = {check: 0 for check in _DOCUMENT_QUALITY_CHECKS}
    issue_invalid = False
    for position, sample in enumerate(samples, start=1):
        if not isinstance(sample, Mapping):
            errors.append("adjudication:invalid_document_quality_samples")
            continue
        try:
            sample_index = int(sample.get("sample_index"))
        except (TypeError, ValueError):
            sample_index = -1
        if isinstance(sample.get("sample_index"), bool) or sample_index != position:
            errors.append("adjudication:invalid_document_quality_samples")
        checks = sample.get("checks")
        issues = sample.get("issues")
        if not isinstance(checks, Mapping) or not isinstance(issues, list):
            errors.append("adjudication:invalid_document_quality_samples")
            checks = checks if isinstance(checks, Mapping) else {}
            issues = issues if isinstance(issues, list) else []

        valid_issue_checks: Counter[str] = Counter()
        for issue in issues:
            if not isinstance(issue, Mapping):
                issue_invalid = True
                continue
            check = issue.get("check")
            reason = issue.get("reason")
            spans = issue.get("spans")
            spans_valid = isinstance(spans, list) and bool(spans)
            if spans_valid:
                for span in spans:
                    if not isinstance(span, Mapping):
                        spans_valid = False
                        break
                    try:
                        start = int(span["start"])
                        end = int(span["end"])
                    except (KeyError, TypeError, ValueError):
                        spans_valid = False
                        break
                    quote = span.get("quote")
                    if (
                        isinstance(span.get("start"), bool)
                        or isinstance(span.get("end"), bool)
                        or not isinstance(quote, str)
                        or not quote
                        or not (0 <= start < end <= len(text))
                        or text[start:end] != quote
                        or span.get("quote_sha256") != _sha256(quote)
                    ):
                        spans_valid = False
                        break
            valid = (
                check in _DOCUMENT_QUALITY_CHECKS
                and isinstance(reason, str)
                and bool(reason.strip())
                and spans_valid
            )
            if valid:
                valid_issue_checks[str(check)] += 1
            else:
                issue_invalid = True

        for check in _DOCUMENT_QUALITY_CHECKS:
            value = checks.get(check)
            if type(value) is bool:
                derived_votes[check][value] += 1
                derived_coverage[check] += 1
            else:
                errors.append(f"adjudication:incomplete_document_quality:{check}")
            if value is False and not valid_issue_checks[check]:
                issue_invalid = True
            if value is True and valid_issue_checks[check]:
                issue_invalid = True
    if issue_invalid:
        errors.append("adjudication:invalid_document_quality_issue")

    persisted_votes = evidence.get("primary_quality_votes")
    persisted_coverage = evidence.get("primary_quality_coverage")
    vote_audit_valid = isinstance(persisted_votes, Mapping) and isinstance(
        persisted_coverage, Mapping
    )
    if vote_audit_valid:
        try:
            for check in _DOCUMENT_QUALITY_CHECKS:
                raw_distribution = persisted_votes[check]
                if not isinstance(raw_distribution, Mapping):
                    raise ValueError
                distribution: dict[bool, int] = {}
                for state, count in raw_distribution.items():
                    if state not in {"true", "false"} or isinstance(count, bool):
                        raise ValueError
                    numeric_count = int(count)
                    if numeric_count < 1 or float(count) != numeric_count:
                        raise ValueError
                    distribution[state == "true"] = numeric_count
                if distribution != dict(derived_votes[check]):
                    raise ValueError
                coverage = persisted_coverage[check]
                if (
                    isinstance(coverage, bool)
                    or int(coverage) != derived_coverage[check]
                ):
                    raise ValueError
        except (KeyError, TypeError, ValueError):
            vote_audit_valid = False
    if not vote_audit_valid:
        errors.append("adjudication:invalid_document_quality_vote_audit")

    passed_map = evidence.get("quality_check_passed")
    all_clean = True
    for check in _DOCUMENT_QUALITY_CHECKS:
        clean = (
            sample_count > 0
            and derived_coverage[check] == sample_count
            and dict(derived_votes[check]) == {True: sample_count}
        )
        all_clean = all_clean and clean
        if not clean:
            errors.append(f"adjudication:document_quality_failed:{check}")
        if not isinstance(passed_map, Mapping) or passed_map.get(check) is not clean:
            errors.append("adjudication:document_quality_summary_mismatch")
    quality_failures = evidence.get("document_quality_gate_failures")
    if (
        not all_clean
        or evidence.get("document_quality_gate_passed") is not True
        or quality_failures not in ([], ())
    ):
        errors.append("adjudication:document_quality_gate_not_passed")


def _validate_adjudication(record: Mapping[str, object], errors: list[str]) -> None:
    direct_authored = (
        record.get("decision_bucket")
        in {DIRECT_AUTHORED_TRAINING_BUCKET, DIRECT_AUTHORED_EVALUATION_BUCKET}
        and record.get("gate_version") == DIRECT_AUTHORED_TRAINING_GATE_VERSION
    )
    if record.get("decision_bucket") != "gold_candidate" and not direct_authored:
        errors.append("adjudication:not_gold_candidate")
        return
    evidence = record.get("consensus_evidence")
    if not isinstance(evidence, Mapping):
        errors.append("missing:consensus_evidence")
        return
    semantic_gate = record.get("gate_version") in {
        "proxy_semantic_v1",
        "proxy_semantic_quality_v2",
        DIRECT_AUTHORED_TRAINING_GATE_VERSION,
    } or evidence.get("schema") in {
        "proxy-semantic-adjudication-v1",
        "proxy-semantic-quality-adjudication-v2",
        "direct-authored-quality-audit-v1",
    }
    if semantic_gate:
        failures = evidence.get("semantic_gate_failures")
        valid_gate_statuses = {"gold_candidate"}
        if direct_authored:
            valid_gate_statuses.add(str(record.get("decision_bucket") or ""))
        if (
            evidence.get("semantic_gate_passed") is not True
            or failures not in ([], ())
            or evidence.get("gate_status") not in valid_gate_statuses
        ):
            errors.append("adjudication:semantic_gate_not_passed")
        if evidence.get("rule_advisory_only") is not True:
            errors.append("adjudication:rule_not_marked_advisory")
        # ``agreement`` remains the literal rule/judge comparison. A semantic
        # pass may therefore truthfully carry agreement=False; it must never be
        # rewritten merely to satisfy the old lexical gate contract.
        rule_agreement = evidence.get("rule_judge_agreement")
        expected_rule_agreement = rule_agreement is True
        if evidence.get("agreement") is not expected_rule_agreement:
            errors.append("adjudication:rule_agreement_audit_mismatch")
        final_label = str(record.get("label") or "")
        intended_label = str(record.get("intended_label") or final_label)
        if (
            evidence.get("intended_primary_agreement") is not True
            or evidence.get("semantic_agreement") is not True
            or evidence.get("primary_grade") != intended_label
        ):
            errors.append("adjudication:intended_primary_mismatch")
        complete = evidence.get("factor_vote_complete")
        matches = evidence.get("factor_vote_expected_match")
        if not isinstance(complete, Mapping) or not all(
            complete.get(factor) is True
            for factor in ("secrecy", "value", "management")
        ):
            errors.append("adjudication:incomplete_primary_factor_votes")
        if not isinstance(matches, Mapping) or not all(
            matches.get(factor) is True for factor in ("secrecy", "value", "management")
        ):
            errors.append("adjudication:primary_factor_vote_disagreement")
        try:
            vote_count = int(evidence.get("primary_vote_count") or 0)
            valid_vote_count = int(evidence.get("primary_valid_vote_count") or 0)
            sample_count = int(evidence.get("primary_sample_count") or 0)
            parse_fail_count = int(evidence.get("primary_parse_fail_count") or 0)
            self_consistency = float(evidence.get("primary_self_consistency"))
            minimum = float(evidence.get("min_self_consistency"))
        except (TypeError, ValueError):
            errors.append("adjudication:invalid_primary_vote_audit")
        else:
            if (
                sample_count < 1
                or vote_count != sample_count
                or valid_vote_count != sample_count
                or parse_fail_count != 0
                or evidence.get("primary_self_consistency_valid") is not True
                or not math.isfinite(self_consistency)
                or not math.isfinite(minimum)
                or not 0.0 <= self_consistency <= 1.0
                or not 0.0 <= minimum <= 1.0
                or self_consistency < minimum
            ):
                errors.append("adjudication:invalid_primary_vote_audit")
            factor_votes = evidence.get("primary_factor_votes")
            coverage = evidence.get("primary_factor_coverage")
            expected = record.get("expected_factor_scores")
            if not all(
                isinstance(value, Mapping)
                for value in (factor_votes, coverage, expected)
            ):
                errors.append("adjudication:invalid_primary_factor_vote_audit")
            else:
                try:
                    for factor in ("secrecy", "value", "management"):
                        distribution = {
                            int(level): int(count)
                            for level, count in factor_votes[factor].items()
                        }
                        expected_level = int(expected[factor])
                        if int(coverage[factor]) != sample_count or distribution != {
                            expected_level: sample_count
                        }:
                            raise ValueError
                except (AttributeError, KeyError, TypeError, ValueError):
                    errors.append("adjudication:invalid_primary_factor_vote_audit")
        _validate_document_quality_audit(record, evidence, errors)
        if (
            evidence.get("primary_factor_derived_grade") != intended_label
            or evidence.get("expected_factor_derived_grade") != intended_label
        ):
            errors.append("adjudication:factor_derived_grade_mismatch")
    elif (
        evidence.get("agreement") is not True
        or evidence.get("gate_status") != "gold_candidate"
    ):
        errors.append("adjudication:consensus_not_passed")
    if int(evidence.get("primary_valid_vote_count") or 0) < 1:
        errors.append("adjudication:no_valid_primary_vote")
    final_label = str(record.get("label") or "")
    intended_label = str(record.get("intended_label") or final_label)
    if final_label != intended_label:
        errors.append(
            f"adjudication:intended_label_mismatch:{final_label}!={intended_label}"
        )
    primary_scores = evidence.get("primary_factor_scores")
    expected_scores = record.get("expected_factor_scores")
    if not isinstance(primary_scores, Mapping):
        errors.append("adjudication:missing_primary_factor_scores")
    else:
        try:
            actual = {
                key: max(0, min(2, round(float(primary_scores[key]))))
                for key in ("secrecy", "value", "management")
            }
        except (KeyError, TypeError, ValueError):
            errors.append("adjudication:invalid_primary_factor_scores")
        else:
            if (
                grade_from_svm(actual["secrecy"], actual["value"], actual["management"])
                != final_label
            ):
                errors.append("adjudication:primary_factors_disagree_with_label")
            if isinstance(expected_scores, Mapping):
                expected = {
                    key: int(expected_scores.get(key, -1))
                    for key in ("secrecy", "value", "management")
                }
                if actual != expected:
                    errors.append("adjudication:primary_factors_disagree_with_expected")
    generator = _generator_identity(record)
    primary_model = str(record.get("primary_judge_model") or "")
    judging_lineage = record.get("judging_lineage")
    if not primary_model and isinstance(judging_lineage, (list, tuple)):
        for entry in judging_lineage:
            if isinstance(entry, str) and entry.startswith("primary_judge:"):
                primary_model = entry.split(":", 2)[-1]
                break
    if not primary_model or any(
        part in _model_identity(primary_model) for part in _BLOCKED_MODEL_PARTS
    ):
        errors.append("adjudication:invalid_primary_judge")
    elif generator and _model_identity(generator[1]) == _model_identity(primary_model):
        errors.append("adjudication:generator_equals_judge")


def validate_proxy_record(
    record: Mapping[str, object],
    *,
    stage: str = "candidate",
    intended_use: IntendedUse = "training",
) -> ProxyRecordCheck:
    """Validate provenance and quality minimums for one proxy candidate.

    The function intentionally does not infer provenance from a convenient source
    label.  New proxy records must declare their origin, licence/reference or
    generation lineage, and source/template family explicitly.
    """
    if stage not in {"candidate", "eligible"}:
        raise ValueError("stage must be 'candidate' or 'eligible'")
    _require_intended_use(intended_use)
    errors: list[str] = []
    warnings: list[str] = []
    doc_id = _required_text(record, "doc_id", errors)
    text = record_text(record)
    if not text:
        errors.append("missing:text")

    grade = _required_text(record, "label", errors)
    if grade and grade not in GRADE_CODES:
        errors.append(f"invalid:label:{grade}")
    origin = _required_text(record, "document_origin", errors)
    if origin and origin not in {PUBLIC_REAL, SYNTHETIC}:
        errors.append(f"invalid:document_origin:{origin}")
    role = _required_text(record, "proxy_role", errors)
    _required_text(record, "document_family_id", errors)
    _required_text(record, "document_type", errors)

    if grade in MIN_TEXT_CHARS and len(text) < MIN_TEXT_CHARS[grade]:
        errors.append(f"too_short:{grade}:{len(text)}<{MIN_TEXT_CHARS[grade]}")
    if text and origin in {PUBLIC_REAL, SYNTHETIC}:
        errors.extend(_quality_errors(text, origin=origin))

    if origin == PUBLIC_REAL:
        if role != PUBLIC_DOCUMENT:
            errors.append(f"invalid:proxy_role_for_public:{role}")
        _required_text(record, "source_reference", errors)
        _required_text(record, "source_license", errors)
        if intended_use == "training":
            if record.get("training_use_permitted") is not True:
                errors.append("training_use_not_permitted")
        else:
            if record.get("evaluation_use_permitted") is not True:
                errors.append("evaluation_use_not_permitted")
            elif record.get("training_use_permitted") is not True:
                warnings.append("evaluation_only:not_training_permitted")
        source_id = _required_text(record, "source_id", errors)
        _required_text(record, "source_sha256", errors)
        _required_text(record, "retrieved_at", errors)
        _required_text(record, "license_evidence_sha256", errors)
        if source_id == AIHUB_71813_SOURCE_ID:
            _validate_aihub_71813_contract(record, errors)
        # The public-real layer is an S3/publicity floor.  It must never create
        # artificial TS/S1 ground truth merely because a public document is rich.
        if grade and grade != "S3":
            errors.append(f"public_real_requires_S3:{grade}")

    if origin == SYNTHETIC:
        if role != CONFIDENTIAL_SIMULATION:
            errors.append(f"invalid:proxy_role_for_synthetic:{role}")
        permission_field = f"{intended_use}_use_permitted"
        if record.get(permission_field) is not True:
            errors.append(f"{intended_use}_use_not_permitted")
        _required_text(record, "scenario_id", errors)
        lineage = record.get("generation_lineage")
        if not isinstance(lineage, (list, tuple, dict)) or not lineage:
            errors.append("missing:generation_lineage")
        _validate_generation_identity(record, errors)
        expected_scores = record.get("expected_factor_scores")
        if isinstance(expected_scores, Mapping):
            try:
                expected_grade = grade_from_svm(
                    int(expected_scores["secrecy"]),
                    int(expected_scores["value"]),
                    int(expected_scores["management"]),
                )
            except (KeyError, TypeError, ValueError):
                errors.append("invalid:expected_factor_scores")
            else:
                intended = str(record.get("intended_label") or grade)
                if expected_grade != intended:
                    errors.append(
                        f"mismatch:expected_factor_scores:{expected_grade}!={intended}"
                    )
        else:
            errors.append("missing:expected_factor_scores")
        if grade in {"TS", "S1"}:
            evidence = record.get("evidence_card")
            if not isinstance(evidence, Mapping):
                errors.append("missing:evidence_card")
            elif evidence.get("schema") != "proxy-evidence-v1":
                missing = sorted(
                    key
                    for key in REQUIRED_HIGH_GRADE_EVIDENCE
                    if not str(evidence.get(key) or "").strip()
                )
                if missing:
                    errors.append(f"missing:evidence_card:{','.join(missing)}")

        if stage == "eligible":
            # A deterministic scaffold is useful for workflow rehearsal, but
            # it must never silently enter a frozen evaluation or training
            # corpus.  Its generator sets this flag explicitly; only a named
            # human approval can clear the boundary.
            if record.get("requires_manual_audit") is True:
                audit = record.get("manual_audit")
                if (
                    not isinstance(audit, Mapping)
                    or audit.get("approved") is not True
                    or not str(audit.get("auditor_id") or "").strip()
                    or not str(audit.get("signed_at") or "").strip()
                ):
                    errors.append("manual_audit:required_before_eligible")
            _validate_adjudication(record, errors)
            _validate_evidence_card(record, text, grade, errors)

    if origin == SYNTHETIC and grade == "S3":
        warnings.append("synthetic_S3:not_public_real_accuracy_evidence")
    return ProxyRecordCheck(
        doc_id=doc_id, errors=tuple(errors), warnings=tuple(warnings)
    )


def validate_proxy_corpus(
    records: Iterable[Mapping[str, object]],
    *,
    stage: str = "eligible",
    intended_use: IntendedUse = "training",
) -> dict:
    """Validate a corpus and return an audit-friendly aggregate report."""
    _require_intended_use(intended_use)
    rows = list(records)
    checks = [
        validate_proxy_record(record, stage=stage, intended_use=intended_use)
        for record in rows
    ]
    error_counts: Counter[str] = Counter()
    warning_counts: Counter[str] = Counter()
    for check in checks:
        error_counts.update(check.errors)
        warning_counts.update(check.warnings)
    valid_rows = [row for row, check in zip(rows, checks, strict=True) if check.ok]
    shortcut_report: dict = {"gate": {"status": "not_evaluated", "passed": False}}
    if stage == "eligible" and len(valid_rows) >= 100:
        from koipa.proxy_shortcuts import strict_shortcut_gate  # noqa: PLC0415

        shortcut_report = strict_shortcut_gate(valid_rows, frozen_gold=True)
    return {
        "total": len(checks),
        "valid": sum(check.ok for check in checks),
        "invalid": sum(not check.ok for check in checks),
        "error_counts": dict(sorted(error_counts.items())),
        "warning_counts": dict(sorted(warning_counts.items())),
        "quality_policy_version": QUALITY_POLICY_VERSION,
        "validation_stage": stage,
        "intended_use": intended_use,
        "shortcut_report": shortcut_report,
        "checks": [check.to_dict() for check in checks],
    }


def _select_family_diverse(records: Sequence[dict], count: int) -> list[dict]:
    """Round-robin families so one template cannot dominate the selected set."""
    by_family: dict[str, list[dict]] = {}
    for record in records:
        family = str(record["document_family_id"])
        by_family.setdefault(family, []).append(record)
    for rows in by_family.values():
        rows.sort(key=lambda row: str(row.get("doc_id") or ""))

    selected: list[dict] = []
    offsets = {family: 0 for family in by_family}
    families = sorted(by_family)
    while len(selected) < count:
        progressed = False
        for family in families:
            offset = offsets[family]
            rows = by_family[family]
            if offset >= len(rows):
                continue
            selected.append(rows[offset])
            offsets[family] = offset + 1
            progressed = True
            if len(selected) == count:
                break
        if not progressed:
            break
    return selected


def assemble_proxy_gold(
    records: Iterable[Mapping[str, object]],
    *,
    targets: Mapping[str, int] = DEFAULT_TARGET_COUNTS,
    blocked_doc_ids: Iterable[str] = (),
    blocked_family_ids: Iterable[str] = (),
    blocked_text_hashes: Iterable[str] = (),
    min_families: Mapping[str, int] = DEFAULT_MIN_FAMILIES,
    max_family_share: float = DEFAULT_MAX_FAMILY_SHARE,
    require_shortcut_gate: bool | None = None,
    require_catalog_usage_contract: bool = False,
    required_synthetic_gate_version: str | None = None,
    intended_use: IntendedUse = "evaluation",
    expected_origins: Mapping[str, str] = DEFAULT_TARGET_ORIGINS,
    scenario_targets: Mapping[str, int] | None = None,
    scenario_target_grades: Mapping[str, str] | None = None,
    scenario_factor_profiles: Mapping[str, str] | None = None,
) -> ProxyAssembly:
    """Select an exact proxy-gold candidate set after strict validation.

    Invalid rows, duplicate IDs/texts, and families already used by training are
    excluded and counted.  ``ready`` is true only when every requested grade
    reaches its exact target.
    """
    rows = [dict(record) for record in records]
    _require_intended_use(intended_use)
    if not 0 < max_family_share <= 1:
        raise ValueError("max_family_share must be in (0, 1]")
    normalized_expected_origins: dict[str, str] = {}
    for grade in targets:
        if grade not in GRADE_CODES:
            raise ValueError(f"invalid target grade: {grade}")
        origin = str(expected_origins.get(grade) or "").strip()
        if origin not in {PUBLIC_REAL, SYNTHETIC}:
            raise ValueError(
                f"expected origin for {grade} must be {PUBLIC_REAL} or {SYNTHETIC}"
            )
        normalized_expected_origins[grade] = origin
    scenario_contract_values = (
        scenario_targets,
        scenario_target_grades,
        scenario_factor_profiles,
    )
    if any(value is not None for value in scenario_contract_values) and not all(
        value is not None for value in scenario_contract_values
    ):
        raise ValueError(
            "scenario_targets, scenario_target_grades, and "
            "scenario_factor_profiles must be provided together"
        )
    normalized_scenario_targets: dict[str, int] = {}
    normalized_scenario_grades: dict[str, str] = {}
    normalized_scenario_profiles: dict[str, str] = {}
    target_by_factor_profile: Counter[str] = Counter()
    if scenario_targets is not None:
        assert scenario_target_grades is not None
        assert scenario_factor_profiles is not None
        if set(scenario_targets) != set(scenario_target_grades) or set(
            scenario_targets
        ) != set(scenario_factor_profiles):
            raise ValueError("scenario quota maps must have identical keys")
        target_by_scenario_grade: Counter[str] = Counter()
        for raw_scenario_id, raw_count in scenario_targets.items():
            scenario_id = str(raw_scenario_id).strip()
            grade = str(scenario_target_grades[raw_scenario_id]).strip()
            profile = str(scenario_factor_profiles[raw_scenario_id]).strip()
            if not scenario_id or not profile or grade not in GRADE_CODES:
                raise ValueError("scenario quota metadata is invalid")
            if isinstance(raw_count, bool) or not isinstance(raw_count, int):
                raise ValueError("scenario quota counts must be positive integers")
            if raw_count < 1:
                raise ValueError("scenario quota counts must be positive integers")
            normalized_scenario_targets[scenario_id] = raw_count
            normalized_scenario_grades[scenario_id] = grade
            normalized_scenario_profiles[scenario_id] = profile
            target_by_scenario_grade[grade] += raw_count
            target_by_factor_profile[profile] += raw_count
        target_counts = Counter({grade: int(count) for grade, count in targets.items()})
        if any(target_by_scenario_grade[grade] != target_counts[grade] for grade in target_by_scenario_grade):
            raise ValueError("scenario quotas must sum exactly to each covered grade target")
    blocked_ids = {
        str(value).strip() for value in blocked_doc_ids if str(value).strip()
    }
    blocked = {str(value).strip() for value in blocked_family_ids if str(value).strip()}
    blocked_hashes = {
        str(value).strip() for value in blocked_text_hashes if str(value).strip()
    }
    invalid = 0
    blocked_doc_id_count = 0
    blocked_count = 0
    blocked_text_count = 0
    duplicate_id = 0
    duplicate_text = 0
    unexpected_scenario = 0
    scenario_contract_mismatch = 0
    seen_ids: set[str] = set()
    seen_texts: set[str] = set()
    eligible: list[dict] = []
    for row in rows:
        check = validate_proxy_record(row, stage="eligible", intended_use=intended_use)
        if not check.ok:
            invalid += 1
            continue
        if require_catalog_usage_contract and row.get("document_origin") == SYNTHETIC:
            try:
                declared_use = proxy_record_intended_use(row)
            except ValueError:
                invalid += 1
                continue
            if declared_use != intended_use:
                invalid += 1
                continue
        if (
            required_synthetic_gate_version
            and row.get("document_origin") == SYNTHETIC
            and row.get("gate_version") != required_synthetic_gate_version
        ):
            invalid += 1
            continue
        if normalized_scenario_targets and row.get("document_origin") == SYNTHETIC:
            scenario_id = str(row.get("scenario_id") or "").strip()
            if scenario_id not in normalized_scenario_targets:
                unexpected_scenario += 1
                continue
            if (
                str(row.get("label") or "")
                != normalized_scenario_grades[scenario_id]
                or str(row.get("factor_profile_id") or "").strip()
                != normalized_scenario_profiles[scenario_id]
            ):
                scenario_contract_mismatch += 1
                invalid += 1
                continue
        doc_id = str(row["doc_id"])
        if doc_id in blocked_ids:
            blocked_doc_id_count += 1
            continue
        family = str(row["document_family_id"])
        if family in blocked:
            blocked_count += 1
            continue
        text = record_text(row)
        normalized_hash = text_hash(text)
        if normalized_hash in blocked_hashes:
            blocked_text_count += 1
            continue
        if doc_id in seen_ids:
            duplicate_id += 1
            continue
        if normalized_hash in seen_texts:
            duplicate_text += 1
            continue
        seen_ids.add(doc_id)
        seen_texts.add(normalized_hash)
        eligible.append(row)

    selected: list[dict] = []
    selected_by_grade: dict[str, int] = {}
    available_by_grade: dict[str, int] = {}
    families_by_grade: dict[str, int] = {}
    missing_by_grade: dict[str, int] = {}
    wrong_origin_by_grade: dict[str, int] = {}
    required_families_by_grade: dict[str, int] = {}
    family_shortfall_by_grade: dict[str, int] = {}
    max_selected_family_share_by_grade: dict[str, float] = {}
    family_share_violations: dict[str, float] = {}
    available_by_scenario: dict[str, int] = {}
    selected_by_scenario: dict[str, int] = {}
    missing_by_scenario: dict[str, int] = {}
    for grade, target_value in targets.items():
        target = int(target_value)
        expected_origin = normalized_expected_origins[grade]
        grade_all = [row for row in eligible if row.get("label") == grade]
        grade_rows = [
            row for row in grade_all if row.get("document_origin") == expected_origin
        ]
        wrong_origin_by_grade[grade] = len(grade_all) - len(grade_rows)
        available_by_grade[grade] = len(grade_rows)
        families_by_grade[grade] = len(
            {row["document_family_id"] for row in grade_rows}
        )
        required_families = min(target, int(min_families.get(grade, 1)))
        required_families_by_grade[grade] = required_families
        if families_by_grade[grade] < required_families:
            family_shortfall_by_grade[grade] = (
                required_families - families_by_grade[grade]
            )
        grade_scenarios = sorted(
            scenario_id
            for scenario_id, scenario_grade in normalized_scenario_grades.items()
            if scenario_grade == grade
        )
        if normalized_scenario_targets and grade_scenarios:
            picked = []
            for scenario_id in grade_scenarios:
                scenario_rows = [
                    row
                    for row in grade_rows
                    if str(row.get("scenario_id") or "") == scenario_id
                ]
                scenario_target = normalized_scenario_targets[scenario_id]
                available_by_scenario[scenario_id] = len(scenario_rows)
                scenario_picked = _select_family_diverse(
                    scenario_rows, scenario_target
                )
                picked.extend(scenario_picked)
                selected_by_scenario[scenario_id] = len(scenario_picked)
                if len(scenario_picked) < scenario_target:
                    missing_by_scenario[scenario_id] = (
                        scenario_target - len(scenario_picked)
                    )
        else:
            picked = _select_family_diverse(grade_rows, target)
        selected.extend(picked)
        selected_by_grade[grade] = len(picked)
        if len(picked) < target:
            missing_by_grade[grade] = target - len(picked)
        family_counts = Counter(str(row["document_family_id"]) for row in picked)
        selected_share = (
            max(family_counts.values(), default=0) / len(picked) if picked else 0.0
        )
        max_selected_family_share_by_grade[grade] = round(selected_share, 6)
        # A percentage cap cannot be satisfied mathematically for tiny test or
        # pilot cells.  At least one row per family is therefore always allowed.
        allowed_per_family = max(1, math.ceil(target * max_family_share))
        if family_counts and max(family_counts.values()) > allowed_per_family:
            family_share_violations[grade] = round(selected_share, 6)

    selected.sort(key=lambda row: (str(row.get("label")), str(row.get("doc_id"))))
    available_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "")
        for row in eligible
        if row.get("document_origin")
        == normalized_expected_origins.get(str(row.get("label") or ""))
    )
    selected_by_factor_profile = Counter(
        str(row.get("factor_profile_id") or "") for row in selected
    )
    missing_by_factor_profile = {
        profile: target - selected_by_factor_profile[profile]
        for profile, target in sorted(target_by_factor_profile.items())
        if selected_by_factor_profile[profile] < target
    }
    target_total = sum(int(value) for value in targets.values())
    if require_shortcut_gate is None:
        require_shortcut_gate = target_total >= 100
    shortcut_report: dict = {
        "gate": {
            "status": "not_required_for_small_pilot",
            "passed": not require_shortcut_gate,
        }
    }
    counts_complete = not missing_by_grade and len(selected) == target_total
    if require_shortcut_gate and counts_complete:
        from koipa.proxy_shortcuts import strict_shortcut_gate  # noqa: PLC0415

        shortcut_report = strict_shortcut_gate(selected, frozen_gold=True)

    stats = {
        "input": len(rows),
        "eligible": len(eligible),
        "selected": len(selected),
        "targets": {grade: int(count) for grade, count in targets.items()},
        "selected_by_grade": selected_by_grade,
        "available_by_grade": available_by_grade,
        "families_by_grade": families_by_grade,
        "required_families_by_grade": required_families_by_grade,
        "family_shortfall_by_grade": family_shortfall_by_grade,
        "max_selected_family_share_by_grade": max_selected_family_share_by_grade,
        "max_family_share": max_family_share,
        "family_share_violations": family_share_violations,
        "expected_origin_by_grade": normalized_expected_origins,
        "origin_contract": (
            "matched_synthetic_primary"
            if set(normalized_expected_origins.values()) == {SYNTHETIC}
            else "mixed_origin_compatibility"
        ),
        "wrong_origin_by_grade": wrong_origin_by_grade,
        "missing_by_grade": missing_by_grade,
        "target_by_scenario": dict(sorted(normalized_scenario_targets.items())),
        "available_by_scenario": dict(sorted(available_by_scenario.items())),
        "selected_by_scenario": dict(sorted(selected_by_scenario.items())),
        "missing_by_scenario": dict(sorted(missing_by_scenario.items())),
        "target_by_factor_profile": dict(sorted(target_by_factor_profile.items())),
        "available_by_factor_profile": dict(
            sorted(available_by_factor_profile.items())
        ),
        "selected_by_factor_profile": dict(
            sorted(selected_by_factor_profile.items())
        ),
        "missing_by_factor_profile": missing_by_factor_profile,
        "invalid": invalid,
        "blocked_doc_id_records": blocked_doc_id_count,
        "blocked_family_records": blocked_count,
        "blocked_text_records": blocked_text_count,
        "dropped_duplicate_id": duplicate_id,
        "dropped_duplicate_text": duplicate_text,
        "unexpected_scenario_records": unexpected_scenario,
        "scenario_contract_mismatch_records": scenario_contract_mismatch,
        "scenario_quota_contract": bool(normalized_scenario_targets),
        "claim_scope": "synthetic_proxy_regression_and_calibration_only",
        "human_reviewed": False,
        "require_catalog_usage_contract": require_catalog_usage_contract,
        "required_synthetic_gate_version": required_synthetic_gate_version,
        "shortcut_gate": shortcut_report["gate"],
        "shortcut_report": shortcut_report,
    }
    shortcut_passed = bool(shortcut_report["gate"].get("passed"))
    ready = (
        not missing_by_grade
        and not missing_by_scenario
        and not missing_by_factor_profile
        and not family_shortfall_by_grade
        and not family_share_violations
        and shortcut_passed
    )
    return ProxyAssembly(ready=ready, selected=tuple(selected), stats=stats)
