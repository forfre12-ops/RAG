"""라이브 FNR 배선 검증 — confirm/relabel 정답 확정 시 classify_total/_correct_total 동반 증가.

정탐(확정등급==예측)이면 total·correct 둘 다, 오탐(불일치)이면 total만 증가해야 한다.
서빙 시점(정답 없음)엔 증가하지 않으므로 여기(확정 경로)가 유일 배선점이다.
"""
from __future__ import annotations

from koipa.api.prom_metrics import CLASSIFY_CORRECT_TOTAL, CLASSIFY_TOTAL
from koipa.services.confirm_service import _record_live_fnr


def _read(counter) -> float:
    return counter._value.get()  # noqa: SLF001 — 테스트 전용 카운터 스냅샷


def test_confirmed_match_increments_both():
    t0, c0 = _read(CLASSIFY_TOTAL), _read(CLASSIFY_CORRECT_TOTAL)
    _record_live_fnr(predicted_level_id=3, final_level_id=3)  # 정탐
    assert _read(CLASSIFY_TOTAL) == t0 + 1
    assert _read(CLASSIFY_CORRECT_TOTAL) == c0 + 1


def test_relabel_mismatch_increments_total_only():
    t0, c0 = _read(CLASSIFY_TOTAL), _read(CLASSIFY_CORRECT_TOTAL)
    _record_live_fnr(predicted_level_id=3, final_level_id=1)  # 오탐(미탐 교정)
    assert _read(CLASSIFY_TOTAL) == t0 + 1
    assert _read(CLASSIFY_CORRECT_TOTAL) == c0  # 분자 불변


def test_none_predicted_counts_as_miss():
    """예측 등급 미상(None)은 정탐으로 셀 수 없다 — total만 증가(분자 불변)."""
    t0, c0 = _read(CLASSIFY_TOTAL), _read(CLASSIFY_CORRECT_TOTAL)
    _record_live_fnr(predicted_level_id=None, final_level_id=1)
    assert _read(CLASSIFY_TOTAL) == t0 + 1
    assert _read(CLASSIFY_CORRECT_TOTAL) == c0
