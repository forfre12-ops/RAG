"""[B-1] build_synthetic_golden 섀도 심판 해석(_resolve_shadow_model) 단위 테스트.

섀도는 주 심판과 독립이어야 production_suspect(배포 실패 예측) 신호가 있다. 심판과 동일하면
자기합의라 shadow_grade==llm_grade가 강제돼 신호 0 → 자동 비활성(경고). 기본은 ON(Qwen)로,
옛 스크립트 기본 None(섀도 없음=production_suspect 상시 False=안전장치 no-op)을 교정한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from build_synthetic_golden import _resolve_shadow_model  # noqa: E402


def _capture():
    msgs: list[str] = []
    return msgs, msgs.append


def test_default_shadow_independent_of_judge() -> None:
    msgs, log = _capture()
    assert _resolve_shadow_model("qwen3:14b", "gemma3:12b", log) == "qwen3:14b"
    assert msgs == []  # 독립 → 경고 없음, 섀도 활성


def test_shadow_equal_judge_disabled_with_warning() -> None:
    msgs, log = _capture()
    # 섀도==심판 → 자기합의라 production_suspect 발화 불가 → None(비활성) + 경고.
    assert _resolve_shadow_model("gemma3:12b", "gemma3:12b", log) is None
    assert msgs and any("자기합의" in m for m in msgs)


def test_empty_shadow_is_none() -> None:
    msgs, log = _capture()
    assert _resolve_shadow_model(None, "gemma3:12b", log) is None
    assert _resolve_shadow_model("", "gemma3:12b", log) is None
    assert msgs == []  # 애초에 섀도 없음 → 경고 아님


def test_explicit_distinct_shadow_kept() -> None:
    msgs, log = _capture()
    assert _resolve_shadow_model("exaone3.5:latest", "gemma3:12b", log) == "exaone3.5:latest"
    assert msgs == []
