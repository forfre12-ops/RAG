"""회사 문서 '형태' 8종 정의 - v8 학습셋이 형태 결박을 푸는 재료.

왜(실측 2026-08-13). 지금 학습셋의 문서 형태는 **1종**이다. 9개 번호 섹션짜리 검토서 한
틀에서 주제어만 바꿔 찍는다. 그래서 요소 문장이 항상 같은 자리에 온다 - 비공지성은 §4,
경제적유용성은 §5. 모델은 "무엇이 적혀 있나"가 아니라 "어느 자리에 있나"를 배운다.

결과가 이렇다:

    같은 형태(hardened42)   신뢰도 중앙 0.755 · 자동확정 40.5%
    다른 형태(v3)           신뢰도 중앙 0.389 · 자동확정  0.0%

회원사 문서는 우리가 만든 어떤 형태도 아니다. 형태가 바뀌면 무너지는 모델을 넣으면
자동분류가 0 이 된다.

이 모듈은 형태를 8종으로 늘린다. 핵심은 종류 수가 아니라 **세 가지가 형태마다 달라지는
것**이다:

    1. 섹션 구성    개수·이름·순서가 다르다
    2. 요소 위치    비공지성이 2번 섹션인 형태도, 6번인 형태도, 표 안에 있는 형태도 있다
    3. 서술 형식    산문 · 개조식 · 표 · 항목번호가 섞인다

⚠ 지키는 규칙 두 개(과거 누출의 재발 방지):
    - 형태는 **등급과 무관하게** 배정한다. 특정 형태가 특정 등급에 몰리면 형태가 새 tell 이 된다.
    - 섹션 제목이 등급을 말하지 않는다. v6 에서 "등급 판단 근거" 같은 제목 자체가 tell 이었다.

⚠ 홀드아웃: 이 목록 중 마지막 2종(`FORM_HOLDOUT`)은 학습에 쓰지 않는다. 모델이 **한 번도
본 적 없는 형태**에서 신뢰도가 유지되는지가 이 작업의 판정 기준이다. 회원사 상황을 그대로
재현한 것이다.
"""
from __future__ import annotations

# 각 형태는 (form_id, 제목 꼬리표, 머리말 형식, 섹션 목록) 이다.
# 섹션은 (제목, 종류). 종류가 factor:* 면 그 자리에 해당 요소 문장이 들어간다.
#   intro / observe / procedure / numbers / exception / closing / memo = 채움 서술
#   factor:secrecy / factor:value / factor:management = 요소 문장 자리
#
# 요소 자리가 형태마다 다른 번호에 오는 것이 이 표의 전부다. 그것이 목적이다.

FORMS: list[dict] = [
    {
        "id": "tech_spec",
        "title": "기술사양서",
        "header": "문서번호 {docnum} / 개정 {rev}차 / 작성부서 {dept}",
        "style": "prose",
        "sections": [
            ("적용 범위", "intro"),
            ("구성 및 사양", "numbers"),
            ("취급 제한", "factor:secrecy"),
            ("검증 방법", "procedure"),
            ("사업적 의의", "factor:value"),
            ("보관 및 접근", "factor:management"),
            ("개정 이력", "memo"),
        ],
    },
    {
        "id": "cost_sheet",
        "title": "원가산출서",
        "header": "산출기준일 {date} / 승인 {dept} / 관리번호 {docnum}",
        "style": "bullet",
        "sections": [
            ("산출 목적", "intro"),
            ("경쟁상 의미", "factor:value"),
            ("원가 구성", "numbers"),
            ("대외 공개 여부", "factor:secrecy"),
            ("예외 및 보류", "exception"),
            ("문서 통제", "factor:management"),
        ],
    },
    {
        "id": "meeting_note",
        "title": "회의록",
        "header": "일시 {date} / 참석 {owners}명 / 작성 {dept}",
        "style": "bullet",
        "sections": [
            ("안건", "intro"),
            ("논의 요지", "observe"),
            ("자료 성격", "factor:secrecy"),
            ("업무 영향", "factor:value"),
            ("공유 범위", "factor:management"),
            ("후속 조치", "closing"),
        ],
    },
    {
        "id": "change_order",
        "title": "설계변경서",
        "header": "변경번호 {docnum} / 적용 {date} / 요청부서 {dept}",
        "style": "prose",
        "sections": [
            ("변경 사유", "intro"),
            ("변경 전후 비교", "numbers"),
            ("영향 범위", "factor:value"),
            ("검증 절차", "procedure"),
            ("자료 관리", "factor:management"),
            ("공개 가능 범위", "factor:secrecy"),
            ("보류 조건", "exception"),
        ],
    },
    {
        "id": "test_report",
        "title": "시험성적서",
        "header": "시험번호 {docnum} / 시험일 {date} / 시험자 {owners}명",
        "style": "table",
        "sections": [
            ("시험 개요", "intro"),
            ("측정 결과", "numbers"),
            ("결과 해석", "observe"),
            ("자료 취급", "factor:secrecy"),
            ("보존 및 열람", "factor:management"),
            ("활용 가치", "factor:value"),
        ],
    },
    {
        "id": "work_manual",
        "title": "업무매뉴얼",
        "header": "{dept} 표준 / 제정 {date} / 문서 {docnum}",
        "style": "numbered",
        "sections": [
            ("목적과 적용", "intro"),
            ("절차", "procedure"),
            ("절차의 가치", "factor:value"),
            ("문서 성격", "factor:secrecy"),
            ("예외 처리", "exception"),
            ("관리 책임", "factor:management"),
            ("부속 기록", "memo"),
        ],
    },
    # ── 이하 2종은 홀드아웃 (학습 금지) ────────────────────────────────────
    {
        "id": "contract_terms",
        "title": "계약조건 검토의견",
        "header": "검토번호 {docnum} / 검토일 {date} / 검토 {dept}",
        "style": "prose",
        "sections": [
            ("검토 배경", "intro"),
            ("조건별 쟁점", "observe"),
            ("협상상 위치", "factor:value"),
            ("자료 공개 여부", "factor:secrecy"),
            ("취급 지침", "factor:management"),
            ("결론 및 유보", "closing"),
        ],
    },
    {
        "id": "customer_list",
        "title": "거래처 관리대장",
        "header": "대장번호 {docnum} / 기준일 {date} / 관리 {dept}",
        "style": "table",
        "sections": [
            ("관리 목적", "intro"),
            ("등재 기준", "procedure"),
            ("접근 통제", "factor:management"),
            ("수집 경로", "factor:secrecy"),
            ("영업상 의미", "factor:value"),
            ("갱신 주기", "memo"),
        ],
    },
    # ── 이하 6종은 **2차 판정면(확대판)** 전용이다 ─────────────────────────
    # 1차 판정면은 형태 2종 x 프레임 4~6종이라 조합이 좁다. 거기서 조건을 통과해도
    # "그 조합에서 통과" 이지 실문서 보장이 아니고, 계속 그것만 보면 과적합한다.
    # 그래서 학습·1차 판정 어디에도 쓰지 않은 형태를 따로 둔다. 문서 골격을 더 크게
    # 흔든다 - 섹션 수 4~9 · 요소 위치 극단(맨앞/맨뒤) · 요소 두 개가 붙어 있는 배치.
    {
        "id": "audit_memo",
        "title": "내부감사 메모",
        "header": "감사번호 {docnum} / 실시 {date} / 감사인 {owners}명",
        "style": "prose",
        "sections": [
            ("자료 보관 상태", "factor:management"),
            ("확인 경위", "intro"),
            ("대외 노출 여부", "factor:secrecy"),
            ("업무상 중요도", "factor:value"),
        ],
    },
    {
        "id": "handover",
        "title": "업무인수인계서",
        "header": "인계 {dept} / 인수일 {date} / 관리번호 {docnum}",
        "style": "numbered",
        "sections": [
            ("인계 범위", "intro"),
            ("진행 현황", "observe"),
            ("절차 요약", "procedure"),
            ("주요 수치", "numbers"),
            ("예외 사항", "exception"),
            ("자료 성격", "factor:secrecy"),
            ("보관 인계", "factor:management"),
            ("업무 영향", "factor:value"),
            ("특이사항", "memo"),
        ],
    },
    {
        "id": "incident_report",
        "title": "사고보고서",
        "header": "접수 {date} / 보고 {dept} / 문서 {docnum}",
        "style": "bullet",
        "sections": [
            ("발생 개요", "intro"),
            ("업무 파급", "factor:value"),
            ("경과", "observe"),
            ("자료 취급 상태", "factor:management"),
            ("대외 알림 여부", "factor:secrecy"),
            ("재발 방지", "closing"),
        ],
    },
    {
        "id": "budget_plan",
        "title": "예산집행계획",
        "header": "회계연도 {date} / 부서 {dept} / 계획 {docnum}",
        "style": "table",
        "sections": [
            ("집행 근거", "intro"),
            ("항목별 배정", "numbers"),
            ("공개 범위", "factor:secrecy"),
            ("집행 통제", "factor:management"),
            ("기대 효과", "factor:value"),
            ("보류 항목", "exception"),
            ("비고", "memo"),
        ],
    },
    {
        "id": "spec_change_log",
        "title": "사양변경이력",
        "header": "이력번호 {docnum} / 최종 {date} / 관리 {dept}",
        "style": "table",
        "sections": [
            ("변경 이력", "memo"),
            ("사업적 의미", "factor:value"),
            ("자료 공개 상태", "factor:secrecy"),
            ("이력 관리 방식", "factor:management"),
        ],
    },
    {
        "id": "risk_review",
        "title": "위험성검토서",
        "header": "검토 {date} / 주관 {dept} / 문서 {docnum}",
        "style": "prose",
        "sections": [
            ("검토 배경", "intro"),
            ("식별된 위험", "observe"),
            ("통제 현황", "factor:management"),
            ("완화 절차", "procedure"),
            ("잔여 위험", "exception"),
            ("정보 노출도", "factor:secrecy"),
            ("손실 규모", "factor:value"),
            ("결론", "closing"),
        ],
    },
    # ── 이하 4종은 **요소 섹션이 아예 없는** 형태다 ────────────────────────
    # 실측(2026-08-14) 에서 드러난 결함에 대응한다. 실데이터 400건 중 399건이 S3 로
    # 떨어졌고 원인은 secrecy=absent 369건이었다. 우리 학습셋은 unknown 문서조차
    # '자료 성격'·'공유 범위' 같은 **요소 섹션을 갖고 있어서**, 모델이 "요소 얘기가
    # 나오는 문서" 만 봤다. 실제 업무문서는 기술 내용만 쓰고 요소를 언급하지 않는다.
    #
    # 이 형태들은 요소 자리를 두지 않는다. 세 요소가 전부 unknown 이 되고, 모델은
    # "근거가 없으면 unknown" 을 배워야 한다. 지금은 그 자리에서 absent(=부재가 입증됨)
    # 라고 단언하는데 그것이 미탐의 뿌리다.
    {
        "id": "tech_note",
        "title": "기술검토 노트",
        "header": "검토 {date} / 작성 {dept} / 문서 {docnum}",
        "style": "prose",
        "sections": [
            ("검토 배경", "intro"),
            ("구성 및 수치", "numbers"),
            ("확인 사항", "observe"),
            ("후속", "closing"),
        ],
    },
    {
        "id": "process_sheet",
        "title": "공정 조건표",
        "header": "관리번호 {docnum} / 적용 {date} / 담당 {dept}",
        "style": "table",
        "sections": [
            ("적용 대상", "intro"),
            ("조건값", "numbers"),
            ("작업 순서", "procedure"),
            ("예외", "exception"),
            ("비고", "memo"),
        ],
    },
    {
        "id": "meeting_brief",
        "title": "약식 회의 메모",
        "header": "일시 {date} / 참석 {owners}명",
        "style": "bullet",
        "sections": [
            ("논의", "observe"),
            ("결정", "closing"),
        ],
    },
    {
        "id": "field_log",
        "title": "현장 점검 일지",
        "header": "점검일 {date} / 점검자 {owners}명 / {dept}",
        "style": "numbered",
        "sections": [
            ("점검 범위", "intro"),
            ("측정값", "numbers"),
            ("이상 여부", "observe"),
            ("조치 절차", "procedure"),
            ("특이사항", "memo"),
        ],
    },
]

# 학습에 쓰지 않는 형태. 모델이 한 번도 못 본 형태에서 신뢰도가 유지되는지가 판정 기준이다.
FORM_HOLDOUT = ("contract_terms", "customer_list")

# 2차 판정면(확대판) 전용 형태. 학습에도 1차 판정면에도 쓰지 않는다.
# 1차 판정면에서 조건을 통과한 뒤 **여기서 다시 재는 것**이 과적합 여부를 가른다.
FORM_HOLDOUT2 = ("audit_memo", "handover", "incident_report",
                 "budget_plan", "spec_change_log", "risk_review")

# 요소 섹션이 없는 형태. 세 요소가 전부 unknown 이 되며 학습에 쓴다 —
# "근거가 없으면 unknown" 을 배우게 하는 것이 목적이다.
FORM_NO_FACTOR = ("tech_note", "process_sheet", "meeting_brief", "field_log")

FORM_BY_ID = {f["id"]: f for f in FORMS}
TRAIN_FORMS = [f for f in FORMS
               if f["id"] not in FORM_HOLDOUT + FORM_HOLDOUT2 + FORM_NO_FACTOR]
NO_FACTOR_FORMS = [f for f in FORMS if f["id"] in FORM_NO_FACTOR]
HOLDOUT_FORMS = [f for f in FORMS if f["id"] in FORM_HOLDOUT]
HOLDOUT2_FORMS = [f for f in FORMS if f["id"] in FORM_HOLDOUT2]


def sanity_check() -> None:
    """형태 정의가 규칙을 지키는지 - 생성 전에 부른다."""
    ids = [f["id"] for f in FORMS]
    if len(ids) != len(set(ids)):
        raise ValueError("form id 중복")
    for f in FORMS:
        kinds = [k for _, k in f["sections"]]
        if f["id"] in FORM_NO_FACTOR:
            # 무요소 형태는 요소 자리를 두지 않는 것이 목적이다.
            if any(k.startswith("factor:") for k in kinds):
                raise ValueError(f"{f['id']}: 무요소 형태에 요소 섹션이 있다")
            continue
        for need in ("factor:secrecy", "factor:value", "factor:management"):
            if kinds.count(need) != 1:
                raise ValueError(f"{f['id']}: {need} 가 정확히 1회 있어야 한다")
    # 요소가 같은 자리에만 오면 형태를 늘린 의미가 없다.
    for need in ("factor:secrecy", "factor:value", "factor:management"):
        pos = {f["id"]: [k for _, k in f["sections"]].index(need)
               for f in FORMS if f["id"] not in FORM_NO_FACTOR}
        if len(set(pos.values())) < 3:
            raise ValueError(f"{need} 위치가 {sorted(set(pos.values()))} 뿐이다 - 더 흩어야 한다")
    if not HOLDOUT_FORMS:
        raise ValueError("홀드아웃 형태가 없다 - 미지 형태 검증을 못 한다")


if __name__ == "__main__":
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    sanity_check()
    print(f"형태 {len(FORMS)}종 (학습 {len(TRAIN_FORMS)} · 홀드아웃 {len(HOLDOUT_FORMS)})")
    for f in FORMS:
        kinds = [k for _, k in f["sections"]]
        mark = " [홀드아웃]" if f["id"] in FORM_HOLDOUT else ""
        print(f"  {f['id']:<16} 섹션 {len(f['sections'])} · "
              f"S{kinds.index('factor:secrecy')} V{kinds.index('factor:value')} "
              f"M{kinds.index('factor:management')} · {f['style']}{mark}")
