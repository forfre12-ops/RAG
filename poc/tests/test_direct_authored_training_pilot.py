from __future__ import annotations

from collections import Counter

from koipa.proxy_corpus import validate_proxy_record
from scripts.assemble_proxy_training_pool import _candidate_errors
from scripts import build_direct_authored_training_pilot as pilot


def test_direct_authored_pilot_is_balanced_training_only_and_candidate_valid():
    rows = [
        pilot._record(case, grade)
        for case in pilot.CASES
        for grade in ("TS", "S1", "S2", "S3")
    ]
    assert len(rows) == len(pilot.CASES) * 4
    assert Counter(row["label"] for row in rows) == {
        "TS": len(pilot.CASES),
        "S1": len(pilot.CASES),
        "S2": len(pilot.CASES),
        "S3": len(pilot.CASES),
    }
    assert all(row["training_use_permitted"] is True for row in rows)
    assert all(row["evaluation_use_permitted"] is False for row in rows)
    assert all(
        validate_proxy_record(row, stage="eligible", intended_use="training").ok
        for row in rows
    )
    assert all(not _candidate_errors(row) for row in rows)


def test_each_minimal_difference_family_has_four_distinct_documents():
    rows = [
        pilot._record(case, grade)
        for case in pilot.CASES
        for grade in ("TS", "S1", "S2", "S3")
    ]
    for case in pilot.CASES:
        family_rows = [
            row
            for row in rows
            if row["document_family_id"] == f"direct-train-v1-{case['case_id']}"
        ]
        assert {row["label"] for row in family_rows} == {"TS", "S1", "S2", "S3"}
        assert len({row["text"] for row in family_rows}) == 4
        assert min(len(str(row["text"])) for row in family_rows) >= 1200
