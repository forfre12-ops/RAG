#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 4차 명세 — 경계 대조쌍(한 축만 다른 가족) 160건 = 가족 40 × 4.

지금 오답이 몰린 자리를 겨냥한다: 3차 시험에서 특급(TS)→1급(S1) 한 단계 아래 오답이 78/145, 1급→2급 31/140.
가족 유형 두 가지(각 20개):
  A형 TS/S1 경계 — 기준 TS(2,2,2) 1건 + 한 축만 한 단계 낮춘 S1 세 건: (1,2,2)·(2,1,2)·(2,2,1)
  B형 S1/S2/S3 경계 — 기준 S1 1건 + 한 축만 한 단계 낮춰 S2 두 건 + 한 축을 0 으로 낮춘 S3 한 건
      S1 기준 (2,2,1): S2 (1,2,1)·(2,1,1), S3 (2,2,0)  /  (2,1,2): S2 (1,1,2)·(2,1,1), S3 (2,0,2)  /  (1,2,2): S2 (1,1,2)·(1,2,1), S3 (0,2,2)
각 문서에 typed_facts(정책 엔진용 확정 사실 값)·label_basis(곱셈표 계산식)·delta_axis(기준 대비 달라진 축)를 붙인다. 등급은 작성자에게 주지 않는다.
typed_facts 매핑(가정): S=0 → source_type 'public' · S=1 → 'external_confidential' · S=2 → 'internal' / M=0 → access_scope 'all_employees' · M=1 → 'department' · M=2 → 'approved_only'
                         / V → value_evidence{attributed_cost_man_won, attributed_hours, economic_use}. source_type·access_scope 는 ICD 계약 필드, value_evidence 는 새 필드(policy_facts.FACT_TYPES 에 없음).
가족 40개 중 10개는 '4차 시험셋'(학습에 안 씀), 30개는 학습 후보.
출력: reports/CLAUDE_DOCGEN_R4_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..07.json · AVOID_PHRASES.json
"""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass
POC = Path(__file__).resolve().parents[1]
OUT = POC / "reports" / "CLAUDE_DOCGEN_R4_20260921"
SEED = 20260930
GRADE_OF = {0: "S3", 1: "S2", 2: "S2", 4: "S1", 8: "TS"}

SUBJECTS = [
    ("연구개발", "고체 전해질 계면 저항 저감 시험"), ("연구개발", "음성 인식 모델 경량화 결과"), ("연구개발", "바이오 센서 교정 절차 개선"), ("연구개발", "3D 프린팅 소재 수축률 보정"),
    ("인사관리", "핵심 인재 잔류 지원 방안"), ("인사관리", "평가 결과 이의 신청 처리"), ("인사관리", "전환 배치 대상자 면담 결과"), ("인사관리", "복리후생 제도 개편안"),
    ("경영기획", "신규 시장 진입 재무 시나리오"), ("경영기획", "협력 투자 지분 구조 검토"), ("경영기획", "본사 이전 비용 분석"), ("경영기획", "사업부 통합 효과 추정"),
    ("품질관리", "고객 반품 원인 재분석"), ("품질관리", "협력사 공정 감사 결과"), ("품질관리", "시험 장비 오차 관리 기준"), ("품질관리", "초도품 승인 판정 기록"),
    ("영업판매", "장기 계약 갱신 조건 협상"), ("영업판매", "지역 유통 파트너 재편"), ("영업판매", "견적 원가 산정 근거"), ("영업판매", "프로모션 성과 분석"),
    ("생산제조", "설비 성능 시운전 결과"), ("생산제조", "공정 온도 설정값 조정"), ("생산제조", "원료 배합 비율 변경 시험"), ("생산제조", "작업 표준 개정 초안"),
    ("구매조달", "핵심 소재 공급 안정화 방안"), ("구매조달", "단가 협상 준비 자료"), ("구매조달", "긴급 조달 처리 경과"), ("구매조달", "대체 공급사 평가 점수"),
    ("재무회계", "자산 손상 인식 검토"), ("재무회계", "환율 변동 대응 방안"), ("재무회계", "원가 배부 기준 개정"), ("재무회계", "세무 조사 소명 자료"),
    ("법무", "라이선스 분쟁 대응 전략"), ("법무", "공동 개발 계약 조항 검토"), ("법무", "정보 유출 의혹 조사 결과"), ("법무", "규제 준수 점검 결과"),
    ("정보보안", "침해 사고 원인 분석"), ("정보보안", "접속 통제 정책 개정"), ("마케팅", "가격 실험 결과 요약"), ("물류", "거점 재배치 시뮬레이션 결과"),
]
FORMS = ["고객 불만 접수 기록", "사내 Q&A 답변", "결과 공유 이메일", "기술 검토 회의 메모", "이슈 트래커 본문", "구두 보고 메모", "인수 후보 검토 노트", "사후 회고 문서", "협상 전 준비 메모", "감사 인터뷰 기록",
         "제휴 제안 회신", "특별 승인 요청서", "월간 운영 요약", "표준 절차 개정 초안", "위험 등록부 항목", "예산 편성 근거 메모", "신규 입사자 안내", "연구 노트", "정례 회의 안건", "현장 확인 결과 보고"]
AVOID = ["명단", "개인별", "역할 기준", "게시판", "전 직원이", "상당한 노력", "열람 신청서", "승인권자의 결재", "결재를 받아야", "격리 서버", "보안 구역", "지정된 열람자", "서약서", "전자공시", "학회지", "보도자료",
         "공개된 특허공보", "홈페이지에 올라", "컨소시엄 회원사", "유료 회원제", "공동 개발 파트너", "역설계", "구두 노하우", "억 단위", "소액", "사내 포털 첫 화면", "팀 공유 폴더", "직무 권한 그룹",
         "링크만 있으면", "외부로 반출"]
TRAPS = ["문서 어딘가에 '목록'이나 '리스트'라는 낱말이 나오되 이 문서의 접근 통제와는 무관한 대상에 쓰이게 한다",
         "'공개'라는 낱말이 나오되 핵심 정보의 공개 여부와는 다른 대상(모집 공고, 일정 안내 등)에 쓰이게 한다",
         "'전체 공유'나 '게시'라는 낱말이 나오되 핵심 정보 자체의 열람 범위와는 무관한 안내에 쓰이게 한다",
         "큰 금액(계약 총액, 설비 가격, 프로젝트 예산)을 함께 적되 이 정보에 귀속된 비용이 아님을 문맥으로 구분한다"]
S1_BASES = {(2, 2, 1): {"S2": [(1, 2, 1), (2, 1, 1)], "S3": [(2, 2, 0)]}, (2, 1, 2): {"S2": [(1, 1, 2), (2, 1, 1)], "S3": [(2, 0, 2)]}, (1, 2, 2): {"S2": [(1, 1, 2), (1, 2, 1)], "S3": [(0, 2, 2)]}}
SRC = {0: "public", 1: "external_confidential", 2: "internal"}
ACC = {0: "all_employees", 1: "department", 2: "approved_only"}     # ICD §3.3 허용값(rule_engine._ICD_SCOPE_TO_M): approved_only=M2 · designated/department=M1 · all_employees=M0


def v_numbers(v: int, rng: random.Random) -> dict:
    if v == 0:
        return {"cost_man_won": 0, "hours": 0, "note": "이 정보에 귀속된 비용·인시는 없거나 0이고 경제적 활용 가치가 없음"}
    if v == 1:
        cost, hours = rng.choice([(rng.randint(10, 100), rng.randint(4, 40)), (rng.randint(10, 100), 0), (0, rng.randint(4, 40))])
        return {"cost_man_won": cost, "hours": hours, "note": "귀속 비용 100만원 이하 그리고 투입 40인시 이하(둘 중 하나 이상은 0보다 큼)"}
    kind = rng.choice(["cost", "hours", "both"])
    return {"cost_man_won": rng.randint(3000, 40000) if kind in ("cost", "both") else rng.randint(200, 2500),
            "hours": rng.randint(480, 6000) if kind in ("hours", "both") else rng.randint(30, 400), "note": "귀속 비용 3,000만원 이상이거나 투입 480인시 이상(둘 다여도 됨)"}


def make_family(fam: str, kind: str, domain: str, subject: str, form: str, rng: random.Random) -> list[dict]:
    if kind == "A":
        base = (2, 2, 2)
        combos = [("base", base), ("S-1", (1, 2, 2)), ("V-1", (2, 1, 2)), ("M-1", (2, 2, 1))]
    else:
        b = rng.choice(list(S1_BASES))
        combos = [("base", b)] + [("lower1", c) for c in S1_BASES[b]["S2"]] + [("lower0", S1_BASES[b]["S3"][0])]
    facts_v = {}                      # 같은 가족 안에서 같은 V 수준이면 같은 숫자를 쓴다(대조가 사실 수준 차이만 되도록)
    items = []
    for role, (s, v, m) in combos:
        if v not in facts_v:
            facts_v[v] = v_numbers(v, rng)
        vn = facts_v[v]
        g = GRADE_OF[s * v * m]
        base = combos[0][1]
        delta = [ax for ax, (a, b_) in zip("SVM", zip((s, v, m), base)) if a != b_]
        items.append({"doc_key": f"{fam}-{g}-{len(items)}", "family_id": fam, "family_type": kind, "domain": domain, "form": form, "subject": subject, "role": role, "S": s, "V": v, "M": m, "grade": g,
                      "delta_axis": delta, "v_numbers": vn, "implicit_axes": rng.sample(["S", "V", "M"], rng.choices([0, 1], weights=[0.5, 0.5])[0]),
                      "trap": rng.choice(TRAPS) if rng.random() < 0.35 else None, "length_chars": rng.choice([560, 640, 720, 800]),
                      "label_basis": f"S×V×M = {s}×{v}×{m} = {s * v * m} → {g} (가이드 12쪽 곱셈표)",
                      "typed_facts": {"source_type": SRC[s], "access_scope": ACC[m],
                                      "value_evidence": {"attributed_cost_man_won": vn["cost_man_won"], "attributed_hours": vn["hours"], "economic_use": v > 0}}})
    return items


def main() -> int:
    rng = random.Random(SEED)
    slots = SUBJECTS[:]
    rng.shuffle(slots)
    forms = FORMS[:]
    rng.shuffle(forms)
    forms = (forms * 2)[:40]
    kinds = ["A"] * 20 + ["B"] * 20
    rng.shuffle(kinds)
    fam_ids = [f"J{i + 1:02d}" for i in range(40)]
    test_fams = set(rng.sample(fam_ids, 10))
    specs, keymap = [], {}
    for fam, kind, (domain, subject), form in zip(fam_ids, kinds, slots, forms):
        items = make_family(fam, kind, domain, subject, form, rng)
        rng.shuffle(items)
        for j, it in enumerate(items):
            it["agent_key"] = f"{fam}-{j + 1}"
            it["split"] = "test4" if fam in test_fams else "train_candidate"
            keymap[it["agent_key"]] = it["doc_key"]
            specs.append(it)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "specs_pilot.json").write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "KEY_MAP_PRIVATE.json").write_text(json.dumps(keymap, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "AVOID_PHRASES.json").write_text(json.dumps(AVOID, ensure_ascii=False), encoding="utf-8")
    fams = sorted({s["family_id"] for s in specs})
    for b in range(8):
        chunk = [{k: s[k] for k in ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "delta_axis", "implicit_axes", "trap", "length_chars")}
                 for s in specs if s["family_id"] in fams[b * 5:(b + 1) * 5]]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"4차 명세 {len(specs)}건 · 가족 {len(fams)}(A형 {kinds.count('A')}·B형 {kinds.count('B')}) · 등급 {dict(Counter(s['grade'] for s in specs))} · 시험 가족 {len(test_fams)}개({sum(1 for s in specs if s['split'] == 'test4')}건)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
