# -*- coding: utf-8 -*-
"""S·V·M 결정표 골격 계약 — 승인된 칸과 코드가 어긋나면 잡는다.

왜(2026-09-14). "등급 기준을 명확히 하자" 가 여러 번 나왔는데 매번 27조합 이야기였다.
실제로 막히는 자리는 **미확인(unknown)** 이고, 그 칸은 승인 대상조차 아니다.
그리고 세어 보니 27칸 중 외부 승인된 것은 **5칸뿐**이었다.

이 시험이 잠그는 것 셋:
  1) 승인된 5칸은 배포 기본 모드(v22)와 일치해야 한다 — 어긋나면 기준이 둘이 된다
  2) 승인 안 된 칸에 답을 채우면 안 된다 — 코드 답은 기준이 아니다
  3) 모드를 바꾸면 승인 앵커가 깨지는 자리를 명시한다 — 실제로 1칸 있다
"""
from __future__ import annotations

import itertools
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "scripts"))

from build_decision_table import UNKNOWN, build, load_anchors  # noqa: E402
from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    return build()


def test_table_covers_64_cells(rows) -> None:
    """축당 4값(0·1·2·미확인)이면 4^3 = 64. 27 만 세면 미확인 축을 통째로 빠뜨린다."""
    assert len(rows) == 64
    assert len({r["rule_id"] for r in rows}) == 64
    known = [r for r in rows if not r["unknown_axes"]]
    assert len(known) == 27


def test_approved_cells_match_deployed_default(rows) -> None:
    """외부 승인 칸은 배포 기본 모드와 같아야 한다 — 다르면 기준이 두 벌이다."""
    approved = [r for r in rows if r["authority"] == "guide_p12"]
    assert approved, "승인 앵커를 하나도 못 읽었다 — guide_v2_anchors.jsonl 확인"
    for r in approved:
        got = grade_from_svm(int(r["s"]), int(r["v"]), int(r["m"]))  # 기본 모드
        assert got == r["grade"], (
            f"{r['rule_id']}: 승인 {r['grade']} 인데 배포 기본 모드는 {got}"
        )


def test_only_five_cells_are_externally_approved(rows) -> None:
    """지금 외부 승인은 5칸뿐이다. 늘어나면(= 20 기준사례가 들어오면) 이 수를 갱신한다."""
    approved = [r for r in rows if r["authority"] == "guide_p12"]
    assert len(approved) == 5, (
        f"승인 칸이 {len(approved)}개다 — 결정표 승인 범위가 바뀌었으면 이 시험과 "
        "datasets/gold/decision_table_v0.jsonl 을 같이 갱신할 것"
    )


def test_unapproved_cells_have_no_grade(rows) -> None:
    """코드가 답을 낸다고 그것이 기준이 되지는 않는다 — 승인 안 된 칸은 비워 둔다."""
    for r in rows:
        if r["authority"] != "guide_p12":
            assert r["grade"] is None, f"{r['rule_id']} 에 승인 없이 답이 채워졌다"


def test_unknown_cells_are_unapproved_and_reviewed(rows) -> None:
    """미확인이 낀 칸은 승인 대상조차 아니고, 후보가 갈리면 검수로 가야 한다."""
    unk = [r for r in rows if r["unknown_axes"]]
    assert len(unk) == 37
    for r in unk:
        assert r["authority"] == "UNAPPROVED"
        assert r["grade"] is None
        assert r["grade_candidates"], f"{r['rule_id']} 에 후보가 비었다"
        if len(r["grade_candidates"]) > 1:
            assert r["needs_review"] is True


def test_candidates_are_derived_not_invented(rows) -> None:
    """후보 집합은 미확인 축을 0·1·2 로 움직인 결과여야 한다 — 지어낸 값이면 안 된다."""
    for r in rows:
        axes = [(0, 1, 2) if r[a] == UNKNOWN else (int(r[a]),) for a in ("s", "v", "m")]
        expect = {grade_from_svm(x, y, z) for x, y, z in itertools.product(*axes)}
        assert set(r["grade_candidates"]) == expect, r["rule_id"]


def test_guide_mode_breaks_one_approved_anchor(rows) -> None:
    """⚠ 승인 앵커 5칸 중 1칸이 guide 모드와 어긋난다 — 모드를 바꿀 때 가장 먼저 부딪힌다.

    인사평가보고서 (1,2,2): 승인값 S2 · 가이드 p12 원본 S1 · 코드 guide 모드 S1.
    앵커 레코드가 그 차이를 `guide_p12_grade` 로 정직하게 병기해 두었다.
    """
    broken = []
    for r in rows:
        if r["authority"] != "guide_p12":
            continue
        if grade_from_svm(int(r["s"]), int(r["v"]), int(r["m"]), mode="guide") != r["grade"]:
            broken.append(r["rule_id"])
    assert broken == ["REF-SVM-S1V2M2"], (
        f"guide 모드와 어긋나는 승인 칸이 {broken} 로 바뀌었다 — 발주처 협의 항목이 변했는지 확인"
    )


def test_anchor_records_disclose_guide_original(rows) -> None:
    """보정한 앵커는 원본 등급을 병기해야 한다 — 숨기면 감리에서 설명할 수 없다."""
    anchors = load_anchors()
    corrected = [a for a in anchors.values()
                 if a.get("guide_p12_grade") and a["guide_p12_grade"] != a["grade"]]
    assert corrected, "보정 앵커가 사라졌다 — 원본 병기 규율이 유지되는지 확인"
    for a in corrected:
        assert a.get("note"), f"{a.get('doc_type')} 보정 사유가 비었다"


def test_saved_table_matches_generator() -> None:
    """저장본이 생성기와 어긋나면 둘 중 하나가 낡은 것이다."""
    path = POC / "datasets/gold/decision_table_v0.jsonl"
    if not path.exists():
        pytest.skip("결정표 저장본 없음 — scripts/build_decision_table.py --out 로 생성")
    saved = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    fresh = build()
    assert len(saved) == len(fresh)
    by_id = {r["rule_id"]: r for r in fresh}
    for r in saved:
        assert r == by_id[r["rule_id"]], f"{r['rule_id']} 저장본이 생성기와 다르다"
