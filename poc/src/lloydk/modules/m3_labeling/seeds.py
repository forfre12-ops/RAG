"""영업비밀 보호 가이드라인 기반 키워드 시드 + 4대 평가요소.

근거: 부정경쟁방지법 §2조 2호 + KOIPA 영업비밀 관리 가이드라인.
용도:
  - DB `level_keywords` 시드
  - 룰 엔진 1차 라벨링
  - 합성문서(M1) 등급별 토픽 가이드

등급 → 4단계 (OpenAPI Grade enum 정합):
  TS  특급기밀     leak 시 회사 존립 위협
  S1  1급 비밀     leak 시 중대한 손해
  S2  2급 대외비   leak 시 경쟁상 불이익
  S3  3급 공개     leak 시 영향 미미 / 일반 사내자료
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
# pattern_type: exact|regex|semantic (semantic은 임베딩 매칭 — 현재는 placeholder)
KEYWORD_SEEDS: list[dict] = [
    # ---------- TS 특급기밀 ----------
    {"grade": "TS", "keyword": "특급기밀", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "Top Secret", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "TS등급", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "핵심 원천기술", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "M&A 계획", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "인수합병", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "차세대 제품 설계도", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "신약 화합물", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "임직원 인사 이동", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "TS", "keyword": "회사 매각", "weight": 0.85, "factor": "LEAK_IMPACT"},

    # ---------- S1 1급 비밀 ----------
    {"grade": "S1", "keyword": "1급 비밀", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "영업비밀", "weight": 0.95, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "공정 노하우", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "원가 구조", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 데이터베이스", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 정보", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "특허 출원 계획", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "알고리즘 소스코드", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "마케팅 전략", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "신제품 개발 로드맵", "weight": 0.8, "factor": "NON_PUBLICITY"},

    # ---------- S2 2급 대외비 ----------
    {"grade": "S2", "keyword": "대외비", "weight": 1.0, "factor": "LEAK_IMPACT"},
    {"grade": "S2", "keyword": "2급", "weight": 0.9, "factor": "LEAK_IMPACT"},
    {"grade": "S2", "keyword": "내부 검토", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "내부 자료", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "분기 매출", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "사업 계획", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "조직 개편", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "거래처 명단", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "공급망", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "예산 배정", "weight": 0.65, "factor": "ECONOMIC_VALUE"},

    # ---------- S3 3급 공개 ----------
    {"grade": "S3", "keyword": "공개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "보도자료", "weight": 0.9, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공시", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공고", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "이용약관", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "회사 소개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "채용 공고", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "FAQ", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "사보", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "외부 공지", "weight": 0.75, "factor": "NON_PUBLICITY"},
]
