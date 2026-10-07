from __future__ import annotations

from collections import Counter

from koipa.proxy_corpus import validate_proxy_record
from koipa.proxy_eval_split import split_frozen_proxy_eval
from scripts import build_direct_authored_proxy_eval as evaluation
from scripts import build_direct_authored_proxy_eval_v2_2 as evaluation_v2_2


def test_direct_authored_evaluation_is_training_forbidden_and_splittable():
    rows = evaluation.build_records()

    assert len(rows) == 1_000
    assert Counter(row["label"] for row in rows) == {
        "TS": 200,
        "S1": 250,
        "S2": 250,
        "S3": 300,
    }
    assert all(row["training_use_permitted"] is False for row in rows)
    assert all(row["evaluation_use_permitted"] is True for row in rows)
    assert all(
        validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok
        for row in rows
    )

    split = split_frozen_proxy_eval(rows)
    assert len(split.development) == 200
    assert len(split.final) == 800
    assert {
        row["document_family_id"] for row in split.development
    }.isdisjoint({row["document_family_id"] for row in split.final})


def test_direct_authored_evaluation_v2_2_is_training_forbidden_and_splittable():
    rows = evaluation_v2_2.build_records()

    assert len(rows) == 1_000
    assert Counter(row["label"] for row in rows) == {
        "TS": 200,
        "S1": 250,
        "S2": 250,
        "S3": 300,
    }
    assert all(row["training_use_permitted"] is False for row in rows)
    assert all(row["evaluation_use_permitted"] is True for row in rows)
    assert all(
        validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok
        for row in rows
    )

    split = split_frozen_proxy_eval(rows)
    assert len(split.development) == 200
    assert len(split.final) == 800
    assert {
        row["document_family_id"] for row in split.development
    }.isdisjoint({row["document_family_id"] for row in split.final})
