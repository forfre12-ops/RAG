"""grade_formula_mode(v22/guide/fnr)를 바꿔도 룰 엔진의 최종 등급이 안 바뀐다는 것을 잠근다.

배경(2026-09-13 실측, rule_engine.py 의 grade_from_svm 독스트링에 기록됨): hardened42·
holdout109·golden100 세 면을 v22 와 guide 모드로 각각 서빙 경로 전체에 태웠더니 무음
미탐·자동확정률이 소수점까지 동일했다. 이유: LabelRuleEngine.label() 이 최종 등급으로
min-rank(svm_grade, content_grade) 를 쓰는데, s_lv·v_lv 가 애초에 content_grade(키워드
argmax) 에서 역산되므로 svm_grade 는 content_grade 를 거의 그대로 따라간다 — 곱셈
단계(grade_formula_mode)가 바뀌어도 갈리는 지점이 거의 없다.

이 사실 자체는 이미 실측됐지만 회귀 테스트로 고정된 적이 없었다(2026-09-16 확인 — grep
결과 0건). 곱셈식 리팩터링이나 s_lv/v_lv 역산 로직이 바뀌면 이 무동작이 조용히 깨질 수
있으므로, 실문서 텍스트(hardened42·holdout109에서 각 10건)로 세 모드를 직접 비교해 잠근다.

⚠ 이 테스트가 실패하면 "formula mode 는 no-op" 이라는 그동안의 전제(여러 분석·로드맵의
근거)가 더는 맞지 않는다는 뜻이다 — 실패를 무시하고 스킵하지 말 것.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.config import settings
from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine

_POC = Path(__file__).resolve().parents[1]
_MODES = ("v22", "guide", "fnr")
_N_PER_FILE = 10


def _sample_texts(rel_path: str, n: int) -> list[str]:
    path = _POC / rel_path
    texts: list[str] = []
    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            text = row.get("text") or row.get("content") or ""
            if text.strip():
                texts.append(text)
            if len(texts) >= n:
                break
    assert texts, f"{rel_path} 에서 텍스트를 하나도 못 읽었다"
    return texts


_HARDENED = _sample_texts("datasets/gold_real/holdout_eval.hardened.jsonl", _N_PER_FILE)
_HOLDOUT = _sample_texts("datasets/gold_real/holdout_eval.jsonl", _N_PER_FILE)


def _grade_of(text: str) -> str:
    result = LabelRuleEngine().label(text)
    g = result.grade
    return g.value if hasattr(g, "value") else str(g)


@pytest.mark.parametrize("surface,texts", [("hardened42", _HARDENED), ("holdout109", _HOLDOUT)])
def test_formula_mode_does_not_change_final_grade(monkeypatch, surface, texts):
    """세 모드(v22/guide/fnr)로 같은 실문서를 태워도 최종 등급이 같아야 한다."""
    by_mode: dict[str, list[str]] = {}
    for mode in _MODES:
        monkeypatch.setattr(settings, "grade_formula_mode", mode)
        by_mode[mode] = [_grade_of(t) for t in texts]

    baseline = by_mode["v22"]
    for mode in ("guide", "fnr"):
        mismatches = [
            (i, baseline[i], by_mode[mode][i])
            for i in range(len(texts))
            if baseline[i] != by_mode[mode][i]
        ]
        assert not mismatches, (
            f"[{surface}] v22 대비 {mode} 모드에서 등급이 갈린 문서가 있다 — "
            f"formula-mode-is-noop 전제가 깨졌다: {mismatches}"
        )


def test_three_modes_are_actually_distinct_in_isolation():
    """no-op 확인이 '모드 전환 자체가 고장나서 항상 같은 값을 낸' 게 아님을 함께 확인한다.

    grade_from_svm 단독 호출로는 27조합 중 4개가 실제로 다르다(2026-09-16 재확인) —
    그 갈림이 label() 안에서는 content_grade 우선 로직에 가려진다는 것이 이 테스트군의
    핵심 주장이므로, 모드 자체는 살아 있어야 위 테스트가 의미가 있다.
    """
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    diffs = [
        (s, v, m)
        for s in (0, 1, 2)
        for v in (0, 1, 2)
        for m in (0, 1, 2)
        if grade_from_svm(s, v, m, mode="v22") != grade_from_svm(s, v, m, mode="guide")
    ]
    assert len(diffs) == 4, (
        f"grade_from_svm 자체의 v22/guide 불일치 개수가 4가 아니라 {len(diffs)} 다 — "
        "산정식이 바뀌었을 수 있으니 위 no-op 전제를 다시 실측할 것"
    )
