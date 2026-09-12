# -*- coding: utf-8 -*-
"""룰 판정식이 최종 등급에 무엇을 하는지 잰다 — 분기 전수열거 + 평가셋 4종 실측.

왜 필요한가(2026-08-26). 백서는 "3요건(S·V·M)을 각 0·1·2로 독립 판독하고 곱으로 등급을
산정한다"고 쓴다. 그런데 rule_engine.label() 의 s_lv·v_lv·m_lv 는 content_grade(키워드
argmax 등급)에서 역산되고, 그 뒤 FNR-safe min-rank 가 곱셈 결과를 다시 덮는다. 그러면
곱셈 단계가 최종 등급을 실제로 바꾸는 경우가 남아 있는지를 세어야 서술을 고칠 수 있다.

측정 항목:
    ① 분기 전수열거   content×public×has_mgmt 16조합에서 최종등급이 바뀌는 조합 수
    ② 실측 발동 횟수   평가셋에서 곱셈 단계가 등급을 실제로 바꾼 문서 수
    ③ 등급 정확도     셋별 등급일치·미탐(낮게봄)·과분류(높게봄)·무매칭·실근거 비율

⚠ 이 스크립트는 판정하지 않는다. 시드·패턴·게이트를 바꾸기 전후로 같은 명령을 돌려
비교하는 용도다. `measure_rule_extractor.py`(요소 S/V/M 정확도)와 짝이다.

사용:
    TESTING=1 python scripts/audit_rule_formula.py
"""
from __future__ import annotations

import collections
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

GRADES = ("TS", "S1", "S2", "S3")
RANK = {g: i for i, g in enumerate(GRADES)}

_SVM_WARN = re.compile(
    r"svm=\d+\((?P<svm>\w+)\).*content\((?P<content>\w+)\).*FNR-safe\s+(?P<final>\w+)"
)

EVAL_SETS = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "clean42": "datasets/gold_real/holdout_eval.clean.jsonl",
    "holdout109": "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    "v3_final800": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl",
}


def enumerate_branches() -> list[tuple]:
    """label() 의 SVM 블록을 분기 전수열거해 최종 등급이 바뀌는 조합만 반환."""
    from koipa.modules.m3_labeling.rule_engine import grade_from_svm

    changed = []
    for content in GRADES:
        for public in (False, True):
            for has_mgmt in (False, True):
                strong = content in ("TS", "S1")
                s = 0 if (public or content == "S3") else (2 if strong else 1)
                v = 2 if strong else (0 if content == "S3" else 1)
                m = 2 if has_mgmt else (0 if content in ("S3", "S1") else 1)
                svm_g = grade_from_svm(s, v, m)
                final = min([svm_g, content], key=lambda g: RANK[g])
                if final != content:
                    changed.append((content, public, has_mgmt, (s, v, m), svm_g, final))
    return changed


def audit_set(engine, rows: list[dict]) -> dict:
    """셋 하나를 훑어 등급 정확도 + 곱셈단계 발동 횟수를 센다."""
    from koipa.modules.m3_labeling.rule_engine import has_real_evidence

    conf = collections.Counter()
    stat = collections.Counter()
    for r in rows:
        text = r.get("text") or ""
        truth = r.get("label")
        res = engine.label(text)
        conf[(truth, res.grade)] += 1
        if truth == res.grade:
            stat["hit"] += 1
        elif RANK.get(res.grade, 9) > RANK.get(truth, 9):
            stat["miss"] += 1        # 정답보다 낮게 = 미탐 방향
        else:
            stat["over"] += 1        # 정답보다 높게 = 과분류 방향
        if not res.matched_keywords:
            stat["nomatch"] += 1
        if has_real_evidence(res):
            stat["evidence"] += 1
        # svm 경고 문구가 곱셈등급·콘텐츠등급·최종등급을 그대로 담는다:
        #   "svm=8(TS)<->content(S1) -> FNR-safe TS"
        # 경고 발생(곱셈 != 콘텐츠)과 최종등급 실제 변경(FNR-safe 결과 != 콘텐츠)을 갈라 센다.
        for w in res.warnings:
            m = _SVM_WARN.search(w)
            if not m:
                continue
            stat["svm_warned"] += 1
            if m.group("final") != m.group("content"):
                stat["svm_changed"] += 1
    keys = ("hit", "miss", "over", "nomatch", "evidence", "svm_warned", "svm_changed")
    return {"n": len(rows), "conf": conf, **{k: stat.get(k, 0) for k in keys}}


def main(argv: list[str] | None = None) -> int:
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    print("① 분기 전수열거 - content×public×has_mgmt 16조합")
    changed = enumerate_branches()
    for content, public, mgmt, svm, svm_g, final in changed:
        print(f"   content={content} public={public} mgmt={mgmt} → s,v,m={svm} → {svm_g} → 최종 {final}")
    print(f"   최종 등급이 바뀌는 조합: {len(changed)}/16\n")

    engine = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    print("② ③ 평가셋 실측")
    print(f"   {'셋':<14}{'N':>5}{'일치':>9}{'미탐':>7}{'과분류':>8}{'무매칭':>8}{'실근거':>8}{'곱셈경고':>9}{'등급변경':>9}")
    for name, rel in EVAL_SETS.items():
        path = _ROOT / rel
        if not path.exists():
            print(f"   {name:<14} (없음: {rel})")
            continue
        rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
        a = audit_set(engine, rows)
        n = a["n"]
        print(f"   {name:<14}{n:>5}{a['hit']/n:>8.1%}{a['miss']:>7}{a['over']:>8}"
              f"{a['nomatch']:>8}{a['evidence']:>8}{a['svm_warned']:>9}{a['svm_changed']:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
