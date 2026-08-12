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
]

# 학습에 쓰지 않는 형태. 모델이 한 번도 못 본 형태에서 신뢰도가 유지되는지가 판정 기준이다.
FORM_HOLDOUT = ("contract_terms", "customer_list")

FORM_BY_ID = {f["id"]: f for f in FORMS}
TRAIN_FORMS = [f for f in FORMS if f["id"] not in FORM_HOLDOUT]
HOLDOUT_FORMS = [f for f in FORMS if f["id"] in FORM_HOLDOUT]


def sanity_check() -> None:
    """형태 정의가 규칙을 지키는지 - 생성 전에 부른다."""
    ids = [f["id"] for f in FORMS]
    if len(ids) != len(set(ids)):
        raise ValueError("form id 중복")
    for f in FORMS:
        kinds = [k for _, k in f["sections"]]
        for need in ("factor:secrecy", "factor:value", "factor:management"):
            if kinds.count(need) != 1:
                raise ValueError(f"{f['id']}: {need} 가 정확히 1회 있어야 한다")
    # 요소가 같은 자리에만 오면 형태를 늘린 의미가 없다.
    for need in ("factor:secrecy", "factor:value", "factor:management"):
        pos = {f["id"]: [k for _, k in f["sections"]].index(need) for f in FORMS}
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
