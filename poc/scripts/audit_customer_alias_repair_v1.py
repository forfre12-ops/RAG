"""Independent bounded alias checks and preregistered paired diagnostics.

Never imports the repair transform to derive expected text. This script does
not clear broader authoring HOLD, certify semantics, or release any dataset.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import sys

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_batch06 as original_batch
from audit_customer_title_sensitivity_v1 import validate_grouping
from koipa.customer_benchmark import (
    FLAGS,
    GRADES,
    presented_text,
    strict_loads,
    validate_answers,
    validate_documents,
)
from koipa.customer_reference_audit_v1 import audit_reference
from koipa.policy_facts import require, text_digest, value_digest

ORIGINAL_MANIFEST_SHA256 = (
    "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95"
)
GROUPING_FILE_SHA256 = (
    "c491d00a77c0b8aee0c753c036ce8865df975d9d0c47212ff7a7fe0d840a60ec"
)
KNOWN_MARKER = (
    r"(?:분말|표면|효소|저장소|분할|저장 경로|거래군|공급군|경로|상품|구역|라인) [A-Z]"
)
BROAD_MARKER = r"(?<![A-Za-z])[A-Z](?![A-Za-z])"
NOUNS = (
    "저장 경로",
    "기질 배치",
    "분말",
    "표면",
    "효소",
    "저장소",
    "분할",
    "거래군",
    "공급군",
    "경로",
    "상품",
    "구역",
    "라인",
)
ALIAS = re.compile(
    r"(?<![가-힣A-Za-z])(?P<noun>"
    + "|".join(NOUNS)
    + r") (?P<alias>[A-Z])(?P<particle>에는|에서는|으로|에서|은|는|이|가|을|를|과|와|의|에|도|로)(?=$|[\s,.!?])"
)
NUMBER = re.compile(r"[0-9]+(?:[.,][0-9]+)*")
CONDITION_FRAGMENTS = (
    "않",
    "없",
    "아니",
    "못",
    "경우",
    "때",
    "이전",
    "이후",
    "전에",
    "뒤",
    "한정",
    "별도",
)
PROFILES = ("body_only", "body_context")
ARMS = ("original_training", "repaired_training")
VIEWS = ("original", "repaired")
# Full-body read of these 16 originals found surviving local restrictions, but
# none restores the removed named target. These quotes are evidence, not a pass.
SCOPE_REVIEW = {
    "ceramic-ramp-knee": "밀도가 다른 성형체로 그대로 옮기지 않는다.",
    "droplet-hysteresis": "두 값은 같은 액체와 표면 배치에서 얻었다.",
    "powder-charge-decay": "받침 재질을 바꾸면 기존 조건을 그대로 적용하지 않습니다.",
    "enzyme-addition-lag": "이번 결과는 기질 배치 M에 한정하며 다른 농도의 반응 속도로 그대로 환산하지 않습니다.",
    "snapshot-cut-index": "번호가 연속인 시험 로그의 값이다.",
    "bloom-probe-order": "필터 판본과 데이터 판본이 일치할 때만",
    "multipart-commit-map": "이번 결합에서 검증한 조각은 몇 개인가요?",
    "candidate-prune-order": "오래된 상한값을 새 자료에 그대로 적용하지 않습니다.",
    "bundle-exit-clause": "이번 표본의 문의 감소를 다른 상품의 해지율 감소로 해석하지 않는다.",
    "supplier-delay-window": "이 조건은 해당 부품군의 관측에서 얻었으며 모든 공급자에게 그대로 적용하지 않는다.",
    "channel-incentive-return": "경로별 고객 구성 차이를 지원금 효과로 단정하지 않습니다.",
    "subscription-downgrade": "이 절차를 다른 계약의 권리 판단에 그대로 적용하지 않습니다.",
    "cleanroom-return-slot": "이 설정은 빈 작업대 조건이며 실제 작업 중의 상태는 별도 검증한다.",
    "slot-replenish-wave": "이 규칙을 품목 규격이 다른 창고에 그대로 적용하지 않는다.",
    "furnace-standby-chain": "이 문답은 기록된 순서의 의미를 설명하며 가열 설비의 실제 조작을 승인하지 않습니다.",
    "hoist-transfer-window": "이 문서는 모사 결과 비교용이며 실제 인양·이송을 지시하거나 안전 적합성을 승인하지 않습니다.",
}


def scope_review(record, expected):
    text, family = record["input"]["text"], record["family_id"]
    quote = SCOPE_REVIEW.get(family.removeprefix("family-"))
    if quote is not None:
        require(text.count(quote) == 1, "alias_manual_scope_quote_changed")
    return {
        "title": text.splitlines()[0],
        "family_id": family,
        "full_body_bounded_read_performed": quote is not None,
        "remaining_local_scope_evidence": None
        if quote is None
        else {
            "quote": quote,
            "start": text.index(quote),
            "end": text.index(quote) + len(quote),
        },
        "removed_target_bindings": expected["distinct_noun_targets"],
        "target_binding_preservation_certified": False,
        "status": "TARGET_BINDING_NOT_CERTIFIED_HOLD",
        "reason": "Named target identity is removed; remaining local conditions do not prove the noun denotes exactly the same target. This is a diagnostic manipulation, not certified semantics-preserving repair.",
    }


def _particle(noun, particle):
    final = (ord(noun[-1]) - 0xAC00) % 28
    require(0xAC00 <= ord(noun[-1]) <= 0xD7A3, "alias_noun_not_hangul")
    for consonant, vowel in (("은", "는"), ("이", "가"), ("을", "를"), ("과", "와")):
        if particle in (consonant, vowel):
            return consonant if final else vowel
    if particle in ("으로", "로"):
        return "로" if final in (0, 8) else "으로"
    return particle


def independent_expected(text):
    """Separate noun/particle parser, not the author's per-parent registry."""
    require(type(text) is str and bool(text), "alias_empty_text")
    spans, pieces, cursor, delta = [], [], 0, 0
    targets = {}
    for match in ALIAS.finditer(text):
        noun, alias, particle = match.group("noun", "alias", "particle")
        require(
            noun not in targets or targets[noun] == alias,
            "alias_distinct_targets_would_collapse",
        )
        targets[noun] = alias
        after = noun + _particle(noun, particle)
        before = match.group()
        pieces.extend((text[cursor : match.start()], after))
        spans.append(
            {
                "start": match.start(),
                "end": match.end(),
                "before": before,
                "after": after,
                "target_start": match.start() + delta,
                "target_end": match.start() + delta + len(after),
                "noun": noun,
                "alias": alias,
            }
        )
        delta += len(after) - len(before)
        cursor = match.end()
    pieces.append(text[cursor:])
    result = "".join(pieces)
    require(
        not re.search(r"(?:" + "|".join(NOUNS) + r") [A-Z](?![A-Za-z])", result),
        "alias_unreviewed_particle_or_alias",
    )
    require(NUMBER.findall(text) == NUMBER.findall(result), "alias_numeric_change")
    require(
        all(text.count(word) == result.count(word) for word in CONDITION_FRAGMENTS),
        "alias_condition_fragment_change",
    )
    require(text.splitlines()[0] == result.splitlines()[0], "alias_title_change")
    return {
        "text": result,
        "edits": spans,
        "distinct_noun_targets": targets,
        "numeric_sequence": NUMBER.findall(text),
        "changed": text != result,
    }


def _rebound_claims(claims, text, edits):
    def position(offset):
        require(
            not any(e["start"] < offset < e["end"] for e in edits),
            "alias_claim_boundary_inside_edit",
        )
        return offset + sum(
            len(e["after"]) - len(e["before"]) for e in edits if e["end"] <= offset
        )

    result = []
    for claim in claims:
        require(
            claim["claim"] == claim["quote"], "alias_nonliteral_claim_requires_review"
        )
        start, end = position(claim["start"]), position(claim["end"])
        quote = text[start:end]
        result.append(
            {
                **claim,
                "start": start,
                "end": end,
                "quote": quote,
                "claim": quote,
                "sha256": text_digest(quote),
            }
        )
    return result


def marker_counts(records, labels):
    rows = []
    for record in records:
        inp = record["input"]
        body = inp["text"]
        rows.append(
            {
                "doc_id": inp["doc_id"],
                "body_sha256": text_digest(body),
                "reference_grade": labels[inp["doc_id"]],
                "known_marker": [
                    {"start": m.start(), "end": m.end(), "quote": m.group()}
                    for m in re.finditer(KNOWN_MARKER, body)
                ],
                "broad_single_letter": [
                    {
                        "start": m.start(),
                        "end": m.end(),
                        "quote": m.group(),
                        "nearby": body[max(0, m.start() - 10) : m.end() + 10],
                    }
                    for m in re.finditer(BROAD_MARKER, body)
                ],
            }
        )
    return {
        "rows": rows,
        "by_grade": {
            g: {
                "documents": sum(r["reference_grade"] == g for r in rows),
                "known_marker_documents": sum(
                    r["reference_grade"] == g and bool(r["known_marker"]) for r in rows
                ),
                "known_marker_occurrences": sum(
                    len(r["known_marker"]) for r in rows if r["reference_grade"] == g
                ),
                "broad_marker_documents": sum(
                    r["reference_grade"] == g and bool(r["broad_single_letter"])
                    for r in rows
                ),
                "broad_marker_occurrences": sum(
                    len(r["broad_single_letter"])
                    for r in rows
                    if r["reference_grade"] == g
                ),
            }
            for g in GRADES
        },
        "known_pattern": KNOWN_MARKER,
        "broad_pattern": BROAD_MARKER,
        "broad_matches_are_not_automatically_defects": True,
        "zero_count_is_not_quality_pass": True,
    }


def audit_repair(
    original_records,
    original_answers,
    original_details,
    active_records,
    active_answers,
    active_details,
    lineage,
):
    """Compare two populations; only explicit lineage parents may be replaced."""
    before = value_digest(
        [
            original_records,
            original_answers,
            original_details,
            active_records,
            active_answers,
            active_details,
            lineage,
        ]
    )
    audit_reference(original_records, original_answers, original_details)
    audit_reference(active_records, active_answers, active_details)
    require(
        len(active_records) == len(original_records), "alias_population_count_changed"
    )
    require(
        type(lineage) is list and 0 < len(lineage) <= len(original_records),
        "alias_lineage_empty_or_invalid",
    )
    old = {r["input"]["doc_id"]: r for r in original_records}
    new = {r["input"]["doc_id"]: r for r in active_records}
    oa, na = (
        {a["doc_id"]: a for a in rows} for rows in (original_answers, active_answers)
    )
    od, nd = (
        {d["doc_id"]: d for d in rows} for rows in (original_details, active_details)
    )
    require(
        all(
            type(row) is dict
            and row.get("parent_doc_id") in old
            and row.get("child_doc_id") in new
            for row in lineage
        ),
        "alias_lineage_unknown_id",
    )
    parents, children = (
        [r["parent_doc_id"] for r in lineage],
        [r["child_doc_id"] for r in lineage],
    )
    require(
        len(set(parents)) == len(parents) and len(set(children)) == len(children),
        "alias_lineage_reused_id",
    )
    require(
        set(new) == (set(old) - set(parents)) | set(children),
        "alias_active_population_mismatch",
    )
    for doc_id in set(old) - set(parents):
        require(
            old[doc_id] == new[doc_id]
            and oa[doc_id] == na[doc_id]
            and od[doc_id] == nd[doc_id],
            "alias_nonpanel_changed",
        )
    rows = []
    for item in lineage:
        parent_id, child_id = item["parent_doc_id"], item["child_doc_id"]
        parent, child = old[parent_id], new[child_id]
        expected = independent_expected(parent["input"]["text"])
        require(
            child["input"]["text"] == expected["text"], "alias_expected_text_mismatch"
        )
        require(
            child["input"]["context"] == parent["input"]["context"],
            "alias_context_changed",
        )
        require(
            {
                k: v
                for k, v in parent.items()
                if k not in {"input", "input_sha256", "claims"}
            }
            == {
                k: v
                for k, v in child.items()
                if k not in {"input", "input_sha256", "claims"}
            },
            "alias_metadata_changed",
        )
        require(
            child["claims"]
            == _rebound_claims(parent["claims"], expected["text"], expected["edits"]),
            "alias_evidence_binding_changed",
        )
        edits = item.get("edits")
        require(
            type(edits) is list and len(edits) == len(expected["edits"]),
            "alias_edit_coverage",
        )
        for actual, wanted in zip(edits, expected["edits"], strict=True):
            require(
                type(actual) is dict
                and all(actual.get(k) == v for k, v in wanted.items()),
                "alias_edit_mismatch",
            )
            require(
                type(actual.get("reason")) is str
                and len(actual["reason"].strip()) >= 10,
                "alias_edit_reason_missing",
            )
        require(
            item.get("parent_input_sha256") == parent["input_sha256"]
            and item.get("child_input_sha256") == child["input_sha256"]
            and item.get("parent_body_sha256") == text_digest(parent["input"]["text"])
            and item.get("child_body_sha256") == text_digest(child["input"]["text"]),
            "alias_lineage_hash_mismatch",
        )
        require(
            all(
                item.get(k) == v
                for k, v in {
                    "parent_answer_sha256": value_digest(oa[parent_id]),
                    "child_answer_sha256": value_digest(na[child_id]),
                    "parent_evidence_sha256": value_digest(od[parent_id]),
                    "child_evidence_sha256": value_digest(nd[child_id]),
                    "context_sha256": value_digest(parent["input"]["context"]),
                    "policy_sha256": oa[parent_id]["policy_sha256"],
                    "family_id": parent["family_id"],
                    "scenario_id": parent["scenario_id"],
                    "template_family_id": parent["template_family_id"],
                }.items()
            ),
            "alias_lineage_binding_mismatch",
        )
        require(
            item.get("changed") is expected["changed"]
            and type(item.get("new_document_count")) is int
            and item["new_document_count"] == 0
            and all(item.get(k) is False for k in FLAGS),
            "alias_lineage_permission_or_count_changed",
        )
        require(
            (parent_id != child_id) == expected["changed"], "alias_changed_id_invalid"
        )
        if expected["changed"]:
            require(child_id not in old, "alias_child_reuses_existing_id")
        else:
            require(
                parent == child
                and oa[parent_id] == na[child_id]
                and od[parent_id] == nd[child_id],
                "alias_unchanged_parent_mutated",
            )
        require(
            {
                k: v
                for k, v in oa[parent_id].items()
                if k not in {"doc_id", "input_sha256"}
            }
            == {
                k: v
                for k, v in na[child_id].items()
                if k not in {"doc_id", "input_sha256"}
            },
            "alias_reference_or_exclusion_changed",
        )
        require(
            {
                k: v
                for k, v in od[parent_id].items()
                if k not in {"doc_id", "input_sha256", "body_sha256"}
            }
            == {
                k: v
                for k, v in nd[child_id].items()
                if k not in {"doc_id", "input_sha256", "body_sha256"}
            },
            "alias_policy_evidence_changed",
        )
        rows.append(
            {
                "parent_doc_id": parent_id,
                "child_doc_id": child_id,
                "changed": expected["changed"],
                "independent_expected_edits": expected["edits"],
                "distinct_noun_targets": expected["distinct_noun_targets"],
                "reference_grade": oa[parent_id]["reference_grade"],
                "bounded_syntactic_checks_passed": True,
                "scope_review": scope_review(parent, expected)
                if expected["changed"]
                else None,
            }
        )
    require(
        value_digest(
            [
                original_records,
                original_answers,
                original_details,
                active_records,
                active_answers,
                active_details,
                lineage,
            ]
        )
        == before,
        "alias_audit_mutated_input",
    )
    return {
        **FLAGS,
        "status": "BOUNDED_ALIAS_CHECK_PASSED_BROADER_AUTHORING_HOLD",
        "original_documents": len(old),
        "diagnostic_perturbation_only": True,
        "adoption_allowed": False,
        "authoritative_parent_replaced": False,
        "target_binding_not_certified": True,
        "active_documents": len(new),
        "panel_parents": len(parents),
        "changed_parents": sum(r["changed"] for r in rows),
        "unchanged_panel_parents": sum(not r["changed"] for r in rows),
        "fixed_old_controls": len(old) - len(parents),
        "new_benchmark_documents": 0,
        "rows": rows,
        "checked_edit_count": sum(len(r["independent_expected_edits"]) for r in rows),
        "before_markers": marker_counts(
            [old[i] for i in parents], {k: v["reference_grade"] for k, v in oa.items()}
        ),
        "after_markers": marker_counts(
            [new[i] for i in children], {k: v["reference_grade"] for k, v in na.items()}
        ),
        "source_collection_sha256": before,
        "semantic_equivalence_certified": False,
        "all_korean_prose_grammar_certified": False,
        "scope": "Separate noun/particle oracle and exact unchanged-span/claim/context/policy checks. Not a general semantic validator.",
        "broader_authoring_quality_hold_cleared": False,
    }


def default_pipeline(seed):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC

    return make_pipeline(
        TfidfVectorizer(
            analyzer="char", ngram_range=(2, 5), min_df=1, max_features=30000
        ),
        LinearSVC(random_state=seed, max_iter=5000),
    )


def _summary(rows):
    require(bool(rows), "alias_summary_empty")
    n = len(rows)
    flips = sum(
        r["predictions"]["original"] != r["predictions"]["repaired"] for r in rows
    )
    return {
        "parent_seed_observations": n,
        "unique_parents": len({r["parent_doc_id"] for r in rows}),
        "reference_agreement": {
            view: sum(r["predictions"][view] == r["reference_grade"] for r in rows) / n
            for view in VIEWS
        },
        "test_view_prediction_flips": flips,
        "test_view_flip_rate": flips / n,
        "correct_to_wrong": sum(
            r["predictions"]["original"] == r["reference_grade"]
            and r["predictions"]["repaired"] != r["reference_grade"]
            for r in rows
        ),
        "wrong_to_correct": sum(
            r["predictions"]["original"] != r["reference_grade"]
            and r["predictions"]["repaired"] == r["reference_grade"]
            for r in rows
        ),
        "confusion": {
            view: {
                truth: {
                    pred: sum(
                        r["reference_grade"] == truth and r["predictions"][view] == pred
                        for r in rows
                    )
                    for pred in GRADES
                }
                for truth in GRADES
            }
            for view in VIEWS
        },
    }


def measure(
    original_records,
    original_answers,
    active_records,
    lineage,
    grouping,
    *,
    seeds=5,
    folds=5,
    pipeline_factory=None,
):
    import numpy as np
    import sklearn
    from sklearn.model_selection import StratifiedGroupKFold

    require(
        type(seeds) is int
        and 1 <= seeds <= 30
        and type(folds) is int
        and 2 <= folds <= 5,
        "alias_cv_parameters_invalid",
    )
    before = value_digest(
        [original_records, original_answers, active_records, lineage, grouping]
    )
    docs = sorted(validate_documents(original_records), key=lambda d: d.input.doc_id)
    active = {d.input.doc_id: d for d in validate_documents(active_records)}
    validate_answers(docs, original_answers)
    labels_by_id = {a["doc_id"]: a["reference_grade"] for a in original_answers}
    require(
        set(labels_by_id) == {d.input.doc_id for d in docs}
        and len(original_answers) == len(docs),
        "alias_diagnostic_label_coverage",
    )
    require(type(lineage) is list and bool(lineage), "alias_diagnostic_empty_lineage")
    paired = {r["parent_doc_id"]: r["child_doc_id"] for r in lineage}
    require(
        len(paired) == len(lineage)
        and set(paired) <= set(labels_by_id)
        and len(set(paired.values())) == len(paired)
        and set(active) == (set(labels_by_id) - set(paired)) | set(paired.values()),
        "alias_diagnostic_pair_invalid",
    )
    variants = {
        d.input.doc_id: active[paired.get(d.input.doc_id, d.input.doc_id)] for d in docs
    }
    for d in docs:
        require(
            variants[d.input.doc_id].input.text
            == independent_expected(d.input.text)["text"]
            if d.input.doc_id in paired
            else variants[d.input.doc_id] == d,
            "alias_diagnostic_unverified_view",
        )
        require(
            variants[d.input.doc_id].input.context == d.input.context,
            "alias_diagnostic_context_changed",
        )
    changed = {
        d.input.doc_id
        for d in docs
        if variants[d.input.doc_id].input.text != d.input.text
    }
    cohorts = {
        "all_population": set(labels_by_id),
        "panel": set(paired),
        "changed_panel": changed,
        "unchanged_panel": set(paired) - changed,
        "old_unchanged_controls": set(labels_by_id) - set(paired),
    }
    mapping = validate_grouping(docs, grouping)
    labels = np.array([labels_by_id[d.input.doc_id] for d in docs])
    groups = np.array([mapping[d.input.doc_id] for d in docs])
    require(
        set(labels) == set(GRADES)
        and min(Counter(labels).values()) >= folds
        and len(set(groups)) >= folds,
        "alias_cv_population_invalid",
    )
    factory = default_pipeline if pipeline_factory is None else pipeline_factory
    rows, assignments = [], []
    for seed in range(seeds):
        seen = set()
        split = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
        for fold, (train, test) in enumerate(
            split.split(np.arange(len(docs)), labels, groups)
        ):
            require(
                len(train) > 0
                and len(test) > 0
                and not set(groups[train]) & set(groups[test])
                and not seen & set(test),
                "alias_cv_group_overlap",
            )
            require(
                set(labels[train]) == set(GRADES), "alias_cv_training_class_missing"
            )
            seen.update(test)
            assignments.append(
                {
                    "seed": seed,
                    "fold": fold,
                    "train_parent_ids": [docs[i].input.doc_id for i in train],
                    "test_parent_ids": [docs[i].input.doc_id for i in test],
                    "train_groups": sorted(set(groups[train])),
                    "test_groups": sorted(set(groups[test])),
                }
            )
            for profile in PROFILES:
                for arm in ARMS:
                    model = factory(seed)
                    train_docs = [
                        docs[i]
                        if arm == "original_training"
                        else variants[docs[i].input.doc_id]
                        for i in train
                    ]
                    model.fit(
                        [presented_text(d, profile) for d in train_docs],
                        labels[train].tolist(),
                    )
                    predictions = {}
                    for view in VIEWS:
                        test_docs = [
                            docs[i]
                            if view == "original"
                            else variants[docs[i].input.doc_id]
                            for i in test
                        ]
                        values = list(
                            model.predict(
                                [presented_text(d, profile) for d in test_docs]
                            )
                        )
                        require(
                            len(values) == len(test)
                            and all(
                                type(v) in (str, np.str_) and str(v) in GRADES
                                for v in values
                            ),
                            "alias_prediction_invalid",
                        )
                        predictions[view] = list(map(str, values))
                    for offset, i in enumerate(test):
                        rows.append(
                            {
                                "seed": seed,
                                "fold": fold,
                                "profile": profile,
                                "training_arm": arm,
                                "parent_doc_id": docs[i].input.doc_id,
                                "reference_grade": str(labels[i]),
                                "predictions": {
                                    view: predictions[view][offset] for view in VIEWS
                                },
                            }
                        )
        require(seen == set(range(len(docs))), "alias_cv_seed_incomplete")
    expected = len(docs) * seeds * len(PROFILES) * len(ARMS)
    require(
        len(rows) == expected
        and len(
            {
                (r["seed"], r["profile"], r["training_arm"], r["parent_doc_id"])
                for r in rows
            }
        )
        == expected,
        "alias_cv_pair_coverage",
    )
    summary = {
        profile: {
            arm: {
                cohort: _summary(
                    [
                        r
                        for r in rows
                        if r["profile"] == profile
                        and r["training_arm"] == arm
                        and r["parent_doc_id"] in ids
                    ]
                )
                for cohort, ids in cohorts.items()
                if ids
            }
            for arm in ARMS
        }
        for profile in PROFILES
    }
    training_changes = {}
    keyed = {
        (r["seed"], r["profile"], r["training_arm"], r["parent_doc_id"]): r
        for r in rows
    }
    for profile in PROFILES:
        training_changes[profile] = {}
        for view in VIEWS:
            comparisons = []
            for r in rows:
                if r["profile"] != profile or r["training_arm"] != "original_training":
                    continue
                changed_row = keyed[
                    r["seed"], profile, "repaired_training", r["parent_doc_id"]
                ]
                comparisons.append(
                    {
                        "parent_doc_id": r["parent_doc_id"],
                        "reference_grade": r["reference_grade"],
                        "predictions": {
                            "original": r["predictions"][view],
                            "repaired": changed_row["predictions"][view],
                        },
                    }
                )
            training_changes[profile][view] = {}
            for cohort, ids in cohorts.items():
                if not ids:
                    continue
                cell = _summary([r for r in comparisons if r["parent_doc_id"] in ids])
                cell["training_arm_prediction_flips"] = cell.pop(
                    "test_view_prediction_flips"
                )
                cell["training_arm_flip_rate"] = cell.pop("test_view_flip_rate")
                cell["comparison_keys_refer_to_training_arms"] = True
                training_changes[profile][view][cohort] = cell
    require(
        value_digest(
            [original_records, original_answers, active_records, lineage, grouping]
        )
        == before,
        "alias_cv_mutated_inputs",
    )
    return {
        **FLAGS,
        "status": "PAIRED_ALIAS_DIAGNOSTIC_BROADER_AUTHORING_HOLD",
        "source_documents": len(docs),
        "source_groups": len(set(groups)),
        "seeds": list(range(seeds)),
        "folds": folds,
        "diagnostic_model_fits": seeds * folds * len(PROFILES) * len(ARMS),
        "cohort_sizes": {k: len(v) for k, v in cohorts.items()},
        "summary": summary,
        "training_arm_comparisons_same_test_view": training_changes,
        "fold_assignments": assignments,
        "paired_predictions": rows,
        "source_collection_sha256": before,
        "input_views": [
            {
                "parent_doc_id": d.input.doc_id,
                "child_doc_id": variants[d.input.doc_id].input.doc_id,
                "original_input_sha256": d.input_sha256,
                "repaired_input_sha256": variants[d.input.doc_id].input_sha256,
                "presented_sha256": {
                    profile: {
                        "original": text_digest(presented_text(d, profile)),
                        "repaired": text_digest(
                            presented_text(variants[d.input.doc_id], profile)
                        ),
                    }
                    for profile in PROFILES
                },
            }
            for d in docs
        ],
        "grouping_sha256": value_digest(grouping),
        "numpy_version": np.__version__,
        "sklearn_version": sklearn.__version__,
        "new_benchmark_documents": 0,
        "production_model_trained": False,
        "confidence_intervals_computed": False,
        "diagnostic_perturbation_only": True,
        "adoption_allowed": False,
        "authoritative_parent_replaced": False,
        "body_only_grade_scoring_allowed": False,
        "semantic_equivalence_certified": False,
        "broader_authoring_quality_hold_cleared": False,
        "warning": "Repeated parent-seed observations, not independent documents. Conditional-reference agreement is not customer accuracy; lower probe agreement is not an authoring quality pass.",
    }


def _rows(path):
    rows = [
        strict_loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(bool(rows), "alias_zero_input_cases")
    return rows


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verify_repair_manifest(root, expected):
    require(
        type(expected) is str
        and re.fullmatch(r"[a-f0-9]{64}", expected) is not None
        and _sha(root / "manifest.json") == expected,
        "alias_repair_pin_mismatch",
    )
    manifest = strict_loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(
        all(type(manifest.get(k)) is bool and manifest[k] is False for k in FLAGS),
        "alias_repair_permission_invalid",
    )
    require(
        type(manifest.get("files")) is dict and bool(manifest["files"]),
        "alias_repair_manifest_empty",
    )
    require(
        manifest.get("parent_manifest_sha256") == ORIGINAL_MANIFEST_SHA256,
        "alias_repair_parent_pin_invalid",
    )
    sources = manifest.get("source_files_sha256")
    require(
        type(sources) is dict and bool(sources), "alias_repair_source_inventory_empty"
    )
    for name, digest in sources.items():
        require(
            type(name) is str and not Path(name).is_absolute() and "\\" not in name,
            "alias_repair_source_path_invalid",
        )
        path = (POC / name).resolve()
        require(
            path.is_relative_to(POC) and path != POC and _sha(path) == digest,
            "alias_repair_source_drift",
        )
    for name, digest in manifest["files"].items():
        path = (root / name).resolve()
        require(
            type(name) is str
            and not Path(name).is_absolute()
            and "\\" not in name
            and path.is_relative_to(root)
            and path != root,
            "alias_repair_manifest_escape",
        )
        require(_sha(path) == digest, "alias_repair_payload_changed")
    require(
        {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        == set(manifest["files"]) | {"manifest.json"},
        "alias_repair_unlisted_file",
    )
    return manifest


def _output_guard(out, original_pack, repair_pack):
    require(
        not out.exists()
        and not out.is_relative_to(original_pack)
        and not out.is_relative_to(repair_pack)
        and not any((ancestor / "manifest.json").exists() for ancestor in out.parents),
        "alias_output_unsafe_or_exists",
    )


def run(
    original_pack,
    repair_pack,
    grouping_path,
    out,
    *,
    expected_repair_manifest_sha256,
    seeds=5,
    perform_cv=False,
    expected_source_sha256=None,
):
    original_pack, repair_pack, grouping_path, out = [
        Path(p).resolve() for p in (original_pack, repair_pack, grouping_path, out)
    ]
    source_file = Path(__file__).resolve()
    source_sha = (
        _sha(source_file) if expected_source_sha256 is None else expected_source_sha256
    )
    require(_sha(source_file) == source_sha, "alias_source_changed_before_run")
    completion = out.with_name(out.name + ".manifest.json")
    _output_guard(out, original_pack, repair_pack)
    _output_guard(completion, original_pack, repair_pack)
    original_batch.verify(original_pack)
    require(
        _sha(original_pack / "manifest.json") == ORIGINAL_MANIFEST_SHA256
        and _sha(grouping_path) == GROUPING_FILE_SHA256,
        "alias_original_or_group_pin_mismatch",
    )
    manifest = _verify_repair_manifest(repair_pack, expected_repair_manifest_sha256)
    paths = (
        "authoring/documents.jsonl",
        "answers/answers.candidate.jsonl",
        "answers/evidence.jsonl",
    )
    original = [_rows(original_pack / p) for p in paths]
    active = [_rows(repair_pack / p) for p in paths]
    lineage = _rows(repair_pack / "audit/lineage.jsonl")
    parents = [
        _rows(repair_pack / p)
        for p in (
            "parents/documents.jsonl",
            "parents/answers.candidate.jsonl",
            "parents/evidence.jsonl",
        )
    ]
    parent_ids = {r["parent_doc_id"] for r in lineage}
    for index, (subset, population) in enumerate(zip(parents, original, strict=True)):
        identify = (
            (lambda r: r["input"]["doc_id"]) if index == 0 else (lambda r: r["doc_id"])
        )
        require(
            sorted(subset, key=identify)
            == sorted(
                [r for r in population if identify(r) in parent_ids], key=identify
            ),
            "alias_saved_parent_subset_mismatch",
        )
    grouping = strict_loads(grouping_path.read_text(encoding="utf-8"))
    require(
        len(original[0]) == len(active[0]) == 260
        and len(lineage) == 64
        and len(grouping["groups"]) == 236,
        "alias_fixed_population_mismatch",
    )
    dependencies = {
        name: _sha(POC / name)
        for name in (
            "src/koipa/customer_reference_audit_v1.py",
            "src/koipa/customer_benchmark.py",
            "src/koipa/policy_facts.py",
            "scripts/audit_customer_title_sensitivity_v1.py",
            "scripts/build_customer_guide_batch06.py",
        )
    }
    checked = audit_repair(*original, *active, lineage)
    result = {
        **FLAGS,
        "status": "ALIAS_INDEPENDENT_CHECK_BROADER_AUTHORING_HOLD",
        "independent_audit": checked,
        "diagnostic_perturbation_only": True,
        "adoption_allowed": False,
        "authoritative_parent_replaced": False,
        "completion_requires_sibling_manifest": completion.name,
        "paired_diagnostic": measure(
            original[0], original[1], active[0], lineage, grouping, seeds=seeds
        )
        if perform_cv
        else None,
        "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
        "repair_manifest_sha256": expected_repair_manifest_sha256,
        "grouping_file_sha256": GROUPING_FILE_SHA256,
        "source_script_sha256": source_sha,
        "dependency_source_sha256": dependencies,
        "repair_source_files_sha256": manifest.get("source_files_sha256", {}),
        "actual_cv_executed": perform_cv,
    }
    original_batch.verify(original_pack)
    _verify_repair_manifest(repair_pack, expected_repair_manifest_sha256)
    require(
        _sha(original_pack / "manifest.json") == ORIGINAL_MANIFEST_SHA256
        and _sha(grouping_path) == GROUPING_FILE_SHA256
        and _sha(source_file) == source_sha,
        "alias_source_changed",
    )
    require(
        all(_sha(POC / name) == digest for name, digest in dependencies.items()),
        "alias_dependency_changed",
    )
    _output_guard(out, original_pack, repair_pack)
    _output_guard(completion, original_pack, repair_pack)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("x", encoding="utf-8", newline="\n") as target:
        target.write(
            json.dumps(
                result, ensure_ascii=False, sort_keys=True, allow_nan=False, indent=2
            )
            + "\n"
        )
    require(
        _sha(original_pack / "manifest.json") == ORIGINAL_MANIFEST_SHA256
        and _sha(repair_pack / "manifest.json") == expected_repair_manifest_sha256
        and _sha(grouping_path) == GROUPING_FILE_SHA256
        and _sha(source_file) == source_sha,
        "alias_source_changed_during_output",
    )
    _verify_repair_manifest(repair_pack, expected_repair_manifest_sha256)
    original_batch.verify(original_pack)
    require(
        all(_sha(POC / name) == digest for name, digest in dependencies.items()),
        "alias_dependency_changed_during_output",
    )
    require(
        _sha(source_file) == source_sha and _sha(grouping_path) == GROUPING_FILE_SHA256,
        "alias_late_source_changed",
    )
    require(
        strict_loads(out.read_text(encoding="utf-8")) == result,
        "alias_output_payload_changed",
    )
    _output_guard(completion, original_pack, repair_pack)
    with completion.open("x", encoding="utf-8", newline="\n") as target:
        target.write(
            json.dumps(
                {
                    **FLAGS,
                    "status": "diagnostic_completed_hold_not_cleared",
                    "result_file": out.name,
                    "result_sha256": _sha(out),
                    "source_script_sha256": source_sha,
                    "original_manifest_sha256": ORIGINAL_MANIFEST_SHA256,
                    "repair_manifest_sha256": expected_repair_manifest_sha256,
                    "grouping_file_sha256": GROUPING_FILE_SHA256,
                },
                sort_keys=True,
                indent=2,
            )
            + "\n"
        )
    return result


def main(argv=None):
    source_sha = _sha(Path(__file__).resolve())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original-pack", type=Path, required=True)
    parser.add_argument("--repair-pack", type=Path, required=True)
    parser.add_argument("--repair-manifest-sha256", required=True)
    parser.add_argument("--grouping", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--run-cv-after-freeze", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = run(
            args.original_pack,
            args.repair_pack,
            args.grouping,
            args.out,
            expected_repair_manifest_sha256=args.repair_manifest_sha256,
            seeds=args.seeds,
            perform_cv=args.run_cv_after_freeze,
            expected_source_sha256=source_sha,
        )
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "actual_cv_executed": result["actual_cv_executed"],
                    "changed_parents": result["independent_audit"]["changed_parents"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (
        OSError,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        ImportError,
    ):
        print(
            json.dumps(
                {"status": "failed", "code": "customer_alias_independent_failed"}
            )
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
