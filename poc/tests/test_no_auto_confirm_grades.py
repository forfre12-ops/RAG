"""특정 등급을 자동확정에서 빼는 손잡이 — 기본값 무동작 잠금.

왜(2026-09-13). golden100 적대셋 100건을 서빙 경로 전체로 재니 이랬다:

    기본(빈 목록)   무음 미탐 8건 · 자동확정 74%
    ["S2"]          무음 미탐 2건 · 자동확정 35%

미탐 8건 중 6건이 S1 을 S2 로 본 것이라 S2 를 자동확정에서 빼면 대부분 걸린다.
같은 날 LLM 2차의견도 재봤는데 미탐 1건·자동확정 39% 로 거의 같은 자리였고,
건당 4초가 더 들면서 판별력은 없었다(발동 35건 중 실제 고등급 11 — 무작위 기대 17.5 이하).

이 시험이 잠그는 것은 둘이다.
  ① **기본값은 아무 일도 하지 않는다** — 설정을 안 건드리면 배포본 판정이 그대로다
  ② 설정이 들어오면 그 등급만 검수로 가고 **등급 자체는 안 바뀐다**
"""
from __future__ import annotations

import pytest

from koipa.config import settings


def test_default_is_empty_so_nothing_changes() -> None:
    """기본값이 비어 있어야 한다. 여기가 깨지면 배포본 자동확정률이 조용히 바뀐다."""
    assert list(getattr(settings, "no_auto_confirm_grades", [])) == []


def test_setting_accepts_grade_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "no_auto_confirm_grades", ["S2"], raising=False)
    assert [g.upper() for g in settings.no_auto_confirm_grades] == ["S2"]


def test_gate_is_wired_and_grade_preserving() -> None:
    """게이트가 서빙 경로에 실제로 있고, 등급을 바꾸지 않는지 소스로 확인한다.

    ⚠ 서빙 전체를 태우는 시험은 API·모델이 필요해 여기서 하지 않는다. 대신
      '등급 무변경' 계약이 코드에 남아 있는지를 본다 — 누가 status 대신 label 을
      건드리면 이 시험이 깨진다.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src/koipa/services/classify_service.py"
    text = src.read_text(encoding="utf-8")
    assert "no_auto_confirm_grades" in text, "게이트가 서빙에 배선되지 않았다"
    block = text[text.index("[no-auto-confirm-grades]"):]
    block = block[: block.index("[abbrev-only-escalation")]
    assert 'status = "needs_review"' in block, "검수 라우팅이 없다"
    assert "pred.label =" not in block, "등급을 바꾸면 안 된다 — 검수 라우팅만"
    assert "grade unchanged" in block, "등급 무변경 계약이 경고 문구에 없다"
