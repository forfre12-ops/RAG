from __future__ import annotations

from collections import Counter

from koipa.proxy_corpus import validate_proxy_record
from scripts import build_direct_authored_training_corpus as corpus


def test_direct_authored_corpus_is_exact_balanced_and_training_only():
    rows = corpus.build_records()

    assert len(rows) == 2_700
    assert Counter(row["label"] for row in rows) == {
        "TS": 750,
        "S1": 750,
        "S2": 750,
        "S3": 450,
    }
    assert len({row["document_family_id"] for row in rows}) == 225
    assert len({row["family_profile_id"] for row in rows}) == 12
    assert len({row["length_profile_id"] for row in rows}) == 3
    assert all(row["training_use_permitted"] is True for row in rows)
    assert all(row["evaluation_use_permitted"] is False for row in rows)
    assert len({row["text"] for row in rows}) == len(rows)
    assert min(len(str(row["text"])) for row in rows) >= 3_100
    for grade in ("TS", "S1", "S2"):
        shape_counts = Counter(
            row["family_profile_id"] for row in rows if row["label"] == grade
        )
        assert max(shape_counts.values()) - min(shape_counts.values()) <= 1


def test_direct_authored_corpus_rows_pass_eligible_contract():
    rows = corpus.build_records()
    checks = [
        validate_proxy_record(row, stage="eligible", intended_use="training")
        for row in rows
    ]
    assert all(check.ok for check in checks)
