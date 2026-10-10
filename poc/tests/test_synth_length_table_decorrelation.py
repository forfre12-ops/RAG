"""길이-등급 독립 배정(2026-10-11) — assign_length_table_specs + generate() 연동.

reports/full1001_20261010 1,001건 실측(length_only_1nn 실측 vs 라벨섞음평균 차이
-1.11%p, 안전)으로 검증된 설계를 콘솔 생성 경로(POST /synth/generate →
SyntheticDocGenerator.generate)에도 적용한 것을 검증한다. 핵심 불변식: 길이대·표
포함 여부를 정하는 함수는 target_grade를 절대 받지 않는다 — 이 시험은 그 서명
자체를 확인해, 나중에 누가 "등급별로 보정"을 넣으려는 변경을 내면 바로 걸린다.
"""

from __future__ import annotations

import inspect

from koipa.adapters.llm import NoopProvider
from koipa.modules.m1_synthesis.generator import (
    LENGTH_TABLE_BUCKETS,
    SynthRequest,
    SyntheticDocGenerator,
    assign_length_table_specs,
)


def test_assign_length_table_specs_signature_has_no_grade_parameter():
    """등급을 받으면 등급별로 버킷을 고르게 할 수 있다 — 매개변수에 없어야 설계가 선다."""
    params = set(inspect.signature(assign_length_table_specs).parameters)
    assert "grade" not in params
    assert "target_grade" not in params


def test_assign_length_table_specs_empty_for_zero_count():
    assert assign_length_table_specs(0) == []
    assert assign_length_table_specs(-3) == []


def test_assign_length_table_specs_covers_all_buckets_for_count_matching_bucket_count():
    specs = assign_length_table_specs(8, seed=1)
    assert len(specs) == 8
    lengths = {(lo, hi) for lo, hi, _tbl in specs}
    assert lengths == set(LENGTH_TABLE_BUCKETS)
    # 4개 버킷 x 8건이면 버킷마다 정확히 2번씩 나와야 한다(순환 배정 후 섞기).
    from collections import Counter

    counts = Counter((lo, hi) for lo, hi, _tbl in specs)
    assert set(counts.values()) == {2}


def test_assign_length_table_specs_table_flags_are_balanced():
    specs = assign_length_table_specs(8, seed=2)
    table_flags = [tbl for _lo, _hi, tbl in specs]
    assert table_flags.count(True) == 4
    assert table_flags.count(False) == 4


def test_assign_length_table_specs_lengths_stay_within_declared_buckets():
    for lo, hi, _tbl in assign_length_table_specs(40, seed=3):
        assert (lo, hi) in LENGTH_TABLE_BUCKETS
        assert lo < hi


def test_generate_varies_length_and_table_across_a_single_batch():
    """count>1이면 더는 모든 문서가 같은 len_min/len_max/include_table을 받지 않는다.

    고치기 전에는 generate()가 같은 req를 count번 그대로 재사용해 배치 안 모든 문서가
    동일한 길이 구간·표 여부였다 — 이 시험이 그 상태로 되돌아가면 실패한다.
    """
    gen = SyntheticDocGenerator(llm=NoopProvider())
    docs = gen.generate(
        SynthRequest(target_grade="S2", domain="business", count=8)
    )
    assert len(docs) == 8

    length_pairs = {(d.assigned_len_min, d.assigned_len_max) for d in docs}
    assert len(length_pairs) > 1, "배치 안 길이 구간이 전부 같다 — 등급무관 재배정이 안 됐다"

    table_flags = {d.include_table for d in docs}
    assert table_flags == {True, False}, "표 포함 여부가 배치 안에서 한쪽으로만 나왔다"

    for lo, hi in length_pairs:
        assert (lo, hi) in LENGTH_TABLE_BUCKETS


def test_generate_assigned_lengths_independent_of_grade_parameter():
    """assign_length_table_specs 호출부가 req.target_grade 를 넘기지 않는지 직접 확인.

    generate() 내부에서 이 함수를 호출할 때 target_grade 인자를 전달하면 TypeError가
    난다(함수가 그 파라미터를 받지 않으므로) — 즉 이 시험이 그냥 통과한다는 사실 자체가
    "등급을 넘기지 않는다"의 실행 시점 증거다. 더해서 각 문서의 target_grade 는 유지되되
    assigned_len_* 는 grade 값과 무관하게 4개 버킷에서만 나와야 한다.
    """
    gen = SyntheticDocGenerator(llm=NoopProvider())
    for grade in ("TS", "S1", "S2", "S3"):
        docs = gen.generate(SynthRequest(target_grade=grade, domain="mixed", count=4))
        assert len(docs) == 4
        for d in docs:
            assert d.target_grade == grade
            assert (d.assigned_len_min, d.assigned_len_max) in LENGTH_TABLE_BUCKETS


def test_build_user_prompt_table_instruction_matches_include_table_flag():
    """표 지시 문구가 req.include_table 값대로 정확히 갈리는지 — 등급은 언급하지 않는다."""
    gen = SyntheticDocGenerator(llm=NoopProvider())

    with_table = gen._build_user_prompt(
        SynthRequest(target_grade="S1", domain="mixed", count=1, include_table=True),
        "S1",
        "mixed",
    )
    without_table = gen._build_user_prompt(
        SynthRequest(target_grade="S1", domain="mixed", count=1, include_table=False),
        "S1",
        "mixed",
    )
    assert "마크다운 표" in with_table
    assert "표를 넣지 않는다" in without_table
    assert "마크다운 표" not in without_table
