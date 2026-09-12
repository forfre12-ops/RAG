"""품질 지표 카드는 **방향과 결과**를 말해야 한다.

왜(사용자 지적 2026-08-18). 길이 누출 카드가 이렇게 떠 있었다.

    길이만으로 등급 맞히기
    0.301
    무작위 0.25 · 0.40 초과면 경고

세 가지가 오해를 만든다.
  ① 대부분의 지표는 높을수록 좋은데 이건 반대다. 그 말이 카드에 없다.
     0.301 > 0.25 를 보고 "무작위보다 높으니 괜찮네" 로 읽으면 정확히 거꾸로다.
  ② 제목이 '등급 맞히기' 라 **기능처럼** 읽힌다. 실제로는 결함 지표다.
  ③ 무엇이 걸린 문제인지 안 적혀 있다 — 이 값이 높으면 이 셋으로 잰 정확도가 부풀려진다.

옆 카드는 잘 돼 있다: "본문에 등급 노출 / 3건 / 문서에 답이 적혀 있으면 검수가 무의미".
결과를 말한다. 이 카드도 같은 규율을 따르게 하고, 되돌아가지 않게 잠근다.
"""

from __future__ import annotations

import pytest

from koipa.api.golden import _render_specledger_gold_console_html


@pytest.fixture(scope="module")
def console() -> str:
    return _render_specledger_gold_console_html()


def test_leakage_card_states_which_direction_is_good(console):
    """이것 하나가 빠지면 숫자를 정반대로 읽는다."""
    assert "낮을수록 좋습니다" in console


def test_leakage_card_states_what_is_at_stake(console):
    """무엇이 걸린 문제인지 없으면 '그래서 뭐' 로 끝난다."""
    assert "정확도는 부풀려집니다" in console


def test_leakage_card_title_is_not_read_as_a_feature(console):
    """'등급 맞히기' 는 기능처럼 읽힌다 — 결함 지표라는 것이 제목에서 드러나야 한다."""
    assert "길이만으로 등급 맞히기" not in console
    assert "길이가 등급을 알려주는 정도" in console


def test_leakage_card_keeps_the_baseline_and_threshold(console):
    """읽는 방법을 고치는 것이지 근거를 빼는 것이 아니다."""
    assert "무작위 " in console
    assert "LEAK_MAX" in console and "초과면 경고" in console


def test_sibling_cards_still_name_their_consequence(console):
    """같은 규율이 옆 카드에도 유지되는지 — 한쪽만 고쳐 놓으면 다시 어긋난다."""
    assert "문서에 답이 적혀 있으면 검수가 무의미" in console
    assert "모델이 내용 대신 길이를 외웁니다" in console, "표 아래 해설이 사라졌다"
