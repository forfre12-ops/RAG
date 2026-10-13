from koipa.services.automation_report import build_automation_report


def _record(*, predicted="S2", truth="S2", initial_status="staging", confidence=0.72, margin=0.18, reason=None):
    return {
        "model_version": "v-test",
        "predicted_label": predicted,
        "truth_label": truth,
        "initial_status": initial_status,
        "automation_assessment": {
            "selected_confidence": confidence,
            "score_margin": margin,
            "causal_review_reason": reason,
        },
    }


def test_report_uses_final_human_labels_and_detects_silent_high_grade_miss():
    report = build_automation_report([
        _record(predicted="S1", truth="S1"),
        _record(predicted="S3", truth="TS"),
        _record(predicted="S2", truth="S1", initial_status="needs_review", reason="low-confidence"),
    ])

    assert report["summary"] == {
        "reviewed": 3,
        "correct": 1,
        "accuracy": 0.3333,
        "initial_auto_confirmed": 2,
        "auto_confirm_rate": 0.6667,
        "auto_confirm_correct": 1,
        "auto_confirm_precision": 0.5,
        "high_grade_silent_miss": 1,
    }
    assert report["initial_review_reason_counts"] == {
        "auto_confirmed": 2,
        "low-confidence": 1,
    }


def test_report_excludes_records_without_reconstructable_shadow_signals():
    report = build_automation_report([
        _record(),
        {"predicted_label": "S2", "truth_label": "S2", "initial_status": "staging"},
        _record(predicted="unknown", truth="S2"),
    ])

    assert report["included_records"] == 1
    assert report["excluded_records"] == {
        "missing_assessment": 1,
        "unsupported_grade": 1,
    }


def test_report_exposes_confidence_and_margin_bins_for_policy_selection():
    report = build_automation_report([
        _record(confidence=0.64, margin=0.04),
        _record(confidence=0.71, margin=0.11),
    ])

    assert report["by_confidence_bin"]["0.60–0.65"]["reviewed"] == 1
    assert report["by_confidence_bin"]["0.70–0.80"]["reviewed"] == 1
    assert report["by_score_margin_bin"]["0.00–0.05"]["reviewed"] == 1
    assert report["by_score_margin_bin"]["0.10–0.20"]["reviewed"] == 1
