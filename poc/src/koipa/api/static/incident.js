// 와우 D — FNR(미탐) 사고 시뮬레이션 데이터.
// V2 통합본 §9 "FNR ≤ 5% 핵심" 절을 화면에서 가시화.
//
// 정직성 표기 (2026-05-29 추가):
//   본 시퀀스의 손해액·지연 기간은 우리가 직접 측정한 값이 아니라,
//   영업비밀 침해 사고 일반론에 기반한 "예시 시나리오"입니다.
//   KOIPA 공식 통계 인용으로 보이지 않도록 모든 추정치에
//   "예시 / 미검증" 표기를 명시.

export const INCIDENT = {
  // 미탐 발생 시 6단계 사고 시퀀스
  fnr_sequence: [
    {
      step: 1,
      title: "분류 누락",
      detail: "AI가 영업비밀 문서를 일반(S3)으로 잘못 판정",
      tag: "AI 오판",
    },
    {
      step: 2,
      title: "일반 폴더 저장",
      detail: "보호 등급 없이 공용 폴더에 저장됨 (워터마크·DRM 미적용)",
      tag: "보호조치 누락",
    },
    {
      step: 3,
      title: "외부 메일 첨부",
      detail: "직원이 협력사 메일에 첨부 — 시스템이 차단 신호 못 받음",
      tag: "유통 확산",
    },
    {
      step: 4,
      title: "외부 유출",
      detail: "경쟁사 또는 제3자에게 도달 — 회수 불가",
      tag: "유출 완료",
    },
    {
      step: 5,
      title: "사고 인지",
      detail: "사고 인지가 수개월 후로 지연되는 패턴이 일반적 (예시 시나리오)",
      tag: "지연 인지",
    },
    {
      step: 6,
      title: "법적 분쟁 + 손해",
      detail: "부경법 §18 형사처벌 대상 + 민사 손해배상 + 기술 가치 하락",
      tag: "사후 대응",
    },
  ],
  // 차단 시퀀스 — 우리 시스템이 이 사고를 어떻게 막는가
  prevention_sequence: [
    {
      step: 1,
      title: "신뢰도 검사",
      detail: "분류 결과의 confidence < 0.7 → 자동 검수자 큐 적재",
      tag: "low-confidence 게이트",
    },
    {
      step: 2,
      title: "검수자 알림",
      detail: "FUN-024 검수 대시보드에 미확정 건으로 표시",
      tag: "human-in-the-loop",
    },
    {
      step: 3,
      title: "재라벨 (/relabel)",
      detail: "검수자가 정확한 등급으로 교정 — 능동학습 큐 적재",
      tag: "active learning",
    },
    {
      step: 4,
      title: "자동 재학습 트리거",
      detail: "큐 100건 누적 시 Celery 잡으로 모델 재학습 자동 실행",
      tag: "재학습",
    },
    {
      step: 5,
      title: "FNR 개선 검증",
      detail: "이전 vs 신규 FNR 비교 — 개선되면 Staging → Production 승격",
      tag: "회귀 보장",
    },
  ],
  // 손해 추정 — 모두 예시 시나리오 / 목표값. 실측 X.
  estimates: [
    {
      label: "건당 평균 영업비밀 침해 손해액",
      value: "수십억원 단위",
      note: "예시 시나리오 (실 통계 미검증, 발주처 자료 입수 후 갱신 예정)",
      flag: "예시",
    },
    {
      label: "AI 영업비밀 자동분류 도입 시 미탐 감소 목표",
      value: "≤ 5%",
      note: "V2 통합본 §9 합격선 — 현재는 목표값, 실측치 아님",
      flag: "목표",
    },
    {
      label: "사고 인지 평균 지연",
      value: "수개월",
      note: "예시 시나리오 (KOIPA 공식 통계 인용 아님)",
      flag: "예시",
    },
    {
      label: "형사처벌 기준",
      value: "부경법 §18",
      note: "10년 이하 징역 또는 5억 이하 벌금 (법령 명문 조항)",
      flag: "법령",
    },
  ],
};
