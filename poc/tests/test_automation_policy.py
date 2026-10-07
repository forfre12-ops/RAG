from koipa.services.automation_policy import simulate_policy_grid


def _record(*, predicted="S2", truth="S2", initial_status="staging", confidence=0.72, margin=0.2, reason=None, hits=None):
    return {
        "predicted_label": predicted,
        "truth_label": truth,
        "initial_status": initial_status,
        "automation_assessment": {
            "selected_confidence": confidence,
            "score_margin": margin,
            "causal_review_reason": reason,
            "review_gate_hits": hits or [],
        },
    }


def test_lowering_threshold_requires_full_gate_replay_for_newly_opened_review():
    report = simulate_policy_grid([
        _record(initial_status="needs_review", confidence=0.66, reason="low-confidence", hits=["low-confidence"]),
    ], thresholds=[0.70, 0.65])

    strict, relaxed = report["simulations"]
    assert strict["provisional_auto_confirmed"] == 0
    assert relaxed["provisional_auto_confirmed"] == 1
    assert relaxed["requires_full_gate_replay"] == 1
    assert relaxed["deployable"] is False
    assert report["recommended"] is None


def test_known_hard_gate_never_becomes_candidate_by_lowering_confidence_threshold():
    report = simulate_policy_grid([
        _record(initial_status="needs_review", confidence=0.95, reason="agreement-gate", hits=["agreement-gate"]),
    ], thresholds=[0.50])

    result = report["simulations"][0]
    assert result["provisional_auto_confirmed"] == 0
    assert result["excluded"] == {"known_hard_gate": 1}


def test_currently_automatic_clean_records_can_form_deployable_baseline_only():
    report = simulate_policy_grid([
        _record(predicted="S1", truth="S1", confidence=0.91),
        _record(predicted="S2", truth="S2", confidence=0.80),
    ], thresholds=[0.70, 0.65], min_margin=0.10, min_evaluated=2)

    assert report["recommended"]["threshold"] == 0.65
    assert all(row["deployable"] for row in report["simulations"])
