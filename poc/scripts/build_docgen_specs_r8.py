#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 8차 명세 — '문체·양식 변형' 독립 시험 문서 120건 = 가족 30 × 4.

목적: 4·5·6·7차는 작성 모델을 바꿔도 같은 지시문 틀(완결된 업무 문서 문체, 같은 양식 목록)에서 나왔다. FS7 94% 가 그 틀에 익숙해진 값인지 가른다.
      8차는 사실 정의(S/V/M)와 라벨 규칙은 그대로 두고, ① 새 업무 유형(이전에 안 쓴 10개 × 15개 분야) ② 새 양식(메신저·수기 메모·음성 전사 등 비정형 30종)
      ③ 비정형 문체 지시(축약·주어 생략·약어·불규칙한 줄바꿈·핵심과 무관한 잡담 섞기·핵심 사실 밖 사소한 오타)로 쓴다. 작성 모델 opus·fable(각 3명), 판정 opus·fable.
      학습에 쓰지 않는다 — 학습된 FS·FS7 모델을 그대로 적용한다(scripts/eval_r8_style_shift.py, 사전 등록 PREREG_R8.md).
출력: reports/CLAUDE_DOCGEN_R8_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..05.json · AVOID_PHRASES.json · WRITER_MODELS_PRIVATE.json
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_docgen_specs_r4 import ACC, AVOID, GRADE_OF, SRC, TRAPS, v_numbers  # noqa: E402
from build_docgen_specs_r5 import DOMAINS  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R8_20260921"
SEED = 20261104
N_FAM = 30
WRITER_MODELS = ["opus", "fable", "opus", "fable", "opus", "fable"]
NEW_TASKS = ["예산 재배정 협의", "외주 검수 결과 확인", "규격 변경 통보 대응", "고객 클레임 대응 경과", "교육 결과 정리", "입찰 준비 점검", "보관 문서 정리 결과",
             "재고 실사 결과", "설비 이관 계획 협의", "감사 지적 조치 경과"]
FORMS = ["메신저 단체방 대화 발췌", "회의 중 급히 적은 손필기 메모를 옮긴 것", "이메일 회신 스레드(인용 포함)", "음성 메모를 자동 전사한 텍스트", "슬랙식 채널 스레드",
         "팀 위키 임시 메모", "현장 순회 수기 노트", "전화 통화 후 정리 메모", "구두 지시를 받아 적은 인수인계 메모", "발표 슬라이드 본문 텍스트",
         "스프레드시트 셀을 복사한 표 형태 텍스트", "고객센터 상담 로그", "주간 업무 일지", "작업 지시 티켓 댓글 모음", "출장 결과를 짧게 알리는 메신저 글",
         "이슈 회고 회의 발언 요약", "메일 여러 통을 이어 붙인 요약 다이제스트", "감사 준비 체크리스트 메모", "신입에게 보내는 인수 메모", "결재 상신 코멘트",
         "임원 보고 전 사전 조율 메일", "협력사와 주고받은 문의 메일 요약", "월말 마감 회의 노트", "장애 대응 타임라인 로그", "반려 사유 회신",
         "사내 지식 공유 툴의 질의응답 스레드", "교대 근무 인수인계 노트", "견적 검토 메모(수치 나열)", "설문·인터뷰 정리 메모", "실험 노트 요약"]
TS = [(2, 2, 2)] * N_FAM
S1 = [(2, 2, 1)] * 10 + [(2, 1, 2)] * 10 + [(1, 2, 2)] * 10
S2 = [(1, 1, 1)] * 6 + [(2, 1, 1)] * 8 + [(1, 2, 1)] * 8 + [(1, 1, 2)] * 8
S3 = ([(0, 1, 1)] * 3 + [(0, 2, 2)] * 3 + [(0, 2, 1)] * 2 + [(0, 1, 2)] * 2 + [(1, 0, 1)] * 2 + [(2, 0, 2)] * 3 + [(2, 0, 1)] * 2
      + [(2, 2, 0)] * 5 + [(2, 1, 0)] * 3 + [(1, 2, 0)] * 3 + [(1, 1, 0)] * 2)


def main() -> int:
    rng = random.Random(SEED)
    combos = [(d, t) for d in DOMAINS for t in NEW_TASKS]
    rng.shuffle(combos)
    combos = combos[:N_FAM]
    forms = FORMS[:]
    rng.shuffle(forms)
    forms = forms[:N_FAM]
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    assert all(len(p) == N_FAM for p in pools.values())
    for p in pools.values():
        rng.shuffle(p)
    specs, keymap = [], {}
    for i, ((domain, task), form) in enumerate(zip(combos, forms)):
        fam = f"S{i + 1:02d}"
        items, vn_by_level = [], {}
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert GRADE_OF[s * v * m] == g
            vn = vn_by_level.setdefault(v, v_numbers(v, rng))
            items.append({"doc_key": f"{fam}-{g}", "family_id": fam, "domain": domain, "form": form, "subject": f"{domain} — {task}(구체 상황·대상은 작성자가 고안)",
                          "S": s, "V": v, "M": m, "grade": g, "v_numbers": vn,
                          "implicit_axes": rng.sample(["S", "V", "M"], rng.choices([1, 2], weights=[0.5, 0.5])[0]),
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
    print(f"8차 명세 {len(specs)}건 · 가족 {len(fams)} · 등급 {dict(Counter(s['grade'] for s in specs))} · 양식 {len(set(s['form'] for s in specs))}종 · 작성 모델 {dict(Counter(fam_model.values()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
