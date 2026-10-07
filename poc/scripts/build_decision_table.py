#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""S·V·M 결정표 골격을 만든다 — 미확인을 포함한 64칸에서 **무엇이 비어 있는지** 센다.

## 왜 (2026-09-14)

"등급 기준을 명확히 하자" 는 제안이 여러 번 나왔는데, 매번 27조합(3값^3) 이야기였다.
그런데 실제로 막히는 자리는 **미확인(unknown)** 이다 —

    management_from_metadata() 는 present / proven_absent / unknown 3상태를 돌려준다.
    factor_model 은 축당 4클래스(CLS_UNKNOWN 포함)라 4^3 = 64 를 표현할 수 있다.
    그런데 cls_to_worst 가 등급 산출 **직전에** unknown 을 2 로 접어 27 로 되돌린다.

그래서 결정표는 **접히기 전 단계**에 두어야 한다. 64칸을 세면 이렇게 갈린다.

    27칸  세 축이 모두 확정 — 곱셈식이 답을 준다(단 승인된 것은 5칸뿐)
    37칸  한 축이라도 미확인 — **승인된 답이 존재하지 않는다**

## 승인 권위를 세 단계로 나눈다

    guide_p12       발주처 가이드 p12 워크드 예시 — 외부 승인. 5칸뿐이다.
    v22_code_only   현행 배포 코드가 답을 내지만 외부 승인은 없다.
    UNAPPROVED      승인도 코드 답도 없다(미확인 축이 낀 칸).

⚠ 기대값을 **시험 대상 함수로 다시 계산하지 않는다.** 그렇게 만든 표는 식이 틀렸을 때
   시험도 같이 틀린다(test_grade_formula_modes.py 가 그 모양이었다). 여기서는
   코드 답을 `code_*` 칸에 **참고로만** 적고, `grade` 는 승인된 것만 채운다.

미확인 칸의 `grade_candidates` 는 그 축을 0·1·2 로 움직여 나오는 등급 집합이다.
이것은 답을 지어내는 것이 아니라 **"사람이 무엇 중에서 골라야 하는가"** 를 적는 것이다.
후보가 2개 이상이면 `needs_review=true` 다.

사용:
    PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/build_decision_table.py
    ... --out datasets/gold/decision_table_v0.jsonl
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(POC / "src"))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

from koipa.modules.m3_labeling.rule_engine import grade_from_svm  # noqa: E402

ANCHORS = POC / "datasets/gold/guide_v2_anchors.jsonl"
UNKNOWN = "unknown"
LEVELS = (0, 1, 2, UNKNOWN)
AXES = ("s", "v", "m")
GRADE_ORDER = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}


def load_anchors() -> dict[tuple[int, int, int], dict]:
    """발주처 가이드 p12 워크드 예시 — 외부 승인된 유일한 칸들."""
    out: dict[tuple[int, int, int], dict] = {}
    if not ANCHORS.exists():
        return out
    for line in ANCHORS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") != "svm_anchor":
            continue
        key = (int(r["secrecy"]), int(r["value"]), int(r["management"]))
        out[key] = r
    return out


def _rule_id(s, v, m) -> str:
    def tok(x):
        return "U" if x == UNKNOWN else str(x)
    return f"REF-SVM-S{tok(s)}V{tok(v)}M{tok(m)}"


def candidates(s, v, m) -> list[str]:
    """미확인 축을 0·1·2 로 움직여 나오는 등급 집합. 답이 아니라 **선택지**다."""
    axes = []
    for x in (s, v, m):
        axes.append((0, 1, 2) if x == UNKNOWN else (x,))
    got = {grade_from_svm(a, b, c) for a, b, c in itertools.product(*axes)}
    return sorted(got, key=lambda g: GRADE_ORDER[g])


def build() -> list[dict]:
    anchors = load_anchors()
    rows: list[dict] = []
    for s, v, m in itertools.product(LEVELS, LEVELS, LEVELS):
        unknown_axes = [a for a, x in zip(AXES, (s, v, m)) if x == UNKNOWN]
        cands = candidates(s, v, m)
        row: dict = {
            "rule_id": _rule_id(s, v, m),
            "s": s, "v": v, "m": m,
            "unknown_axes": unknown_axes,
            "grade_candidates": cands,
            "needs_review": len(cands) > 1,
        }
        if unknown_axes:
            row["authority"] = "UNAPPROVED"
            row["grade"] = None
            row["note"] = (
                f"{'·'.join(unknown_axes)} 축이 미확인 — 승인된 답이 없다. "
                f"후보 {cands} 중 사람이 정한다."
            )
        else:
            si, vi, mi = int(s), int(v), int(m)
            modes = {mode: grade_from_svm(si, vi, mi, mode=mode)
                     for mode in ("v22", "guide", "fnr")}
            row["code_v22"] = modes["v22"]
            row["code_guide"] = modes["guide"]
            row["code_fnr"] = modes["fnr"]
            row["modes_agree"] = len(set(modes.values())) == 1
            a = anchors.get((si, vi, mi))
            if a:
                row["authority"] = "guide_p12"
                row["grade"] = a["grade"]
                row["anchor_doc_type"] = a.get("doc_type")
                if a.get("guide_p12_grade") and a["guide_p12_grade"] != a["grade"]:
                    row["guide_original_grade"] = a["guide_p12_grade"]
                    row["note"] = "⚠ 가이드 원본과 다르다(v2.2 보정) — 발주처 확인 필요"
                elif a.get("note"):
                    row["note"] = a["note"]
            else:
                row["authority"] = "v22_code_only"
                row["grade"] = None
                row["note"] = (
                    "외부 승인 없음 — 코드가 답을 내지만 그것은 우리 구현이지 기준이 아니다."
                    + ("" if row["modes_agree"] else
                       f" 모드별로 갈린다(v22={modes['v22']}·guide={modes['guide']}·fnr={modes['fnr']}).")
                )
        rows.append(row)
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=None, help="결정표 JSONL 저장 경로")
    args = ap.parse_args()

    rows = build()
    auth = Counter(r["authority"] for r in rows)
    known = [r for r in rows if not r["unknown_axes"]]
    unk = [r for r in rows if r["unknown_axes"]]
    disagree = [r for r in known if not r.get("modes_agree", True)]
    review = [r for r in rows if r["needs_review"]]

    print("=" * 78)
    print(f"S·V·M 결정표 골격 — 전체 {len(rows)}칸 (축당 0·1·2·미확인)")
    print("=" * 78)
    print(f"  세 축 확정        {len(known):>3}칸")
    print(f"  미확인 포함       {len(unk):>3}칸   ← 승인된 답이 존재하지 않는다")
    print()
    print("승인 권위")
    for k in ("guide_p12", "v22_code_only", "UNAPPROVED"):
        print(f"  {k:<16} {auth.get(k, 0):>3}칸")
    print()
    print(f"검수 필요(후보 2개 이상)  {len(review):>3}칸")
    print(f"모드별로 답이 갈리는 칸    {len(disagree):>3}칸")
    for r in disagree:
        print(f"    {r['rule_id']}  v22={r['code_v22']} · guide={r['code_guide']} · fnr={r['code_fnr']}")

    print()
    print("외부 승인된 칸 — 이것만 기준으로 인용할 수 있다")
    for r in known:
        if r["authority"] == "guide_p12":
            warn = "  ⚠ " + r["note"] if r.get("guide_original_grade") else ""
            print(f"    {r['rule_id']}  → {r['grade']}   ({r.get('anchor_doc_type')}){warn}")

    print()
    print("=" * 78)
    print("무엇을 채워야 기준이 서는가")
    print("=" * 78)
    print(f"  · 세 축 확정 {len(known)}칸 중 외부 승인은 {auth.get('guide_p12', 0)}칸 "
          f"— 나머지 {auth.get('v22_code_only', 0)}칸은 코드 답만 있다")
    print(f"  · 미확인 {len(unk)}칸은 **승인 대상조차 아직 아니다** — "
          f"'보류'를 등급으로 표현할 그릇이 없다(Grade enum 은 TS·S1·S2·S3 4값)")
    print("  · 20 기준사례를 판정하면 이 표의 어느 칸이 채워지는지로 진척을 셀 수 있다")

    if args.out:
        out = POC / args.out
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                       encoding="utf-8")
        print(f"\n저장: {out.relative_to(POC)}  ({len(rows)}행)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
