#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 6차 명세 — '작성 모델이 다른' 독립 시험 문서 120건 = 가족 30 × 4.

목적: 1~5차는 모두 같은 모델(Sonnet)·같은 지시문 계열이 썼다. 4·5차 학습 문서와 3차 시험 문서가 같은 작성 방식이라
      개발·표현 시험(154건) 정확도 83% 가 '사실을 읽는 능력'인지 '작성 습관'인지 가르지 못한다.
      6차는 5차와 같은 지시문·같은 사실 정의로 **작성 모델만** 바꿔(haiku·opus·fable) 쓰게 하고, 판정도 다른 모델(opus·fable)이 한다.
      학습에 쓰지 않는다 — 학습된 FS/FSO 모델을 그대로 이 문서에 적용해 작성 모델별 정확도를 잰다(scripts/eval_r6_cross_generator.py).
구성: (분야 × 업무 유형) 조합 중 5차가 쓰지 않은 것에서 30개 · 가족당 TS/S1/S2/S3 각 1건 · 가족 5개(20건)를 작성자 1명이 쓴다 → 작성자 6명(모델 3종 × 2).
출력: reports/CLAUDE_DOCGEN_R6_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..05.json · AVOID_PHRASES.json · WRITER_MODELS_PRIVATE.json
"""
from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_docgen_specs_r3 import FORMS as F3  # noqa: E402
from build_docgen_specs_r4 import ACC, AVOID, FORMS as F4, GRADE_OF, SRC, TRAPS, v_numbers  # noqa: E402
from build_docgen_specs_r5 import DOMAINS, TASKS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R6_20260921"
R5 = POC / "reports" / "CLAUDE_DOCGEN_R5_20260921"
SEED = 20261101
N_FAM = 30
WRITER_MODELS = ["haiku", "opus", "fable", "haiku", "opus", "fable"]      # 배치 00..05 를 쓰는 작성 모델
TS = [(2, 2, 2)] * N_FAM
S1 = [(2, 2, 1)] * 10 + [(2, 1, 2)] * 10 + [(1, 2, 2)] * 10
S2 = [(1, 1, 1)] * 6 + [(2, 1, 1)] * 8 + [(1, 2, 1)] * 8 + [(1, 1, 2)] * 8
S3 = ([(0, 1, 1)] * 3 + [(0, 2, 2)] * 3 + [(0, 2, 1)] * 2 + [(0, 1, 2)] * 2 + [(1, 0, 1)] * 2 + [(2, 0, 2)] * 3 + [(2, 0, 1)] * 2
      + [(2, 2, 0)] * 5 + [(2, 1, 0)] * 3 + [(1, 2, 0)] * 3 + [(1, 1, 0)] * 2)


def main() -> int:
    used = set()
    for s in json.loads((R5 / "specs_pilot.json").read_text(encoding="utf-8")):
        m = re.match(r"^(.*?) — (.*?)\(", s["subject"])
        if m:
            used.add((m.group(1), m.group(2)))
    rng = random.Random(SEED)
    combos = [(d, t) for d in DOMAINS for t in TASKS if (d, t) not in used]
    assert len(combos) >= N_FAM
    rng.shuffle(combos)
    combos = combos[:N_FAM]
    forms = list(dict.fromkeys(F3 + F4))
    rng.shuffle(forms)
    forms = (forms * 2)[:N_FAM]
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    assert all(len(p) == N_FAM for p in pools.values()), {k: len(v) for k, v in pools.items()}
    for p in pools.values():
        rng.shuffle(p)
    specs, keymap = [], {}
    for i, ((domain, task), form) in enumerate(zip(combos, forms)):
        fam = f"L{i + 1:02d}"
        items, vn_by_level = [], {}
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert GRADE_OF[s * v * m] == g
            vn = vn_by_level.setdefault(v, v_numbers(v, rng))
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
    fam_model = {}
    for b in range(6):
        for f in fams[b * 5:(b + 1) * 5]:
            fam_model[f] = WRITER_MODELS[b]
        chunk = [{k: s[k] for k in ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "implicit_axes", "trap", "length_chars")}
                 for s in specs if s["family_id"] in fams[b * 5:(b + 1) * 5]]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "WRITER_MODELS_PRIVATE.json").write_text(json.dumps({"batch_models": WRITER_MODELS, "family_model": fam_model}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"6차 명세 {len(specs)}건 · 가족 {len(fams)} · 등급 {dict(Counter(s['grade'] for s in specs))} · 양식 {len(set(s['form'] for s in specs))}종 · 5차 조합 {len(used)}개 제외 · 작성 모델 {dict(Counter(fam_model.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
