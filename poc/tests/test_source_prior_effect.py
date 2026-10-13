# -*- coding: utf-8 -*-
"""출처 cap 효과 측정의 집계 계약.

왜(2026-09-14). 공개 보도자료를 서빙에 태웠더니 격상(TS·S1) 28.3% 가 나왔는데,
metadata.source_type 하나를 같이 보내니 0.0% 가 됐다. 이 수치가 감리 185(가) 대응에
그대로 나가므로 집계식을 고정한다 — 특히 **과탐과 격상은 다른 축**이고
**검수로 간 것은 자동확정 과탐에서 빠진다**.
"""
from __future__ import annotations

import sys
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from measure_source_prior_effect import summarize  # noqa: E402


def _r(truth, pred, status="staging", warnings=None):
    return {"truth": truth, "pred": pred, "status": status, "warnings": warnings or []}


def test_overclass_and_severe_are_different_axes() -> None:
    """S3 를 S2 라 부른 것과 TS 라 부른 것은 같은 '과탐' 이지만 무게가 다르다."""
    s = summarize([_r("S3", "S2"), _r("S3", "TS"), _r("S3", "S3")])
    assert s["n_low"] == 3
    assert s["overclass"] == 2
    assert s["severe"] == 1
    assert s["severe_rate"] < s["overclass_rate"]


def test_review_routed_overclass_is_not_auto_confirmed() -> None:
    """cap-conflict 로 검수에 보낸 것은 '자동확정 과탐' 이 아니다 — 사람이 본다."""
    s = summarize([_r("S3", "TS", "needs_review"), _r("S3", "TS", "staging")])
    assert s["overclass"] == 2
    assert s["auto_overclass"] == 1


def test_high_grade_truth_is_excluded_from_overclass_denominator() -> None:
    """과탐 분모는 저등급 정답면이다 — 고등급 문서를 섞으면 비율이 희석된다."""
    s = summarize([_r("TS", "TS"), _r("S3", "TS")])
    assert s["n_low"] == 1
    assert s["overclass"] == 1
    assert s["overclass_rate"] == 1.0


def test_review_rate_uses_all_documents() -> None:
    """검수율 분모는 전체다 — 저등급만 세면 업무량을 과소평가한다."""
    s = summarize([_r("TS", "TS", "needs_review"), _r("S3", "S3", "staging")])
    assert s["n_low"] == 1
    assert s["review_load"] == 1
    assert s["review_rate"] == 0.5


def test_perfect_cap_gives_zero_not_none() -> None:
    """전부 S3 로 cap 되면 0.0% 다 — 분모가 있으므로 '못 잼' 이 아니다."""
    s = summarize([_r("S3", "S3") for _ in range(10)])
    assert s["n_low"] == 10
    assert s["overclass_rate"] == 0.0
    assert s["severe_rate"] == 0.0


def test_measured_shape_2026_09_14() -> None:
    """60건 실측 형태를 고정한다 — 메타 없음에서 격상이 나오고 메타 있음에서 0 이 된다."""
    without = ([_r("S3", "TS")] * 12 + [_r("S3", "S1")] * 5
               + [_r("S3", "S2")] * 1 + [_r("S3", "S3")] * 42)
    with_md = [_r("S3", "S3") for _ in range(60)]
    a, b = summarize(without), summarize(with_md)
    assert a["severe"] == 17
    assert abs(a["severe_rate"] - 17 / 60) < 1e-9
    assert b["severe"] == 0
    assert b["overclass_rate"] == 0.0


# ── ICD 계약값이 실제로 cap 을 발동시키는가 ──────────────────────────────

def test_icd_source_type_values_drive_the_cap() -> None:
    """KL 이 ICD §3.1 대로 보내는 값이 출처 cap 을 발동시켜야 한다.

    실측 2026-09-14: 레거시 한글 토큰('보도자료')과 ICD 정식값('public')이
    같은 60건에서 **동일한 결과**를 냈다(격상 28.3% → 0.0%). 토큰 목록이
    바뀌어 ICD 값이 빠지면 그 순간 KL 이 보내는 메타데이터가 무시된다.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "src"))
    from koipa.modules.m3_labeling.rule_engine import _ICD_SOURCE_TYPES
    from koipa.modules.m5_inference.pipeline import _source_prior_is_public

    # 공개로 보아 S3 로 cap 해야 하는 값
    for v in ("public", "registered_patent", "academic"):
        assert v in _ICD_SOURCE_TYPES, f"{v} 가 ICD 값 집합에서 빠졌다"
        assert _source_prior_is_public(v) is True, f"{v} 가 cap 을 발동시키지 않는다"

    # 비공개 — cap 이 걸리면 내부 문서가 S3 로 떨어져 미탐이 된다
    for v in ("internal", "external_confidential"):
        assert v in _ICD_SOURCE_TYPES
        assert _source_prior_is_public(v) is False, f"{v} 에 cap 이 걸린다 — 미탐 위험"


def test_negative_tokens_beat_public_tokens() -> None:
    """'미공개 보도자료' 처럼 부정어가 붙으면 공개로 보면 안 된다."""
    from koipa.modules.m5_inference.pipeline import _source_prior_is_public
    assert _source_prior_is_public("보도자료") is True
    for v in ("미공개", "비공개 보도자료", "internal draft", "공개특허 초안"):
        assert _source_prior_is_public(v) is False, f"{v!r} 를 공개로 봤다"
