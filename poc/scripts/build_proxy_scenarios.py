"""Generate catalog-driven high-fidelity proxy candidates.

This produces *candidates*, not human-signed gold.  It never fetches customer
documents and refuses noop/non-JSON output, so placeholder text cannot enter the
proxy training or evaluation path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path
from typing import Iterable, Mapping, Sequence

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC))
sys.path.insert(0, str(_POC / "src"))

from koipa.adapters.llm import build_provider  # noqa: E402
from koipa.modules.m1_synthesis.generator import (  # noqa: E402
    SYSTEM_PROMPT,
    USER_TEMPLATE_V2,
    SynthDoc,
    SynthRequest,
    SyntheticDocGenerator,
)
from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402
from koipa.ollama_attestation import (  # noqa: E402
    OllamaAttestationError,
    pending_ollama_model_attestation,
    validate_ollama_attestation,
    verify_ollama_model,
)
from koipa.proxy_corpus import (  # noqa: E402
    proxy_record_intended_use,
    validate_proxy_record,
)


RUN_SCHEMA_VERSION = "proxy-generation-run-v3"
FACT_LEDGER_VERSION = "proxy-fact-ledger-v1"
FACT_LEDGER_NUMERIC_GUARD_VERSION = "proxy-fact-ledger-v2"
FACT_LEDGER_MATERIALIZATION_VERSION = "proxy-fact-ledger-materialization-v2"
_SHARED_FILE_MODE = 0o640
_SHARED_DIRECTORY_MODE = 0o2750
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_BLOCKED_IDENTITIES = frozenset(
    {"", "noop", "unknown", "none", "null", "empty", "unset", "mock", "fake", "test"}
)
_OLLAMA_GENERATION_PROVIDERS = frozenset({"local_openai", "ollama"})
_RETRY_DRAFT_MAX_CHARS = 2400
# KL Ollama is configured with an 8,192-token context.  Observed Qwen3:4b
# prompt-eval counts were about 1,108 tokens initially and 1,550 on the
# stricter internal JSON-repair call.  The old compact (3,500) and standard
# (3,958) completion ceilings were both reached exactly before JSON closed.
#
# These per-profile contracts leave 384 tokens of safety after a conservative
# prompt ceiling.  On an outer quality retry compact/standard carry at most
# 1,600 characters of the prior draft; extended carries no raw draft (the
# generic problem summary remains) so its longer document can retain enough
# completion room.  Every row satisfies output + prompt + safety <= 8,192.
_OLLAMA_CONTEXT_WINDOW_TOKENS = 8192
_CONTEXT_SAFETY_TOKENS = 384
_PROFILE_TOKEN_CONTRACT = {
    "compact": {
        "initial_prompt_ceiling": 1800,
        "initial_output_tokens": 6000,
        "quality_retry_prompt_ceiling": 3000,
        "quality_retry_output_tokens": 4800,
        "quality_retry_draft_chars": 1600,
    },
    "standard": {
        "initial_prompt_ceiling": 1800,
        "initial_output_tokens": 6000,
        "quality_retry_prompt_ceiling": 3000,
        "quality_retry_output_tokens": 4800,
        "quality_retry_draft_chars": 1600,
    },
    "extended": {
        "initial_prompt_ceiling": 1700,
        "initial_output_tokens": 6100,
        "quality_retry_prompt_ceiling": 2000,
        "quality_retry_output_tokens": 5800,
        "quality_retry_draft_chars": 0,
    },
}
_CATALOG_USE_PERMISSIONS = {
    "train_pool_only": {
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
    },
    "frozen_proxy_eval_only": {
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
    },
}
_FACTOR_NAMES = ("secrecy", "value", "management")
_PLAUSIBLE_FACTOR_TRIPLES = frozenset(
    (secrecy, value, management)
    for secrecy in range(3)
    for value in range(3)
    for management in range(3)
    # A document whose complete contents are actually public cannot also have
    # meaningful access controls over those same contents.  These six cells
    # are syntactically legal in the rule table but semantically contradictory.
    if not (secrecy == 0 and management > 0)
)
_REPRESENTATIVE_FACTOR_TRIPLES = frozenset(
    {(2, 2, 2), (2, 2, 0), (1, 1, 1), (0, 0, 0)}
)
_FULL_RULE_TABLE_COUNTS = Counter(
    grade_from_svm(secrecy, value, management)
    for secrecy in range(3)
    for value in range(3)
    for management in range(3)
)
_PLAUSIBLE_PROFILE_COUNTS = Counter(
    grade_from_svm(*triple) for triple in _PLAUSIBLE_FACTOR_TRIPLES
)
_DIRECT_LABEL_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:TS|S[1-3])(?:\s*(?:등급|급))?(?![A-Za-z0-9])"
    r"|특급\s*(?:기밀|비밀)|[1-3]\s*급\s*(?:비밀|기밀|대외비)"
    r"|(?:보안|비밀|기밀)\s*등급\s*[:：]?\s*(?:TS|S[1-3]|특급|[1-3]급)"
    r"|대외비|극비"
)
_CLASSIFICATION_STYLE_MARKER_RE = re.compile(
    r"(?i)(?:[\(\[（【]\s*공개(?:판|본)\s*[\)\]）】])"
    r"|(?<![가-힣A-Za-z0-9])(?:공개\s*용|외부\s*배포\s*용|내부\s*용|열람\s*등급)"
    r"(?![가-힣A-Za-z0-9])"
)
_NUMBERED_OR_MARKED_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*|\d+(?:[.-]\d+)*[.)]\s*|[가-힣A-Za-z][.)]\s*|"
    r"[-*•■□●○▶※]\s+)"
)

_PROFILE_STRUCTURE_REQUIREMENTS = {
    "compact": (
        "compact 문서 계약: 이름이 있는 절을 최소 5개 작성하고, 각 절은 서로 다른 사실을 "
        "다룬다. 헤더 1행과 데이터 4행 이상인 표를 1개 포함한다. 후속 조치는 최소 "
        "3개이며 각 조치마다 책임 역할, 기한, 완료 판정 기준을 적는다. 핵심 설명 절은 "
        "각각 대략 150~220자로 작성해 표나 목록만으로 분량을 대신하지 않는다."
    ),
    "standard": (
        "standard 문서 계약: 이름이 있는 절을 최소 6개 작성하고, 각 절은 서로 다른 사실을 "
        "다룬다. 헤더 1행과 데이터 5행 이상인 비교·결과 표를 1개 포함한다. 후속 조치는 "
        "최소 4개이며 각 조치마다 책임 역할, 기한, 완료 판정 기준을 적는다. 핵심 설명 "
        "절은 각각 대략 200~280자로 작성한다."
    ),
    "extended": (
        "extended 문서 계약: 이름이 있는 절을 최소 8개 작성하고, 핵심 설명 절은 각각 "
        "대략 250~330자의 서로 다른 내용으로 채운다. 헤더를 제외한 데이터 행이 합계 "
        "6행 이상인 표를 하나 이상 포함한다. 대안 세 가지를 각각 전제·비용·일정·위험과 "
        "함께 비교하고, 후속 조치를 최소 5개 작성해 책임 역할, 기한, 완료 판정 기준을 "
        "적는다. 단순 문장 반복이나 표 행 복제로 글자 수를 채우지 않는다."
    ),
}

_PROXY_TITLE_HEADER_REQUIREMENT = (
    "제목·절 제목·머리말에는 분류나 배포 대상을 암시하는 꼬리표를 붙이지 않는다. "
    "괄호형 공개판·공개본, 공개용, 외부 배포용, 내부용, 열람등급 같은 표기는 쓰지 "
    "않는다. 공개 사실이 필요한 문서는 본문에서 가상 공식 채널명, 게시 시점, 로그인·"
    "승인 없이 열람 가능하다는 사실을 자연스러운 문장으로 설명한다."
)


class ProxyGenerationRunError(ValueError):
    """Immutable-run, resume, provider, or artifact contract violation."""


def _hangul_syllable_count(value: str) -> int:
    """Count precomposed Hangul syllables without relying on source encoding."""
    return sum(0xAC00 <= ord(char) <= 0xD7A3 for char in value)


def _require_korean_catalog_text(value: object, *, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"catalog Korean text is missing: {field}")
    if "\ufffd" in value or any(
        ord(char) < 32 and char not in "\n\r\t" for char in value
    ):
        raise SystemExit(f"catalog Korean text has invalid Unicode: {field}")
    hangul = _hangul_syllable_count(value)
    alphabetic = sum(char.isalpha() for char in value)
    if hangul < 3 or (alphabetic and hangul / alphabetic < 0.55):
        raise SystemExit(
            f"catalog Korean text failed the UTF-8/Hangul integrity gate: {field}"
        )


def validate_catalog_language_contract(raw: Mapping[str, object]) -> None:
    """Fail closed before a damaged Korean catalog reaches an LLM prompt."""
    field_contracts = {
        "instance_profiles": ("context",),
        "family_profiles": ("document_shape", "context"),
        "archetypes": ("document_type", "shared_context", "harm_potential"),
        "grade_variants": (
            "content_condition",
            "management_condition",
            "disclosure_scope",
            "nonpublicity_claim",
            "value_claim",
            "management_claim",
        ),
    }
    for collection, fields in field_contracts.items():
        rows = raw.get(collection)
        if not isinstance(rows, list) or not rows:
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise SystemExit(f"catalog {collection}[{index}] must be an object")
            for field in fields:
                _require_korean_catalog_text(
                    row.get(field), field=f"{collection}[{index}].{field}"
                )
            if collection == "grade_variants" and "harm_potential" in row:
                _require_korean_catalog_text(
                    row.get("harm_potential"),
                    field=f"{collection}[{index}].harm_potential",
                )

    factor_axes = raw.get("factor_axes")
    if isinstance(factor_axes, Mapping):
        for factor in _FACTOR_NAMES:
            rows = factor_axes.get(factor)
            if not isinstance(rows, list):
                raise SystemExit(f"catalog factor_axes.{factor} must be a list")
            for index, row in enumerate(rows):
                if not isinstance(row, Mapping):
                    raise SystemExit(
                        f"catalog factor_axes.{factor}[{index}] must be an object"
                    )
                for field in ("condition", "claim"):
                    _require_korean_catalog_text(
                        row.get(field),
                        field=f"factor_axes.{factor}[{index}].{field}",
                    )
                extra_field = {
                    "secrecy": "disclosure_scope",
                    "value": "harm_potential",
                }.get(factor)
                if extra_field:
                    _require_korean_catalog_text(
                        row.get(extra_field),
                        field=f"factor_axes.{factor}[{index}].{extra_field}",
                    )

    profile_policy = raw.get("factor_profile_policy")
    if isinstance(profile_policy, Mapping):
        exclusions = profile_policy.get("excluded_combinations")
        if isinstance(exclusions, list):
            for index, exclusion in enumerate(exclusions):
                if not isinstance(exclusion, Mapping):
                    raise SystemExit(
                        f"catalog excluded_combinations[{index}] must be an object"
                    )
                _require_korean_catalog_text(
                    exclusion.get("reason"),
                    field=f"factor_profile_policy.excluded_combinations[{index}].reason",
                )


def _factor_profile_variants(raw: Mapping[str, object]) -> list[dict] | None:
    """Validate and materialize the complete 21-profile plausible S/V/M set."""
    raw_axes = raw.get("factor_axes")
    raw_profiles = raw.get("factor_profiles")
    if raw_axes is None and raw_profiles is None:
        return None
    if not isinstance(raw_axes, Mapping) or not isinstance(raw_profiles, list):
        raise SystemExit("catalog factor_axes and factor_profiles must be defined together")
    if set(raw_axes) != set(_FACTOR_NAMES):
        raise SystemExit("catalog factor_axes must contain secrecy, value, management")

    axes: dict[str, dict[int, Mapping[str, object]]] = {}
    for factor in _FACTOR_NAMES:
        rows = raw_axes.get(factor)
        if not isinstance(rows, list) or len(rows) != 3:
            raise SystemExit(f"catalog factor_axes.{factor} must define scores 0, 1, 2")
        lookup: dict[int, Mapping[str, object]] = {}
        for row in rows:
            if not isinstance(row, Mapping):
                raise SystemExit(f"catalog factor_axes.{factor} contains a non-object")
            try:
                score = int(row["score"])
            except (KeyError, TypeError, ValueError) as exc:
                raise SystemExit(
                    f"catalog factor_axes.{factor} has an invalid score"
                ) from exc
            if isinstance(row.get("score"), bool) or score not in {0, 1, 2}:
                raise SystemExit(
                    f"catalog factor_axes.{factor} score must be 0, 1, or 2"
                )
            if score in lookup:
                raise SystemExit(f"catalog factor_axes.{factor} has duplicate score {score}")
            lookup[score] = row
        if set(lookup) != {0, 1, 2}:
            raise SystemExit(f"catalog factor_axes.{factor} must define scores 0, 1, 2")
        axes[factor] = lookup

    variants: list[dict] = []
    seen_ids: set[str] = set()
    seen_triples: set[tuple[int, int, int]] = set()
    quota_by_grade: Counter[str] = Counter()
    representative_triples: set[tuple[int, int, int]] = set()
    for index, profile in enumerate(raw_profiles):
        if not isinstance(profile, Mapping):
            raise SystemExit(f"catalog factor_profiles[{index}] must be an object")
        profile_id = str(profile.get("profile_id") or "").strip()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{2,63}", profile_id):
            raise SystemExit(f"invalid factor profile_id: {profile_id!r}")
        if profile_id in seen_ids:
            raise SystemExit(f"duplicate factor profile_id: {profile_id}")
        scores = profile.get("expected_factor_scores")
        if not isinstance(scores, Mapping) or set(scores) != set(_FACTOR_NAMES):
            raise SystemExit(f"factor profile {profile_id} has incomplete S/V/M scores")
        try:
            triple = tuple(int(scores[name]) for name in _FACTOR_NAMES)
        except (TypeError, ValueError) as exc:
            raise SystemExit(f"factor profile {profile_id} has invalid S/V/M scores") from exc
        if any(isinstance(scores[name], bool) for name in _FACTOR_NAMES):
            raise SystemExit(f"factor profile {profile_id} cannot use boolean scores")
        if triple not in _PLAUSIBLE_FACTOR_TRIPLES:
            raise SystemExit(
                f"factor profile {profile_id} is semantically incompatible: {triple}"
            )
        expected_suffix = f"-s{triple[0]}-v{triple[1]}-m{triple[2]}"
        if not profile_id.endswith(expected_suffix):
            raise SystemExit(
                f"factor profile_id must encode its S/V/M scores: {profile_id}"
            )
        if triple in seen_triples:
            raise SystemExit(f"duplicate factor score triple: {triple}")
        label = str(profile.get("label") or "")
        if grade_from_svm(*triple) != label:
            raise SystemExit(
                f"factor profile {profile_id} declares {label} but derives "
                f"{grade_from_svm(*triple)}"
            )
        quota = profile.get("target_count_per_archetype")
        if isinstance(quota, bool) or not isinstance(quota, int) or quota < 1:
            raise SystemExit(f"factor profile {profile_id} must have a positive quota")
        representative = profile.get("representative_pilot", False)
        if not isinstance(representative, bool):
            raise SystemExit(
                f"factor profile {profile_id} representative_pilot must be boolean"
            )
        if representative:
            representative_triples.add(triple)

        secrecy, value, management = triple
        secrecy_axis = axes["secrecy"][secrecy]
        value_axis = axes["value"][value]
        management_axis = axes["management"][management]
        variants.append(
            {
                "factor_profile_id": profile_id,
                "representative_pilot": representative,
                "label": label,
                "target_count_per_archetype": quota,
                "expected_factor_scores": {
                    "secrecy": secrecy,
                    "value": value,
                    "management": management,
                },
                "content_condition": (
                    "[정보 유통 상태] "
                    f"{secrecy_axis['condition']}\n"
                    "[업무상 영향] "
                    f"{value_axis['condition']}"
                ),
                "management_condition": (
                    "[문서 운영] "
                    f"{management_axis['condition']}\n"
                    "[작성 원칙] 위 조건은 문서의 관찰 가능한 사실로만 반영한다. "
                    "지시문의 제목, 법률·평가 용어, 점수 또는 판정 문구를 본문에 쓰지 않는다."
                ),
                "disclosure_scope": str(secrecy_axis["disclosure_scope"]),
                "harm_potential": str(value_axis["harm_potential"]),
                "nonpublicity_claim": str(secrecy_axis["claim"]),
                "value_claim": str(value_axis["claim"]),
                "management_claim": str(management_axis["claim"]),
            }
        )
        seen_ids.add(profile_id)
        seen_triples.add(triple)
        quota_by_grade[label] += quota

    if seen_triples != set(_PLAUSIBLE_FACTOR_TRIPLES):
        missing = sorted(set(_PLAUSIBLE_FACTOR_TRIPLES) - seen_triples)
        extra = sorted(seen_triples - set(_PLAUSIBLE_FACTOR_TRIPLES))
        raise SystemExit(
            f"factor profile set must equal the 21 plausible cells; missing={missing}, "
            f"extra={extra}"
        )
    if representative_triples != set(_REPRESENTATIVE_FACTOR_TRIPLES):
        raise SystemExit("factor profile representative pilot must contain the canonical four")

    policy = raw.get("factor_profile_policy")
    if not isinstance(policy, Mapping):
        raise SystemExit("catalog factor_profile_policy is required")
    declared_full_counts = policy.get("full_rule_table_grade_counts")
    declared_profile_counts = policy.get("included_profile_grade_counts")
    if not isinstance(declared_full_counts, Mapping) or not isinstance(
        declared_profile_counts, Mapping
    ):
        raise SystemExit("factor_profile_policy must declare full and included counts")
    if set(declared_full_counts) != {"TS", "S1", "S2", "S3"} or set(
        declared_profile_counts
    ) != {"TS", "S1", "S2", "S3"}:
        raise SystemExit("factor profile count maps must contain exactly TS/S1/S2/S3")
    try:
        if any(isinstance(value, bool) for value in declared_full_counts.values()) or any(
            isinstance(value, bool) for value in declared_profile_counts.values()
        ):
            raise TypeError
        full_counts = {
            str(key): int(value) for key, value in declared_full_counts.items()
        }
        profile_counts = {
            str(key): int(value) for key, value in declared_profile_counts.items()
        }
        raw_included_count = policy.get("included_profile_count")
        raw_excluded_count = policy.get("excluded_profile_count")
        if isinstance(raw_included_count, bool) or isinstance(
            raw_excluded_count, bool
        ):
            raise TypeError
        included_count = int(raw_included_count)
        excluded_count = int(raw_excluded_count)
    except (TypeError, ValueError) as exc:
        raise SystemExit("factor_profile_policy counts must be integers") from exc
    if full_counts != dict(_FULL_RULE_TABLE_COUNTS):
        raise SystemExit("factor_profile_policy full rule-table counts are incorrect")
    if profile_counts != dict(_PLAUSIBLE_PROFILE_COUNTS):
        raise SystemExit("factor_profile_policy included profile counts are incorrect")
    if included_count != len(_PLAUSIBLE_FACTOR_TRIPLES) or excluded_count != 6:
        raise SystemExit("factor_profile_policy must include 21 and exclude 6 profiles")
    exclusions = policy.get("excluded_combinations")
    if not isinstance(exclusions, list) or len(exclusions) != 1:
        raise SystemExit("factor_profile_policy must declare the S=0,M>0 exclusion")
    exclusion = exclusions[0]
    if not isinstance(exclusion, Mapping) or exclusion.get("predicate") != {
        "secrecy": 0,
        "management": [1, 2],
        "value": [0, 1, 2],
    }:
        raise SystemExit("factor_profile_policy exclusion predicate is incorrect")
    expected_quota = policy.get("per_archetype_grade_quota")
    if not isinstance(expected_quota, Mapping):
        raise SystemExit("factor_profile_policy.per_archetype_grade_quota is required")
    if set(expected_quota) != {"TS", "S1", "S2", "S3"}:
        raise SystemExit("factor profile grade quotas must contain exactly TS/S1/S2/S3")
    try:
        if any(isinstance(value, bool) for value in expected_quota.values()):
            raise TypeError
        normalized_quota = {
            str(key): int(value) for key, value in expected_quota.items()
        }
    except (TypeError, ValueError) as exc:
        raise SystemExit("factor profile grade quotas must be integers") from exc
    if dict(sorted(quota_by_grade.items())) != dict(sorted(normalized_quota.items())):
        raise SystemExit(
            "factor profile quotas do not match per_archetype_grade_quota"
        )
    return variants


def expand_catalog_scenarios(raw: dict) -> list[dict]:
    """Expand shared archetypes into matched grade counterfactuals.

    A flat catalog is still accepted for backwards compatibility, but the v2
    and later proxy designs use one topic/document archetype across every grade.
    Only the S/V/M facts differ, which prevents origin, industry, or document
    type from becoming a label shortcut in the primary frozen proxy set.
    """
    validate_catalog_language_contract(raw)
    flat = raw.get("scenarios")
    has_factor_schema = raw.get("factor_axes") is not None or raw.get(
        "factor_profiles"
    ) is not None
    if has_factor_schema and isinstance(flat, list) and flat:
        raise SystemExit("factor-profile catalogs cannot also declare flat scenarios")
    if has_factor_schema and raw.get("grade_variants") is not None:
        raise SystemExit("factor-profile catalogs cannot also declare grade_variants")
    if isinstance(flat, list) and flat:
        scenarios = [row for row in flat if isinstance(row, dict)]
    else:
        archetypes = [row for row in raw.get("archetypes", []) if isinstance(row, dict)]
        variants = _factor_profile_variants(raw)
        if variants is None:
            variants = [
                row for row in raw.get("grade_variants", []) if isinstance(row, dict)
            ]
        scenarios = []
        for archetype in archetypes:
            for variant in variants:
                scores = variant.get("expected_factor_scores") or {}
                try:
                    derived = grade_from_svm(
                        int(scores["secrecy"]),
                        int(scores["value"]),
                        int(scores["management"]),
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise SystemExit(
                        "invalid expected_factor_scores in grade variant"
                    ) from exc
                label = str(variant.get("label") or "")
                if derived != label:
                    raise SystemExit(
                        f"catalog S/V/M mismatch: {archetype.get('archetype_id')} "
                        f"declares {label} but derives {derived}"
                    )
                archetype_id = str(archetype["archetype_id"])
                factor_profile_id = str(
                    variant.get("factor_profile_id") or label.lower()
                )
                scenarios.append(
                    {
                        **archetype,
                        "scenario_id": f"{archetype_id}-{factor_profile_id}",
                        "document_family_id": str(archetype["document_family_id"]),
                        "factor_profile_id": factor_profile_id,
                        "representative_pilot": bool(
                            variant.get("representative_pilot", False)
                        ),
                        "label": label,
                        "target_count": int(variant["target_count_per_archetype"]),
                        "min_chars": int(raw.get("min_chars", 1200)),
                        "max_chars": int(raw.get("max_chars", 3200)),
                        "expected_factor_scores": dict(scores),
                        "scenario_context": (
                            f"{archetype['shared_context']}\n"
                            f"{variant['content_condition']}\n"
                            f"{variant['management_condition']}\n"
                            "[사실 반영 지시] 위 세 조건은 서로 다른 자연스러운 문장이나 "
                            "표 항목에 관찰 가능한 업무 사실로 반영한다. 조건의 제목·판정 "
                            "용어·점수·등급을 복사하거나 설명하지 않는다."
                        ),
                        "disclosure_scope": str(variant["disclosure_scope"]),
                        # A public S3 counterfactual must explicitly override the
                        # archetype's confidential harm.  Older catalogs without
                        # a variant override retain their original behaviour.
                        "harm_potential": str(
                            variant.get("harm_potential") or archetype["harm_potential"]
                        ),
                        # Preliminary catalog claims are generation controls only.
                        # The independent judge replaces these with exact text spans.
                        "evidence_card": {
                            "nonpublicity": str(variant["nonpublicity_claim"]),
                            "competitive_value": str(variant["value_claim"]),
                            "access_controls": str(variant["management_claim"]),
                        },
                    }
                )
    if not scenarios:
        raise SystemExit("scenario catalog is empty")
    fact_ledger_contract = str(raw.get("fact_ledger_contract") or "").strip()
    if fact_ledger_contract not in {
        "",
        FACT_LEDGER_VERSION,
        FACT_LEDGER_NUMERIC_GUARD_VERSION,
    }:
        raise SystemExit(
            f"unsupported catalog fact_ledger_contract: {fact_ledger_contract!r}"
        )
    split_role = str(raw.get("split_role") or "").strip()
    permissions = _CATALOG_USE_PERMISSIONS.get(split_role)
    if permissions is None:
        allowed = ", ".join(sorted(_CATALOG_USE_PERMISSIONS))
        raise SystemExit(
            f"catalog split_role must be one of [{allowed}]; found {split_role!r}"
        )
    for scenario in scenarios:
        scenario["catalog_split_role"] = split_role
        scenario["fact_ledger_contract"] = fact_ledger_contract
        scenario.update(permissions)
    return scenarios


def load_catalog(path: Path) -> tuple[dict, list[dict]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"invalid scenario catalog: {path}")
    scenarios = expand_catalog_scenarios(raw)
    return raw, scenarios


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_text(value: object) -> str:
    return _sha256_bytes(str(value).encode("utf-8"))


def _blocked_identity(value: object) -> bool:
    compact = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    return compact in _BLOCKED_IDENTITIES or any(
        compact.startswith(prefix)
        for prefix in ("noop", "unknown", "mock", "fake", "test")
    )


def _profile_structure_requirements(family_profile: Mapping[str, object]) -> str:
    """Return a measurable writing contract for one document-length profile."""
    profile_id = str(family_profile.get("length_profile_id") or "").strip().lower()
    if profile_id not in _PROFILE_STRUCTURE_REQUIREMENTS:
        profile_min = int(family_profile.get("min_chars") or 0)
        profile_max = int(family_profile.get("max_chars") or 0)
        if profile_min >= 2200:
            profile_id = "extended"
        elif profile_max and profile_max <= 1599:
            profile_id = "compact"
        else:
            profile_id = "standard"
    return (
        _PROFILE_STRUCTURE_REQUIREMENTS[profile_id]
        + "\n"
        + _PROXY_TITLE_HEADER_REQUIREMENT
    )


def _normalized_length_profile(family_profile: Mapping[str, object]) -> str:
    profile_id = str(family_profile.get("length_profile_id") or "").strip().lower()
    profile_max = int(family_profile.get("max_chars") or 0)
    if profile_id in _PROFILE_TOKEN_CONTRACT:
        return profile_id
    if profile_max and profile_max <= 1599:
        return "compact"
    if profile_max >= 2200:
        return "extended"
    return "standard"


def _proxy_output_token_budget(
    family_profile: Mapping[str, object], *, quality_retry: bool
) -> int:
    """Allocate a profile-specific completion budget inside KL's 8k context."""
    profile_id = _normalized_length_profile(family_profile)
    contract = _PROFILE_TOKEN_CONTRACT[profile_id]
    phase = "quality_retry" if quality_retry else "initial"
    output_tokens = int(contract[f"{phase}_output_tokens"])
    prompt_ceiling = int(contract[f"{phase}_prompt_ceiling"])
    if output_tokens + prompt_ceiling + _CONTEXT_SAFETY_TOKENS > (
        _OLLAMA_CONTEXT_WINDOW_TOKENS
    ):
        raise ProxyGenerationRunError(
            f"unsafe generation token contract for {profile_id}:{phase}"
        )
    return output_tokens


def _proxy_retry_draft_limit(family_profile: Mapping[str, object]) -> int:
    profile_id = _normalized_length_profile(family_profile)
    return int(_PROFILE_TOKEN_CONTRACT[profile_id]["quality_retry_draft_chars"])


def _heading_has_classification_marker(line: str) -> bool:
    """Detect artificial label-like markers only in heading-shaped body lines."""
    stripped = line.strip()
    marker = _CLASSIFICATION_STYLE_MARKER_RE.search(stripped)
    if not marker:
        return False
    if _NUMBERED_OR_MARKED_HEADING_RE.match(stripped):
        return True
    if marker.start() == 0:
        return True
    colon = min(
        (
            position
            for position in (stripped.find(":"), stripped.find("："))
            if position >= 0
        ),
        default=-1,
    )
    return 0 <= colon <= 20 and marker.start() > colon


def _classification_style_marker_errors(doc: object) -> list[str]:
    """Reject title/header shortcuts without banning factual publicity prose."""
    errors: list[str] = []
    title = str(getattr(doc, "title", "") or "").strip()
    if _CLASSIFICATION_STYLE_MARKER_RE.search(title):
        errors.append("quality:classification_style_marker:title")
    body = str(getattr(doc, "body", "") or "")
    if any(_heading_has_classification_marker(line) for line in body.splitlines()):
        errors.append("quality:classification_style_marker:body_heading")
    return errors


_PROMPT_ARTIFACT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("control_tag", re.compile(r"(?i)</?copy_exactly>")),
    ("ledger_heading", re.compile(r"\[\s*(?:사실\s*검산|코드\s*산출\s*사실\s*원장)")),
    ("instruction_heading", re.compile(r"\[\s*판정\s*근거\s*문장\s*\]")),
    (
        "rubric_term",
        re.compile(r"(?:비공지성|경제적\s*유용성|비밀관리성)"),
    ),
)


def _generation_prompt_artifact_errors(doc: object) -> list[str]:
    """Reject leaked control/rubric text before deterministic append."""
    raw = "\n".join(
        part
        for part in (
            str(getattr(doc, "title", "") or ""),
            str(getattr(doc, "body", "") or ""),
        )
        if part
    )
    return [
        f"quality:generation_prompt_artifact:{name}"
        for name, pattern in _PROMPT_ARTIFACT_PATTERNS
        if pattern.search(raw)
    ]


_DECIMAL_TOKEN = r"-?\d+(?:,\d{3})*(?:\.\d+)?"
_MEASUREMENT_UNIT = r"(?:%|℃|°C|㎛|μm|mm|cm|m|MPa|kPa|Pa|ms|초|분|시간|원|건|개|점|배)"


def _canonical_numeric_token(value: str) -> str:
    """Compare generated numerals by value, not comma/decimal presentation."""
    decimal = Decimal(value.replace(",", ""))
    canonical = format(decimal.normalize(), "f")
    return canonical.rstrip("0").rstrip(".") if "." in canonical else canonical


def _numeric_tokens(text: object) -> set[str]:
    return {
        _canonical_numeric_token(match.group(0))
        for match in re.finditer(_DECIMAL_TOKEN, str(text or ""))
    }


def _allowed_prompt_numeric_tokens(
    scenario: Mapping[str, object],
    instance_profile: Mapping[str, object],
    family_profile: Mapping[str, object],
) -> set[str]:
    """Numbers may only come from explicit, auditable generation inputs."""
    fields = (
        scenario.get("scenario_context"),
        instance_profile.get("context"),
        family_profile.get("context"),
        scenario.get("disclosure_scope"),
        scenario.get("harm_potential"),
    )
    return set().union(*(_numeric_tokens(field) for field in fields))


def _unapproved_numeric_claim_errors(
    doc: object, *, allowed_numeric_tokens: set[str]
) -> list[str]:
    """Reject model-created numeric claims that have no prompt provenance."""
    raw = "\n".join(
        part
        for part in (
            str(getattr(doc, "title", "") or ""),
            str(getattr(doc, "body", "") or ""),
        )
        if part
    )
    unexpected = sorted(_numeric_tokens(raw) - allowed_numeric_tokens)
    return [f"quality:unapproved_numeric_claim:{value}" for value in unexpected]


_FAILURE_CONDITION_RE = re.compile(
    rf"(?:(?P<symbol><=|>=|≤|≥|<|>)\s*"
    rf"(?P<symbol_value>{_DECIMAL_TOKEN})\s*(?P<symbol_unit>{_MEASUREMENT_UNIT})?"
    rf"|(?P<word_value>{_DECIMAL_TOKEN})\s*(?P<word_unit>{_MEASUREMENT_UNIT})?\s*"
    rf"(?P<word_op>이상|이하|초과|미만))"
)
_CELL_NUMBER_RE = re.compile(
    rf"(?P<value>{_DECIMAL_TOKEN})\s*(?P<unit>{_MEASUREMENT_UNIT})?"
)
_FAILURE_STATUS_RE = re.compile(r"(?:실패|부적합|미달|이탈|불합격)")
_SUCCESS_STATUS_RE = re.compile(r"(?:정상|적합|통과|성공|합격|충족)")


def _markdown_cells(line: str) -> list[str]:
    if line.count("|") < 2:
        return []
    parts = line.strip().split("|")
    if parts and not parts[0].strip():
        parts = parts[1:]
    if parts and not parts[-1].strip():
        parts = parts[:-1]
    return [part.strip() for part in parts]


def _is_markdown_separator(cells: Sequence[str]) -> bool:
    return bool(cells) and all(
        re.fullmatch(r":?-{3,}:?", re.sub(r"\s+", "", cell)) for cell in cells
    )


def _single_cell_number(cell: str) -> tuple[Decimal, str] | None:
    matches = list(_CELL_NUMBER_RE.finditer(cell))
    if len(matches) != 1:
        return None
    match = matches[0]
    value = Decimal(match.group("value").replace(",", ""))
    unit = str(match.group("unit") or "").replace("°C", "℃")
    return value, unit


def _failure_condition(cell: str) -> tuple[str, Decimal, str] | None:
    matches = list(_FAILURE_CONDITION_RE.finditer(cell))
    if len(matches) != 1:
        return None
    match = matches[0]
    if match.group("symbol"):
        operator = {"≤": "<=", "≥": ">="}.get(
            match.group("symbol"), match.group("symbol")
        )
        value = match.group("symbol_value")
        unit = match.group("symbol_unit") or ""
    else:
        operator = {
            "이상": ">=",
            "이하": "<=",
            "초과": ">",
            "미만": "<",
        }[match.group("word_op")]
        value = match.group("word_value")
        unit = match.group("word_unit") or ""
    return operator, Decimal(value.replace(",", "")), unit.replace("°C", "℃")


def _failure_condition_holds(actual: Decimal, operator: str, limit: Decimal) -> bool:
    return {
        "<": actual < limit,
        "<=": actual <= limit,
        ">": actual > limit,
        ">=": actual >= limit,
    }[operator]


def _explicit_status(cell: str) -> str | None:
    failed = bool(_FAILURE_STATUS_RE.search(cell))
    passed = bool(_SUCCESS_STATUS_RE.search(cell))
    if failed == passed:
        return None
    return "failure" if failed else "success"


def _table_threshold_status_errors(lines: Sequence[str]) -> list[str]:
    """Check only explicit Markdown failure-threshold/value/status columns."""
    errors: list[str] = []
    index = 0
    while index + 1 < len(lines):
        headers = _markdown_cells(lines[index])
        separator = _markdown_cells(lines[index + 1])
        if (
            not headers
            or len(headers) != len(separator)
            or not _is_markdown_separator(separator)
        ):
            index += 1
            continue
        row_index = index + 2
        while row_index < len(lines):
            cells = _markdown_cells(lines[row_index])
            if not cells or len(cells) != len(headers):
                break
            compact_headers = [re.sub(r"\s+", "", value) for value in headers]
            threshold_indices = [
                position
                for position, header in enumerate(compact_headers)
                if "실패" in header and re.search(r"경계|기준|조건", header)
            ]
            if len(threshold_indices) != 1:
                row_index += 1
                continue
            threshold_index = threshold_indices[0]
            condition = _failure_condition(cells[threshold_index])
            if condition is None:
                row_index += 1
                continue
            operator, limit, threshold_unit = condition
            actuals: list[Decimal] = []
            statuses: list[str] = []
            for position, (header, cell) in enumerate(zip(compact_headers, cells)):
                if position == threshold_index:
                    continue
                if re.search(r"판정|상태|평가|결과", header):
                    status = _explicit_status(cell)
                    if status:
                        statuses.append(status)
                if re.search(r"실측|측정|실제|현재|기존|변경|결과|값", header):
                    parsed = _single_cell_number(cell)
                    if parsed is None:
                        continue
                    actual, actual_unit = parsed
                    if threshold_unit and actual_unit and threshold_unit != actual_unit:
                        continue
                    actuals.append(actual)
            if len(set(statuses)) != 1 or not actuals:
                row_index += 1
                continue
            outcomes = {
                _failure_condition_holds(actual, operator, limit) for actual in actuals
            }
            if len(outcomes) == 1:
                threshold_failed = next(iter(outcomes))
                status_failed = statuses[0] == "failure"
                if threshold_failed != status_failed:
                    errors.append(
                        f"quality:failure_threshold_status_mismatch:line={row_index + 1}"
                    )
            row_index += 1
        index = max(index + 1, row_index)
    return errors


def _claimed_precision_tolerance(raw: str) -> Decimal:
    decimals = len(raw.partition(".")[2])
    return Decimal("0.5").scaleb(-decimals) + Decimal("0.0001")


def _explicit_ratio_errors(body: str) -> list[str]:
    """Reject only locally explicit percentage-to-multiple contradictions."""
    errors: list[str] = []
    percent_re = re.compile(rf"(?P<value>{_DECIMAL_TOKEN})\s*%")
    multiple_re = re.compile(rf"(?P<value>{_DECIMAL_TOKEN})\s*배")
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        percents = list(percent_re.finditer(sentence))
        multiples = list(multiple_re.finditer(sentence))
        if len(percents) != 2 or len(multiples) != 1:
            continue
        relation_text = sentence[percents[0].start() : multiples[0].end()]
        if not re.search(r"의|대비|보다", relation_text):
            continue
        if multiples[0].start() - percents[-1].end() > 40:
            continue
        first = Decimal(percents[0].group("value").replace(",", ""))
        second = Decimal(percents[1].group("value").replace(",", ""))
        if first == 0 or second == 0:
            continue
        claimed_raw = multiples[0].group("value").replace(",", "")
        claimed = Decimal(claimed_raw)
        expected = (abs(first / second), abs(second / first))
        tolerance = _claimed_precision_tolerance(claimed_raw)
        if min(abs(claimed - value) for value in expected) > tolerance:
            errors.append(f"quality:derived_ratio_mismatch:{claimed}")
            break
    return errors


def _actual_publication_proven(body: str) -> bool:
    """Return true only for unrestricted access to this completed whole document."""
    for passage in re.split(r"\n\s*\n|(?<=[.!?])\s+", body):
        same_document = re.search(
            r"동일한\s*(?:전체\s*)?내용|내용이\s*동일한\s*(?:전체\s*)?문서|"
            r"(?:이|해당|본)\s*(?:문서|자료|파일)\s*(?:전체|전문)|"
            r"본문\s*전체|전체\s*동일본",
            passage,
        )
        public_channel = re.search(
            r"공식(?:\s*(?:기관|회사))?\s*"
            r"(?:웹\s*페이지|홈페이지|대외\s*채널|외부\s*사이트)",
            passage,
        )
        unrestricted = re.search(
            r"누구나|불특정\s*다수|일반인",
            passage,
        ) and re.search(r"열람|다운로드|접근", passage)
        no_control = re.search(
            r"(?:로그인|승인|권한|등록)"
            r"(?:[\s·,와과이나또는]*(?:로그인|승인|권한|등록))*\s*없이|"
            r"접근\s*제한(?:이|은)?\s*없",
            passage,
        )
        if same_document and public_channel and unrestricted and no_control:
            return True
    return False


def _document_access_errors(doc: object, *, expected_secrecy: int) -> list[str]:
    """Apply the actual-public/nonpublic contract from S, never from the grade."""
    if isinstance(expected_secrecy, bool) or expected_secrecy not in {0, 1, 2}:
        raise ValueError("expected_secrecy must be 0, 1, or 2")
    body = str(getattr(doc, "body", "") or "")
    proven_public = _actual_publication_proven(body)
    if expected_secrecy == 0 and not proven_public:
        return ["quality:public_document_not_actually_published"]
    if expected_secrecy > 0 and proven_public:
        return ["quality:nonpublic_document_explicitly_public"]
    return []


def _nonpublic_document_access_errors(doc: object, *, expected_label: str) -> list[str]:
    """Compatibility wrapper for the historical four-grade pilot tests."""
    expected_secrecy = 0 if expected_label == "S3" else 1
    errors = _document_access_errors(doc, expected_secrecy=expected_secrecy)
    if expected_label == "S3":
        return []
    return errors


def _document_completion_errors(doc: object) -> list[str]:
    """Catch deterministic table truncation and explicit subtraction errors."""
    body = str(getattr(doc, "body", "") or "")
    lines = body.splitlines()
    table_rows: list[tuple[int, list[str]]] = []
    for index, line in enumerate(lines):
        if line.count("|") < 2:
            continue
        parts = line.strip().split("|")
        if parts and not parts[0].strip():
            parts = parts[1:]
        if parts and not parts[-1].strip():
            parts = parts[:-1]
        cells = [part.strip() for part in parts]
        if len(cells) >= 2:
            table_rows.append((index, cells))

    errors: list[str] = []
    placeholder_cells = {"", "-", "—", "–"}
    for _, cells in table_rows:
        compact_cells = [re.sub(r"\s+", "", cell) for cell in cells]
        separator_row = all(re.fullmatch(r":?-{3,}:?", cell) for cell in compact_cells)
        if not separator_row and any(cell in placeholder_cells for cell in cells):
            errors.append("quality:table_blank_or_dash_cell")
            break
    if table_rows:
        closing_text = "\n".join(lines[table_rows[-1][0] + 1 :]).strip()
        has_closing_section = len(closing_text) >= 60 and re.search(
            r"결론|후속\s*조치|다음\s*조치|조치\s*계획|권고|결정\s*사항",
            closing_text,
        )
        if not has_closing_section:
            errors.append("quality:table_missing_closing_section")
    errors.extend(_table_threshold_status_errors(lines))
    errors.extend(_explicit_ratio_errors(body))

    unit = r"(?:℃|°C|%|원|일|주|개월|시간|분|초|건|명|회|개|배|ms|MPa)"
    transition_re = re.compile(
        rf"(?P<before>\d+(?:\.\d+)?)\s*(?P<before_unit>{unit})?\s*"
        rf"(?:에서|→|->)\s*(?:변경(?:된|한|하여)?\s*)?"
        rf"(?P<after>\d+(?:\.\d+)?)\s*(?P<after_unit>{unit})?"
    )
    claim_before_re = re.compile(
        rf"(?:차이|변동\s*폭|증가분|감소분)\s*(?:은|는|이|가|:)?\s*"
        rf"(?P<claimed>\d+(?:\.\d+)?)\s*(?P<claim_unit>{unit})?"
    )
    claim_after_re = re.compile(
        rf"(?P<claimed>\d+(?:\.\d+)?)\s*(?P<claim_unit>{unit})?\s*"
        rf"(?:이상\s*|이하\s*)?(?:차이|변동|증가|감소)(?!\s*(?:시|경우))"
    )
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", body):
        transitions = list(transition_re.finditer(sentence))
        claims = list(claim_before_re.finditer(sentence)) + list(
            claim_after_re.finditer(sentence)
        )
        if len(transitions) != 1 or len(claims) != 1:
            continue
        transition = transitions[0]
        claim = claims[0]
        transition_unit = transition.group("after_unit") or transition.group(
            "before_unit"
        )
        claim_unit = claim.group("claim_unit")
        if claim_unit == "%" and transition_unit != "%":
            continue
        if transition_unit and claim_unit and transition_unit != claim_unit:
            continue
        expected = abs(
            Decimal(transition.group("after")) - Decimal(transition.group("before"))
        )
        claimed = Decimal(claim.group("claimed"))
        if expected != claimed:
            errors.append(f"quality:derived_difference_mismatch:{claimed}!={expected}")
            break
    return errors


def _validate_generated_proxy_record(record: Mapping[str, object]) -> object:
    """Validate against the catalog's immutable train/evaluation boundary."""
    try:
        intended_use = proxy_record_intended_use(record)
    except ValueError as exc:
        raise ProxyGenerationRunError(str(exc)) from exc
    return validate_proxy_record(record, intended_use=intended_use)


def _bounded_retry_draft(
    doc: object, *, max_chars: int = _RETRY_DRAFT_MAX_CHARS
) -> str:
    """Carry useful prior work into a quality retry without target-label leakage."""
    if max_chars <= 0:
        return ""
    raw = "\n\n".join(
        part
        for part in (
            str(getattr(doc, "title", "") or "").strip(),
            str(getattr(doc, "body", "") or "").strip(),
        )
        if part
    )
    # A failed model response can itself contain an illicit target marker.  It
    # must not become a target-specific hint on the next call.
    sanitized = _DIRECT_LABEL_RE.sub("[분류 표기 제거]", raw)
    sanitized = _CLASSIFICATION_STYLE_MARKER_RE.sub("[머리말 표기 제거]", sanitized)
    if len(sanitized) <= max_chars:
        return sanitized
    separator = "\n\n[중간 생략]\n\n"
    head_chars = max(1, (max_chars - len(separator)) * 2 // 3)
    tail_chars = max_chars - head_chars - len(separator)
    return (
        sanitized[:head_chars].rstrip() + separator + sanitized[-tail_chars:].lstrip()
    )


def _retry_problem_summary(errors: Sequence[object]) -> str:
    """Describe retry causes without echoing a grade-bearing validator string."""
    categories: list[str] = []
    for raw in errors:
        error = str(raw)
        if "profile_too_short" in error or error.startswith("too_short:"):
            category = "요청한 본문 분량 미달"
        elif "profile_too_long" in error or error.startswith("too_long:"):
            category = "요청한 본문 분량 초과"
        elif "classification_style_marker" in error or "direct_grade_marker" in error:
            category = "제목 또는 머리말의 분류형 꼬리표"
        elif "duplicate" in error or "unique_" in error or "max_alnum" in error:
            category = "반복 또는 낮은 내용 다양성"
        elif "blocks" in error:
            category = "문서 절과 단락 구조 부족"
        elif "numeric_facts" in error:
            category = "검산 가능한 기초 수치 부족"
        elif "derived_difference_mismatch" in error:
            category = "변경 전후 차이값 산술 불일치"
        elif "derived_ratio_mismatch" in error:
            category = "비율 또는 배수 계산 불일치"
        elif "failure_threshold_status_mismatch" in error:
            category = "실패 경계와 측정값·판정 불일치"
        elif "nonpublic_document_explicitly_public" in error:
            category = "비공개 문서의 외부 공개 서술 모순"
        elif "public_document_not_actually_published" in error:
            category = "공개 문서의 실제 전체 공표 근거 누락"
        elif "unapproved_numeric_claim" in error:
            category = "입력 근거가 없는 수치·날짜·범위 또는 비율 포함"
        elif "fact_ledger_" in error:
            category = "코드 산출 사실 원장의 값·판정 누락 또는 상충"
        elif "reconstruction_detail" in error:
            category = "경제적 유용성 없음 조건과 모순되는 재현 가능 경계값 포함"
        elif "table_blank_or_dash_cell" in error:
            category = "표의 빈 셀 또는 대시 placeholder"
        elif "table_missing_closing_section" in error:
            category = "마지막 표 뒤 결론과 후속 조치 누락"
        elif error.startswith("generator_exception"):
            category = "이전 생성 호출 실패"
        else:
            category = "후보 품질 기준 미달"
        if category not in categories:
            categories.append(category)
    return ", ".join(categories) or "후보 품질 기준 미달"


def _retry_revision_context(errors: Sequence[object], draft: str) -> str:
    summary = _retry_problem_summary(errors)
    numeric_guard_failed = any(
        "unapproved_numeric_claim" in str(error) for error in errors
    )
    instructions = (
        f"이전 출력의 문제: {summary}. 누락된 절·표·조치와 "
        "새로운 판단 근거를 추가해 문서 전체를 다시 작성한다. 기존 문장을 바꾸어 반복하거나 "
        "끝에 군더더기를 붙여 분량을 채우지 않는다. 차이·비율·합계·절감액은 기초값으로 "
        "다시 계산하고, 맞지 않거나 불필요하면 파생값을 삭제한다."
    )
    if numeric_guard_failed:
        return (
            f"{instructions} 단, 이전 초안의 수치가 허용 입력에 없었다. 이전 초안을 참고하거나 "
            "수정하지 말고 완전히 새로 작성한다. 제목·본문·표에는 아라비아 숫자, 날짜, 금액, "
            "단위, 범위, 비율, 순번을 쓰지 않는다. 정량 사실은 코드가 별도 부록으로 붙인다."
        )
    if not draft:
        return instructions
    return (
        f"{instructions} 아래 초안은 사실 소재를 이어가기 위한 참고일 뿐 정답이나 확정 "
        f"수치가 아니다. 유효한 기초 사실만 유지한다.\n\n"
        f"[이전 초안 원문 — {len(draft)}자 발췌]\n{draft}"
    )


_FACT_LEDGER_METRICS = {
    "technology": (
        ("검증 안정성 점수", "점", 80),
        ("시험 통과 건수", "건", 60),
        ("재현성 평가 점수", "점", 80),
        ("검증 완료율", "%", 70),
        ("품질 적합률", "%", 70),
    ),
    "operations": (
        ("운영 안정성 점수", "점", 80),
        ("처리 완료 건수", "건", 60),
        ("대응 절차 준수율", "%", 70),
        ("서비스 충족률", "%", 70),
        ("후속조치 완료 건수", "건", 60),
    ),
    "commercial": (
        ("대안 적합성 점수", "점", 80),
        ("비교 검토 완료 건수", "건", 60),
        ("협상 준비율", "%", 70),
        ("제안 충족률", "%", 70),
        ("의사결정 검토 건수", "건", 60),
    ),
    "governance": (
        ("검토 완결성 점수", "점", 80),
        ("통제 점검 완료 건수", "건", 60),
        ("조치 이행률", "%", 70),
        ("증거 충족률", "%", 70),
        ("승인 검토 건수", "건", 60),
    ),
}


def _fact_ledger_for_item(
    item: tuple[dict, dict, dict, int],
) -> dict[str, object] | None:
    """Derive grade-safe arithmetic anchors from family and ordinal.

    Counterfactual profiles share the same baseline facts.  Publicity is tied
    to expected secrecy, while validation detail is tied to expected value;
    neither property is inferred from the final grade label.
    """
    scenario, instance_profile, _, _ = item
    contract = str(scenario.get("fact_ledger_contract") or "").strip()
    if not contract:
        return None
    if contract not in {FACT_LEDGER_VERSION, FACT_LEDGER_NUMERIC_GUARD_VERSION}:
        raise ProxyGenerationRunError(f"unsupported fact ledger: {contract}")
    family_id = str(scenario["document_family_id"])
    instance_id = str(instance_profile["instance_profile_id"])
    baseline_key = f"{family_id}:{instance_id}"
    baseline_offset = int(
        hashlib.sha256(baseline_key.encode("utf-8")).hexdigest()[:8], 16
    )
    metric_profiles = _FACT_LEDGER_METRICS.get(
        str(scenario.get("domain") or ""), _FACT_LEDGER_METRICS["governance"]
    )
    metric_name, unit, baseline_floor = metric_profiles[
        baseline_offset % len(metric_profiles)
    ]
    baseline = baseline_floor + (baseline_offset // 5) % 15
    magnitude = 6 + 2 * ((baseline_offset // 11) % 5)
    signed_delta = magnitude if (baseline_offset // 17) % 2 == 0 else -magnitude
    revised = baseline + signed_delta
    rate = (Decimal(abs(signed_delta)) * 100 / Decimal(baseline)).quantize(
        Decimal("0.1")
    )
    start_week = 1 + (baseline_offset // 23) % 5
    scores = scenario.get("expected_factor_scores")
    if not isinstance(scores, Mapping):
        raise ProxyGenerationRunError("scenario expected_factor_scores are invalid")
    secrecy = int(scores["secrecy"])
    value = int(scores["value"])
    if secrecy == 0:
        profile = "public_validation" if value > 0 else "public_aggregate"
    else:
        profile = "internal_validation" if value > 0 else "nonvaluable_aggregate"
    ledger: dict[str, object] = {
        "schema": FACT_LEDGER_VERSION,
        "profile": profile,
        "expected_secrecy": secrecy,
        "expected_value": value,
        "matched_baseline_key": baseline_key,
        "metric_name": metric_name,
        "unit": unit,
        "before": baseline,
        "after": revised,
        "absolute_difference": abs(signed_delta),
        "change_direction": "증가" if signed_delta > 0 else "감소",
        "change_rate_percent": format(rate, "f"),
        "timeline": {
            "착수": f"{start_week}주차",
            "변경": f"{start_week + 1}주차",
            "검증": f"{start_week + 2}주차",
            "후속조치": f"{start_week + 3}주차",
        },
    }
    if value > 0:
        lower = baseline - 10
        upper = baseline + 10
        if revised < lower:
            status = "하한 미만"
        elif revised > upper:
            status = "상한 초과"
        else:
            status = "정상 범위"
        ledger.update(
            {
                "normal_lower": lower,
                "normal_upper": upper,
                "observed": revised,
                "status": status,
            }
        )
    return ledger


def _fact_ledger_block(ledger: Mapping[str, object]) -> str:
    """Render the deterministic, document-visible ledger appendix."""
    timeline = ledger["timeline"]
    if not isinstance(timeline, Mapping):
        raise ProxyGenerationRunError("fact ledger timeline is invalid")
    lines = [
        "[사실 검산]",
        f"- 지표명: {ledger['metric_name']}",
        f"- 변경 전: {ledger['before']}{ledger['unit']} · 변경 후: "
        f"{ledger['after']}{ledger['unit']} · 절대 차이: "
        f"{ledger['absolute_difference']}{ledger['unit']}",
        f"- {ledger['change_direction']}율: {ledger['change_rate_percent']}%",
        "- 시간 순서: "
        + " → ".join(
            f"{stage} {timeline[stage]}"
            for stage in ("착수", "변경", "검증", "후속조치")
        ),
    ]
    if "normal_lower" in ledger:
        lines.append(
            f"- 정상 범위: {ledger['normal_lower']}~{ledger['normal_upper']}"
            f"{ledger['unit']} · 실측값: {ledger['observed']}{ledger['unit']} · "
            f"판정: {ledger['status']}"
        )
    return "\n".join(lines)


def _fact_ledger_prompt(ledger: Mapping[str, object]) -> str:
    lines = [
        "[코드 참조 사실 — 출력 금지]",
        "코드가 생성 뒤 유일한 검산 부록을 붙인다. 이 제목·목록·태그·검산용 문구는 제목이나 "
        "본문에 복사하지 않는다.",
        "- 본문 표와 서술에는 검산 지표명, 변경 전·후 값, 차이, 증감률, 정상 범위, 실측값, "
        "판정, 주차를 새로 쓰거나 계산하지 않는다. 그 값은 코드 부록에만 남긴다.",
        "- 제목과 본문에는 숫자·날짜·금액·범위·비율·순번을 전혀 쓰지 않는다. 원장에 없는 "
        "합계·평균·비율·배수·차이·예측값도 추가하지 않는다.",
        "- 카탈로그나 시나리오의 일정·예산은 정성적인 업무 맥락으로만 유지한다. 그 수치를 "
        "본문에 옮기거나 원장 계산에 섞거나 새 파생값의 근거로 사용하지 않는다.",
        "- 생성 뒤 코드는 별도 검산 부록을 자동으로 붙인다. 그 부록의 제목이나 행을 작성하지 않는다.",
    ]
    is_public = int(ledger["expected_secrecy"]) == 0
    is_nonvaluable = int(ledger["expected_value"]) == 0
    if is_public:
        lines.append(
            "- 이 원장의 수치와 문서 전체가 이미 실제 공표된 사실임을 본문에서 분명히 한다."
        )
    else:
        lines.append(
            "- 이 원장은 비공개 문맥의 사실이며, 공개되었거나 누구나 열람한다고 바꾸지 않는다."
        )
    if "normal_lower" in ledger:
        lines.append(
            "- 정상 범위·실측값·판정에 관한 구체값이나 관계도 본문에 재구성하지 않는다. "
            "공개·비공개 상태는 시나리오의 관찰 가능한 업무 사실만으로 서술한다."
        )
    if is_nonvaluable:
        lines.extend(
            [
                "- 경제적 가치가 없는 집계의 전후 변화와 일정만 쓴다.",
                "- 실패 경계, 정상 범위, 내부 임계값, 재현 가능한 공정·가격·모델 "
                "파라미터를 추가하지 않는다.",
            ]
        )
    return "\n".join(lines)


def _number_pattern(value: object) -> str:
    raw = str(value)
    if raw.endswith(".0"):
        return re.escape(raw[:-2]) + r"(?:\.0)?"
    return re.escape(raw)


def _normalized_unit(raw: str | None) -> str:
    return (raw or "").replace("°C", "℃").replace("μm", "㎛")


_LEDGER_VALUE_LINK = (
    r"[ \t|:=]*(?:(?:기준(?:값|수치)?|지표(?:값|수치)?|측정값|결과값|평균|"
    r"합계|점수|건수|비율|값|수치)[ \t]*)?"
    r"(?:에는|에서|은|는|이|가|을|를)?[ \t|:=]*"
)


def _numeric_anchor_mentions(body: str, anchor: str) -> list[tuple[Decimal, str]]:
    pattern = re.compile(
        rf"(?:{anchor}){_LEDGER_VALUE_LINK}"
        rf"(?P<value>{_DECIMAL_TOKEN})\s*(?P<unit>{_MEASUREMENT_UNIT})?"
        # ``변경 후 7주차`` is a time expression, not an after-value of 7.
        # Without this guard the optional measurement unit is empty and a
        # correct Korean timeline becomes a false fact-ledger conflict.
        rf"(?!\s*(?:주차|주째|주간|개월|영업일|일차))"
    )
    return [
        (
            Decimal(match.group("value").replace(",", "")),
            _normalized_unit(match.group("unit")),
        )
        for match in pattern.finditer(body)
    ]


def _numeric_anchor_has_conflict(
    mentions: Sequence[tuple[Decimal, str]],
    *,
    expected_value: object,
    expected_unit: object,
) -> bool:
    expected_number = Decimal(str(expected_value).replace(",", ""))
    normalized_expected_unit = _normalized_unit(str(expected_unit))
    return any(
        value != expected_number or (unit != "" and unit != normalized_expected_unit)
        for value, unit in mentions
    )


def _normal_range_mentions(body: str) -> list[tuple[Decimal, Decimal, str, str]]:
    pattern = re.compile(
        rf"정상\s*범위{_LEDGER_VALUE_LINK}"
        rf"(?P<lower>{_DECIMAL_TOKEN})\s*(?P<lower_unit>{_MEASUREMENT_UNIT})?\s*"
        rf"(?:~|∼|에서|부터)\s*"
        rf"(?P<upper>{_DECIMAL_TOKEN})\s*(?P<upper_unit>{_MEASUREMENT_UNIT})?"
    )
    return [
        (
            Decimal(match.group("lower").replace(",", "")),
            Decimal(match.group("upper").replace(",", "")),
            _normalized_unit(match.group("lower_unit")),
            _normalized_unit(match.group("upper_unit")),
        )
        for match in pattern.finditer(body)
    ]


_LEDGER_STATUS_TOKEN = (
    r"(?:하한\s*미만|상한\s*초과|정상\s*범위|실패|부적합|미달|이탈|불합격|"
    r"적합|통과|성공|합격|충족|정상)"
)


def _scoped_ledger_status_mentions(body: str, *, metric_name: str) -> list[str]:
    """Return only verdicts attached to this ledger's measurement context.

    Realistic documents contain unrelated checklist verdicts such as
    ``품질점검 판정: 통과``.  Treating every occurrence of ``판정`` as the
    fact-ledger verdict would reject valid documents.  A verdict is in scope
    only when its sentence/table row also contains the ledger metric or one of
    the measurement anchors used by the ledger contract.
    """
    boundary_spans = [
        match.span() for match in re.finditer(r"(?<!\d)[.!?](?!\d)|[;\n]", body)
    ]
    mentions: list[str] = []
    for match in re.finditer(
        rf"판정[^\n]{{0,20}}?(?P<status>{_LEDGER_STATUS_TOKEN})", body
    ):
        start = max(
            (end for _, end in boundary_spans if end <= match.start()), default=0
        )
        end = min(
            (begin for begin, _ in boundary_spans if begin >= match.end()),
            default=len(body),
        )
        # Remove the verdict itself so "판정: 정상 범위" cannot use its own
        # status words as the nearby normal-range evidence.
        context = body[start : match.start()] + body[match.end() : end]
        scoped = metric_name in context or bool(
            re.search(r"(?:실측|측정)(?:값|\s*결과)?|정상\s*범위", context)
        )
        if scoped:
            mentions.append(re.sub(r"\s+", "", match.group("status")))
    return mentions


_TIMELINE_STAGE_PATTERNS = {
    "착수": r"착수",
    "변경": r"변경",
    "검증": r"검증",
    "후속조치": r"후속\s*조치",
}


def _timeline_stage_mentions(
    body: str, stage: str, *, ignored_metric_name: str = ""
) -> list[Decimal]:
    r"""Read a ledger week in the two ordinary Korean word orders.

    The forward form is intentionally narrow.  A permissive pattern such as
    ``검증[^\d]{0,20}\d+주차`` can attach a later follow-up week to ``검증
    완료 후``.  Reverse forms allow only the short particles commonly found
    between ``6주차`` and ``조건 변경``.
    """
    if ignored_metric_name:
        body = body.replace(ignored_metric_name, "[지표]")
    stage_pattern = _TIMELINE_STAGE_PATTERNS[stage]
    if stage == "검증":
        stage_pattern = rf"(?<!재){stage_pattern}"
    patterns = (
        (
            rf"(?:{stage_pattern})(?!\s*(?:전|후))\s*"
            # A bare topic particle is ambiguous: ``조건 변경은 4주차에
            # 검증`` means the change happened earlier and was *verified* in
            # week 4.  Accept ``변경 3주차`` or an explicit
            # ``변경 일정은 3주차``, not ``변경은 4주차``.
            rf"(?:(?:시점|일정)\s*(?:은|는|이|가)?\s*)?[:：-]?\s*"
            rf"(?P<week>\d+)\s*주차"
        ),
        (
            rf"(?P<week>\d+)\s*주차(?:에|에는|부터)?\s*"
            rf"(?:조건(?:을|이)?\s*)?(?:{stage_pattern})"
        ),
    )
    return [
        Decimal(match.group("week"))
        for pattern in patterns
        for match in re.finditer(pattern, body)
    ]


def _natural_ledger_value_checks(
    body: str, ledger: Mapping[str, object]
) -> dict[str, bool]:
    """Recognize exact ledger facts expressed in normal Korean prose.

    These alternatives never infer a value.  They interpolate every expected
    number and unit into the regex, so accepting a natural word order does not
    weaken the fail-closed equality contract.
    """
    unit = re.escape(str(ledger["unit"]))
    before = _number_pattern(ledger["before"])
    after = _number_pattern(ledger["after"])
    difference = _number_pattern(ledger["absolute_difference"])
    direction = re.escape(str(ledger["change_direction"]))
    rate = _number_pattern(ledger["change_rate_percent"])

    transition_patterns = (
        # Compact transition: ``73%에서 59%로`` / ``73점 → 59점``.
        rf"{before}\s*{unit}\s*(?:에서|→|->|대비)\s*"
        rf"{after}\s*{unit}(?:로|으로)?",
        # Narrative transition: ``기존 92점이었으나, 변경안 적용 후 80점``.
        rf"(?:기존|종전|당초)[^\d\n]{{0,20}}{before}\s*{unit}"
        rf"[^\n.!?]{{0,60}}?(?:변경안?\s*(?:적용\s*)?후|조정\s*후|개선\s*후)"
        rf"[^\d\n]{{0,12}}{after}\s*{unit}",
    )
    transition = any(re.search(pattern, body) for pattern in transition_patterns)
    return {
        "before": transition,
        "after": transition,
        # ``14% 감소 (감소율 19.2%)`` states both absolute movement and
        # relative rate without using the literal word ``차이``.
        "difference": bool(
            re.search(
                rf"{difference}\s*{unit}\s*(?:포인트\s*)?{direction}(?!율)", body
            )
        ),
        # A model may put direction after a percentage rather than writing
        # ``감소율``.  Only the exact computed rate is accepted here.
        "rate": bool(
            re.search(rf"{rate}\s*%\s*(?:가량\s*)?{direction}(?!율)", body)
        ),
    }


_LEDGER_TABLE_FIELDS = frozenset(
    {"metric", "before", "after", "difference", "normal_range", "observed", "status"}
)


def _normalized_ledger_header_cell(cell: str) -> str:
    # Units and short explanatory notes in parentheses do not change a column's
    # semantic role: ``변경 전(%)`` is still the before column.
    without_notes = re.sub(r"\([^)]*\)|\[[^]]*\]", "", cell)
    return re.sub(r"[\s:：_/·-]+", "", without_notes).casefold()


def _ledger_header_roles(cell: str) -> set[str]:
    normalized = _normalized_ledger_header_cell(cell)
    if normalized in {"변경전후", "적용전후", "기존변경안", "종전변경후"}:
        return {"before", "after"}
    patterns = {
        "metric": r"(?:측정지표|평가지표|지표명?|항목|구분)",
        "before": r"(?:변경전|적용전|기존|종전)(?:값|수치|점수|결과)?",
        "after": r"(?:변경후|적용후|변경안|개선후)(?:값|수치|점수|결과)?",
        "difference": r"(?:절대차이|차이값?|증감값?|변동폭)",
        "normal_range": r"(?:정상범위|허용범위|기준범위)",
        "observed": r"(?:실측값?|측정값|측정결과|관찰값|검증값|검증결과)",
        "status": r"(?:판정(?:결과)?|결과상태|상태)",
    }
    return {
        field for field, pattern in patterns.items() if re.search(pattern, normalized)
    }


def _ledger_header_mapping(
    cells: Sequence[str],
) -> tuple[dict[str, int], set[str], bool]:
    """Map semantic ledger fields to columns and expose ambiguity explicitly."""
    mapping: dict[str, int] = {}
    ambiguous: set[str] = set()
    recognized = False
    for index, cell in enumerate(cells):
        roles = _ledger_header_roles(cell)
        if not roles:
            continue
        recognized = True
        if len(roles) != 1:
            ambiguous.update(roles)
            continue
        role = next(iter(roles))
        if role in mapping:
            ambiguous.add(role)
        else:
            mapping[role] = index
    for role in ambiguous:
        mapping.pop(role, None)
    return mapping, ambiguous, recognized


def _ledger_numeric_cell(cell: str) -> tuple[Decimal, str] | None:
    match = re.fullmatch(
        rf"\s*(?P<value>{_DECIMAL_TOKEN})\s*(?P<unit>{_MEASUREMENT_UNIT})?\s*",
        cell,
    )
    if not match:
        return None
    return (
        Decimal(match.group("value").replace(",", "")),
        _normalized_unit(match.group("unit")),
    )


def _ledger_range_cell(
    cell: str,
) -> tuple[Decimal, Decimal, str, str] | None:
    match = re.fullmatch(
        rf"\s*(?P<lower>{_DECIMAL_TOKEN})\s*"
        rf"(?P<lower_unit>{_MEASUREMENT_UNIT})?\s*(?:~|∼|에서|부터)\s*"
        rf"(?P<upper>{_DECIMAL_TOKEN})\s*"
        rf"(?P<upper_unit>{_MEASUREMENT_UNIT})?\s*(?:까지)?\s*",
        cell,
    )
    if not match:
        return None
    return (
        Decimal(match.group("lower").replace(",", "")),
        Decimal(match.group("upper").replace(",", "")),
        _normalized_unit(match.group("lower_unit")),
        _normalized_unit(match.group("upper_unit")),
    )


def _metric_table_row_evidence(
    body: str, ledger: Mapping[str, object]
) -> tuple[dict[str, bool], set[str]]:
    """Validate metric table cells by their explicit header, never by position.

    Unitless values remain valid only inside a uniquely mapped row naming the
    exact metric.  Reordered columns therefore work, while duplicate/ambiguous
    headers, column-count drift, and wrong explicit units fail closed.
    """
    passed = {
        "before": False,
        "after": False,
        "difference": False,
        "normal_range": False,
        "observed": False,
        "status": False,
    }
    conflicts: set[str] = set()
    metric_name = str(ledger["metric_name"])
    expected_unit = _normalized_unit(str(ledger["unit"]))
    expected_numbers: dict[str, Decimal] = {
        "before": Decimal(str(ledger["before"])),
        "after": Decimal(str(ledger["after"])),
        "difference": Decimal(str(ledger["absolute_difference"])),
    }
    if "observed" in ledger:
        expected_numbers["observed"] = Decimal(str(ledger["observed"]))
    expected_status = re.sub(r"\s+", "", str(ledger.get("status") or ""))

    lines = body.splitlines()
    table_headers = [
        index
        for index in range(len(lines) - 1)
        if _markdown_cells(lines[index])
        and _is_markdown_separator(_markdown_cells(lines[index + 1]))
    ]
    for header_position, header_index in enumerate(table_headers):
        header_cells = _markdown_cells(lines[header_index])
        separator_cells = _markdown_cells(lines[header_index + 1])
        header_mapping, header_ambiguous, recognized = _ledger_header_mapping(
            header_cells
        )
        if not recognized:
            continue
        data_fields = set(header_mapping) - {"metric"}
        ambiguous_data_fields = header_ambiguous - {"metric"}
        transition_complete = (
            {"before", "after", "difference"} <= set(header_mapping)
            and not ({"before", "after", "difference"} & header_ambiguous)
        )
        validation_complete = (
            {"normal_range", "observed", "status"} <= set(header_mapping)
            and not ({"normal_range", "observed", "status"} & header_ambiguous)
        )
        next_header = (
            table_headers[header_position + 1]
            if header_position + 1 < len(table_headers)
            else len(lines)
        )
        for row_index in range(header_index + 2, next_header):
            cells = _markdown_cells(lines[row_index])
            if not cells:
                break
            if _is_markdown_separator(cells):
                break
            metric_columns = [
                cell_index
                for cell_index, cell in enumerate(cells)
                if metric_name in cell
            ]
            if not metric_columns:
                continue

            schema_fields = data_fields | ambiguous_data_fields
            schema_invalid = (
                len(header_cells) != len(separator_cells)
                or len(header_cells) != len(cells)
                or bool(header_ambiguous)
                or "metric" not in header_mapping
                or len(metric_columns) != 1
                or (
                    "metric" in header_mapping
                    and metric_columns[0] != header_mapping["metric"]
                )
            )
            if schema_invalid:
                conflicts.add("table_schema")
                conflicts.update(schema_fields or set(passed))
                continue

            conflicts.update(ambiguous_data_fields)
            for field in ("before", "after", "difference", "observed"):
                if field not in header_mapping:
                    continue
                parsed = _ledger_numeric_cell(cells[header_mapping[field]])
                if parsed is None or field not in expected_numbers:
                    conflicts.add(field)
                    continue
                value, unit = parsed
                expected_value = expected_numbers[field]
                value_matches = (
                    abs(value) == expected_value
                    if field == "difference"
                    else value == expected_value
                )
                if not value_matches or unit not in {"", expected_unit}:
                    conflicts.add(field)
                elif (
                    field in {"before", "after", "difference"}
                    and transition_complete
                ) or (field == "observed" and validation_complete):
                    passed[field] = True

            if "normal_range" in header_mapping:
                parsed_range = _ledger_range_cell(
                    cells[header_mapping["normal_range"]]
                )
                range_matches = False
                if "normal_lower" in ledger and parsed_range is not None:
                    lower, upper, lower_unit, upper_unit = parsed_range
                    range_matches = (
                        lower == Decimal(str(ledger["normal_lower"]))
                        and upper == Decimal(str(ledger["normal_upper"]))
                        and lower_unit in {"", expected_unit}
                        and upper_unit in {"", expected_unit}
                    )
                if not range_matches:
                    conflicts.add("normal_range")
                elif validation_complete:
                    passed["normal_range"] = True

            if "status" in header_mapping:
                actual_status = re.sub(
                    r"\s+", "", cells[header_mapping["status"]]
                ).strip(".,;:：")
                if not expected_status or actual_status != expected_status:
                    conflicts.add("status")
                elif validation_complete:
                    passed["status"] = True
    return passed, conflicts


def _ledger_anchor_category_count(text: str) -> int:
    """Count distinct, explicit fact-ledger anchor categories in a sentence."""
    patterns = (
        r"변경\s*전",
        r"변경\s*후",
        r"(?:절대\s*)?차이(?:값)?",
        r"(?:증가율|감소율|증감률|변동률)",
        r"착수\s*\d+\s*주차|\d+\s*주차[^\d\n]{0,12}착수",
        r"변경\s*\d+\s*주차|\d+\s*주차[^\d\n]{0,12}변경",
        r"검증\s*\d+\s*주차|\d+\s*주차[^\d\n]{0,12}검증",
        r"후속\s*조치\s*\d+\s*주차|\d+\s*주차[^\d\n]{0,12}후속\s*조치",
        r"정상\s*범위",
        r"(?:실측|측정)(?:값|\s*결과)?",
        r"판정",
    )
    return sum(bool(re.search(pattern, text)) for pattern in patterns)


def _fact_ledger_conflict_scope(body: str, *, metric_name: str) -> str:
    """Return only prose that can honestly refer to this ledger's metric.

    Presence checks intentionally inspect the whole document.  Conflict checks
    cannot: realistic process reports contain other metrics (for example
    ``압력 변동률 18%``) whose correct value must not be compared with this
    ledger's completion-rate anchor.  The authoritative conflict scope is an
    explicit ``[사실 검산]`` block, a sentence naming the exact metric, or a
    strongly anchored continuation such as a separate contradictory record.
    """
    selected: list[str] = []
    lines = body.splitlines()
    fact_lines_remaining = 0
    for line in lines:
        stripped = line.strip()
        if re.search(r"\[\s*사실\s*검산\s*\]", stripped):
            selected.append(line)
            # Four mandatory rows plus one optional normal-range row.
            fact_lines_remaining = 5
            continue
        if fact_lines_remaining:
            if not stripped or (
                stripped.startswith("[") and "사실 검산" not in stripped
            ):
                fact_lines_remaining = 0
            else:
                selected.append(line)
                fact_lines_remaining -= 1
                continue

        sentence_parts = [
            part.strip()
            for part in re.split(r"(?<!\d)[.!?](?!\d)|;", line)
            if part.strip()
        ]
        metric_seen = False
        for sentence in sentence_parts:
            if metric_name in sentence:
                selected.append(sentence)
                metric_seen = True
                continue
            if not metric_seen:
                continue
            anchor_count = _ledger_anchor_category_count(sentence)
            explicit_correction = bool(
                re.match(
                    r"(?:별도\s*(?:메모|기록)|다른\s*(?:메모|기록)|반면|그러나)",
                    sentence,
                )
            )
            if anchor_count >= 2 or (explicit_correction and anchor_count >= 1):
                selected.append(sentence)
    return "\n".join(selected)


def _fact_ledger_errors(doc: object, ledger: Mapping[str, object]) -> list[str]:
    body = str(getattr(doc, "body", "") or "")
    unit = re.escape(str(ledger["unit"]))
    expected_rate_label = f"{ledger['change_direction']}율"
    conflict_body = _fact_ledger_conflict_scope(
        body, metric_name=str(ledger["metric_name"])
    )
    natural_checks = _natural_ledger_value_checks(body, ledger)
    table_checks, table_conflicts = _metric_table_row_evidence(body, ledger)
    checks: dict[str, bool] = {
        "metric_name": str(ledger["metric_name"]) in body,
        "before": bool(
            re.search(
                rf"변경\s*전[^\d\n]{{0,24}}{_number_pattern(ledger['before'])}\s*{unit}",
                body,
            )
        )
        or natural_checks["before"]
        or table_checks["before"],
        "after": bool(
            re.search(
                rf"변경\s*후[^\d\n]{{0,24}}{_number_pattern(ledger['after'])}\s*{unit}",
                body,
            )
        )
        or natural_checks["after"]
        or table_checks["after"],
        "difference": bool(
            re.search(
                rf"(?:절대\s*)?차이(?:값)?[^\d\n]{{0,24}}"
                rf"{_number_pattern(ledger['absolute_difference'])}\s*{unit}",
                body,
            )
        )
        or natural_checks["difference"]
        or table_checks["difference"],
        "rate": bool(
            re.search(
                rf"{re.escape(expected_rate_label)}[^\d\n]{{0,24}}"
                rf"{_number_pattern(ledger['change_rate_percent'])}\s*%",
                body,
            )
        )
        or natural_checks["rate"],
    }
    timeline = ledger.get("timeline")
    if not isinstance(timeline, Mapping):
        return ["quality:fact_ledger_mismatch:timeline"]
    for stage in ("착수", "변경", "검증", "후속조치"):
        expected_week = Decimal(re.sub(r"\D", "", str(timeline[stage])))
        checks[f"timeline_{stage}"] = expected_week in _timeline_stage_mentions(
            body, stage, ignored_metric_name=str(ledger["metric_name"])
        )
    if "normal_lower" in ledger:
        expected_status = re.sub(r"\s+", "", str(ledger["status"]))
        status_mentions = _scoped_ledger_status_mentions(
            body, metric_name=str(ledger["metric_name"])
        )
        checks.update(
            {
                "normal_range": bool(
                    re.search(
                        rf"정상\s*범위[^\d\n]{{0,20}}"
                        rf"{_number_pattern(ledger['normal_lower'])}\s*(?:~|∼|에서)\s*"
                        rf"{_number_pattern(ledger['normal_upper'])}\s*{unit}",
                        body,
                    )
                )
                or table_checks["normal_range"],
                "observed": bool(
                    re.search(
                        rf"(?:실측|측정)(?:값|\s*결과)?[^\d\n]{{0,20}}"
                        rf"{_number_pattern(ledger['observed'])}\s*{unit}",
                        body,
                    )
                )
                or table_checks["observed"],
                "status": expected_status in status_mentions
                or table_checks["status"],
            }
        )
    errors = [
        f"quality:fact_ledger_mismatch:{field}"
        for field, passed in checks.items()
        if not passed
    ]
    errors.extend(
        f"quality:fact_ledger_conflict:{field}" for field in sorted(table_conflicts)
    )

    # Presence alone is insufficient: v7 documents sometimes repeated a
    # correct table value and then contradicted it in prose.  Inspect every
    # explicit anchor mention and fail closed if any value differs from the
    # deterministic ledger, even when another occurrence is correct.
    numeric_anchors = {
        "before": (r"변경\s*전(?!후)", ledger["before"], ledger["unit"]),
        "after": (r"변경\s*후", ledger["after"], ledger["unit"]),
        "difference": (
            r"(?:절대\s*)?차이(?:값)?",
            ledger["absolute_difference"],
            ledger["unit"],
        ),
    }
    for field, (anchor, expected_value, expected_unit) in numeric_anchors.items():
        mentions = _numeric_anchor_mentions(conflict_body, anchor)
        if _numeric_anchor_has_conflict(
            mentions,
            expected_value=expected_value,
            expected_unit=expected_unit,
        ):
            errors.append(f"quality:fact_ledger_conflict:{field}")

    rate_mentions = [
        (
            match.group("label"),
            Decimal(match.group("value").replace(",", "")),
            _normalized_unit(match.group("unit")),
        )
        for match in re.finditer(
            rf"(?P<label>증가율|감소율|증감률|변동률){_LEDGER_VALUE_LINK}"
            rf"(?P<value>{_DECIMAL_TOKEN})\s*(?P<unit>{_MEASUREMENT_UNIT})?",
            conflict_body,
        )
    ]
    expected_rate = Decimal(str(ledger["change_rate_percent"]))
    if any(
        label != expected_rate_label
        or value != expected_rate
        or (unit != "" and unit != "%")
        for label, value, unit in rate_mentions
    ):
        errors.append("quality:fact_ledger_conflict:rate")

    for stage in ("착수", "변경", "검증", "후속조치"):
        mentions = _timeline_stage_mentions(
            conflict_body, stage, ignored_metric_name=str(ledger["metric_name"])
        )
        expected_week = Decimal(re.sub(r"\D", "", str(timeline[stage])))
        if any(value != expected_week for value in mentions):
            errors.append(f"quality:fact_ledger_conflict:timeline_{stage}")

    if "normal_lower" in ledger:
        expected_lower = Decimal(str(ledger["normal_lower"]))
        expected_upper = Decimal(str(ledger["normal_upper"]))
        expected_unit = _normalized_unit(str(ledger["unit"]))
        if any(
            lower != expected_lower
            or upper != expected_upper
            or (lower_unit != "" and lower_unit != expected_unit)
            or (upper_unit != "" and upper_unit != expected_unit)
            for lower, upper, lower_unit, upper_unit in _normal_range_mentions(
                conflict_body
            )
        ):
            errors.append("quality:fact_ledger_conflict:normal_range")

        observed_mentions = _numeric_anchor_mentions(
            conflict_body, r"(?:실측|측정)(?:값|\s*결과)?"
        )
        if _numeric_anchor_has_conflict(
            observed_mentions,
            expected_value=ledger["observed"],
            expected_unit=ledger["unit"],
        ):
            errors.append("quality:fact_ledger_conflict:observed")

        conflict_status_mentions = _scoped_ledger_status_mentions(
            conflict_body, metric_name=str(ledger["metric_name"])
        )
        if any(status != expected_status for status in conflict_status_mentions):
            errors.append("quality:fact_ledger_conflict:status")

    return errors


def _zero_value_reconstruction_detail_errors(
    doc: object, *, expected_value: int
) -> list[str]:
    """Block reconstructive internals only when V=0, independent of grade."""
    if isinstance(expected_value, bool) or expected_value not in {0, 1, 2}:
        raise ValueError("expected_value must be 0, 1, or 2")
    if expected_value != 0:
        return []
    body = str(getattr(doc, "body", "") or "")
    blocked = {
        "failure_boundary": r"실패\s*(?:경계|기준|조건)",
        "normal_range": r"정상\s*(?:범위|상한|하한)",
        "internal_threshold": r"(?:내부\s*)?(?:임계|한계)\s*값",
        "reconstructable_parameter": (
            r"(?:공정|배합|가격|모델)\s*(?:조건|파라미터|설정값)|"
            r"(?:온도|압력|속도|농도|배합비|혼합비|투입량|두께|공차|원가|단가|"
            r"할인율|유량|회전수|전압|전류|점도|산도|학습률|배치\s*(?:크기|수)|"
            r"에폭(?:\s*수)?|샘플링\s*온도|temperature|top[_ -]?p|"
            r"모델\s*(?:계수|가중치)|가중치|계수)"
        ),
    }

    # Public S3 output may say that a sensitive detail was omitted, but it must
    # never assign a numeric detail to that concept.  Search both orders
    # ("failure boundary 160" and "160 is the failure boundary") with only
    # assignment-like particles between them.  This keeps a later, unrelated
    # public aggregate from turning an explicit omission sentence into a hit.
    numeric = rf"(?:[<>≤≥]\s*)?{_DECIMAL_TOKEN}"
    numeric_with_unit = rf"{numeric}\s*(?:{_MEASUREMENT_UNIT}|주차|rpm|V|A|Hz|L/min)?"
    link = (
        r"[ \t|:=]*(?:은|는|이|가|을|를|의)?[ \t|:=]*"
        r"(?:(?:설정|기준)?(?:값|수치|범위)(?:은|는|이|가)?[ \t|:=]*)?"
        r"(?:약\s*)?"
    )
    reverse_link = (
        r"[ \t|:=]*(?:이상|이하|초과|미만)?\s*"
        r"(?:은|는|이|가|을|를|으로|로)?[ \t|:=]*"
    )

    def has_numeric_detail(term: str) -> bool:
        term_then_number = rf"(?:{term}){link}{numeric_with_unit}"
        number_then_term = rf"{numeric_with_unit}{reverse_link}(?:{term})"
        range_then_term = (
            rf"{numeric_with_unit}\s*(?:~|∼|에서|부터)\s*{numeric_with_unit}"
            rf"{reverse_link}(?:{term})"
        )
        return bool(
            re.search(term_then_number, body)
            or re.search(number_then_term, body)
            or re.search(range_then_term, body)
        )

    return [
        f"quality:zero_value_reconstruction_detail:{name}"
        for name, term in blocked.items()
        if has_numeric_detail(term)
    ]


def _s3_reconstruction_detail_errors(doc: object, *, expected_label: str) -> list[str]:
    """Compatibility wrapper for the historical four-grade pilot tests."""
    errors = _zero_value_reconstruction_detail_errors(
        doc, expected_value=0 if expected_label == "S3" else 1
    )
    return [error.replace("zero_value_", "s3_") for error in errors]


def _provider_base_url(provider: object) -> object:
    client = getattr(provider, "_client", None)
    return getattr(provider, "base_url", None) or getattr(client, "base_url", None)


def generation_model_attestation(
    provider: object,
    *,
    requested_name: str,
    expected_model_revision: str | None,
    live: bool,
) -> dict[str, object]:
    """Resolve and attest the exact Ollama blob used for generation.

    Only the local OpenAI/Ollama provider aliases are accepted.  A
    ``local_openai`` endpoint that is actually vLLM or LM Studio fails closed
    because it cannot answer Ollama's ``/api/tags`` inventory contract.
    """
    normalized_provider = str(requested_name or "").strip().lower()
    if normalized_provider not in _OLLAMA_GENERATION_PROVIDERS:
        raise ProxyGenerationRunError(
            "proxy generation requires provider local_openai or ollama with "
            "an Ollama-compatible /v1 endpoint"
        )
    model = str(getattr(provider, "model", "") or "").strip()
    try:
        if live:
            if expected_model_revision is None:
                raise OllamaAttestationError(
                    "expected model manifest digest is required as sha256:<64 hex>"
                )
            return verify_ollama_model(
                base_url=_provider_base_url(provider),
                requested_model=model,
                expected_manifest_sha256=expected_model_revision,
            )
        return pending_ollama_model_attestation(
            base_url=_provider_base_url(provider),
            requested_model=model,
            expected_manifest_sha256=expected_model_revision,
        )
    except OllamaAttestationError as exc:
        raise ProxyGenerationRunError(str(exc)) from exc


def provider_run_identity(
    provider: object,
    *,
    requested_name: str,
    declared_model_revision: str | None = None,
    model_attestation: Mapping[str, object] | None = None,
) -> dict:
    """Return an auditable runtime identity and reject placeholder providers."""
    runtime_name = str(getattr(provider, "name", "") or "").strip()
    model = str(getattr(provider, "model", "") or "").strip()
    revision = str(
        declared_model_revision
        or getattr(provider, "model_revision", None)
        or getattr(provider, "revision", None)
        or "unavailable"
    )
    if declared_model_revision and not re.fullmatch(
        r"sha256:[0-9a-f]{64}", declared_model_revision
    ):
        raise ProxyGenerationRunError("declared model revision must be sha256:<64 hex>")
    client = getattr(provider, "_client", None)
    endpoint = str(
        getattr(provider, "base_url", None)
        or getattr(client, "base_url", None)
        or "unavailable"
    )
    for context, value in (
        ("requested provider", requested_name),
        ("runtime provider", runtime_name),
        ("runtime model", model),
    ):
        if _blocked_identity(value):
            raise ProxyGenerationRunError(f"{context} is noop/unknown/fake")
    # Never persist the endpoint itself because a malformed URL can contain
    # credentials.  Its hash still makes endpoint changes invalidate resume.
    endpoint_identity_sha256 = _sha256_text(endpoint)
    attestation_binding: str | None = None
    if model_attestation is not None:
        try:
            checked_attestation = validate_ollama_attestation(
                model_attestation,
                require_verified=model_attestation.get("status") == "verified",
            )
        except OllamaAttestationError as exc:
            raise ProxyGenerationRunError(str(exc)) from exc
        endpoint_identity_sha256 = str(
            checked_attestation["endpoint_identity_sha256"]
        )
        attestation_binding = str(checked_attestation["binding_sha256"])
    provider_material = {
        "requested": requested_name,
        "runtime": runtime_name,
        "endpoint_identity_sha256": endpoint_identity_sha256,
    }
    model_material = {
        "model": model,
        "revision": revision,
        "model_attestation_binding_sha256": attestation_binding,
    }
    return {
        **provider_material,
        **model_material,
        "provider_identity_sha256": _sha256_bytes(
            _canonical_json_bytes(provider_material)
        ),
        "model_identity_sha256": _sha256_bytes(_canonical_json_bytes(model_material)),
        "model_attestation_binding_sha256": attestation_binding,
    }


def _validated_generation_namespace(value: object) -> str:
    namespace = str(value or "").strip()
    if (
        not _RUN_ID_RE.fullmatch(namespace)
        or namespace in {".", ".."}
        or str(value or "") != namespace
    ):
        raise ProxyGenerationRunError(
            "generation_namespace must be 1-96 safe identifier characters"
        )
    return namespace


def plan_item_descriptor(
    item: tuple[dict, dict, dict, int], *, generation_namespace: str = "main"
) -> dict:
    scenario, instance_profile, family_profile, ordinal = item
    namespace = _validated_generation_namespace(generation_namespace)
    descriptor = {
        "generation_namespace": namespace,
        "scenario_id": str(scenario["scenario_id"]),
        "label": str(scenario["label"]),
        "factor_profile_id": str(scenario.get("factor_profile_id") or ""),
        "expected_factor_scores": dict(scenario["expected_factor_scores"]),
        "instance_profile_id": str(instance_profile["instance_profile_id"]),
        "family_profile_id": str(family_profile["family_profile_id"]),
        "ordinal": int(ordinal),
    }
    fact_ledger = _fact_ledger_for_item(item)
    if fact_ledger is not None:
        descriptor["fact_ledger_sha256"] = _sha256_bytes(
            _canonical_json_bytes(fact_ledger)
        )
    # Time is excluded, while the immutable generation namespace prevents a
    # targeted top-up run from reusing the initial run's resume keys.
    descriptor["resume_key"] = _sha256_bytes(_canonical_json_bytes(descriptor))
    return descriptor


def describe_plan(
    plan: Sequence[tuple[dict, dict, dict, int]],
    *,
    generation_namespace: str = "main",
) -> tuple[list[dict], dict]:
    namespace = _validated_generation_namespace(generation_namespace)
    descriptors = [
        plan_item_descriptor(item, generation_namespace=namespace) for item in plan
    ]
    if not descriptors:
        raise ProxyGenerationRunError("generation plan is empty")
    keys = [str(row["resume_key"]) for row in descriptors]
    if len(keys) != len(set(keys)):
        raise ProxyGenerationRunError("generation plan contains duplicate resume keys")
    by_grade = Counter(str(row["label"]) for row in descriptors)
    by_scenario = Counter(str(row["scenario_id"]) for row in descriptors)
    by_factor_profile = Counter(
        str(row["factor_profile_id"]) for row in descriptors
    )
    summary = {
        "generation_namespace": namespace,
        "planned": len(descriptors),
        "by_grade": dict(sorted(by_grade.items())),
        "by_scenario": dict(sorted(by_scenario.items())),
        "by_factor_profile": dict(sorted(by_factor_profile.items())),
        "plan_sha256": _sha256_bytes(_canonical_json_bytes(descriptors)),
    }
    return descriptors, summary


def _atomic_write_bytes(path: Path, payload: bytes, *, replace: bool = False) -> None:
    if path.exists() and not replace:
        raise ProxyGenerationRunError(f"refusing to overwrite artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            os.chmod(temp_path, _SHARED_FILE_MODE)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists() and not replace:
            raise ProxyGenerationRunError(f"refusing to overwrite artifact: {path}")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: object, *, replace: bool = False) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    _atomic_write_bytes(path, data, replace=replace)


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(_canonical_json_bytes(row) + b"\n" for row in rows)


def _publish_or_verify(path: Path, payload: bytes) -> None:
    """Publish once, or verify an identical file left by an interrupted commit."""
    if path.exists():
        if path.read_bytes() != payload:
            raise ProxyGenerationRunError(
                f"existing final artifact differs from journal: {path}"
            )
        os.chmod(path, _SHARED_FILE_MODE)
        return
    _atomic_write_bytes(path, payload)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProxyGenerationRunError(f"invalid run metadata: {path}") from exc
    if not isinstance(value, dict):
        raise ProxyGenerationRunError(f"run metadata must be an object: {path}")
    return value


def _read_journal(path: Path) -> list[dict]:
    if not path.exists():
        raise ProxyGenerationRunError(f"missing run journal: {path}")
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProxyGenerationRunError(
                    f"truncated/corrupt journal at {path}:{line_no}"
                ) from exc
            if not isinstance(row, dict) or not row.get("generation_resume_key"):
                raise ProxyGenerationRunError(
                    f"journal row lacks deterministic resume key: {path}:{line_no}"
                )
            rows.append(row)
    return rows


class _JournalWriter:
    def __init__(self, path: Path, *, resume: bool) -> None:
        self.path = path
        mode = "a" if resume else "x"
        self._handle = path.open(mode, encoding="utf-8", newline="")
        try:
            os.chmod(path, _SHARED_FILE_MODE)
        except OSError:
            self._handle.close()
            raise

    def append(self, row: Mapping[str, object]) -> None:
        self._handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        self._handle.flush()
        os.fsync(self._handle.fileno())

    def close(self) -> None:
        self._handle.close()


def _create_empty_journal(path: Path) -> None:
    with path.open("xb") as handle:
        os.chmod(path, _SHARED_FILE_MODE)
        handle.flush()
        os.fsync(handle.fileno())


def _create_run_dir(root: Path, run_id: str | None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    chosen = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:10]
    )
    if not _RUN_ID_RE.fullmatch(chosen) or chosen in {".", ".."}:
        raise ProxyGenerationRunError("invalid run_id")
    path = root / chosen
    try:
        path.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ProxyGenerationRunError(
            f"run directory already exists; refusing overwrite: {path}"
        ) from exc
    os.chmod(path, _SHARED_DIRECTORY_MODE)
    return path


def _resolve_resume_run(out_root: Path, value: Path) -> Path:
    candidate = value if value.is_absolute() or value.exists() else out_root / value
    if not candidate.is_dir():
        raise ProxyGenerationRunError(f"resume run directory not found: {candidate}")
    os.chmod(candidate, _SHARED_DIRECTORY_MODE)
    return candidate


def _render_generated_text(doc: object) -> str:
    return "\n\n".join(
        part
        for part in (
            str(getattr(doc, "title", "") or "").strip(),
            str(getattr(doc, "body", "") or "").strip(),
        )
        if part
    )


def _deterministic_qualitative_scaffold(
    *,
    scenario: Mapping[str, object],
    family_profile: Mapping[str, object],
    fact_ledger: Mapping[str, object] | None = None,
) -> SynthDoc:
    """Create an auditable no-numeral fallback after model attempts fail.

    This is intentionally marked as a scaffold, never as a model-generated
    document.  It keeps the factual/quantitative layer deterministic while a
    later human audit decides whether its qualitative realism is sufficient.
    """
    document_type = str(scenario.get("document_type") or "내부 검토 문서")
    document_shape = str(family_profile.get("document_shape") or "검토 기록")
    focus_by_archetype = {
        "process-optimization": "공정 조건과 현장 검증",
        "pricing-policy": "가격 결정 조건과 거래 검토",
        "formulation-screen": "배합 조건과 시험 재현성",
        "model-quality": "모델 성능과 배포 전 검증",
        "security-recovery": "보안 대응과 복구 절차",
        "supplier-sourcing": "공급처 선정과 계약 검토",
        "roadmap-capacity": "제품 로드맵과 자원 배분",
        "field-validation": "현장 검증과 품질 확인",
        "workforce-plan": "인력 운영과 역할 배치",
        "deal-evaluation": "거래 조건과 사업성 검토",
    }
    focus = focus_by_archetype.get(
        str(scenario.get("archetype_id") or ""), "운영 변경과 검토 절차"
    )
    title = f"{document_type} {document_shape}"
    body = "\n\n".join(
        (
            "검토 목적\n이번 문서는 운영 변경안을 적용하기 전에 담당 조직이 확인한 관찰 사항과 "
            "의사결정 근거를 정리하기 위해 작성한다. 검토 범위는 현장 업무의 연결 관계, "
            "승인 절차, 예외 발생 시의 대응 흐름을 함께 살피는 데 둔다. 문서 작성자는 "
            "확정되지 않은 추정이나 개인 식별 정보 대신 확인 가능한 업무 기록만 사용한다.",
            "업무 맥락\n담당 조직은 변경안이 기존 절차와 만나는 지점을 구분해 기록하고, "
            "관련 역할이 같은 이해를 갖도록 검토 순서를 공유한다. 현장 관찰은 결과만 나열하지 "
            "않고 어떤 조건에서 판단이 달라졌는지 설명하며, 검토 중 발견된 예외는 별도 조치로 "
            "연결한다. 이를 통해 후속 담당자가 문서의 맥락을 다시 확인할 수 있게 한다.",
            "검토 관찰\n관찰 과정에서는 작업 흐름의 안정성, 승인 전 확인 항목, 자료 전달 방식, "
            "예외 처리의 책임 경계를 함께 살폈다. 담당자는 원자료와 검토 메모의 관계를 확인하고, "
            "서로 다른 의견이 있으면 근거를 남겨 조정한다. 판단이 불확실한 항목은 성급히 결론내리지 "
            "않고 추가 확인 대상으로 분류한다.",
            "책임과 접근 관리\n문서는 승인된 업무공간에서 역할별로 열람하며, 필요한 경우에만 "
            "공유한다. 자료를 전달할 때는 목적과 수신 범위를 기록하고, 변경 요청은 책임 역할의 "
            "확인 뒤 반영한다. 접근 권한과 보관 위치에 대한 문의는 운영 책임자가 검토하며, "
            "업무 종료 뒤에는 불필요한 접근 경로를 정리한다.",
            "| 검토 항목 | 확인 내용 | 담당 역할 |\n| --- | --- | --- |\n| 업무 흐름 | 변경안과 기존 절차의 연결을 확인 | 운영 담당 |\n| 자료 관리 | 원자료와 검토 메모의 대응을 확인 | 품질 담당 |\n| 승인 절차 | 예외 처리와 책임 경계를 확인 | 검토 책임자 |\n| 후속 조치 | 추가 확인 항목과 종료 기준을 정리 | 실행 담당 |",
            "결론 및 후속 조치\n현재 검토 결과는 즉시 확정하기보다 담당 역할별 확인을 거쳐 "
            "다음 조치로 연결한다. 운영 담당은 현장 기록의 누락 여부를 확인하고, 품질 담당은 "
            "근거 자료의 대응 관계를 다시 살핀다. 검토 책임자는 조치 결과와 예외 처리 내용을 "
            "확인한 뒤 문서 보완 여부를 판단하며, 완료 기준이 충족되면 관련 기록을 보관한다.",
        )
    )
    # Preserve all quantities in the code-appended ledger.  This short,
    # deterministic paragraph makes the fallback specific to the underlying
    # business scenario without inventing any new factual claim.
    metric_clause = "관련 운영 지표의 변동과 관측 상태"
    if fact_ledger is not None:
        metric_clause = (
            f"{fact_ledger['metric_name']}의 {fact_ledger['change_direction']} 흐름과 "
            f"관측 상태인 {fact_ledger.get('status', '검토 대상')}"
        )
    def first_safe_sentence(value: object) -> str:
        for sentence in re.split(r"(?<=[.!?])\s*", str(value or "").strip()):
            if sentence and not re.search(r"\d", sentence):
                return sentence
        return ""

    scenario_context = first_safe_sentence(scenario.get("shared_context"))
    disclosure = first_safe_sentence(scenario.get("disclosure_scope"))
    evidence_card = scenario.get("evidence_card")
    access_controls = ""
    if isinstance(evidence_card, Mapping):
        access_controls = first_safe_sentence(evidence_card.get("access_controls"))
    harm = first_safe_sentence(scenario.get("harm_potential"))
    # The fact card comes from the catalog, not from the failed model draft.
    # It gives an auditor the concrete subject, access posture, and decision
    # consequence of this particular scenario while keeping quantities only in
    # the ledger appendix.
    fact_card = " ".join(
        part
        for part in (
            scenario_context or focus,
            f"담당자는 {metric_clause}를 원자료와 대조해 변경안의 적용 여부를 다시 확인한다.",
            disclosure,
            access_controls,
            harm,
        )
        if part
    )
    body += "\n\n사안별 판단\n" + fact_card
    return SynthDoc(
        target_grade=str(scenario["label"]),
        domain=str(scenario["domain"]),
        title=title,
        body=body,
        document_type=document_type,
        dept_hint="운영 검토",
        rationale_tags=["deterministic_qualitative_scaffold"],
        llm_provider="deterministic_scaffold",
        llm_model="qualitative-scaffold-v1",
        label_source="deterministic_qualitative_scaffold",
        response_audit=[],
    )


def _materialize_fact_ledger_block(
    doc: object, ledger: Mapping[str, object]
) -> dict[str, object]:
    """Append the code-derived ledger only to a raw model draft without it."""
    block = _fact_ledger_block(ledger)
    raw_body = str(getattr(doc, "body", "") or "")
    raw_text = _render_generated_text(doc)
    raw_exact_block_count = raw_body.count(block)
    raw_heading_count = len(re.findall(r"\[\s*사실\s*검산\s*\]", raw_body))
    raw_control_tag_count = len(re.findall(r"(?i)</?copy_exactly>", raw_body))
    appended = raw_heading_count == 0 and raw_exact_block_count == 0 and raw_control_tag_count == 0
    quality_errors: list[str] = []
    if not appended:
        quality_errors.append(
            "quality:fact_ledger_materialization:model_emitted_ledger_artifact"
        )
    separator = ""
    if appended:
        if raw_body.endswith("\n\n") or not raw_body:
            separator = ""
        elif raw_body.endswith("\n"):
            separator = "\n"
        else:
            separator = "\n\n"
        setattr(doc, "body", raw_body + separator + block)
    final_text = _render_generated_text(doc)
    final_body = str(getattr(doc, "body", "") or "")
    final_exact_block_count = final_body.count(block)
    final_heading_count = len(
        re.findall(r"\[\s*사실\s*검산\s*\]", final_body)
    )
    if (final_heading_count, final_exact_block_count) != (1, 1):
        quality_errors.append("quality:fact_ledger_materialization:invalid_final_state")
    return {
        "schema": FACT_LEDGER_MATERIALIZATION_VERSION,
        "policy": "reject_model_ledger_then_append",
        "mode": "code_appended_exact_block" if appended else "model_ledger_rejected",
        "source": "deterministic_code" if appended else "model_emitted_rejected",
        "appended": appended,
        "raw_pre_materialization_text_sha256": _sha256_text(raw_text),
        "raw_pre_materialization_text_chars": len(raw_text),
        "raw_pre_materialization_body_sha256": _sha256_text(raw_body),
        "raw_pre_materialization_body_chars": len(raw_body),
        "raw_exact_block_count": raw_exact_block_count,
        "raw_fact_heading_count": raw_heading_count,
        "raw_control_tag_count": raw_control_tag_count,
        "fact_ledger_sha256": _sha256_bytes(_canonical_json_bytes(ledger)),
        "canonical_block_sha256": _sha256_text(block),
        "canonical_block_chars": len(block),
        "append_separator_chars": len(separator),
        "final_body_sha256": _sha256_text(final_body),
        "final_body_chars": len(final_body),
        "final_text_sha256": _sha256_text(final_text),
        "final_text_chars": len(final_text),
        "final_exact_block_count": final_exact_block_count,
        "final_fact_heading_count": final_heading_count,
        "quality_errors": quality_errors,
    }


def make_candidate(
    *,
    scenario: dict,
    instance_profile: dict,
    family_profile: dict,
    ordinal: int,
    doc: object,
    catalog_version: str,
    generation_namespace: str = "main",
) -> dict:
    """Convert one successful generator response into an auditable candidate."""
    text = _render_generated_text(doc)
    provider = str(getattr(doc, "llm_provider", "") or "unknown")
    model = str(getattr(doc, "llm_model", "") or "unknown")
    scenario_id = str(scenario["scenario_id"])
    instance_profile_id = str(instance_profile["instance_profile_id"])
    family_profile_id = str(family_profile["family_profile_id"])
    namespace = _validated_generation_namespace(generation_namespace)
    namespace_sha256 = _sha256_text(namespace)
    return {
        "doc_id": (
            f"proxy-{namespace_sha256}-{scenario_id}-{instance_profile_id}-"
            f"{ordinal:04d}"
        ),
        "text": text,
        "label": scenario["label"],
        "label_source": "proxy_scenario_spec",
        "review_status": "proxy_gold_candidate",
        "source": "synthetic",
        "document_origin": "synthetic",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": scenario["catalog_split_role"],
        "training_use_permitted": scenario["training_use_permitted"],
        "evaluation_use_permitted": scenario["evaluation_use_permitted"],
        "document_family_id": f"{scenario['document_family_id']}:{instance_profile_id}",
        "document_type": family_profile_id,
        "scenario_document_type": scenario["document_type"],
        "domain": scenario["domain"],
        "industry": scenario["industry"],
        "scenario_id": scenario_id,
        "generation_namespace": namespace,
        "generation_namespace_sha256": namespace_sha256,
        "factor_profile_id": str(scenario.get("factor_profile_id") or ""),
        "scenario_instance_id": f"{scenario_id}:{instance_profile_id}",
        "instance_profile_id": instance_profile_id,
        "family_profile_id": family_profile_id,
        "length_profile_id": family_profile["length_profile_id"],
        "expected_factor_scores": scenario["expected_factor_scores"],
        "evidence_card": scenario["evidence_card"],
        "generation_lineage": [
            f"scenario_catalog:{catalog_version}:{scenario_id}",
            f"generation_namespace_sha256:{namespace_sha256}",
            f"factor_profile:{scenario.get('factor_profile_id') or 'legacy'}",
            f"scenario_instance:{instance_profile_id}",
            f"family_profile:{family_profile_id}",
            f"generator:{provider}:{model}",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def _apportioned_scenario_counts(
    scenarios: Sequence[Mapping[str, object]],
    *,
    per_scenario: int | None,
    count_multiplier: float,
) -> dict[str, int]:
    """Largest-remainder allocation without per-profile ceiling inflation.

    Small boundary-profile quotas must not turn a 2.5x oversample into more
    than 2.5x merely because each profile was rounded separately.  Allocation
    is therefore exact at each document-family/grade cell and deterministic by
    fractional remainder then scenario_id.
    """
    multiplier = Decimal(str(count_multiplier))
    groups: dict[tuple[str, str], list[tuple[str, Decimal, int]]] = {}
    seen: set[str] = set()
    for scenario in scenarios:
        scenario_id = str(scenario["scenario_id"])
        if scenario_id in seen:
            raise ValueError(f"duplicate scenario_id: {scenario_id}")
        seen.add(scenario_id)
        base_count = int(
            scenario["target_count"] if per_scenario is None else per_scenario
        )
        if base_count < 1:
            raise ValueError(f"scenario target_count must be positive: {scenario_id}")
        raw_count = Decimal(base_count) * multiplier
        floor_count = int(raw_count)
        key = (str(scenario["document_family_id"]), str(scenario["label"]))
        groups.setdefault(key, []).append((scenario_id, raw_count, floor_count))

    allocated: dict[str, int] = {}
    for rows in groups.values():
        target = int(
            sum((raw for _, raw, _ in rows), Decimal(0)).to_integral_value(
                rounding=ROUND_CEILING
            )
        )
        floor_total = sum(floor for _, _, floor in rows)
        bonuses = target - floor_total
        ranked = sorted(
            rows,
            key=lambda row: (-(row[1] - Decimal(row[2])), row[0]),
        )
        bonus_ids = {scenario_id for scenario_id, _, _ in ranked[:bonuses]}
        for scenario_id, _, floor_count in rows:
            allocated[scenario_id] = floor_count + int(scenario_id in bonus_ids)
    return allocated


def generation_plan(
    scenarios: list[dict],
    instance_profiles: list[dict],
    family_profiles: list[dict],
    *,
    per_scenario: int | None,
    count_multiplier: float = 1.0,
) -> list[tuple[dict, dict, dict, int]]:
    """Expand scenarios into exact work items, distributed across families."""
    if not instance_profiles or not family_profiles:
        raise ValueError("instance_profiles and family_profiles must not be empty")
    if count_multiplier < 1.0:
        raise ValueError("count_multiplier must be >= 1.0")
    plan: list[tuple[dict, dict, dict, int]] = []
    counts = _apportioned_scenario_counts(
        scenarios,
        per_scenario=per_scenario,
        count_multiplier=count_multiplier,
    )
    # Continue instance and shape round-robins independently per grade across
    # profile boundaries.  This matters now that one profile may have only two
    # or three base records; resetting each profile would tie boundary factors
    # to the first few instances and document shapes.
    grade_offsets: Counter[str] = Counter()
    instance_count = len(instance_profiles)
    shape_count = len(family_profiles)
    for scenario in scenarios:
        count = counts[str(scenario["scenario_id"])]
        grade = str(scenario["label"])
        grade_offset = int(grade_offsets[grade])
        for index in range(count):
            global_ordinal = grade_offset + index
            instance_index = global_ordinal % instance_count
            shape_index = (
                global_ordinal + global_ordinal // shape_count
            ) % shape_count
            instance = instance_profiles[instance_index]
            shape = family_profiles[shape_index]
            plan.append((scenario, instance, shape, index))
        grade_offsets[grade] += count
    return plan


def generation_target_maps(
    scenarios: Sequence[Mapping[str, object]],
    *,
    per_scenario: int | None,
    candidate_buffer_factor: float = 1.0,
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    """Return base and pre-judge candidate targets by scenario and grade."""
    if candidate_buffer_factor < 1.0:
        raise ValueError("candidate_buffer_factor must be >= 1.0")
    base_by_scenario = _apportioned_scenario_counts(
        scenarios,
        per_scenario=per_scenario,
        count_multiplier=1.0,
    )
    candidate_by_scenario = _apportioned_scenario_counts(
        scenarios,
        per_scenario=per_scenario,
        count_multiplier=candidate_buffer_factor,
    )
    scenario_grade: dict[str, str] = {}
    for scenario in scenarios:
        scenario_id = str(scenario["scenario_id"])
        scenario_grade[scenario_id] = str(scenario["label"])

    def by_grade(values: Mapping[str, int]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for scenario_id, count in values.items():
            counts[scenario_grade[scenario_id]] += int(count)
        return dict(sorted(counts.items()))

    return (
        base_by_scenario,
        by_grade(base_by_scenario),
        candidate_by_scenario,
        by_grade(candidate_by_scenario),
    )


def partition_generation_plan_by_family(
    plan: Sequence[tuple[dict, dict, dict, int]],
    *,
    shard_count: int,
    shard_index: int,
) -> tuple[list[tuple[dict, dict, dict, int]], dict[str, object]]:
    """Deterministically shard whole counterfactual document families.

    The full plan must be built before partitioning so shape rotations and
    resume keys are identical to the unsharded plan.  Every document family,
    including all of its selected grades, belongs to exactly one shard.
    """
    if isinstance(shard_count, bool) or shard_count < 1:
        raise ValueError("shard_count must be >= 1")
    if isinstance(shard_index, bool) or not 0 <= shard_index < shard_count:
        raise ValueError("shard_index must satisfy 0 <= index < shard_count")
    family_ids = sorted({str(item[0]["document_family_id"]) for item in plan})
    if shard_count > len(family_ids):
        raise ValueError(
            f"shard_count exceeds document families: {shard_count}>{len(family_ids)}"
        )
    selected_family_ids = family_ids[shard_index::shard_count]
    selected = set(selected_family_ids)
    shard_plan = [
        item for item in plan if str(item[0]["document_family_id"]) in selected
    ]
    if not shard_plan:
        raise ValueError("generation shard is empty")
    metadata: dict[str, object] = {
        "strategy": "document-family-round-robin-v1",
        "shard_count": shard_count,
        "shard_index": shard_index,
        "all_document_family_count": len(family_ids),
        "selected_document_family_ids": selected_family_ids,
        "selected_document_family_count": len(selected_family_ids),
    }
    return shard_plan, metadata


def _rejection_base(item: tuple[dict, dict, dict, int]) -> dict:
    scenario, instance_profile, family_profile, ordinal = item
    return {
        "scenario_id": scenario["scenario_id"],
        "intended_label": scenario["label"],
        "factor_profile_id": str(scenario.get("factor_profile_id") or ""),
        "expected_factor_scores": dict(scenario["expected_factor_scores"]),
        "instance_profile_id": instance_profile["instance_profile_id"],
        "family_profile_id": family_profile["family_profile_id"],
        "ordinal": ordinal,
    }


def _generate_plan_item(
    item: tuple[dict, dict, dict, int],
    *,
    generator: SyntheticDocGenerator,
    catalog_version: str,
    generation_namespace: str = "main",
    max_quality_retries: int = 1,
) -> tuple[dict | None, dict | None]:
    scenario, instance_profile, family_profile, ordinal = item
    if max_quality_retries < 0:
        raise ValueError("max_quality_retries must be >= 0")
    profile_min = int(family_profile.get("min_chars", scenario["min_chars"]))
    profile_max = int(family_profile.get("max_chars", scenario["max_chars"]))
    fact_ledger = _fact_ledger_for_item(item)
    fact_ledger_prompt = _fact_ledger_prompt(fact_ledger) if fact_ledger else ""
    fact_ledger_block = _fact_ledger_block(fact_ledger) if fact_ledger else ""
    # Reserve the worst-case two-newline separator as well as the exact block.
    # The final profile gate below still measures title + body and remains the
    # authoritative fail-closed bound.
    fact_ledger_reserved_chars = len(fact_ledger_block) + 2 if fact_ledger else 0
    if fact_ledger_reserved_chars >= profile_max:
        raise ProxyGenerationRunError(
            "fact ledger materialization does not fit document length profile"
        )
    quality_history: list[dict] = []
    last_candidate: dict | None = None
    previous_draft = ""
    for attempt in range(max_quality_retries + 1):
        buffer_ratio = Decimal("1.20") + Decimal("0.15") * attempt
        unreserved_prompt_min = min(
            profile_max,
            int(
                (Decimal(profile_min) * buffer_ratio).to_integral_value(
                    rounding=ROUND_CEILING
                )
            ),
        )
        prompt_max = profile_max - fact_ledger_reserved_chars
        prompt_min = max(
            1,
            min(
                prompt_max,
                unreserved_prompt_min - fact_ledger_reserved_chars,
            ),
        )
        revision_context = ""
        if quality_history:
            revision_context = _retry_revision_context(
                quality_history[-1]["errors"], previous_draft
            )
        req = SynthRequest(
            target_grade=str(scenario["label"]),
            domain=str(scenario["domain"]),
            len_min=prompt_min,
            len_max=prompt_max,
            scenario_context=(
                f"{scenario['scenario_context']}\n\n"
                f"[독립 가상 인스턴스] {instance_profile['context']}\n"
                f"[문서 계열 조건] {family_profile['context']}"
            ),
            disclosure_scope=str(scenario["disclosure_scope"]),
            harm_potential=str(scenario["harm_potential"]),
            document_type_hint=(
                f"{scenario['document_type']} / {family_profile['document_shape']}"
            ),
            max_output_tokens=_proxy_output_token_budget(
                # Internal JSON repair remains inside generate_one and uses
                # the initial budget.  Every outer retry adds a revision
                # summary, even when no raw draft can be carried.
                family_profile,
                quality_retry=bool(quality_history),
            ),
            structure_requirements="\n\n".join(
                part
                for part in (
                    _profile_structure_requirements(family_profile),
                    fact_ledger_prompt,
                )
                if part
            ),
            revision_context=revision_context,
        )
        try:
            doc = generator.generate_one(req)
        except Exception as exc:  # one failed item must not erase a long run
            quality_history.append(
                {
                    "attempt": attempt + 1,
                    "errors": [f"generator_exception:{type(exc).__name__}"],
                }
            )
            continue
        if getattr(doc, "parse_error", None) or getattr(doc, "pii_violations", None):
            quality_history.append(
                {
                    "attempt": attempt + 1,
                    "errors": [
                        "parse_error" if getattr(doc, "parse_error", None) else "pii"
                    ],
                    "parse_error": getattr(doc, "parse_error", None),
                    "pii_violations": getattr(doc, "pii_violations", []),
                    "response_audit": list(getattr(doc, "response_audit", None) or []),
                }
            )
            continue
        raw_retry_draft = _bounded_retry_draft(
            doc, max_chars=_proxy_retry_draft_limit(family_profile)
        )
        raw_fact_ledger_conflicts: list[str] = []
        raw_prompt_artifacts = _generation_prompt_artifact_errors(doc)
        raw_numeric_claims: list[str] = []
        if scenario.get("fact_ledger_contract") == FACT_LEDGER_NUMERIC_GUARD_VERSION:
            raw_numeric_claims = _unapproved_numeric_claim_errors(
                doc,
                allowed_numeric_tokens=_allowed_prompt_numeric_tokens(
                    scenario, instance_profile, family_profile
                ),
            )
        fact_ledger_materialization: dict[str, object] | None = None
        if fact_ledger is not None:
            raw_fact_ledger_conflicts = [
                error
                for error in _fact_ledger_errors(doc, fact_ledger)
                if error.startswith("quality:fact_ledger_conflict:")
            ]
            fact_ledger_materialization = _materialize_fact_ledger_block(
                doc, fact_ledger
            )
        candidate = make_candidate(
            scenario=scenario,
            instance_profile=instance_profile,
            family_profile=family_profile,
            ordinal=ordinal,
            doc=doc,
            catalog_version=catalog_version,
            generation_namespace=generation_namespace,
        )
        candidate.update(
            {
                "generation_attempt_count": attempt + 1,
                "requested_profile_min_chars": profile_min,
                "requested_profile_max_chars": profile_max,
                "prompt_min_chars": prompt_min,
                "prompt_max_chars": prompt_max,
                "requested_max_output_tokens": req.max_output_tokens,
                "generation_response_audit": list(
                    getattr(doc, "response_audit", None) or []
                ),
            }
        )
        if fact_ledger is not None:
            candidate["generation_fact_ledger"] = fact_ledger
            candidate["generation_fact_ledger_sha256"] = _sha256_bytes(
                _canonical_json_bytes(fact_ledger)
            )
            candidate["generation_fact_ledger_materialization"] = (
                fact_ledger_materialization
            )
            candidate["generation_fact_ledger_reserved_chars"] = (
                fact_ledger_reserved_chars
            )
        check = _validate_generated_proxy_record(candidate)
        errors = list(check.errors)
        errors.extend(raw_prompt_artifacts)
        errors.extend(raw_numeric_claims)
        errors.extend(_classification_style_marker_errors(doc))
        errors.extend(_document_completion_errors(doc))
        expected_scores = scenario["expected_factor_scores"]
        errors.extend(
            _document_access_errors(
                doc, expected_secrecy=int(expected_scores["secrecy"])
            )
        )
        errors.extend(
            _zero_value_reconstruction_detail_errors(
                doc, expected_value=int(expected_scores["value"])
            )
        )
        if fact_ledger is not None:
            materialized_fact_errors = _fact_ledger_errors(doc, fact_ledger)
            errors.extend(materialized_fact_errors)
            errors.extend(
                error
                for error in raw_fact_ledger_conflicts
                if error not in materialized_fact_errors
            )
            if fact_ledger_materialization is not None:
                errors.extend(fact_ledger_materialization["quality_errors"])
        candidate_chars = len(str(candidate["text"]))
        if candidate_chars < profile_min:
            errors.append(f"quality:profile_too_short:{candidate_chars}<{profile_min}")
        if candidate_chars > profile_max:
            errors.append(f"quality:profile_too_long:{candidate_chars}>{profile_max}")
        if not errors:
            candidate["generation_quality_history"] = quality_history
            return candidate, None
        last_candidate = candidate
        previous_draft = raw_retry_draft
        quality_history.append(
            {
                "attempt": attempt + 1,
                "char_count": len(str(candidate["text"])),
                "text_sha256": _sha256_text(candidate["text"]),
                "errors": errors,
                "response_audit": list(getattr(doc, "response_audit", None) or []),
            }
        )
    if (
        scenario.get("fact_ledger_contract") == FACT_LEDGER_NUMERIC_GUARD_VERSION
        and fact_ledger is not None
        and str(scenario.get("label")) != "S3"
    ):
        scaffold_doc = _deterministic_qualitative_scaffold(
            scenario=scenario,
            family_profile=family_profile,
            fact_ledger=fact_ledger,
        )
        scaffold_materialization = _materialize_fact_ledger_block(
            scaffold_doc, fact_ledger
        )
        scaffold_candidate = make_candidate(
            scenario=scenario,
            instance_profile=instance_profile,
            family_profile=family_profile,
            ordinal=ordinal,
            doc=scaffold_doc,
            catalog_version=catalog_version,
            generation_namespace=generation_namespace,
        )
        scaffold_candidate.update(
            {
                "generation_mode": "deterministic_qualitative_scaffold_fallback",
                "requires_manual_audit": True,
                "raw_model_generation_failures": quality_history,
                "generation_fact_ledger": fact_ledger,
                "generation_fact_ledger_sha256": _sha256_bytes(
                    _canonical_json_bytes(fact_ledger)
                ),
                "generation_fact_ledger_materialization": scaffold_materialization,
                "generation_fact_ledger_reserved_chars": fact_ledger_reserved_chars,
            }
        )
        scaffold_check = _validate_generated_proxy_record(scaffold_candidate)
        scaffold_errors = list(scaffold_check.errors)
        scaffold_errors.extend(_classification_style_marker_errors(scaffold_doc))
        scaffold_errors.extend(_document_completion_errors(scaffold_doc))
        expected_scores = scenario["expected_factor_scores"]
        scaffold_errors.extend(
            _document_access_errors(
                scaffold_doc, expected_secrecy=int(expected_scores["secrecy"])
            )
        )
        scaffold_errors.extend(
            _zero_value_reconstruction_detail_errors(
                scaffold_doc, expected_value=int(expected_scores["value"])
            )
        )
        scaffold_errors.extend(_fact_ledger_errors(scaffold_doc, fact_ledger))
        scaffold_errors.extend(scaffold_materialization["quality_errors"])
        scaffold_chars = len(str(scaffold_candidate["text"]))
        if scaffold_chars < profile_min:
            scaffold_errors.append(
                f"quality:profile_too_short:{scaffold_chars}<{profile_min}"
            )
        if scaffold_chars > profile_max:
            scaffold_errors.append(
                f"quality:profile_too_long:{scaffold_chars}>{profile_max}"
            )
        if not scaffold_errors:
            return scaffold_candidate, None

    rejection: dict = {
        **_rejection_base(item),
        "generation_namespace": _validated_generation_namespace(
            generation_namespace
        ),
        "reason": "quality_gate",
        "attempt_count": len(quality_history),
        "quality_history": quality_history,
    }
    if last_candidate is not None:
        rejection["candidate_snapshot"] = last_candidate
    return None, rejection


def generate_candidates(
    plan: list[tuple[dict, dict, dict, int]],
    *,
    provider_name: str | None,
    catalog_version: str,
    generation_namespace: str = "main",
    max_quality_retries: int = 1,
) -> tuple[list[dict], list[dict]]:
    generator = SyntheticDocGenerator(llm=build_provider(provider_name))
    accepted: list[dict] = []
    rejected: list[dict] = []
    for item in plan:
        candidate, rejection = _generate_plan_item(
            item,
            generator=generator,
            catalog_version=catalog_version,
            generation_namespace=generation_namespace,
            max_quality_retries=max_quality_retries,
        )
        if candidate is not None:
            accepted.append(candidate)
        if rejection is not None:
            rejected.append(rejection)
    return accepted, rejected


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    """Backward-compatible one-shot helper with atomic no-overwrite semantics."""
    _atomic_write_bytes(path, _jsonl_bytes(rows))


def _run_contract(
    *,
    catalog_version: str,
    catalog_sha256: str,
    code_sha256: str,
    code_components: Mapping[str, str] | None,
    provider_identity: Mapping[str, object],
    model_attestation: Mapping[str, object],
    plan_summary: Mapping[str, object],
    selection_targets: Mapping[str, int],
    selection_targets_by_scenario: Mapping[str, int] | None,
    base_final_targets: Mapping[str, int],
    base_final_targets_by_scenario: Mapping[str, int] | None,
    partition_metadata: Mapping[str, object] | None,
    max_quality_retries: int,
    generation_namespace: str,
) -> dict:
    material = {
        "schema_version": RUN_SCHEMA_VERSION,
        "fact_ledger_materialization_schema": (
            FACT_LEDGER_MATERIALIZATION_VERSION
        ),
        "generation_namespace": _validated_generation_namespace(
            generation_namespace
        ),
        "catalog_version": catalog_version,
        "catalog_sha256": catalog_sha256,
        "code_sha256": code_sha256,
        "provider_identity_sha256": provider_identity["provider_identity_sha256"],
        "model_identity_sha256": provider_identity["model_identity_sha256"],
        "model_runtime_attestation_sha256": model_attestation["binding_sha256"],
        "plan_sha256": plan_summary["plan_sha256"],
        "selection_targets": dict(sorted(selection_targets.items())),
        "selection_targets_by_scenario": dict(
            sorted((selection_targets_by_scenario or {}).items())
        ),
        "base_final_targets": dict(sorted(base_final_targets.items())),
        "base_final_targets_by_scenario": dict(
            sorted((base_final_targets_by_scenario or {}).items())
        ),
        "partition": dict(partition_metadata or {"strategy": "unsharded-v1"}),
        "max_quality_retries": max_quality_retries,
    }
    for key, value in sorted((code_components or {}).items()):
        if not re.fullmatch(r"[a-z][a-z0-9_]*_sha256", key):
            raise ProxyGenerationRunError(f"invalid code component key: {key}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(value)):
            raise ProxyGenerationRunError(f"invalid code component digest: {key}")
        material[key] = str(value)
    return {
        **material,
        "run_contract_sha256": _sha256_bytes(_canonical_json_bytes(material)),
    }


def _enrich_run_row(
    row: Mapping[str, object],
    *,
    run_id: str,
    descriptor: Mapping[str, object],
    contract: Mapping[str, object],
    provider_identity: Mapping[str, object],
    model_attestation: Mapping[str, object],
    outcome: str,
) -> dict:
    enriched = dict(row)
    enriched.update(
        {
            "generation_run_id": run_id,
            "generation_resume_key": descriptor["resume_key"],
            "generation_outcome": outcome,
            "generation_contract": {
                **contract,
                "provider": provider_identity["runtime"],
                "model": provider_identity["model"],
                "model_revision": provider_identity["revision"],
                "model_attestation": dict(model_attestation),
            },
        }
    )
    if outcome == "candidate":
        lineage = list(enriched.get("generation_lineage") or [])
        lineage.extend(
            [
                f"generation_run:{run_id}",
                f"resume_key:{descriptor['resume_key']}",
                f"catalog_sha256:{contract['catalog_sha256']}",
                f"code_sha256:{contract['code_sha256']}",
                f"provider_identity_sha256:{contract['provider_identity_sha256']}",
                f"model_identity_sha256:{contract['model_identity_sha256']}",
                "model_runtime_attestation_sha256:"
                f"{contract['model_runtime_attestation_sha256']}",
            ]
        )
        enriched["generation_lineage"] = lineage
    return enriched


def _validated_journal_state(
    *,
    candidates_path: Path,
    rejected_path: Path,
    plan_keys: set[str],
    expected_contract: Mapping[str, object],
    expected_provider_identity: Mapping[str, object],
) -> tuple[list[dict], list[dict], set[str]]:
    candidates = _read_journal(candidates_path)
    rejected = _read_journal(rejected_path)
    keys = [str(row["generation_resume_key"]) for row in [*candidates, *rejected]]
    if len(keys) != len(set(keys)):
        raise ProxyGenerationRunError("duplicate resume key found in run journals")
    unknown = sorted(set(keys) - plan_keys)
    if unknown:
        raise ProxyGenerationRunError("journal contains keys outside the current plan")
    for row in [*candidates, *rejected]:
        row_contract = row.get("generation_contract")
        if not isinstance(row_contract, Mapping) or any(
            row_contract.get(key) != value
            for key, value in expected_contract.items()
        ):
            raise ProxyGenerationRunError(
                "journal row generation contract does not match the resumed run"
            )
        if (
            row_contract.get("provider") != expected_provider_identity["runtime"]
            or row_contract.get("model") != expected_provider_identity["model"]
            or row_contract.get("model_revision")
            != expected_provider_identity["revision"]
        ):
            raise ProxyGenerationRunError(
                "journal row provider/model identity does not match the resumed run"
            )
        try:
            row_attestation = validate_ollama_attestation(
                row_contract.get("model_attestation"), require_verified=True
            )
        except OllamaAttestationError as exc:
            raise ProxyGenerationRunError(
                "journal row model attestation is invalid"
            ) from exc
        if (
            row_attestation["binding_sha256"]
            != expected_contract["model_runtime_attestation_sha256"]
        ):
            raise ProxyGenerationRunError(
                "journal row model attestation does not match the resumed run"
            )
    for row in candidates:
        check = _validate_generated_proxy_record(row)
        if not check.ok:
            raise ProxyGenerationRunError(
                "candidate journal contains an invalid/corrupt record"
            )
    return candidates, rejected, set(keys)


def _progress_payload(
    *,
    run_id: str,
    plan_summary: Mapping[str, object],
    candidates: Sequence[Mapping[str, object]],
    rejected: Sequence[Mapping[str, object]],
    status: str,
) -> dict:
    completed = len(candidates) + len(rejected)
    planned = int(plan_summary["planned"])
    return {
        "run_id": run_id,
        "status": status,
        "planned": planned,
        "completed": completed,
        "remaining": planned - completed,
        "candidates": len(candidates),
        "rejected": len(rejected),
        "completed_by_grade": dict(
            sorted(
                Counter(
                    str(row.get("label") or row.get("intended_label") or "unknown")
                    for row in [*candidates, *rejected]
                ).items()
            )
        ),
        "updated_at": _utc_now(),
    }


def _ordered_rows(
    rows: Sequence[dict], descriptors: Sequence[Mapping[str, object]]
) -> list[dict]:
    order = {
        str(descriptor["resume_key"]): index
        for index, descriptor in enumerate(descriptors)
    }
    return sorted(rows, key=lambda row: order[str(row["generation_resume_key"])])


def completion_exit_code(stats: Mapping[str, object], *, allow_partial: bool) -> int:
    return 0 if bool(stats.get("target_met")) or allow_partial else 2


def run_generation(
    plan: Sequence[tuple[dict, dict, dict, int]],
    *,
    provider: object,
    requested_provider: str,
    catalog_version: str,
    catalog_sha256: str,
    code_sha256: str,
    code_components: Mapping[str, str] | None = None,
    declared_model_revision: str | None = None,
    selection_targets: Mapping[str, int] | None = None,
    selection_targets_by_scenario: Mapping[str, int] | None = None,
    base_final_targets: Mapping[str, int] | None = None,
    base_final_targets_by_scenario: Mapping[str, int] | None = None,
    partition_metadata: Mapping[str, object] | None = None,
    out_root: Path,
    run_id: str | None = None,
    resume_run: Path | None = None,
    allow_partial: bool = False,
    max_quality_retries: int = 1,
    generation_namespace: str | None = None,
) -> tuple[Path, dict]:
    """Execute or resume one immutable, journaled proxy-generation run."""
    if run_id and resume_run is not None:
        raise ProxyGenerationRunError("run_id and resume_run are mutually exclusive")
    if max_quality_retries < 0:
        raise ProxyGenerationRunError("max_quality_retries must be >= 0")
    effective_new_run_id = run_id
    if resume_run is None and effective_new_run_id is None:
        effective_new_run_id = (
            datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + "-"
            + uuid.uuid4().hex[:10]
        )
    effective_generation_namespace = _validated_generation_namespace(
        generation_namespace
        or (
            Path(resume_run).name
            if resume_run is not None
            else effective_new_run_id
        )
    )
    provider_identity = provider_run_identity(
        provider,
        requested_name=requested_provider,
        declared_model_revision=declared_model_revision,
    )
    model_attestation = generation_model_attestation(
        provider,
        requested_name=requested_provider,
        expected_model_revision=declared_model_revision,
        live=True,
    )
    provider_identity = provider_run_identity(
        provider,
        requested_name=requested_provider,
        declared_model_revision=declared_model_revision,
        model_attestation=model_attestation,
    )
    descriptors, plan_summary = describe_plan(
        plan, generation_namespace=effective_generation_namespace
    )
    normalized_targets = {
        str(grade): int(count)
        for grade, count in (selection_targets or plan_summary["by_grade"]).items()
    }
    if not normalized_targets or any(
        count < 1 for count in normalized_targets.values()
    ):
        raise ProxyGenerationRunError("selection targets must be positive")
    planned_by_grade = {
        str(grade): int(count) for grade, count in plan_summary["by_grade"].items()
    }
    if any(
        normalized_targets.get(grade, 0) > planned_count
        for grade, planned_count in planned_by_grade.items()
    ) or set(normalized_targets) - set(planned_by_grade):
        raise ProxyGenerationRunError("selection target exceeds generation plan")
    planned_by_scenario = {
        str(scenario_id): int(count)
        for scenario_id, count in plan_summary["by_scenario"].items()
    }
    normalized_scenario_targets = {
        str(scenario_id): int(count)
        for scenario_id, count in (selection_targets_by_scenario or {}).items()
    }
    if normalized_scenario_targets:
        if any(count < 1 for count in normalized_scenario_targets.values()):
            raise ProxyGenerationRunError("scenario selection targets must be positive")
        if set(normalized_scenario_targets) != set(planned_by_scenario) or any(
            count > planned_by_scenario[scenario_id]
            for scenario_id, count in normalized_scenario_targets.items()
        ):
            raise ProxyGenerationRunError(
                "scenario selection targets must exactly cover and fit the plan"
            )
        scenario_grades = {
            str(row["scenario_id"]): str(row["label"]) for row in descriptors
        }
        scenario_targets_by_grade: Counter[str] = Counter()
        for scenario_id, count in normalized_scenario_targets.items():
            scenario_targets_by_grade[scenario_grades[scenario_id]] += count
        if dict(sorted(scenario_targets_by_grade.items())) != dict(
            sorted(normalized_targets.items())
        ):
            raise ProxyGenerationRunError(
                "scenario selection target sums disagree with grade targets"
            )
    normalized_base_targets = {
        str(grade): int(count)
        for grade, count in (base_final_targets or normalized_targets).items()
    }
    if set(normalized_base_targets) != set(normalized_targets) or any(
        count < 1 or count > normalized_targets[grade]
        for grade, count in normalized_base_targets.items()
    ):
        raise ProxyGenerationRunError(
            "base final targets must be positive and no larger than candidate targets"
        )
    normalized_base_scenario_targets = {
        str(scenario_id): int(count)
        for scenario_id, count in (base_final_targets_by_scenario or {}).items()
    }
    if normalized_base_scenario_targets:
        if not normalized_scenario_targets:
            raise ProxyGenerationRunError(
                "base scenario targets require candidate scenario targets"
            )
        if set(normalized_base_scenario_targets) != set(
            normalized_scenario_targets
        ) or any(
            count < 1 or count > normalized_scenario_targets[scenario_id]
            for scenario_id, count in normalized_base_scenario_targets.items()
        ):
            raise ProxyGenerationRunError(
                "base scenario targets must exactly cover and fit candidate targets"
            )
        base_scenario_targets_by_grade: Counter[str] = Counter()
        for scenario_id, count in normalized_base_scenario_targets.items():
            base_scenario_targets_by_grade[scenario_grades[scenario_id]] += count
        if dict(sorted(base_scenario_targets_by_grade.items())) != dict(
            sorted(normalized_base_targets.items())
        ):
            raise ProxyGenerationRunError(
                "base scenario target sums disagree with base grade targets"
            )
    contract = _run_contract(
        catalog_version=catalog_version,
        catalog_sha256=catalog_sha256,
        code_sha256=code_sha256,
        code_components=code_components,
        provider_identity=provider_identity,
        model_attestation=model_attestation,
        plan_summary=plan_summary,
        selection_targets=normalized_targets,
        selection_targets_by_scenario=normalized_scenario_targets,
        base_final_targets=normalized_base_targets,
        base_final_targets_by_scenario=normalized_base_scenario_targets,
        partition_metadata=partition_metadata,
        max_quality_retries=max_quality_retries,
        generation_namespace=effective_generation_namespace,
    )
    plan_keys = {str(row["resume_key"]) for row in descriptors}
    descriptor_by_key = {str(row["resume_key"]): row for row in descriptors}

    is_resume = resume_run is not None
    run_dir = (
        _resolve_resume_run(out_root, resume_run)
        if resume_run is not None
        else _create_run_dir(out_root, effective_new_run_id)
    )
    run_id_value = run_dir.name
    manifest_path = run_dir / "manifest.json"
    progress_path = run_dir / "progress.json"
    candidate_journal_path = run_dir / "candidates.journal.jsonl"
    rejected_journal_path = run_dir / "rejected.journal.jsonl"
    complete_marker_path = run_dir / "COMPLETE.json"

    if is_resume:
        if complete_marker_path.exists():
            raise ProxyGenerationRunError(
                "completed run is immutable and cannot be resumed"
            )
        manifest = _read_json(manifest_path)
        if manifest.get("schema_version") != RUN_SCHEMA_VERSION:
            raise ProxyGenerationRunError("resume run schema version mismatch")
        if manifest.get("run_contract_sha256") != contract["run_contract_sha256"]:
            raise ProxyGenerationRunError(
                "resume contract mismatch (catalog/code/provider/model/plan changed)"
            )
        try:
            prior_model_attestation = validate_ollama_attestation(
                manifest.get("model_attestation"), require_verified=True
            )
        except OllamaAttestationError as exc:
            raise ProxyGenerationRunError(
                "resume manifest model attestation is invalid"
            ) from exc
        if (
            prior_model_attestation["binding_sha256"]
            != contract["model_runtime_attestation_sha256"]
            or model_attestation["binding_sha256"]
            != contract["model_runtime_attestation_sha256"]
        ):
            raise ProxyGenerationRunError(
                "resume model attestation does not match the immutable run contract"
            )
        candidates, rejected, completed_keys = _validated_journal_state(
            candidates_path=candidate_journal_path,
            rejected_path=rejected_journal_path,
            plan_keys=plan_keys,
            expected_contract=contract,
            expected_provider_identity=provider_identity,
        )
        manifest["status"] = "running"
        manifest["resume_count"] = int(manifest.get("resume_count", 0)) + 1
        manifest.setdefault("resumed_at", []).append(_utc_now())
        manifest.setdefault("model_attestation_revalidations", []).append(
            dict(model_attestation)
        )
        manifest["allow_partial"] = allow_partial
        _atomic_write_json(manifest_path, manifest, replace=True)
        _atomic_write_json(
            progress_path,
            _progress_payload(
                run_id=run_id_value,
                plan_summary=plan_summary,
                candidates=candidates,
                rejected=rejected,
                status="running",
            ),
            replace=True,
        )
    else:
        candidates, rejected, completed_keys = [], [], set()
        # Journals exist before the manifest becomes resumable.  A crash after
        # manifest creation can therefore always reconstruct progress from them.
        _create_empty_journal(candidate_journal_path)
        _create_empty_journal(rejected_journal_path)
        manifest = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id_value,
            "status": "running",
            "created_at": _utc_now(),
            "resume_count": 0,
            "allow_partial": allow_partial,
            **contract,
            "provider": dict(provider_identity),
            "model_attestation": dict(model_attestation),
            "model_attestation_revalidations": [],
            "plan": dict(plan_summary),
            "journals": {
                "candidates": candidate_journal_path.name,
                "rejected": rejected_journal_path.name,
            },
        }
        _atomic_write_json(manifest_path, manifest)
        _atomic_write_json(
            progress_path,
            _progress_payload(
                run_id=run_id_value,
                plan_summary=plan_summary,
                candidates=candidates,
                rejected=rejected,
                status="running",
            ),
        )

    candidate_writer: _JournalWriter | None = None
    rejected_writer: _JournalWriter | None = None
    try:
        candidate_writer = _JournalWriter(candidate_journal_path, resume=True)
        rejected_writer = _JournalWriter(rejected_journal_path, resume=True)
        generator = SyntheticDocGenerator(llm=provider)
        for item in plan:
            item_grade = str(item[0]["label"])
            item_scenario = str(item[0]["scenario_id"])
            if normalized_scenario_targets:
                accepted_for_scenario = sum(
                    str(row.get("scenario_id")) == item_scenario for row in candidates
                )
                if accepted_for_scenario >= normalized_scenario_targets[item_scenario]:
                    continue
            else:
                accepted_for_grade = sum(
                    str(row.get("label")) == item_grade for row in candidates
                )
                if accepted_for_grade >= normalized_targets.get(item_grade, 0):
                    continue
            descriptor = descriptor_by_key[
                plan_item_descriptor(
                    item,
                    generation_namespace=effective_generation_namespace,
                )["resume_key"]
            ]
            resume_key = str(descriptor["resume_key"])
            if resume_key in completed_keys:
                continue
            candidate, rejection = _generate_plan_item(
                item,
                generator=generator,
                catalog_version=catalog_version,
                generation_namespace=effective_generation_namespace,
                max_quality_retries=max_quality_retries,
            )
            if candidate is not None:
                enriched = _enrich_run_row(
                    candidate,
                    run_id=run_id_value,
                    descriptor=descriptor,
                    contract=contract,
                    provider_identity=provider_identity,
                    model_attestation=model_attestation,
                    outcome="candidate",
                )
                candidate_writer.append(enriched)
                candidates.append(enriched)
            elif rejection is not None:
                enriched = _enrich_run_row(
                    rejection,
                    run_id=run_id_value,
                    descriptor=descriptor,
                    contract=contract,
                    provider_identity=provider_identity,
                    model_attestation=model_attestation,
                    outcome="rejected",
                )
                rejected_writer.append(enriched)
                rejected.append(enriched)
            else:
                raise RuntimeError("generation item produced no outcome")
            completed_keys.add(resume_key)
            _atomic_write_json(
                progress_path,
                _progress_payload(
                    run_id=run_id_value,
                    plan_summary=plan_summary,
                    candidates=candidates,
                    rejected=rejected,
                    status="running",
                ),
                replace=True,
            )

        candidate_writer.close()
        rejected_writer.close()
        candidate_writer = rejected_writer = None
        candidates = _ordered_rows(candidates, descriptors)
        rejected = _ordered_rows(rejected, descriptors)
        candidate_payload = _jsonl_bytes(candidates)
        rejected_payload = _jsonl_bytes(rejected)
        candidate_final_path = run_dir / "candidates.jsonl"
        rejected_final_path = run_dir / "rejected.jsonl"
        _publish_or_verify(candidate_final_path, candidate_payload)
        _publish_or_verify(rejected_final_path, rejected_payload)

        candidate_by_grade = dict(
            sorted(Counter(str(row["label"]) for row in candidates).items())
        )
        candidate_by_scenario = dict(
            sorted(Counter(str(row["scenario_id"]) for row in candidates).items())
        )
        candidate_by_factor_profile = dict(
            sorted(
                Counter(
                    str(row.get("factor_profile_id") or "") for row in candidates
                ).items()
            )
        )
        grade_selection_sufficient = all(
            int(candidate_by_grade.get(grade, 0)) >= count
            for grade, count in normalized_targets.items()
        )
        scenario_selection_sufficient = not normalized_scenario_targets or all(
            int(candidate_by_scenario.get(scenario_id, 0)) >= count
            for scenario_id, count in normalized_scenario_targets.items()
        )
        selection_sufficient = (
            grade_selection_sufficient and scenario_selection_sufficient
        )
        retention_mode = (
            "pre_judge_buffer"
            if normalized_targets != normalized_base_targets
            else "base_target_only"
        )
        stats = {
            "run_id": run_id_value,
            "generation_namespace": effective_generation_namespace,
            "planned": int(plan_summary["planned"]),
            "completed": len(completed_keys),
            "unused_plan_items": int(plan_summary["planned"]) - len(completed_keys),
            "candidates": len(candidates),
            "rejected": len(rejected),
            "all_plan_items_accepted": len(candidates) == int(plan_summary["planned"]),
            "target_met": selection_sufficient,
            "selection_target_total": sum(normalized_targets.values()),
            "selection_target_by_grade": dict(sorted(normalized_targets.items())),
            "selection_target_by_scenario": dict(
                sorted(normalized_scenario_targets.items())
            ),
            "base_final_target_total": sum(normalized_base_targets.values()),
            "base_final_target_by_grade": dict(sorted(normalized_base_targets.items())),
            "base_final_target_by_scenario": dict(
                sorted(normalized_base_scenario_targets.items())
            ),
            "candidate_buffer_target_total": (
                sum(normalized_targets.values()) - sum(normalized_base_targets.values())
            ),
            "candidate_buffer_target_by_grade": {
                grade: normalized_targets[grade] - normalized_base_targets[grade]
                for grade in sorted(normalized_targets)
            },
            # Keep the complete pre-judge target explicit as well as the
            # incremental buffer.  ``selection_target_*`` remains the
            # canonical compatibility field consumed by the judge attestor.
            "prejudge_candidate_target_total": sum(normalized_targets.values()),
            "prejudge_candidate_target_by_grade": dict(
                sorted(normalized_targets.items())
            ),
            "candidate_buffer_extra_total": (
                sum(normalized_targets.values()) - sum(normalized_base_targets.values())
            ),
            "candidate_buffer_extra_by_grade": {
                grade: normalized_targets[grade] - normalized_base_targets[grade]
                for grade in sorted(normalized_targets)
            },
            "candidate_retention_mode": retention_mode,
            "planned_by_grade": dict(plan_summary["by_grade"]),
            "planned_by_scenario": dict(plan_summary["by_scenario"]),
            "planned_by_factor_profile": dict(plan_summary["by_factor_profile"]),
            "candidate_by_grade": candidate_by_grade,
            "candidate_by_scenario": candidate_by_scenario,
            "candidate_by_factor_profile": candidate_by_factor_profile,
            "rejected_by_reason": dict(
                sorted(Counter(str(row["reason"]) for row in rejected).items())
            ),
            "completed_keys_sha256": _sha256_bytes(
                _canonical_json_bytes(sorted(completed_keys))
            ),
            "claim_scope": "synthetic_proxy_candidate_only",
            "human_reviewed": False,
        }
        stats_payload = (
            json.dumps(stats, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
        )
        stats_path = run_dir / "stats.json"
        _publish_or_verify(stats_path, stats_payload)
        _atomic_write_json(
            progress_path,
            {
                **_progress_payload(
                    run_id=run_id_value,
                    plan_summary=plan_summary,
                    candidates=candidates,
                    rejected=rejected,
                    status="complete",
                ),
                "target_met": stats["target_met"],
            },
            replace=True,
        )
        manifest.update(
            {
                "status": "complete",
                "completed_at": _utc_now(),
                "allow_partial": allow_partial,
                "stats": stats,
                "final_artifacts": {
                    "candidates": candidate_final_path.name,
                    "candidates_sha256": _sha256_bytes(candidate_payload),
                    "rejected": rejected_final_path.name,
                    "rejected_sha256": _sha256_bytes(rejected_payload),
                    "stats": stats_path.name,
                    "stats_sha256": _sha256_bytes(stats_payload),
                },
                "latest_model_attestation": dict(model_attestation),
            }
        )
        _atomic_write_json(manifest_path, manifest, replace=True)
        # COMPLETE is the final commit marker.  Consumers must ignore final
        # artifacts unless this file exists and its contract/hash fields match.
        _atomic_write_json(
            complete_marker_path,
            {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": run_id_value,
                "generation_namespace": effective_generation_namespace,
                "committed_at": _utc_now(),
                "run_contract_sha256": contract["run_contract_sha256"],
                "model_runtime_attestation_sha256": contract[
                    "model_runtime_attestation_sha256"
                ],
                "model_attestation": dict(model_attestation),
                "manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
                "candidates_sha256": _sha256_bytes(candidate_payload),
                "rejected_sha256": _sha256_bytes(rejected_payload),
                "stats_sha256": _sha256_bytes(stats_payload),
                "target_met": stats["target_met"],
                "allow_partial": allow_partial,
            },
        )
        return run_dir, stats
    except KeyboardInterrupt:
        manifest.update({"status": "interrupted", "updated_at": _utc_now()})
        _atomic_write_json(manifest_path, manifest, replace=True)
        raise
    except Exception as exc:
        manifest.update(
            {
                "status": "failed",
                "updated_at": _utc_now(),
                "error_type": type(exc).__name__,
            }
        )
        _atomic_write_json(manifest_path, manifest, replace=True)
        raise
    finally:
        if candidate_writer is not None:
            candidate_writer.close()
        if rejected_writer is not None:
            rejected_writer.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate catalog-driven proxy corpus candidates"
    )
    parser.add_argument(
        "--catalog", default="datasets/proxy_gold/scenario_catalog.v1.json"
    )
    parser.add_argument("--out-root", default="datasets/proxy_gold/generation_runs")
    run_group = parser.add_mutually_exclusive_group()
    run_group.add_argument("--run-id", help="new immutable run id")
    run_group.add_argument(
        "--resume-run",
        type=Path,
        help="existing incomplete run directory or id under --out-root",
    )
    parser.add_argument(
        "--provider",
        required=True,
        help="explicit real provider; noop/unknown/fake are blocked",
    )
    parser.add_argument(
        "--model-manifest-sha256",
        help="pinned model manifest revision as sha256:<64 hex>; required for execution",
    )
    parser.add_argument(
        "--generation-namespace",
        help=(
            "immutable namespace for doc_id/resume keys; defaults to --run-id "
            "or the resumed directory name"
        ),
    )
    parser.add_argument(
        "--per-scenario",
        type=int,
        default=1,
        help="pilot count per scenario (default: 1)",
    )
    parser.add_argument(
        "--target-counts",
        action="store_true",
        help="ignore --per-scenario and use each catalog target_count",
    )
    parser.add_argument(
        "--oversample-factor",
        type=float,
        default=1.0,
        help="generate ceil(target * factor) per scenario while preserving final targets",
    )
    parser.add_argument(
        "--candidate-buffer-factor",
        type=float,
        default=1.0,
        help=(
            "retain ceil(base target * factor) accepted candidates before judging; "
            "must be <= --oversample-factor (default: 1.0, legacy behaviour)"
        ),
    )
    parser.add_argument(
        "--shard-count",
        type=int,
        help="deterministic document-family shard count; requires --shard-index",
    )
    parser.add_argument(
        "--shard-index",
        type=int,
        help="zero-based deterministic document-family shard index",
    )
    parser.add_argument(
        "--scenario", action="append", help="generate only this scenario_id; repeatable"
    )
    parser.add_argument(
        "--grade",
        action="append",
        choices=["TS", "S1", "S2", "S3"],
        help="generate only the selected grade; repeatable",
    )
    parser.add_argument(
        "--factor-profile",
        action="append",
        help="generate only this factor_profile_id; repeatable",
    )
    parser.add_argument(
        "--representative-pilot",
        action="store_true",
        help="select the canonical four S/V/M profiles for a boundary pilot",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="return zero even when rejected outputs leave the target unmet",
    )
    parser.add_argument(
        "--max-quality-retries",
        type=int,
        default=1,
        help="regenerate an item after candidate-quality failure (default: 1)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.per_scenario < 1:
        raise SystemExit("--per-scenario must be >= 1")
    if args.oversample_factor < 1.0:
        raise SystemExit("--oversample-factor must be >= 1.0")
    if args.candidate_buffer_factor < 1.0:
        raise SystemExit("--candidate-buffer-factor must be >= 1.0")
    if args.candidate_buffer_factor > args.oversample_factor:
        raise SystemExit("--candidate-buffer-factor must be <= --oversample-factor")
    if (args.shard_count is None) != (args.shard_index is None):
        raise SystemExit("--shard-count and --shard-index must be provided together")
    if args.max_quality_retries < 0:
        raise SystemExit("--max-quality-retries must be >= 0")
    if (
        not args.dry_run
        and args.generation_namespace is None
        and args.run_id is None
        and args.resume_run is None
    ):
        raise SystemExit(
            "execution requires --run-id or --generation-namespace so top-up "
            "identities cannot reuse the main namespace"
        )
    generation_namespace = _validated_generation_namespace(
        args.generation_namespace
        or args.run_id
        or (args.resume_run.name if args.resume_run is not None else "main")
    )

    catalog_path = Path(args.catalog)
    catalog, scenarios = load_catalog(catalog_path)
    wanted = set(args.scenario or [])
    wanted_grades = set(args.grade or [])
    wanted_profiles = set(args.factor_profile or [])
    if wanted or wanted_grades or wanted_profiles or args.representative_pilot:
        scenarios = [
            scenario
            for scenario in scenarios
            if (not wanted or scenario.get("scenario_id") in wanted)
            and (not wanted_grades or scenario.get("label") in wanted_grades)
            and (
                not wanted_profiles
                or scenario.get("factor_profile_id") in wanted_profiles
            )
            and (
                not args.representative_pilot
                or scenario.get("representative_pilot") is True
            )
        ]
        if not scenarios:
            raise SystemExit(
                "requested scenario/grade/factor-profile filter has no catalog match"
            )
    family_profiles = [
        row for row in catalog.get("family_profiles", []) if isinstance(row, dict)
    ]
    instance_profiles = [
        row for row in catalog.get("instance_profiles", []) if isinstance(row, dict)
    ]
    per_scenario = None if args.target_counts else args.per_scenario
    full_plan = generation_plan(
        scenarios,
        instance_profiles,
        family_profiles,
        per_scenario=per_scenario,
        count_multiplier=args.oversample_factor,
    )
    partition_metadata: dict[str, object] | None = None
    if args.shard_count is not None and args.shard_index is not None:
        try:
            plan, partition_metadata = partition_generation_plan_by_family(
                full_plan,
                shard_count=args.shard_count,
                shard_index=args.shard_index,
            )
        except ValueError as exc:
            raise SystemExit(f"invalid generation shard: {exc}") from exc
        selected_scenario_ids = {str(item[0]["scenario_id"]) for item in plan}
        scenarios = [
            scenario
            for scenario in scenarios
            if str(scenario["scenario_id"]) in selected_scenario_ids
        ]
    else:
        plan = full_plan
    (
        base_targets_by_scenario,
        base_targets,
        selection_targets_by_scenario,
        selection_targets,
    ) = generation_target_maps(
        scenarios,
        per_scenario=per_scenario,
        candidate_buffer_factor=args.candidate_buffer_factor,
    )
    planned_by_scenario = Counter(str(item[0]["scenario_id"]) for item in plan)
    if any(
        planned_by_scenario.get(scenario_id, 0) < target
        for scenario_id, target in selection_targets_by_scenario.items()
    ):
        raise SystemExit(
            "candidate buffer target exceeds oversampled generation plan; "
            "increase --oversample-factor"
        )
    try:
        provider = build_provider(args.provider)
        if not args.dry_run and not args.model_manifest_sha256:
            raise ProxyGenerationRunError(
                "--model-manifest-sha256 is required for an auditable generation run"
            )
        model_attestation = (
            generation_model_attestation(
                provider,
                requested_name=args.provider,
                expected_model_revision=args.model_manifest_sha256,
                live=False,
            )
            if args.dry_run
            else None
        )
        provider_identity = provider_run_identity(
            provider,
            requested_name=args.provider,
            declared_model_revision=args.model_manifest_sha256,
            model_attestation=model_attestation,
        )
        descriptors, plan_summary = describe_plan(
            plan, generation_namespace=generation_namespace
        )
        catalog_sha256 = _sha256_bytes(catalog_path.read_bytes())
        runner_code_sha256 = _sha256_bytes(Path(__file__).read_bytes())
        generator_path = _POC / "src/koipa/modules/m1_synthesis/generator.py"
        generator_code_sha256 = _sha256_bytes(generator_path.read_bytes())
        prompt_contract_sha256 = _sha256_bytes(
            _canonical_json_bytes(
                {
                    "system_prompt": SYSTEM_PROMPT,
                    "user_template": USER_TEMPLATE_V2,
                    "retry_contract": "same-system-plus-json-only;temperature=0.3;max_retries=2",
                }
            )
        )
        code_components = {
            "runner_code_sha256": runner_code_sha256,
            "generator_code_sha256": generator_code_sha256,
            "prompt_contract_sha256": prompt_contract_sha256,
        }
        code_sha256 = _sha256_bytes(_canonical_json_bytes(code_components))
        if args.dry_run:
            if args.resume_run is not None:
                raise ProxyGenerationRunError("dry-run cannot resume an existing run")
            families = {
                f"{scenario['document_family_id']}:{instance['instance_profile_id']}"
                for scenario, instance, _, _ in plan
            }
            shapes = Counter(str(shape["family_profile_id"]) for _, _, shape, _ in plan)
            print(
                json.dumps(
                    {
                        "scenarios": len(scenarios),
                        "documents": len(plan),
                        "families": len(families),
                        "shapes": dict(sorted(shapes.items())),
                        "by_grade": dict(plan_summary["by_grade"]),
                        "by_factor_profile": dict(
                            plan_summary["by_factor_profile"]
                        ),
                        "plan_sha256": plan_summary["plan_sha256"],
                        "catalog_sha256": catalog_sha256,
                        "code_sha256": code_sha256,
                        **code_components,
                        "provider": provider_identity,
                        "model_attestation": model_attestation,
                        "generation_namespace": generation_namespace,
                        "resume_keys": len(descriptors),
                        "selection_target_by_grade": dict(
                            sorted(selection_targets.items())
                        ),
                        "selection_target_by_scenario": dict(
                            sorted(selection_targets_by_scenario.items())
                        ),
                        "base_final_target_by_grade": dict(
                            sorted(base_targets.items())
                        ),
                        "base_final_target_by_scenario": dict(
                            sorted(base_targets_by_scenario.items())
                        ),
                        "oversample_factor": args.oversample_factor,
                        "candidate_buffer_factor": args.candidate_buffer_factor,
                        "partition": partition_metadata or {"strategy": "unsharded-v1"},
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        run_dir, stats = run_generation(
            plan,
            provider=provider,
            requested_provider=args.provider,
            catalog_version=str(catalog.get("version") or "unknown"),
            catalog_sha256=catalog_sha256,
            code_sha256=code_sha256,
            code_components=code_components,
            declared_model_revision=args.model_manifest_sha256,
            selection_targets=selection_targets,
            selection_targets_by_scenario=selection_targets_by_scenario,
            base_final_targets=base_targets,
            base_final_targets_by_scenario=base_targets_by_scenario,
            partition_metadata=partition_metadata,
            out_root=Path(args.out_root),
            run_id=args.run_id,
            resume_run=args.resume_run,
            allow_partial=args.allow_partial,
            max_quality_retries=args.max_quality_retries,
            generation_namespace=generation_namespace,
        )
    except (OSError, ProxyGenerationRunError) as exc:
        print(f"[BLOCK] {exc}", file=sys.stderr)
        return 2

    print(json.dumps({"run_dir": str(run_dir), **stats}, ensure_ascii=False))
    return completion_exit_code(stats, allow_partial=args.allow_partial)


if __name__ == "__main__":
    raise SystemExit(main())
