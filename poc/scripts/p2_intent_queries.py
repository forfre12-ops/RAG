"""의도형 쿼리 30종 — KOIPA 가이드 v2 부재 상황 우회.

배경:
  현행 시드 쿼리는 `{keyword} 관련 자료` 패턴으로, 합성 문서 본문
  (boilerplate: "{keyword} 관련 내용" 5종 반복)과 표면 매칭만 발생.
  이 환경의 천장이 Recall@5 ~0.700 (dense·hybrid·reranker 모두 동일).

의도형 쿼리는 KOIPA 영업비밀 분류 가이드 v1 자체작성본(공개 법령 기반,
시드 v3 313 키워드, 18 도메인) 기준 진짜 사용자 검색 의도를 4 등급
× 7-8종으로 작성. 합성 문서 boilerplate를 가능한 회피하기보다,
실제 발주처 운영 환경에서 들어올 자연어 검색 패턴을 반영.

가이드 v2 도착 후엔 이 모듈을 발주처 v2 키워드 분포에 맞춰 갱신.
"""

from __future__ import annotations

# 등급별 의도형 쿼리. 각 쿼리는 expected_grade를 명시.
INTENT_QUERIES: list[dict] = [
    # ────────────────────────────────────────────────────
    # TS 특급기밀 — 회사 존립 영향 의도
    # ────────────────────────────────────────────────────
    {"text": "회사 매각이나 인수합병 검토 진행 상황 자료", "expected_grade": "TS"},
    {"text": "차세대 제품 핵심 원천기술 설계도 파일", "expected_grade": "TS"},
    {"text": "반도체 EUV 공정 레시피 파라미터 문서", "expected_grade": "TS"},
    {"text": "FDA 임상 1상 결과 발표 전 신약 후보물질 자료", "expected_grade": "TS"},
    {"text": "마스터 암호화 키 또는 루트 인증서 보관", "expected_grade": "TS"},
    {"text": "비상 경영 계획 또는 사업 철수 안건", "expected_grade": "TS"},
    {"text": "국가핵심기술 지정 대상 기술자료", "expected_grade": "TS"},
    {"text": "특허 출원 직전 핵심 발명 명세서", "expected_grade": "TS"},

    # ────────────────────────────────────────────────────
    # S1 1급 비밀 — 중대 손해 의도
    # ────────────────────────────────────────────────────
    {"text": "고객 개인정보 데이터베이스 덤프 또는 백업", "expected_grade": "S1"},
    {"text": "VIP 고객 계좌 정보 또는 환자 식별 데이터", "expected_grade": "S1"},
    {"text": "운영 시스템 알고리즘 소스코드", "expected_grade": "S1"},
    {"text": "외부 API 키 또는 데이터베이스 자격증명", "expected_grade": "S1"},
    {"text": "임원 인사 평가 결과 또는 연봉 책정 내역", "expected_grade": "S1"},
    {"text": "VPN 또는 SSO 접속 토큰 인증 정보", "expected_grade": "S1"},
    {"text": "침투 테스트 결과 또는 보안 취약점 분석", "expected_grade": "S1"},
    {"text": "내부 망 분리 구성도 또는 방화벽 룰셋", "expected_grade": "S1"},

    # ────────────────────────────────────────────────────
    # S2 2급 대외비 — 경쟁상 불이익 의도
    # ────────────────────────────────────────────────────
    {"text": "분기별 부서 매출 실적 미공개 자료", "expected_grade": "S2"},
    {"text": "거래처 명단 또는 단가 협상 내역", "expected_grade": "S2"},
    {"text": "마케팅 캠페인 기획안 사전 공유 금지", "expected_grade": "S2"},
    {"text": "신제품 출시 일정 또는 가격 책정안", "expected_grade": "S2"},
    {"text": "내부 회의록 또는 의사결정 과정 기록", "expected_grade": "S2"},
    {"text": "공급망 협력업체 평가 점수표", "expected_grade": "S2"},
    {"text": "조직 개편 또는 인력 재배치 계획", "expected_grade": "S2"},

    # ────────────────────────────────────────────────────
    # S3 3급 공개 — 일반 사내자료 의도
    # ────────────────────────────────────────────────────
    {"text": "공시된 분기 재무제표 또는 사업보고서", "expected_grade": "S3"},
    {"text": "DART 공시 자료 또는 IR 발표 자료", "expected_grade": "S3"},
    {"text": "보도자료 또는 뉴스 인터뷰 원고", "expected_grade": "S3"},
    {"text": "회사 소개 또는 비전 미션 홍보 자료", "expected_grade": "S3"},
    {"text": "공개된 채용 공고 또는 직무 설명서", "expected_grade": "S3"},
    {"text": "공식 홈페이지 게시용 이용약관", "expected_grade": "S3"},
    {"text": "사내 동호회 모집 또는 일반 안내문", "expected_grade": "S3"},
]

assert len(INTENT_QUERIES) == 30, f"expected 30 intent queries, got {len(INTENT_QUERIES)}"
