"""영업비밀 보호 가이드라인 기반 키워드 시드 + 4대 평가요소.

근거:
- 부정경쟁방지법 §2조 2호 (영업비밀 정의)
- 영업비밀보호법 시행령 (관리 수준 판단)
- 산업기술의 유출방지 및 보호에 관한 법률
- KOIPA 영업비밀 관리 가이드라인 (시드 v1 확정 후 v2 패치 예정)
- W9 일반화 (2026-05-27 v2): KOIPA 외 다른 기관 납품 대응 — 반도체·바이오·SW·금융·공공·법무 도메인 균형

용도:
  - DB `level_keywords` 시드
  - 룰 엔진 1차 라벨링
  - 합성문서(M1) 등급별 토픽 가이드

등급 → 4단계 (OpenAPI Grade enum 정합):
  TS  특급기밀     leak 시 회사 존립 위협
  S1  1급 비밀     leak 시 중대한 손해
  S2  2급 대외비   leak 시 경쟁상 불이익
  S3  3급 공개     leak 시 영향 미미 / 일반 사내자료

v2 확장 정책 (2026-05-27 W9):
- 등급당 약 40~50개 (총 ~170개) — 운영 수준 진입
- 도메인 균형: 반도체·바이오·SW·금융·공공·법무·HR·일반 균등
- 일반화 납품: 특정 산업에 편향되지 않도록 분포 유지
"""

from __future__ import annotations

GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}

# 4대 평가 요소 (DB evaluation_factors 정합)
FACTOR_SEEDS: list[dict] = [
    {"code": "ECONOMIC_VALUE", "name": "경제적 가치", "weight": 0.30},
    {"code": "NON_PUBLICITY", "name": "비공지성", "weight": 0.25},
    {"code": "MANAGEMENT_LEVEL", "name": "관리수준", "weight": 0.15},
    {"code": "LEAK_IMPACT", "name": "유출 시 영향도", "weight": 0.30},
]

# 등급별 키워드 시드.
# weight: 매칭 시 가중치 (높을수록 그 등급에 강한 신호)
# pattern_type: exact|regex|semantic
#   - exact   (기본): 부분 문자열 빈도
#   - regex          : re.findall
#   - semantic       : 임베딩 코사인 유사도 ≥ EMB_SEMANTIC_THRESHOLD(기본 0.75) 시 1회 매칭
#                     provider는 EMB_PROVIDER=hash | lloydk.config.settings.embedding_model 사용
KEYWORD_SEEDS: list[dict] = [
    # ============================================================
    # TS 특급기밀 — 유출 시 회사 존립 위협
    # ============================================================
    # 등급 표기 (명시적 라벨)
    {"grade": "TS", "keyword": "특급기밀", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "Top Secret", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "TS등급", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "극비", "weight": 0.95, "factor": "LEAK_IMPACT"},
    # 경영 전략 — 회사 존립 영향
    {"grade": "TS", "keyword": "M&A 계획", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "인수합병", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "회사 매각", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "지분 매각", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "분사 계획", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "사업 철수", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "비상 경영 계획", "weight": 0.8, "factor": "LEAK_IMPACT"},
    # 핵심 기술 (반도체·SW·바이오·소재)
    {"grade": "TS", "keyword": "핵심 원천기술", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "차세대 제품 설계도", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "반도체 공정 레시피", "weight": 0.95, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "광학 박막 설계", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "EUV 공정 파라미터", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "신약 화합물", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "신약 후보물질", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "임상 1상 결과", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "FDA 승인 전략", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "특수 합금 조성비", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "암호화 알고리즘 키", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "제로데이 취약점", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "AI 학습 데이터 라벨", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 보안·인사 핵심
    {"grade": "TS", "keyword": "최고경영진 인사 이동", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "TS", "keyword": "임직원 인사 이동", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "TS", "keyword": "비상 계좌 정보", "weight": 0.9, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "보안 인증서 개인키", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "마스터 키", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    # 정부·공공
    {"grade": "TS", "keyword": "국가핵심기술", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "방위산업기술", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "산업기술보호위원회 지정", "weight": 0.9, "factor": "LEAK_IMPACT"},
    # 금융
    {"grade": "TS", "keyword": "거래 알고리즘", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "퀀트 모델 파라미터", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "고빈도 매매 시그널", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 법무
    {"grade": "TS", "keyword": "주요 소송 전략", "weight": 0.75, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "기업분할 검토", "weight": 0.8, "factor": "ECONOMIC_VALUE"},

    # ============================================================
    # S1 1급 비밀 — 유출 시 중대한 손해
    # ============================================================
    {"grade": "S1", "keyword": "1급 비밀", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "영업비밀", "weight": 0.95, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "기밀", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "사외비", "weight": 0.85, "factor": "NON_PUBLICITY"},
    # 제조·공정
    {"grade": "S1", "keyword": "공정 노하우", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "수율 개선 방법", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "공정 파라미터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "공정 도면", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "PCB 설계도", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 재무·고객
    {"grade": "S1", "keyword": "원가 구조", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "원가율", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 데이터베이스", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 정보", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "VIP 고객 명단", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 이력", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    # 지재권 · 전략
    {"grade": "S1", "keyword": "특허 출원 계획", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "특허 회피 전략", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "라이선스 협상안", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "기술 이전 계약 초안", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # SW · 알고리즘
    {"grade": "S1", "keyword": "알고리즘 소스코드", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "핵심 모듈 소스", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "내부 API 키", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "데이터베이스 자격증명", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "랜덤시드 고정값", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "모델 가중치 파일", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 마케팅 · 사업
    {"grade": "S1", "keyword": "마케팅 전략", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "신제품 개발 로드맵", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "런칭 일정", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "가격 책정 모델", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "프로모션 단가", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    # 보안 · 인증
    {"grade": "S1", "keyword": "VPN 접속 정보", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "관리자 계정", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "내부 인증 토큰", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    # 바이오 · 의료
    {"grade": "S1", "keyword": "임상시험 프로토콜", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "환자 코호트 분석", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    # 금융
    {"grade": "S1", "keyword": "신용평가 모델", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "이자율 산정식", "weight": 0.75, "factor": "ECONOMIC_VALUE"},

    # ============================================================
    # S2 2급 대외비 — 유출 시 경쟁상 불이익
    # ============================================================
    {"grade": "S2", "keyword": "대외비", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "S2", "keyword": "2급", "weight": 0.9, "factor": "LEAK_IMPACT"},
    {"grade": "S2", "keyword": "Confidential", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "S2", "keyword": "내부 검토", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "내부 자료", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "내부용", "weight": 0.6, "factor": "NON_PUBLICITY"},
    # 재무 · 영업
    {"grade": "S2", "keyword": "분기 매출", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "월별 매출", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "사업 계획", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "사업 계획서", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "예산 배정", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "예산안", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "거래처 명단", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "공급망", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "공급업체 단가", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "수주 현황", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    # 조직 · HR
    {"grade": "S2", "keyword": "조직 개편", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "직원 평가표", "weight": 0.7, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "근무 평정", "weight": 0.7, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "임금 인상안", "weight": 0.65, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "복리후생 변경", "weight": 0.5, "factor": "MANAGEMENT_LEVEL"},
    # 기술 · 일반
    {"grade": "S2", "keyword": "내부 시스템 구성도", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "네트워크 다이어그램", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "운영 매뉴얼", "weight": 0.55, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "운영 절차서", "weight": 0.55, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "회의록", "weight": 0.5, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "주간 보고", "weight": 0.5, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "월간 보고", "weight": 0.5, "factor": "NON_PUBLICITY"},
    # 마케팅 · 시장
    {"grade": "S2", "keyword": "경쟁사 분석", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "시장 조사 결과", "weight": 0.55, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "고객 만족도 조사", "weight": 0.5, "factor": "ECONOMIC_VALUE"},
    # 법무
    {"grade": "S2", "keyword": "계약 조건", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "협의 의사록", "weight": 0.55, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "MOU 초안", "weight": 0.6, "factor": "NON_PUBLICITY"},

    # ============================================================
    # S3 3급 공개 — 일반 사내자료 또는 공개 가능
    # ============================================================
    {"grade": "S3", "keyword": "공개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개가능", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "Public", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "보도자료", "weight": 0.9, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공시", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공시자료", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "사업보고서", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공고", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "이용약관", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "개인정보처리방침", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "회사 소개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "회사 연혁", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "비전 선언문", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "채용 공고", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "채용 안내", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "FAQ", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "자주 묻는 질문", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "사보", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "사내 뉴스레터", "weight": 0.55, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "외부 공지", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "고객 안내문", "weight": 0.7, "factor": "NON_PUBLICITY"},
    # 행사·교육
    {"grade": "S3", "keyword": "오픈 세미나", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 강연", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "교육 자료 일반", "weight": 0.55, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "신입사원 환영문", "weight": 0.55, "factor": "NON_PUBLICITY"},
    # 마케팅 공개
    {"grade": "S3", "keyword": "브랜드 가이드라인", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "광고 문안", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "제품 카탈로그", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "제품 사양서 공개판", "weight": 0.7, "factor": "NON_PUBLICITY"},
    # 공공
    {"grade": "S3", "keyword": "공개특허", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공시 재무제표", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "감사보고서 공개", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "지속가능경영 보고서", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "ESG 보고서", "weight": 0.75, "factor": "NON_PUBLICITY"},
]
