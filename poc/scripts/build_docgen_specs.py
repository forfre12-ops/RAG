#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 명세 — 파일럿 240건(가족 60개 × 등급 4개).

방침: 등급을 정하는 사실(S 공개·취득 경로 / V 정보에 귀속된 비용·인시 / M 접근 범위)을 먼저 고정하고, 본문에 그 사실을 자연스럽게 넣는다.
      라벨은 사람이나 AI 의견이 아니라 가이드 12쪽 곱셈표(S×V×M: 0→S3 · 1·2→S2 · 4→S1 · 8→TS)로 계산한다. 현재 1,000건의 잠정 라벨과 같은 규칙이다.
      (guide 곱셈표와 운영 v22 보정은 (1,2,2)(2,1,2)(2,2,1)(2,2,0)에서 갈린다 — 정책 확정은 발주처 결정 D01~D08 이며 이 파일럿은 가이드 곱셈표를 쓴다.)
가족(document_family_id): 같은 주제·양식의 4개 변형(등급마다 하나). 분할은 가족 단위로 한다.
출력: reports/CLAUDE_DOCGEN_20260921/  specs_pilot.json · batch_XX_specs.json(12개, 배치당 가족 5개 = 문서 20건)
사용:  python scripts/build_docgen_specs.py
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
OUT = POC / "reports" / "CLAUDE_DOCGEN_20260921"
SEED = 20260925

SUBJECTS = {
    "연구개발": ["신규 전해질 첨가제 배합 검토", "고분자 필름 열처리 조건 조정", "센서 보정 알고리즘 개선", "경량 합금 접합 공정 시험", "효소 반응 수율 개선 실험", "반도체 세정 공정 파라미터 변경"],
    "인사관리": ["핵심 인력 보상 체계 개편", "신규 채용 면접 평가 기준 조정", "직무 재배치 계획", "성과 평가 등급 분포 검토", "사내 교육 과정 개편", "임원 승계 후보 검토"],
    "경영기획": ["해외 법인 설립 타당성 검토", "분기 사업계획 수정", "인수 후보 기업 검토", "신규 시장 진입 전략", "사업부 구조조정 시나리오", "중장기 투자 우선순위"],
    "품질관리": ["불량 원인 분석 결과", "검사 기준 개정", "협력사 품질 감사 결과", "고객 클레임 재발 방지", "측정 장비 교정 관리", "공정 능력 지수 개선"],
    "영업판매": ["대형 고객 가격 협상 방안", "대리점 인센티브 개편", "신제품 출시 프로모션", "거래처 신용 한도 조정", "경쟁사 대응 제안서", "분기 판매 실적 분석"],
    "생산제조": ["라인 증설 일정 조정", "설비 예방 정비 계획", "수율 개선 활동", "원재료 투입 비율 변경", "야간 근무 편성", "재고 회전율 개선"],
    "구매조달": ["핵심 부품 공급사 선정", "원자재 장기 계약 조건", "단가 인하 협의 결과", "대체 공급처 발굴", "물류 위탁사 평가", "구매 절차 개선"],
    "재무회계": ["월 결산 마감 일정", "원가 배부 기준 변경", "자금 조달 계획", "세무 조사 대응 자료", "환율 헤지 정책", "매출 채권 회수 현황"],
    "법무": ["특허 침해 경고장 대응", "계약서 표준 조항 개정", "개인정보 처리 점검", "소송 진행 현황", "공정거래 자율 점검", "비밀유지계약 체결 검토"],
    "정보보안": ["접근 권한 재검토 결과", "취약점 점검 보고", "사고 대응 훈련 결과", "보안 솔루션 도입 검토", "외부 반출 통제 개선", "백업 정책 개정"],
}
FORMS = ["내부 보고서", "이메일", "회의록", "업무일지", "검토 메모", "절차서", "공지문", "제안서", "점검 보고서", "간담회 메모", "요약 브리핑", "인수인계 노트"]

# 등급별 (S,V,M) 조합 — 가이드 곱셈표로 등급이 정해지는 조합만. S3 는 곱 0 의 세 원인(공개·가치 없음·관리 없음)을 모두 덮는다.
TS = [(2, 2, 2)] * 60
S1 = [(2, 2, 1)] * 20 + [(2, 1, 2)] * 20 + [(1, 2, 2)] * 20
S2 = [(1, 1, 1)] * 15 + [(2, 1, 1)] * 15 + [(1, 2, 1)] * 15 + [(1, 1, 2)] * 15
S3 = ([(0, 1, 1)] * 6 + [(0, 2, 2)] * 6 + [(0, 2, 1)] * 4 + [(0, 1, 2)] * 4                     # 공개(S=0) 20
      + [(1, 0, 1)] * 5 + [(2, 0, 2)] * 5 + [(2, 0, 1)] * 5                                      # 가치 없음(V=0) 15
      + [(2, 2, 0)] * 10 + [(2, 1, 0)] * 5 + [(1, 2, 0)] * 5 + [(1, 1, 0)] * 5)                 # 관리 없음(M=0) 25
GRADE_OF = {0: "S3", 1: "S2", 2: "S2", 4: "S1", 8: "TS"}


def grade(s: int, v: int, m: int) -> str:
    return GRADE_OF[s * v * m]


def v_numbers(v: int, rng: random.Random) -> dict:
    if v == 0:
        return {"cost_man_won": 0, "hours": 0, "note": "이 정보는 경제적으로 활용할 수 없다고 문서 안에서 분명히 밝히고, 이 정보에 귀속된 비용·인시가 없거나 0 이라고 쓴다"}
    if v == 1:
        cost, hours = rng.choice([(rng.randint(10, 100), rng.randint(4, 40)), (rng.randint(10, 100), 0), (0, rng.randint(4, 40))])
        return {"cost_man_won": cost, "hours": hours, "note": "경제적으로 활용 가능한 정보이고, 이 정보를 얻거나 만드는 데 귀속된 비용은 100만원 이하이고 투입은 40인시 이하(둘 중 하나 이상은 0보다 큼)"}
    kind = rng.choice(["cost", "hours", "both"])
    cost = rng.randint(3000, 40000) if kind in ("cost", "both") else rng.randint(200, 2500)
    hours = rng.randint(480, 6000) if kind in ("hours", "both") else rng.randint(30, 400)
    return {"cost_man_won": cost, "hours": hours, "note": "이 정보를 얻거나 만드는 데 귀속된 비용이 3,000만원 이상이거나 투입이 480인시 이상(둘 다여도 됨)"}


def main() -> int:
    rng = random.Random(SEED)
    fam_slots = [(d, s) for d, subs in SUBJECTS.items() for s in subs]         # 60
    rng.shuffle(fam_slots)
    forms = (FORMS * 5)[:60]
    rng.shuffle(forms)
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    for p in pools.values():
        rng.shuffle(p)
    specs = []
    for i, ((domain, subject), form) in enumerate(zip(fam_slots, forms)):
        fam = f"F{i + 1:02d}"
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert grade(s, v, m) == g
            vn = v_numbers(v, rng)
            specs.append({
                "doc_key": f"{fam}-{g}", "family_id": fam, "domain": domain, "form": form, "subject": subject,
                "S": s, "V": v, "M": m, "grade": g, "v_numbers": vn,
                "distractor": rng.random() < 0.4,                       # 정보 귀속 비용이 아닌 큰 금액(계약 총액·설비 가격·예산)을 일부러 함께 적는다
                "length_chars": rng.choice([480, 560, 640, 720, 800]),
            })
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "specs_pilot.json").write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    fams = sorted({s["family_id"] for s in specs})
    for b in range(12):
        chunk = [s for s in specs if s["family_id"] in fams[b * 5:(b + 1) * 5]]
        (OUT / f"batch_{b:02d}_specs.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    print(f"명세 {len(specs)}건 · 가족 {len(fams)}개 · 등급 {dict(Counter(s['grade'] for s in specs))}")
    print("SVM 조합", dict(sorted(Counter((s['S'], s['V'], s['M']) for s in specs).items())))
    print("양식", dict(Counter(s['form'] for s in specs)), "· 분산 요소 포함", sum(s['distractor'] for s in specs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
