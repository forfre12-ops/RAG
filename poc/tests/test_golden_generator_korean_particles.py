"""골든 후보 본문의 조사가 받침에 맞는가.

왜 이 시험이 있는가(2026-09-12). 생성기 틀에 조사가 한 형태로 박혀 있었다 —
`"{case.issue}가 확정되지 않은 상태에서…"`. 채워 넣는 말은 문서마다 다른데
받침 유무에 따라 이/가·은/는·을/를·과/와가 갈린다. 그래서 후보 본문에

    "…공개 안내 범위를 넘어서는 내부 정보가 섞이지 않았는지 확인가 확정되지 않은…"
    "…차세대 열관리 소재이 외부에 알려질 경우의…"

같은 문장이 실려 나갔다. 현행 후보 풀 실측으로 **파일 1,942개 / 출현 7,758회**였다
(분모 2,344 = 원본 .md + revisions). 검수자와 감리가 읽는 문서다.

⚠ 서술격 '이다/이며'는 조사가 아니다 — 교정기는 조사 뒤가 공백일 때만 손댄다.
  이 시험이 그 경계도 함께 지킨다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_proxy_gold_pilot_100 import (  # noqa: E402
    Case,
    _contextualize_standard_sentences,
    _fix_particles,
    _has_batchim,
)

_PAIRS = (("이", "가"), ("은", "는"), ("을", "를"), ("과", "와"))


def _case(**kw) -> Case:
    base = dict(
        grade="S2",
        kind="운영개선 결과서",
        title="현장 품질 대응 운영개선 결과서 초기 확인 B1-04-01",
        subject="현장 품질 대응",
        issue="예외 승인 절차가 기록으로 남아 있는지 확인",
        evidence="불량 유형·재발 조건·조치 이력",
        owner="품질보증",
    )
    base.update(kw)
    return Case(**base)


@pytest.mark.parametrize(
    "word, batchim",
    [("확인", True), ("소재", False), ("품질보증", True), ("대응", True), ("이력", True)],
)
def test_batchim_detection(word, batchim):
    assert _has_batchim(word[-1]) is batchim


def test_batchim_of_non_hangul_is_undecided():
    """숫자·영문으로 끝나면 읽는 법이 갈린다 — 판단하지 않는다."""
    assert _has_batchim("B1-04-01"[-1]) is None


def test_consonant_ending_word_takes_i_not_ga():
    case = _case()
    text = f"{case.issue}가 확정되지 않은 상태에서 넓게 공유되면"
    assert _fix_particles(text, case).startswith(case.issue + "이 ")


def test_vowel_ending_word_takes_ga_not_i():
    case = _case(subject="차세대 열관리 소재")
    text = f"{case.subject}이 외부에 알려질 경우"
    assert _fix_particles(text, case).startswith(case.subject + "가 ")


def test_copula_is_not_touched():
    """'이다/이며'의 '이'는 서술격이다 — 뒤에 공백이 없으므로 손대지 않는다."""
    case = _case(subject="차세대 열관리 소재")
    for tail in ("이다.", "이며 다음 항목을 본다."):
        text = f"핵심 쟁점은 {case.subject}{tail}"
        assert _fix_particles(text, case) == text


def test_all_particle_pairs_are_corrected():
    case = _case()
    for after_batchim, after_vowel in _PAIRS:
        wrong = f"{case.issue}{after_vowel} 뒤에 온다"
        assert _fix_particles(wrong, case) == f"{case.issue}{after_batchim} 뒤에 온다"


def test_rendered_document_has_no_particle_mismatch():
    """실제로 찍어낸 본문에 어긋난 조사가 남지 않는다."""
    case = _case()
    body = "\n".join(
        f"{getattr(case, field)}{wrong} 확인이 필요하다."
        for field in ("subject", "issue", "evidence", "owner")
        for wrong in ("가", "이", "은", "는", "을", "를", "와", "과")
    )
    out = _contextualize_standard_sentences(body, case)
    for field in ("subject", "issue", "evidence", "owner"):
        word = getattr(case, field)
        batchim = _has_batchim(word[-1])
        for after_batchim, after_vowel in _PAIRS:
            bad = after_vowel if batchim else after_batchim
            assert not re.search(re.escape(word + bad) + r"\s", out), (
                f"{field}={word!r} 뒤에 어긋난 조사 {bad!r} 가 남았다"
            )
