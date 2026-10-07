#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 5차 명세 — 마감용 보충 280건 = 가족 70 × 4 (R3 방식: 표현 자유·1~4차 반복 어휘 금지).

최종 모의문서 1,000건을 채우기 위한 물량 보충. 주제는 (분야 × 업무 유형) 조합에서 무작위로 고르고 구체 상황은 작성자가 고안한다.
typed_facts·label_basis 를 모든 문서에 붙인다(정책 엔진 시험용). 출력: reports/CLAUDE_DOCGEN_R5_20260921/ (agent_batch_00..13)
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_docgen_specs_r3 import FORMS as F3  # noqa: E402
from build_docgen_specs_r4 import ACC, AVOID, FORMS as F4, GRADE_OF, SRC, TRAPS, v_numbers  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R5_20260921"
SEED = 20261001
DOMAINS = ["연구개발", "인사관리", "경영기획", "품질관리", "영업판매", "생산제조", "구매조달", "재무회계", "법무", "정보보안", "마케팅", "물류", "설비관리", "고객지원", "안전환경"]
TASKS = ["시험 결과 정리", "개정안 검토", "점검 결과 보고", "협의 경과 정리", "비용 분석", "일정 조정 협의", "대응 방안 수립", "성과 분석", "개선 제안", "사고·이슈 경과", "평가 결과 공유",
         "계약 조건 검토", "절차 변경 안내", "위험 검토", "인수인계 정리"]
TS = [(2, 2, 2)] * 70
S1 = [(2, 2, 1)] * 24 + [(2, 1, 2)] * 23 + [(1, 2, 2)] * 23
S2 = [(1, 1, 1)] * 16 + [(2, 1, 1)] * 18 + [(1, 2, 1)] * 18 + [(1, 1, 2)] * 18
S3 = ([(0, 1, 1)] * 7 + [(0, 2, 2)] * 7 + [(0, 2, 1)] * 5 + [(0, 1, 2)] * 5 + [(1, 0, 1)] * 5 + [(2, 0, 2)] * 7 + [(2, 0, 1)] * 5
      + [(2, 2, 0)] * 10 + [(2, 1, 0)] * 7 + [(1, 2, 0)] * 7 + [(1, 1, 0)] * 5)


def main() -> int:
    rng = random.Random(SEED)
    combos = [(d, t) for d in DOMAINS for t in TASKS]
    rng.shuffle(combos)
    combos = combos[:70]
    forms = list(dict.fromkeys(F3 + F4))
    rng.shuffle(forms)
    forms = (forms * 2)[:70]
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    for p in pools.values():
        rng.shuffle(p)
    specs, keymap = [], {}
    for i, ((domain, task), form) in enumerate(zip(combos, forms)):
        fam = f"K{i + 1:02d}"
        items = []
        vn_by_level: dict[int, dict] = {}
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert GRADE_OF[s * v * m] == g
            if v not in vn_by_level:
                vn_by_level[v] = v_numbers(v, rng)
            vn = vn_by_level[v]
            items.append({"doc_key": f"{fam}-{g}", "family_id": fam, "domain": domain, "form": form, "subject": f"{domain} — {task}(구체 상황·대상은 작성자가 고안)",
                          "S": s, "V": v, "M": m, "grade": g, "v_numbers": vn,
                          "implicit_axes": rng.sample(["S", "V", "M"], rng.choices([0, 1, 2], weights=[0.4, 0.4, 0.2])[0]),
                          "trap": rng.choice(TRAPS) if rng.random() < 0.4 else None, "length_chars": rng.choice([480, 560, 640, 720, 800, 900]),
                          "label_basis": f"S×V×M = {s}×{v}×{m} = {s * v * m} → {g} (가이드 12쪽 곱셈표)",
                          "typed_facts": {"source_type": SRC[s], "access_scope": ACC[m],
                                          "value_evidence": {"attributed_cost_man_won": vn["cost_man_won"], "attributed_hours": vn["hours"], "economic_use": v > 0}}})
        rng.shuffle(items)
        for j, it in enumerate(items):
            it["agent_key"] = f"{fam}-{j + 1}"
            keymap[it["agent_key"]] = it["doc_key"]
            specs.append(it)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "specs_pilot.json").write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "KEY_MAP_PRIVATE.json").write_text(json.dumps(keymap, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "AVOID_PHRASES.json").write_text(json.dumps(AVOID, ensure_ascii=False), encoding="utf-8")
    fams = sorted({s["family_id"] for s in specs})
    for b in range(14):
        chunk = [{k: s[k] for k in ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "implicit_axes", "trap", "length_chars")}
                 for s in specs if s["family_id"] in fams[b * 5:(b + 1) * 5]]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"5차 명세 {len(specs)}건 · 가족 {len(fams)} · 등급 {dict(Counter(s['grade'] for s in specs))} · 양식 {len(set(s['form'] for s in specs))}종")
    return 0


if __name__ == "__main__":
    sys.exit(main())
