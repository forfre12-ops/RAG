#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 9차 명세 — 8차가 드러낸 오류 자리를 겨냥한 학습 증량 160건 = 경계 대조 가족 40 × 4.

근거(9/22 8차 결과, r8_style_shift_result.txt): FS7 의 8차(문체·양식 변형 시험) 오류는 TS→S1 8건·S1→S2 8건이 대부분이다.
설계: 4·7차식 경계 대조 가족(A형 TS/S1 20개·B형 S1/S2/S3 20개)을 8차의 **비정형 문체**(메신저·손필기 메모·음성 전사 등)로 쓴다.
      업무 조합은 8차가 시험에 쓴 30개와 겹치지 않는 나머지 120개 중에서 고른다(재평가 시 8차 114건 시험셋 오염 방지).
작성 모델 opus·fable(8차와 같은 계열, 각 4명) — 판정도 opus·fable. 전부 학습 후보(시험·봉인에 안 씀).
출력: reports/CLAUDE_DOCGEN_R9_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..07.json · AVOID_PHRASES.json · WRITER_MODELS_PRIVATE.json
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_docgen_specs_r4 import AVOID, make_family  # noqa: E402
from build_docgen_specs_r5 import DOMAINS  # noqa: E402
from build_docgen_specs_r8 import FORMS as R8_FORMS, NEW_TASKS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R9_20260921"
R8 = POC / "reports" / "CLAUDE_DOCGEN_R8_20260921"
SEED = 20261105
N_FAM = 40
WRITER_MODELS = ["opus", "fable"] * 4


def r8_used_combos() -> set:
    specs = json.loads((R8 / "specs_pilot.json").read_text(encoding="utf-8"))
    return {(s["domain"], s["subject"].split(" — ")[1].split("(")[0]) for s in specs}


def main() -> int:
    used = r8_used_combos()
    all_combos = [(d, t) for d in DOMAINS for t in NEW_TASKS]
    combos = [c for c in all_combos if c not in used]
    assert len(combos) >= N_FAM, (len(combos), len(used), len(all_combos))
    rng = random.Random(SEED)
    rng.shuffle(combos)
    combos = combos[:N_FAM]
    forms = (R8_FORMS * 2)[:N_FAM]
    rng.shuffle(forms)
    kinds = ["A"] * 20 + ["B"] * 20
    rng.shuffle(kinds)
    specs, keymap = [], {}
    for i, ((domain, task), form, kind) in enumerate(zip(combos, forms, kinds)):
        fam = f"N{i + 1:02d}"
        items = make_family(fam, kind, domain, f"{task}(구체 상황·대상은 작성자가 고안)", form, rng)
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
    for b in range(8):
        fams_b = fams[b * 5:(b + 1) * 5]
        for f in fams_b:
            fam_model[f] = WRITER_MODELS[b]
        chunk = [{k: s[k] for k in ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "delta_axis", "implicit_axes", "trap", "length_chars")}
                 for s in specs if s["family_id"] in fams_b]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "WRITER_MODELS_PRIVATE.json").write_text(json.dumps({"batch_models": WRITER_MODELS, "family_model": fam_model}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"9차 명세 {len(specs)}건 · 가족 {len(fams)}(A형 {kinds.count('A')}·B형 {kinds.count('B')}) · 등급 {dict(Counter(s['grade'] for s in specs))} · 양식 {len(set(s['form'] for s in specs))}종 · 8차 조합 {len(used)}개 제외")
    return 0


if __name__ == "__main__":
    sys.exit(main())
