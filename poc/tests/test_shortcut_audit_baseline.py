"""지름길 검사의 **기준선**이 라벨 섞기인지 지킨다.

왜 이 시험이 있는가(2026-09-12). 지름길 지표를 "가장 흔한 등급 비율"과 비교하고 있었다.
그 기준은 **범주가 많고 표본이 적으면 관계가 전혀 없어도 크게 나온다** — 칸에 몇 건 없으면
그 안의 최빈값이 저절로 과반이 된다. 실제로 117건 판에서 문서종류 40.2% 가 나와 지름길인
줄 알았는데, 라벨을 섞어도 39.8% 였다(차이 +0.4%p). 헛경보 직전이었다.

기준선을 잘못 잡으면 **없는 결함을 고치느라 시간을 쓰고, 고친 뒤에도 값이 안 내려가** 혼란이 온다.
그래서 판단은 실측이 아니라 **실측 − 라벨섞음** 으로 한다. 이 시험이 그 규칙을 지킨다.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_grade_content_corpus import (  # noqa: E402
    audit,
    category_rate,
    length_rate,
    null_rate,
    phrase_rate,
)

GRADES = ("TS", "S1", "S2", "S3")


def _rows(n: int, *, categories: int, leak: bool, seed: int = 3) -> list[dict]:
    """등급과 범주의 관계를 켜고 끌 수 있는 가짜 자료.

    leak=True 면 범주가 등급을 그대로 알려 준다. leak=False 면 무관하게 고른다.
    길이는 양쪽 모두 등급과 무관하게 둔다.
    """
    rng = random.Random(seed)
    out = []
    for i in range(n):
        grade = GRADES[i % len(GRADES)]
        if leak:
            cat = f"C{GRADES.index(grade)}"
        else:
            cat = f"C{rng.randrange(categories)}"
        out.append({
            "label": grade,
            "document_type": cat,
            "theme": f"T{rng.randrange(categories)}",
            "text": "가" * rng.randrange(400, 600),
        })
    return out


def test_no_relationship_gives_excess_near_zero_even_with_many_categories():
    """관계가 없으면 **차이**가 0 근처여야 한다 — 실측 자체는 클 수 있다."""
    rows = _rows(120, categories=10, leak=False)
    real = category_rate(rows, "document_type")
    null = null_rate(rows, lambda rs: category_rate(rs, "document_type"), trials=100)
    assert real > 30, "작은 표본에서 실측은 최빈등급(25%)보다 크게 나오는 것이 정상이다"
    assert abs(real - null) < 8, f"관계가 없는데 차이가 크다: 실측 {real:.1f} · 섞음 {null:.1f}"


def test_real_relationship_shows_large_excess():
    """범주가 등급을 그대로 알려 주면 차이가 크게 나와야 한다."""
    rows = _rows(120, categories=4, leak=True)
    real = category_rate(rows, "document_type")
    null = null_rate(rows, lambda rs: category_rate(rs, "document_type"), trials=100)
    assert real > 95
    assert real - null > 50, f"진짜 누설인데 차이가 작다: 실측 {real:.1f} · 섞음 {null:.1f}"


def test_majority_baseline_would_have_raised_a_false_alarm():
    """옛 기준(최빈등급 비율)으로 보면 관계가 없는 자료도 '지름길'로 보인다.

    이 시험은 **옛 기준이 왜 못 쓰는지**를 못 박는다. 이 성질이 사라지면(=표본이 크면)
    시험이 깨지는데, 그때는 시험을 고치는 것이 맞다 — 규칙이 바뀐 것이 아니라 조건이 바뀐 것이다.
    """
    rows = _rows(120, categories=10, leak=False)
    real = category_rate(rows, "document_type")
    majority = 100 * max(sum(1 for r in rows if r["label"] == g) for g in GRADES) / len(rows)
    assert real - majority > 10, "옛 기준으로는 부풀림이 그대로 '지름길'로 읽힌다"


def test_length_axis_uses_the_same_rule():
    rows = _rows(200, categories=6, leak=False)
    real = length_rate(rows)
    null = null_rate(rows, length_rate, trials=100)
    assert abs(real - null) < 8


def test_phrase_axis_catches_a_planted_sentence():
    """되풀이 문구 축이 실제로 심은 문장을 잡는가 — 오늘 두 번 데인 자리다."""
    rows = _rows(120, categories=6, leak=False)
    for row in rows:
        if row["label"] == "TS":
            row["text"] = "이 문서는 지정된 조건에서만 열람한다. " + row["text"]
    real = phrase_rate(rows, min_docs=5)
    null = null_rate(rows, lambda rs: phrase_rate(rs, min_docs=5), trials=20)
    assert real - null > 15, f"심은 문장을 못 잡는다: 실측 {real:.1f} · 섞음 {null:.1f}"


def test_audit_reports_all_four_axes_with_null_and_excess():
    rows = _rows(120, categories=6, leak=False)
    rep = audit(rows, trials=40)
    assert set(rep["axes"]) == {"document_type", "theme", "length", "phrase"}
    for name, row in rep["axes"].items():
        assert set(row) == {"rate", "null", "excess_pp"}, name
        assert row["excess_pp"] == pytest.approx(row["rate"] - row["null"], abs=0.11), name


def test_null_rate_does_not_mutate_the_input():
    """섞기가 원본 라벨을 건드리면 그 뒤 측정이 전부 망가진다."""
    rows = _rows(40, categories=4, leak=True)
    before = [r["label"] for r in rows]
    null_rate(rows, lambda rs: category_rate(rs, "document_type"), trials=10)
    assert [r["label"] for r in rows] == before
