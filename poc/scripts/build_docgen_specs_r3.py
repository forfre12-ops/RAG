#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""사실 우선 문서 생성 3차 명세 — '학습에 쓰지 않는 표현 시험셋'(120건 = 가족 30 × 등급 4).

목적: 1·2차 문서로 학습한 모델이 **표현이 새로운 문서**에서 사실을 읽는지 잰다. 그래서 1·2차와 달리 표현 은행을 주지 않고,
      작성자가 자기 말로 표현을 고안하게 하되 1·2차에서 반복된 어휘·표현을 금지한다. 새 주제 30개·새 양식 24종.
사실 조합은 같은 규칙(가이드 곱셈표): TS (2,2,2) 30 · S1 세 조합 각 10 · S2 네 조합 · S3 = 공개 10 + 가치없음 7 + 관리없음 13.
출력: reports/CLAUDE_DOCGEN_R3_20260921/ — specs_pilot.json · KEY_MAP_PRIVATE.json · agent_batch_00..05.json (배치당 가족 5개 = 문서 20건)
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
OUT = POC / "reports" / "CLAUDE_DOCGEN_R3_20260921"
SEED = 20260929

SUBJECTS = [
    ("연구개발", "폐수 처리용 흡착제 성능 비교"), ("연구개발", "웨어러블 센서 배터리 절전 방식"), ("연구개발", "합성 데이터 품질 검증 방법"),
    ("인사관리", "성과 보상 재원 배분 검토"), ("인사관리", "경력 채용자 처우 기준"), ("인사관리", "직무 전환 교육 대상 선정"),
    ("경영기획", "해외 판매 법인 손익 개선안"), ("경영기획", "신사업 진출 우선순위 평가"), ("경영기획", "임원 성과 지표 재설계"),
    ("품질관리", "시험소 결과 불일치 조사"), ("품질관리", "포장 파손 클레임 대응"), ("품질관리", "고객사 감사 대응 자료"),
    ("영업판매", "대형 입찰 가격 전략"), ("영업판매", "구독형 상품 요금 개편"), ("영업판매", "협력 대리점 계약 조건 조정"),
    ("생산제조", "가동 중단 손실 분석"), ("생산제조", "신소재 투입 시험 생산 기록"), ("생산제조", "안전 사고 재발 방지 조치"),
    ("구매조달", "핵심 원료 이중 조달 계획"), ("구매조달", "물류비 절감 협의 결과"), ("구매조달", "공급 계약 해지 검토"),
    ("재무회계", "투자 자산 손상 검토"), ("재무회계", "자금 운용 한도 조정"), ("재무회계", "원가 절감 성과 집계"),
    ("법무", "합작 계약 독점 조항 검토"), ("법무", "정보 유출 사고 법적 대응"), ("정보보안", "클라우드 전환 보안 통제 설계"),
    ("정보보안", "퇴직자 계정 회수 점검"), ("마케팅", "구독자 이탈 원인 조사"), ("물류", "냉장 물류 온도 이탈 분석"),
]
FORMS = ["메일 스레드", "민원·문의 답변서", "회의 안건지", "공문 회신 초안", "점검 체크리스트", "사내 뉴스레터 기사", "위키 문서", "티켓 코멘트", "발표 슬라이드 노트", "작성 예시가 있는 서식",
         "결재 요청 메시지", "현장 방문 메모", "화상회의 요약", "계약 협상 브리프", "임원 보고 요약", "체크인 대화 정리", "감사 준비 노트", "교육 후기", "일일 상황 공유", "이관 요청서",
         "위험 검토 메모", "고객 응대 스크립트", "협력사 통보문", "프로젝트 종료 보고"]
AVOID = ["명단", "개인별", "역할 기준", "게시판", "전 직원이", "상당한 노력", "열람 신청서", "승인권자의 결재", "결재를 받아야", "격리 서버", "보안 구역", "지정된 열람자", "서약서",
         "전자공시", "학회지", "보도자료", "공개된 특허공보", "홈페이지에 올라", "컨소시엄 회원사", "유료 회원제", "공동 개발 파트너", "역설계", "구두 노하우", "억 단위", "소액", "사내 포털 첫 화면",
         "팀 공유 폴더", "직무 권한 그룹", "링크만 있으면", "외부로 반출"]
TS = [(2, 2, 2)] * 30
S1 = [(2, 2, 1)] * 10 + [(2, 1, 2)] * 10 + [(1, 2, 2)] * 10
S2 = [(1, 1, 1)] * 7 + [(2, 1, 1)] * 8 + [(1, 2, 1)] * 8 + [(1, 1, 2)] * 7
S3 = ([(0, 1, 1)] * 3 + [(0, 2, 2)] * 3 + [(0, 2, 1)] * 2 + [(0, 1, 2)] * 2 + [(1, 0, 1)] * 2 + [(2, 0, 2)] * 3 + [(2, 0, 1)] * 2
      + [(2, 2, 0)] * 5 + [(2, 1, 0)] * 3 + [(1, 2, 0)] * 3 + [(1, 1, 0)] * 2)
GRADE_OF = {0: "S3", 1: "S2", 2: "S2", 4: "S1", 8: "TS"}
TRAPS = ["문서 어딘가에 '목록'이나 '리스트'라는 낱말이 나오되 이 문서의 접근 통제와는 무관한 대상에 쓰이게 한다",
         "'공개'라는 낱말이 나오되 핵심 정보의 공개 여부와는 다른 대상(모집 공고, 일정 안내 등)에 쓰이게 한다",
         "'전체 공유'나 '게시'라는 낱말이 나오되 핵심 정보 자체의 열람 범위와는 무관한 안내에 쓰이게 한다",
         "큰 금액(계약 총액, 설비 가격, 프로젝트 예산)을 함께 적되 이 정보에 귀속된 비용이 아님을 문맥으로 구분한다"]


def v_numbers(v: int, rng: random.Random) -> dict:
    if v == 0:
        return {"cost_man_won": 0, "hours": 0, "note": "이 정보에 귀속된 비용·인시는 없거나 0이고 경제적 활용 가치가 없음"}
    if v == 1:
        cost, hours = rng.choice([(rng.randint(10, 100), rng.randint(4, 40)), (rng.randint(10, 100), 0), (0, rng.randint(4, 40))])
        return {"cost_man_won": cost, "hours": hours, "note": "귀속 비용 100만원 이하 그리고 투입 40인시 이하(둘 중 하나 이상은 0보다 큼)"}
    kind = rng.choice(["cost", "hours", "both"])
    return {"cost_man_won": rng.randint(3000, 40000) if kind in ("cost", "both") else rng.randint(200, 2500),
            "hours": rng.randint(480, 6000) if kind in ("hours", "both") else rng.randint(30, 400), "note": "귀속 비용 3,000만원 이상이거나 투입 480인시 이상(둘 다여도 됨)"}


def main() -> int:
    rng = random.Random(SEED)
    slots = SUBJECTS[:]
    rng.shuffle(slots)
    forms = FORMS[:]
    rng.shuffle(forms)
    forms = (forms * 2)[:30]
    pools = {"TS": TS[:], "S1": S1[:], "S2": S2[:], "S3": S3[:]}
    for p in pools.values():
        rng.shuffle(p)
    specs, keymap = [], {}
    for i, ((domain, subject), form) in enumerate(zip(slots, forms)):
        fam = f"H{i + 1:02d}"
        items = []
        for g in ("TS", "S1", "S2", "S3"):
            s, v, m = pools[g].pop()
            assert GRADE_OF[s * v * m] == g
            impl = rng.sample(["S", "V", "M"], rng.choices([0, 1, 2], weights=[0.4, 0.4, 0.2])[0])
            items.append({"doc_key": f"{fam}-{g}", "family_id": fam, "domain": domain, "form": form, "subject": subject, "S": s, "V": v, "M": m, "grade": g,
                          "v_numbers": v_numbers(v, rng), "implicit_axes": impl, "trap": rng.choice(TRAPS) if rng.random() < 0.4 else None,
                          "length_chars": rng.choice([480, 560, 640, 720, 800, 900])})
        rng.shuffle(items)
        for j, it in enumerate(items):
            it["agent_key"] = f"{fam}-{j + 1}"
            keymap[it["agent_key"]] = it["doc_key"]
            specs.append(it)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "specs_pilot.json").write_text(json.dumps(specs, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "KEY_MAP_PRIVATE.json").write_text(json.dumps(keymap, ensure_ascii=False, indent=1), encoding="utf-8")
    fams = sorted({s["family_id"] for s in specs})
    for b in range(6):
        chunk = [{k: s[k] for k in ("agent_key", "family_id", "domain", "form", "subject", "S", "V", "M", "v_numbers", "implicit_axes", "trap", "length_chars")}
                 for s in specs if s["family_id"] in fams[b * 5:(b + 1) * 5]]
        chunk.sort(key=lambda x: x["agent_key"])
        (OUT / f"agent_batch_{b:02d}.json").write_text(json.dumps(chunk, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "AVOID_PHRASES.json").write_text(json.dumps(AVOID, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    print(f"3차 명세 {len(specs)}건 · 가족 {len(fams)} · 등급 {dict(Counter(s['grade'] for s in specs))} · 양식 {len(set(s['form'] for s in specs))}종")
    return 0


if __name__ == "__main__":
    sys.exit(main())
