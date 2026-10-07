#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 7차 명세 — 학습 증량 400건 = 경계 대조 가족 50 × 4 (Q) + 희귀 조합 강조 자유 가족 50 × 4 (P).

근거(9/22 측정): ① 학습량 곡선이 가파르다 — 학습 146→290→579건에서 개발·표현 시험 정확도 45.5→69.2→83.3%, 스텝 수를 맞춘 통제(FS50E20 73.7%)에서도 문서 수 효과 +9.6pt 가 남았다.
                 ② 작성 모델이 다른 6차 시험에서 오류가 V 축 조합에 몰렸다 — (2,0,2)[S3, 학습분 12건]을 S1 로, (1,2,1)[S2]을 S1 로, (2,2,0)을 잘못 읽는다.
                 ③ 개발·표현 시험의 4차 시험 가족(경계 대조) 정확도가 67~77% 로 가장 낮다.
설계: Q = 4차식 대조 가족(A형 TS/S1 25개 · B형 S1/S2/S3 25개, 한 축만 다른 문서 쌍) · P = 5차식 자유 가족(등급별 1건씩, 조합 풀을 희귀·오류 조합 쪽으로 기울임).
      분야×업무 조합은 5·6차가 쓰지 않은 것에서 100개. 작성 모델은 opus·sonnet 을 배치마다 번갈아(각 10명), 6차의 fable 작성 문서는 '독립 작성 모델 시험'으로 남긴다(7차에 fable 을 쓰지 않는다).
      전부 학습 후보(가족 단위)이며 시험·봉인에 쓰지 않는다. 판정은 opus·fable(작성 모델과 다른 계열 포함).
출력: reports/CLAUDE_DOCGEN_R7_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..19.json(00~09 = Q, 10~19 = P) · AVOID_PHRASES.json · WRITER_MODELS_PRIVATE.json
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
from build_docgen_specs_r4 import ACC, AVOID, FORMS as F4, GRADE_OF, SRC, TRAPS, make_family, v_numbers  # noqa: E402
from build_docgen_specs_r5 import DOMAINS, TASKS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R7_20260921"
SEED = 20261102
N_Q, N_P = 50, 50
BATCH_MODELS = ["opus", "sonnet"] * 10          # 배치 00..19 의 작성 모델
TS = [(2, 2, 2)] * N_P
S1 = [(2, 1, 2)] * 17 + [(2, 2, 1)] * 17 + [(1, 2, 2)] * 16
S2 = [(1, 2, 1)] * 20 + [(1, 1, 2)] * 12 + [(2, 1, 1)] * 12 + [(1, 1, 1)] * 6
S3 = ([(2, 0, 2)] * 10 + [(2, 0, 1)] * 6 + [(0, 1, 2)] * 6 + [(0, 2, 1)] * 5 + [(1, 0, 1)] * 3 + [(2, 1, 0)] * 5 + [(1, 1, 0)] * 4 + [(1, 2, 0)] * 4
      + [(2, 2, 0)] * 5 + [(0, 2, 2)] * 1 + [(0, 1, 1)] * 1)
KEYS = ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "implicit_axes", "trap", "length_chars")


def used_combos() -> set:
    used = set()
    for r in ("CLAUDE_DOCGEN_R5_20260921", "CLAUDE_DOCGEN_R6_20260921"):
        for s in json.loads((POC / "reports" / r / "specs_pilot.json").read_text(encoding="utf-8")):
            m = re.match(r"^(.*?) — (.*?)\(", s["subject"])
            if m:
                used.add((m.group(1), m.group(2)))
    return used


def main() -> int:
    assert all(len(p) == N_P for p in (TS, S1, S2, S3)), [len(p) for p in (TS, S1, S2, S3)]
    rng = random.Random(SEED)
    used = used_combos()
    combos = [(d, t) for d in DOMAINS for t in TASKS if (d, t) not in used]
    assert len(combos) >= N_Q + N_P, len(combos)
    rng.shuffle(combos)
    forms = list(dict.fromkeys(F3 + F4))
    rng.shuffle(forms)
    forms = (forms * 3)[:N_Q + N_P]
    specs, keymap = [], {}
    kinds = ["A"] * 25 + ["B"] * 25
    rng.shuffle(kinds)
    # Q — 경계 대조 가족
    for i in range(N_Q):
        fam = f"Q{i + 1:02d}"
        domain, task = combos[i]
        subject = f"{task}({domain} 분야, 구체 상황·대상은 작성자가 고안하되 같은 가족의 4건은 같은 상황)"
        items = make_family(fam, kinds[i], domain, subject, forms[i], rng)
        rng.shuffle(items)
        for j, it in enumerate(items):
            it["agent_key"] = f"{fam}-{j + 1}"
            it["kind"] = "contrast"
            keymap[it["agent_key"]] = it["doc_key"]
            specs.append(it)
    # P — 희귀 조합 강조 자유 가족
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    for p in pools.values():
        rng.shuffle(p)
    for i in range(N_P):
        fam = f"P{i + 1:02d}"
        domain, task = combos[N_Q + i]
        items, vn_by_level = [], {}
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert GRADE_OF[s * v * m] == g
            vn = vn_by_level.setdefault(v, v_numbers(v, rng))
            items.append({"doc_key": f"{fam}-{g}", "family_id": fam, "family_type": "free", "kind": "free", "domain": domain, "form": forms[N_Q + i],
                          "subject": f"{domain} — {task}(구체 상황·대상은 작성자가 고안)", "S": s, "V": v, "M": m, "grade": g, "v_numbers": vn,
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
    fam_model, batches = {}, []
    qf = [f"Q{i + 1:02d}" for i in range(N_Q)]
    pf = [f"P{i + 1:02d}" for i in range(N_P)]
    for b in range(20):
        fams = qf[b * 5:(b + 1) * 5] if b < 10 else pf[(b - 10) * 5:(b - 9) * 5]
        for f in fams:
            fam_model[f] = BATCH_MODELS[b]
        keys = KEYS + (("delta_axis",) if b < 10 else ())
        chunk = [{k: s[k] for k in keys} for s in specs if s["family_id"] in fams]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
        batches.append(len(chunk))
    (OUT / "WRITER_MODELS_PRIVATE.json").write_text(json.dumps({"batch_models": BATCH_MODELS, "family_model": fam_model}, ensure_ascii=False, indent=1), encoding="utf-8")
    combo_cnt = Counter((s["S"], s["V"], s["M"]) for s in specs)
    print(f"7차 명세 {len(specs)}건 · 가족 {len(fam_model)}(Q {N_Q}·P {N_P}) · 등급 {dict(Counter(s['grade'] for s in specs))} · 배치 크기 {sorted(set(batches))} · 5·6차 조합 {len(used)}개 제외")
    print("조합 건수:", {k: combo_cnt[k] for k in sorted(combo_cnt)})
    return 0


if __name__ == "__main__":
    sys.exit(main())
