from scripts.eval_p1_model_gold import apply_source_prior_cap, compute_metrics, is_public_source


def test_public_source_prior_caps_only_public_sources() -> None:
    rows = [
        {"label": "S3", "source": "판례_공개문서"},
        {"label": "TS", "source": "public_scenario"},
        {"label": "S2", "source": "internal_memo"},
    ]
    preds = [
        {"label": "TS", "confidence": 0.8, "scores": {"TS": 0.8, "S3": 0.1}},
        {"label": "TS", "confidence": 0.9, "scores": {"TS": 0.9, "S3": 0.0}},
        {"label": "S2", "confidence": 0.7, "scores": {"S2": 0.7, "S3": 0.1}},
    ]

    capped, applied = apply_source_prior_cap(rows, preds, "S3")

    assert applied == 1
    assert [p["label"] for p in capped] == ["S3", "TS", "S2"]
    assert is_public_source(rows[0])
    assert not is_public_source(rows[1])


def test_source_prior_cap_improves_public_false_positive_without_high_risk_to_s3() -> None:
    rows = [
        {"label": "S3", "source": "판례_공개문서"},
        {"label": "TS", "source": "public_scenario"},
    ]
    preds = [
        {"label": "TS", "confidence": 0.8, "scores": {"TS": 0.8, "S3": 0.1}},
        {"label": "TS", "confidence": 0.9, "scores": {"TS": 0.9, "S3": 0.0}},
    ]

    capped, applied = apply_source_prior_cap(rows, preds, "S3")
    metrics = compute_metrics([r["label"] for r in rows], [p["label"] for p in capped])

    assert applied == 1
    assert metrics["high_risk_to_s3"] == 0
    assert metrics["confusion_matrix"]["S3"]["S3"] == 1
