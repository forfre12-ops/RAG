"""골든 후보 생성기가 본문에 등급을 알려 주는 흔적을 심지 않는가.

왜 이 시험이 있는가(2026-09-12). 후보 1,055건을 전수로 재 보니 **되풀이되는 단서 하나로
등급을 91.4% 맞힐 수 있었다**(기준선 32.4% = 가장 흔한 등급만 찍기). 원인은 생성기가 등급마다
고정된 어휘를 넣은 것이었다:

    ISSUES          등급마다 '확인 문제' 문장 하나  → 그 문장만으로 85.3%
    DOCUMENT_FORMS  등급 전용 문서종류 목록        → 문서종류만으로 85.4%
    risk_words      등급마다 고정된 위험 서술      → 964건에 그대로 남아 있었다

정답 누출 세척(clean_candidate_answer_leak.py)을 통과한 판인데도 그랬다 — 세척은 아는
형태('## 등급 제안 사유' 절)만 지우기 때문이다. 그래서 **지우는 쪽이 아니라 만드는 쪽**을 막는다.

⚠ 이 시험은 상관을 0 으로 요구하지 않는다. 실제 문서에서도 '공개 안내문'은 공개 등급에 많다.
  요구하는 것은 **단일 단서로 등급이 결정되지 않을 것** — 어떤 어휘도 한 등급 전용이 아니어야 한다.
"""

from __future__ import annotations

import collections
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

batch = pytest.importorskip("build_proxy_gold_batch_300")
pilot = pytest.importorskip("build_proxy_gold_pilot_100")


def _owners(mapping: dict[str, tuple[str, ...]]) -> dict[str, set[str]]:
    """어휘 → 그 어휘가 나오는 등급 집합."""
    out: dict[str, set[str]] = collections.defaultdict(set)
    for grade, words in mapping.items():
        for w in words:
            out[w].add(grade)
    return out


def test_no_document_form_belongs_to_one_grade_only():
    solo = {w: g for w, g in _owners(batch.DOCUMENT_FORMS).items() if len(g) < 2}
    assert not solo, f"한 등급 전용 문서종류: {sorted(solo)}"


def test_no_issue_sentence_belongs_to_one_grade_only():
    solo = {w: g for w, g in _owners(batch.ISSUES).items() if len(g) < 2}
    assert not solo, f"한 등급 전용 확인문제 문장: {sorted(solo)}"


def test_single_clue_cannot_decide_the_grade():
    """생성될 300건에서 '어휘 하나로 최빈 등급 찍기' 적중률이 기준선 근처여야 한다."""
    cases = batch.make_cases(1)
    n = len(cases)
    base = collections.Counter(c.grade for c in cases).most_common(1)[0][1] / n

    for label, pick in (("문서종류", lambda c: c.kind), ("확인문제", lambda c: c.issue)):
        by_clue: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        for c in cases:
            by_clue[pick(c)][c.grade] += 1
        hit = sum(d.most_common(1)[0][1] for d in by_clue.values())
        acc = hit / n
        # 기준선(가장 흔한 등급만 찍기)의 2배 미만. 옛 판은 1.00 이었다.
        assert acc < base * 2, f"{label} 단서만으로 {acc:.1%} 적중 (기준선 {base:.1%})"


def test_risk_sentence_does_not_depend_on_grade():
    """본문의 위험 서술이 등급에 따라 달라지면 그 문장이 곧 정답이다."""
    made = {
        g: pilot._case_specific_appendix(
            pilot.Case(grade=g, kind="변경영향 분석서", title="같은 제목 B9-01-01",
                       subject="운영 자동화 전환", issue="예외 승인 절차가 기록으로 남아 있는지 확인",
                       evidence="처리 기준·예외 흐름·성능 기록", owner="서비스운영")
        )
        for g in ("TS", "S1", "S2", "S3")
    }
    bodies = set(made.values())
    assert len(bodies) == 1, "제목·내용이 같은데 등급만 다르면 본문도 같아야 한다 — 다르면 등급이 새어 나간다"
