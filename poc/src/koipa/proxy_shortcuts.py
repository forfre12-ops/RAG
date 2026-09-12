"""Detect metadata shortcuts in proxy-gold candidates.

All predictive scores are out-of-family: records sharing ``document_family_id``
are assigned to the same deterministic cross-validation fold.  This prevents a
template family from appearing in both train and validation data.

The implementation is intentionally standard-library only.  Single categorical
features use a training-fold category-majority predictor, while the combined
metadata baseline is a Laplace-smoothed categorical Naive Bayes classifier.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter, defaultdict
from typing import Iterable, Mapping, Sequence


GRADE_ORDER = ("TS", "S1", "S2", "S3")
DEFAULT_SHORTCUT_FEATURES = (
    "domain",
    "industry",
    "document_type",
    "family_profile_id",
    "length_bin",
)
KNOWN_CONFOUND_FEATURES = ("document_origin", "provider", "model")

MIN_GATE_RECORDS = 100
LARGE_CELL_MIN_RECORDS = 20
LARGE_CELL_MIN_GRADES = 3
MAX_SINGLE_THEIL_U = 0.25
MAX_COMBINED_MACRO_F1 = 0.45

_MISSING = "<missing>"


def _record_text(record: Mapping[str, object]) -> str:
    text = str(record.get("text") or "").strip()
    if text:
        return text
    return "\n\n".join(
        part
        for part in (
            str(record.get("title") or "").strip(),
            str(record.get("body") or "").strip(),
        )
        if part
    )


def _derived_length_bin(record: Mapping[str, object]) -> str:
    length = len(_record_text(record))
    if length < 500:
        return "0000-0499"
    if length < 1000:
        return "0500-0999"
    if length < 2000:
        return "1000-1999"
    if length < 4000:
        return "2000-3999"
    return "4000-plus"


def _stable_category(value: object) -> str:
    if value is None:
        return _MISSING
    if isinstance(value, str):
        return value.strip() or _MISSING
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def category_value(record: Mapping[str, object], feature: str) -> str:
    """Return one deterministic categorical value, deriving length when needed."""
    if feature == "length_bin":
        explicit = record.get(feature)
        return _stable_category(explicit) if explicit not in (None, "") else _derived_length_bin(record)
    if feature == "provider":
        return _stable_category(record.get("provider") or record.get("llm_provider"))
    if feature == "model":
        return _stable_category(record.get("model") or record.get("llm_model"))
    return _stable_category(record.get(feature))


def _rows_and_labels(
    records: Iterable[Mapping[str, object]],
    *,
    label_key: str,
    family_key: str,
) -> tuple[list[Mapping[str, object]], list[str], tuple[str, ...]]:
    rows = list(records)
    if not rows:
        raise ValueError("records must not be empty")
    labels: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"record[{index}] must be a mapping")
        label = str(row.get(label_key) or "").strip()
        family = str(row.get(family_key) or "").strip()
        if not label:
            raise ValueError(f"record[{index}] missing {label_key}")
        if not family:
            raise ValueError(f"record[{index}] missing {family_key}")
        labels.append(label)
    observed = set(labels)
    ordered = tuple(label for label in GRADE_ORDER if label in observed)
    extras = tuple(sorted(observed.difference(GRADE_ORDER)))
    return rows, labels, ordered + extras


def deterministic_group_folds(
    records: Sequence[Mapping[str, object]],
    *,
    family_key: str = "document_family_id",
    n_splits: int = 5,
) -> tuple[int, ...]:
    """Assign whole families to stable, approximately size-balanced folds."""
    if n_splits < 2:
        raise ValueError("n_splits must be >= 2")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, record in enumerate(records):
        family = str(record.get(family_key) or "").strip()
        if not family:
            raise ValueError(f"record[{index}] missing {family_key}")
        groups[family].append(index)
    if not groups:
        return ()

    fold_count = min(n_splits, len(groups))
    fold_sizes = [0] * fold_count
    family_fold: dict[str, int] = {}
    ordered_groups = sorted(
        groups.items(),
        key=lambda item: (
            -len(item[1]),
            hashlib.sha256(item[0].encode("utf-8")).hexdigest(),
            item[0],
        ),
    )
    for family, indices in ordered_groups:
        fold = min(range(fold_count), key=lambda value: (fold_sizes[value], value))
        family_fold[family] = fold
        fold_sizes[fold] += len(indices)
    return tuple(family_fold[str(row[family_key]).strip()] for row in records)


def _majority_label(counts: Mapping[str, int], label_order: Sequence[str]) -> str:
    rank = {label: index for index, label in enumerate(label_order)}
    return max(
        label_order,
        key=lambda label: (int(counts.get(label, 0)), -rank[label]),
    )


def _classification_metrics(
    true_labels: Sequence[str],
    predictions: Sequence[str],
    label_order: Sequence[str],
) -> tuple[float, float]:
    if not true_labels or len(true_labels) != len(predictions):
        raise ValueError("true labels and predictions must be non-empty and aligned")
    accuracy = sum(
        truth == prediction
        for truth, prediction in zip(true_labels, predictions, strict=True)
    ) / len(true_labels)
    f1_values: list[float] = []
    for label in label_order:
        true_positive = sum(
            truth == label and prediction == label
            for truth, prediction in zip(true_labels, predictions, strict=True)
        )
        false_positive = sum(
            truth != label and prediction == label
            for truth, prediction in zip(true_labels, predictions, strict=True)
        )
        false_negative = sum(
            truth == label and prediction != label
            for truth, prediction in zip(true_labels, predictions, strict=True)
        )
        denominator = 2 * true_positive + false_positive + false_negative
        f1_values.append(0.0 if denominator == 0 else 2 * true_positive / denominator)
    return accuracy, sum(f1_values) / len(f1_values)


def _entropy(counts: Mapping[str, int]) -> float:
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    result = 0.0
    for count in counts.values():
        if count <= 0:
            continue
        probability = count / total
        result -= probability * math.log(probability)
    return result


def theils_u_label_given_feature(
    records: Sequence[Mapping[str, object]],
    feature: str,
    *,
    label_key: str = "label",
) -> float:
    """Return uncertainty coefficient U(label | feature), clamped to [0, 1]."""
    label_counts: Counter[str] = Counter()
    by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        label = str(record.get(label_key) or "").strip()
        if not label:
            raise ValueError(f"record missing {label_key}")
        value = category_value(record, feature)
        label_counts[label] += 1
        by_category[value][label] += 1
    label_entropy = _entropy(label_counts)
    if label_entropy <= 0.0:
        return 0.0
    total = sum(label_counts.values())
    conditional_entropy = sum(
        (sum(counts.values()) / total) * _entropy(counts)
        for counts in by_category.values()
    )
    return min(1.0, max(0.0, (label_entropy - conditional_entropy) / label_entropy))


def _single_feature_predictions(
    rows: Sequence[Mapping[str, object]],
    labels: Sequence[str],
    folds: Sequence[int],
    feature: str,
    label_order: Sequence[str],
) -> list[str] | None:
    unique_folds = sorted(set(folds))
    if len(unique_folds) < 2:
        return None
    predictions = [""] * len(rows)
    for fold in unique_folds:
        train_indices = [index for index, value in enumerate(folds) if value != fold]
        test_indices = [index for index, value in enumerate(folds) if value == fold]
        global_counts = Counter(labels[index] for index in train_indices)
        fallback = _majority_label(global_counts, label_order)
        category_counts: dict[str, Counter[str]] = defaultdict(Counter)
        for index in train_indices:
            category_counts[category_value(rows[index], feature)][labels[index]] += 1
        for index in test_indices:
            counts = category_counts.get(category_value(rows[index], feature))
            predictions[index] = (
                _majority_label(counts, label_order) if counts else fallback
            )
    return predictions


def _naive_bayes_predictions(
    rows: Sequence[Mapping[str, object]],
    labels: Sequence[str],
    folds: Sequence[int],
    features: Sequence[str],
    label_order: Sequence[str],
    *,
    alpha: float = 1.0,
) -> list[str] | None:
    unique_folds = sorted(set(folds))
    if len(unique_folds) < 2:
        return None
    predictions = [""] * len(rows)
    rank = {label: index for index, label in enumerate(label_order)}
    for fold in unique_folds:
        train_indices = [index for index, value in enumerate(folds) if value != fold]
        test_indices = [index for index, value in enumerate(folds) if value == fold]
        label_counts = Counter(labels[index] for index in train_indices)
        vocabularies: dict[str, set[str]] = {feature: set() for feature in features}
        conditional: dict[tuple[str, str, str], int] = Counter()
        for index in train_indices:
            label = labels[index]
            for feature in features:
                value = category_value(rows[index], feature)
                vocabularies[feature].add(value)
                conditional[(label, feature, value)] += 1

        prior_denominator = len(train_indices) + alpha * len(label_order)
        for index in test_indices:
            scores: dict[str, float] = {}
            for label in label_order:
                prior = (label_counts[label] + alpha) / prior_denominator
                score = math.log(prior)
                for feature in features:
                    value = category_value(rows[index], feature)
                    vocabulary_size = len(vocabularies[feature]) + 1
                    denominator = label_counts[label] + alpha * vocabulary_size
                    numerator = conditional[(label, feature, value)] + alpha
                    score += math.log(numerator / denominator)
                scores[label] = score
            predictions[index] = max(
                label_order,
                key=lambda label: (scores[label], -rank[label]),
            )
    return predictions


def _cell_report(
    rows: Sequence[Mapping[str, object]],
    labels: Sequence[str],
    feature: str,
) -> dict:
    cells: dict[str, Counter[str]] = defaultdict(Counter)
    missing_count = 0
    for row, label in zip(rows, labels, strict=True):
        value = category_value(row, feature)
        missing_count += value == _MISSING
        cells[value][label] += 1
    violations = []
    large_cells = 0
    for value in sorted(cells):
        counts = cells[value]
        count = sum(counts.values())
        if count < LARGE_CELL_MIN_RECORDS:
            continue
        large_cells += 1
        if len(counts) < LARGE_CELL_MIN_GRADES:
            violations.append(
                {
                    "value": value,
                    "count": count,
                    "grade_count": len(counts),
                    "label_counts": dict(sorted(counts.items())),
                }
            )
    return {
        "category_count": len(cells),
        "missing_count": missing_count,
        "large_cell_count": large_cells,
        "large_cell_violations": violations,
    }


def _feature_metrics(
    rows: Sequence[Mapping[str, object]],
    labels: Sequence[str],
    folds: Sequence[int],
    feature: str,
    label_order: Sequence[str],
    *,
    label_key: str,
) -> dict:
    predictions = _single_feature_predictions(
        rows, labels, folds, feature, label_order
    )
    if predictions is None:
        accuracy = None
        macro_f1 = None
    else:
        accuracy, macro_f1 = _classification_metrics(labels, predictions, label_order)
    return {
        "feature": feature,
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "theil_u": theils_u_label_given_feature(
            rows, feature, label_key=label_key
        ),
        "cv_available": predictions is not None,
        **_cell_report(rows, labels, feature),
    }


def _combined_metrics(
    rows: Sequence[Mapping[str, object]],
    labels: Sequence[str],
    folds: Sequence[int],
    features: Sequence[str],
    label_order: Sequence[str],
) -> dict:
    predictions = _naive_bayes_predictions(
        rows, labels, folds, features, label_order
    )
    if predictions is None:
        accuracy = None
        macro_f1 = None
    else:
        accuracy, macro_f1 = _classification_metrics(labels, predictions, label_order)
    return {
        "method": "laplace_categorical_naive_bayes",
        "features": list(features),
        "accuracy": accuracy,
        "macro_f1": macro_f1,
        "cv_available": predictions is not None,
    }


def analyze_proxy_shortcuts(
    records: Iterable[Mapping[str, object]],
    *,
    n_splits: int = 5,
    label_key: str = "label",
    family_key: str = "document_family_id",
    shortcut_features: Sequence[str] = DEFAULT_SHORTCUT_FEATURES,
    known_confound_features: Sequence[str] = KNOWN_CONFOUND_FEATURES,
) -> dict:
    """Calculate grouped-CV shortcut metrics without applying pass/fail policy."""
    rows, labels, label_order = _rows_and_labels(
        records, label_key=label_key, family_key=family_key
    )
    folds = deterministic_group_folds(
        rows, family_key=family_key, n_splits=n_splits
    )
    family_count = len({str(row[family_key]).strip() for row in rows})
    label_counts = Counter(labels)
    majority_accuracy = max(label_counts.values()) / len(rows)
    primary = {
        feature: _feature_metrics(
            rows,
            labels,
            folds,
            feature,
            label_order,
            label_key=label_key,
        )
        for feature in shortcut_features
    }
    confounds = {
        feature: _feature_metrics(
            rows,
            labels,
            folds,
            feature,
            label_order,
            label_key=label_key,
        )
        for feature in known_confound_features
    }
    combined_primary = _combined_metrics(
        rows, labels, folds, tuple(shortcut_features), label_order
    )
    combined_with_confounds = _combined_metrics(
        rows,
        labels,
        folds,
        tuple(shortcut_features) + tuple(known_confound_features),
        label_order,
    )
    return {
        "n": len(rows),
        "family_count": family_count,
        "fold_count": len(set(folds)),
        "fold_sizes": dict(sorted(Counter(folds).items())),
        "label_order": list(label_order),
        "label_counts": dict(sorted(label_counts.items())),
        "label_majority_accuracy": majority_accuracy,
        "single_features": primary,
        "known_confounds": confounds,
        "combined_baseline": combined_primary,
        "combined_with_known_confounds": combined_with_confounds,
    }


def strict_shortcut_gate(
    records: Iterable[Mapping[str, object]],
    *,
    frozen_gold: bool = True,
    n_splits: int = 5,
    label_key: str = "label",
    family_key: str = "document_family_id",
    shortcut_features: Sequence[str] = DEFAULT_SHORTCUT_FEATURES,
    known_confound_features: Sequence[str] = KNOWN_CONFOUND_FEATURES,
) -> dict:
    """Apply the frozen-gold shortcut gate and return metrics plus evidence.

    Known confounds are always measured.  ``frozen_gold=True`` excludes them
    from hard thresholds while retaining warnings; set it to false for a wider
    diagnostic gate.
    """
    report = analyze_proxy_shortcuts(
        records,
        n_splits=n_splits,
        label_key=label_key,
        family_key=family_key,
        shortcut_features=shortcut_features,
        known_confound_features=known_confound_features,
    )
    violations: list[str] = []
    warnings: list[str] = []
    n = int(report["n"])
    majority_accuracy = float(report["label_majority_accuracy"])
    accuracy_limit = max(0.45, majority_accuracy + 0.10)

    gate_metrics = dict(report["single_features"])
    combined_key = "combined_baseline"
    excluded_confounds: list[str] = []
    if frozen_gold:
        excluded_confounds = list(known_confound_features)
    else:
        gate_metrics.update(report["known_confounds"])
        combined_key = "combined_with_known_confounds"

    for feature, metrics in gate_metrics.items():
        for cell in metrics["large_cell_violations"]:
            violations.append(
                "cell_grade_coverage:"
                f"{feature}:{cell['value']}:{cell['count']}:"
                f"{cell['grade_count']}<{LARGE_CELL_MIN_GRADES}"
            )

    if n < MIN_GATE_RECORDS:
        violations.append(f"insufficient_sample_size:{n}<{MIN_GATE_RECORDS}")
    else:
        for feature, metrics in gate_metrics.items():
            accuracy = metrics["accuracy"]
            if accuracy is None:
                violations.append(f"cv_unavailable:{feature}")
            elif float(accuracy) > accuracy_limit:
                violations.append(
                    f"single_accuracy:{feature}:{accuracy:.6f}>{accuracy_limit:.6f}"
                )
            if float(metrics["theil_u"]) > MAX_SINGLE_THEIL_U:
                violations.append(
                    f"single_theil_u:{feature}:{metrics['theil_u']:.6f}>"
                    f"{MAX_SINGLE_THEIL_U:.6f}"
                )
        combined = report[combined_key]
        combined_macro_f1 = combined["macro_f1"]
        if combined_macro_f1 is None:
            violations.append("cv_unavailable:combined_metadata")
        elif float(combined_macro_f1) > MAX_COMBINED_MACRO_F1:
            violations.append(
                f"combined_macro_f1:{combined_macro_f1:.6f}>"
                f"{MAX_COMBINED_MACRO_F1:.6f}"
            )

    if frozen_gold:
        for feature, metrics in report["known_confounds"].items():
            accuracy = metrics["accuracy"]
            if accuracy is not None and float(accuracy) > accuracy_limit:
                warnings.append(
                    f"known_confound_accuracy:{feature}:"
                    f"{accuracy:.6f}>{accuracy_limit:.6f}"
                )
            if float(metrics["theil_u"]) > MAX_SINGLE_THEIL_U:
                warnings.append(
                    f"known_confound_theil_u:{feature}:"
                    f"{metrics['theil_u']:.6f}>{MAX_SINGLE_THEIL_U:.6f}"
                )
            for cell in metrics["large_cell_violations"]:
                warnings.append(
                    "known_confound_cell_grade_coverage:"
                    f"{feature}:{cell['value']}:{cell['count']}:"
                    f"{cell['grade_count']}<{LARGE_CELL_MIN_GRADES}"
                )

    if n < MIN_GATE_RECORDS:
        status = "inconclusive"
    else:
        status = "fail" if violations else "pass"
    report["gate"] = {
        "status": status,
        "passed": status == "pass",
        "hard_gate_applied": n >= MIN_GATE_RECORDS,
        "frozen_gold": frozen_gold,
        "excluded_known_confounds": excluded_confounds,
        "thresholds": {
            "min_records": MIN_GATE_RECORDS,
            "single_accuracy_max": accuracy_limit,
            "single_theil_u_max": MAX_SINGLE_THEIL_U,
            "combined_macro_f1_max": MAX_COMBINED_MACRO_F1,
            "large_cell_min_records": LARGE_CELL_MIN_RECORDS,
            "large_cell_min_grades": LARGE_CELL_MIN_GRADES,
        },
        "combined_baseline_used": combined_key,
        "violations": violations,
        "warnings": warnings,
    }
    return report


# A concise alias for callers that treat the gate report as the evaluation.
evaluate_proxy_shortcuts = strict_shortcut_gate
