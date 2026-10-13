"""Small char n-gram probes. High scores are warnings, not proof of leakage."""
from __future__ import annotations

from collections import Counter


def measure(rows: list[dict], *, seeds: int, folds: int = 5) -> dict:
    import numpy as np
    import sklearn
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.model_selection import StratifiedGroupKFold, StratifiedKFold
    from sklearn.pipeline import make_pipeline
    from sklearn.svm import LinearSVC

    if not 1 <= seeds <= 100 or folds < 2:
        raise ValueError("CV seeds must be 1..100 and folds >= 2")
    labels = np.array([r["label"] for r in rows])
    texts = np.array([r["text"] for r in rows])
    grades = sorted(set(labels))
    if len(grades) < 2 or min(Counter(labels).values()) < 2:
        raise ValueError("CV requires at least two grades with at least two rows each")
    n_splits = min(folds, min(Counter(labels).values()))
    groups = np.array([r.get("family_id") or "" for r in rows])
    grouped_folds = min(n_splits, *(len(set(groups[labels == g])) for g in grades))
    group_available = all(groups) and grouped_folds >= 2

    def run(seed, grouped, permuted):
        target = np.random.default_rng(seed).permutation(labels) if permuted else labels
        cv = (StratifiedGroupKFold(n_splits=grouped_folds, shuffle=True, random_state=seed)
              if grouped else StratifiedKFold(n_splits=n_splits, shuffle=True, random_state=seed))
        predicted = np.empty(len(rows), dtype=object)
        seen = set()
        for train, test in cv.split(texts, target, groups if grouped else None):
            if grouped and set(groups[train]) & set(groups[test]):
                raise ValueError("Family overlap in CV folds")
            if len(set(target[train])) < 2:
                raise ValueError("Insufficient training classes in a CV fold")
            if seen & set(test):
                raise ValueError("Repeated CV test membership")
            seen.update(test)
            # Vocabulary and IDF are fit inside each training fold, never globally.
            pipe = make_pipeline(TfidfVectorizer(analyzer="char", ngram_range=(2, 5),
                                               min_df=1, max_features=30000),
                                 LinearSVC(random_state=seed, max_iter=5000))
            pipe.fit(texts[train], target[train])
            predicted[test] = pipe.predict(texts[test])
        if len(seen) != len(rows):
            raise ValueError("Incomplete CV prediction coverage")
        return {"seed": seed, "accuracy": float(np.mean(predicted == target)),
                "recall": {g: float(np.mean(predicted[target == g] == g)) for g in grades}}

    result = {"sklearn_version": sklearn.__version__, "seeds": list(range(seeds)),
              "n": len(rows), "grades": grades, "features": "input-view text only; no IDs, labels or answer fields",
              "vectorizer": {"analyzer": "char", "ngram_range": [2, 5], "min_df": 1, "max_features": 30000},
              "classifier": "LinearSVC(max_iter=5000)", "family_cv_available": bool(group_available),
              "diagnostic_probe_fitted": True, "production_model_trained": False,
              "customer_accuracy_measured": False,
              "interpretation": "Shortcut warning only; legitimate content signal can also produce high scores."}
    for grouped in (False, True):
        name = "family_cv" if grouped else "stratified_cv"
        if grouped and not group_available:
            result[name] = {"status": "NOT_RUN_INSUFFICIENT_FAMILY_METADATA"}
            continue
        real = [run(seed, grouped, False) for seed in range(seeds)]
        null = [run(seed, grouped, True) for seed in range(seeds)]
        values = [r["accuracy"] for r in real]
        avg, baseline = float(np.mean(values)), float(np.mean([r["accuracy"] for r in null]))
        result[name] = {"status": "MEASURED", "folds": grouped_folds if grouped else n_splits,
                        "mean": avg, "min": min(values), "max": max(values), "permutation_mean": baseline,
                        "excess_pp": (avg - baseline) * 100, "runs": real, "permutation_runs": null}
    return result
