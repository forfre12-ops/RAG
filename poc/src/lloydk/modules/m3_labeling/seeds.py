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

v3 확장 정책 (2026-05-28 W12+, 무중단 블록 1):
- 등급당 약 100~140개 (총 ~480개) — 운영 풀 진입
- 도메인 확장: v2(8) + 자동차·화학·소재·통신·에너지·국방·식품·물류·콘텐츠·헬스케어(10) = 18 도메인
- 4 평가요소 분포 균형 강화: LEAK_IMPACT·ECONOMIC_VALUE·MANAGEMENT_LEVEL·NON_PUBLICITY 각 ~25%
- 시드 키워드 추가는 공개 출처(영업비밀보호법·시행령·산업기술보호법·KISA·KOTRA 공개 자료)만 사용

v4 확장 정책 (2026-05-29 P0-B5):
- 신규 5 도메인 추가: 해양·우주·항공정밀·정유석유화학·원자력/신재생에너지
- 등급당 ~20개 추가 (총 ~100개) — 1,000+ 키워드 운영 임계 도달
- 출처: 산업기술보호법 시행령 별표(국가핵심기술), KISTEP 공개 산업동향 보고서
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)

GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def get_factor_codes() -> list[str]:
    """FactorRegistry에서 현재 활성 평가요소 코드 목록 반환.

    DB에 커스텀 factor가 있으면 그것을, 없으면 FACTOR_SEEDS 기반 기본값 반환.
    """
    try:
        from lloydk.schemas.common import FactorRegistry  # noqa: PLC0415
        return FactorRegistry.get_codes()
    except Exception:  # noqa: BLE001
        return [f["code"] for f in FACTOR_SEEDS]


def get_grade_order() -> dict[str, int]:
    """GradeRegistry에서 현재 활성 등급의 우선순위 매핑 반환.

    DB에 커스텀 등급이 있으면 그것을, 없으면 GRADE_ORDER 상수를 반환.
    FNR-safe 로직(더 높은 등급 우선 선택)에서 사용.
    """
    try:
        from lloydk.schemas.common import GradeRegistry  # noqa: PLC0415
        return GradeRegistry.get_order()
    except Exception:  # noqa: BLE001
        return GRADE_ORDER


def load_seeds_from_db() -> list[dict] | None:
    """DB level_keywords + evaluation_factors 테이블에서 KEYWORD_SEEDS 형식으로 로드.

    다른 프로젝트에서 도메인 키워드를 DB에 등록하면 코드 변경 없이 룰 엔진에 반영됩니다.

    Returns:
        list[dict] — KEYWORD_SEEDS와 동일한 형식 (keyword, grade, factor, weight, pattern_type)
        None       — DB 미가용 또는 키워드 없음 → 호출자가 KEYWORD_SEEDS로 폴백
    """
    try:
        from lloydk.db import session_scope  # noqa: PLC0415
        from lloydk.db.models import ClassificationLevel, EvaluationFactor, LevelKeyword  # noqa: PLC0415
        with session_scope() as db:
            level_map = {
                lv.level_id: lv.level_code
                for lv in db.query(ClassificationLevel)
                .filter(ClassificationLevel.is_active.is_(True))
                .all()
            }
            factor_map = {
                f.factor_id: f.factor_code
                for f in db.query(EvaluationFactor)
                .filter(EvaluationFactor.is_active.is_(True))
                .all()
            }
            keywords = (
                db.query(LevelKeyword)
                .filter(LevelKeyword.is_active.is_(True))
                .all()
            )
            seeds = []
            for kw in keywords:
                grade = level_map.get(kw.level_id)
                if not grade:
                    continue
                factor = factor_map.get(kw.factor_id) if kw.factor_id else "ECONOMIC_VALUE"
                seeds.append({
                    "keyword": kw.keyword,
                    "grade": grade,
                    "factor": factor or "ECONOMIC_VALUE",
                    "weight": float(kw.weight or 1.0),
                    "pattern_type": kw.pattern_type or "exact",
                })
            if seeds:
                _logger.debug("load_seeds_from_db: %d keywords loaded", len(seeds))
                return seeds
            return None
    except Exception as exc:  # noqa: BLE001
        _logger.debug("load_seeds_from_db failed, caller will use KEYWORD_SEEDS: %s", exc)
        return None

# [레거시·A안 보조] 4요소 가중합 모델 (A안 100점 보조 모드 전용·하위호환).
FACTOR_SEEDS_LEGACY_V1: list[dict] = [
    {"code": "ECONOMIC_VALUE", "name": "경제적 가치", "weight": 0.30},
    {"code": "NON_PUBLICITY", "name": "비공지성", "weight": 0.25},
    {"code": "MANAGEMENT_LEVEL", "name": "관리수준", "weight": 0.15},
    {"code": "LEAK_IMPACT", "name": "유출 시 영향도", "weight": 0.30},
]

# ── 정본 평가요소: 가이드 3요건 (B안 S×V×M) ──
# weight는 곱셈식에서 미사용(하위호환 KeyError 방지용 1.0). 등급 = S×V×M.
FACTOR_SEEDS: list[dict] = [
    {"code": "SECRECY", "name": "비공지성(S)", "weight": 1.0},       # 0이면 곱=0 → 공개 게이트
    {"code": "VALUE", "name": "경제적 유용성(V)", "weight": 1.0},
    {"code": "MANAGEMENT", "name": "비밀관리성(M)", "weight": 1.0},
]
CANONICAL_FACTOR_SEEDS: list[dict] = FACTOR_SEEDS  # 별칭 (정본 = FACTOR_SEEDS)

# 레거시 4요소 factor 태그 → 정본 3요건 매핑 (300+ 시드 재태깅 없이 정합).
# 유출영향도는 정본 3요건에 없음 → 가치 신호로 흡수(R4: 등급 loss_weight로 별도 관리).
LEGACY_FACTOR_ALIAS: dict[str, str] = {
    "NON_PUBLICITY": "SECRECY",
    "ECONOMIC_VALUE": "VALUE",
    "MANAGEMENT_LEVEL": "MANAGEMENT",
    "LEAK_IMPACT": "VALUE",
    # 정본 코드는 그대로 통과
    "SECRECY": "SECRECY",
    "VALUE": "VALUE",
    "MANAGEMENT": "MANAGEMENT",
}


def to_canonical_factor(factor_code: str) -> str:
    """레거시 4요소 코드를 정본 3요건 코드로 정규화. 이미 정본이면 그대로 반환."""
    return LEGACY_FACTOR_ALIAS.get(factor_code, factor_code)

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

    # === v3 확장 (W12+ 블록 1, 도메인 일반화) ===
    # 자동차·전기차
    {"grade": "TS", "keyword": "배터리 셀 화학식", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "배터리 양극재 조성", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "전고체 전해질 조성", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "자율주행 알고리즘 핵심", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "차세대 플랫폼 설계", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "차량 ECU 펌웨어 소스", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    # 화학·소재
    {"grade": "TS", "keyword": "촉매 합성 레시피", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "공정 촉매 조성비", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "OLED 발광 재료 조성", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "포토레지스트 조성", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "특수 코팅 조성식", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 통신·네트워크
    {"grade": "TS", "keyword": "5G 기지국 스택 핵심", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "통신 프로토콜 미공개 확장", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "TS", "keyword": "해저케이블 경로", "weight": 0.85, "factor": "LEAK_IMPACT"},
    # 에너지·원자력
    {"grade": "TS", "keyword": "원자로 노심 설계", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "핵연료 농축 절차", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "발전소 SCADA 구성", "weight": 0.9, "factor": "MANAGEMENT_LEVEL"},
    # 국방·방산
    {"grade": "TS", "keyword": "유도무기 제어 알고리즘", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "위성 통신 키 체계", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "스텔스 코팅 조성", "weight": 0.95, "factor": "ECONOMIC_VALUE"},
    # 헬스케어·바이오 확장
    {"grade": "TS", "keyword": "유전자 편집 표적 서열", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "백신 항원 설계", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "mRNA 변형 서열", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    # 콘텐츠·게임 (산업 보호 대상)
    {"grade": "TS", "keyword": "게임 코어 엔진 소스", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "콘텐츠 추천 핵심 알고리즘", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 보안 운영
    {"grade": "TS", "keyword": "HSM 마스터 시드", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "루트 CA 개인키", "weight": 0.95, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "OT/SCADA 인증서", "weight": 0.9, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "TS", "keyword": "백도어 위치", "weight": 0.95, "factor": "LEAK_IMPACT"},
    # 경영 확장
    {"grade": "TS", "keyword": "비공개 합병 가격", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "비공개 IPO 일정", "weight": 0.9, "factor": "NON_PUBLICITY"},
    {"grade": "TS", "keyword": "전략 제휴 가격 조건", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 정부·공공 확장
    {"grade": "TS", "keyword": "산업기술 국가핵심기술 지정", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "방산물자 지정", "weight": 0.9, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "보호 대상 기술 분류", "weight": 0.85, "factor": "LEAK_IMPACT"},
    # 금융 확장
    {"grade": "TS", "keyword": "리스크 모델 핵심 파라미터", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "트레이딩 시그널 생성식", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "초단타 매매 핵심 로직", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # AI/데이터 확장
    {"grade": "TS", "keyword": "기초모델 사전학습 데이터셋", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "RLHF 보상 모델 가중치", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "강화학습 정책 가중치", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 식품·바이오 확장
    {"grade": "TS", "keyword": "비공개 가공 레시피", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "발효 균주 정보", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    # 물류·SCM
    {"grade": "TS", "keyword": "물류 라우팅 최적화 핵심", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "거래선 단가 변동표", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 분석 (등급 표기 명시 확장)
    {"grade": "TS", "keyword": "1급 보안", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "기밀 1급", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "Eyes-Only", "weight": 0.9, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "Need-to-Know", "weight": 0.85, "factor": "NON_PUBLICITY"},

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
    # [pilot] 자금계획류 — 가이드 회계 예시표상 자금계획 정보 = 1급 비밀(S1)
    {"grade": "S1", "keyword": "자금계획", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "자금조달 전략", "weight": 0.78, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "자금조달 방안", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "자금수지", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "재무 모델링", "weight": 0.72, "factor": "ECONOMIC_VALUE"},
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

    # === v3 확장 (W12+ 블록 1) ===
    # 자동차·모빌리티
    {"grade": "S1", "keyword": "차량 BOM 명세", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "차량 단가표", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "리콜 대응 계획", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "ADAS 시험 데이터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 화학·소재
    {"grade": "S1", "keyword": "원재료 단가 협상안", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "공정 수율 데이터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "제조 BOM", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 통신·네트워크
    {"grade": "S1", "keyword": "네트워크 보안 구성", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "방화벽 룰셋", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "DNS 내부 구성", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    # 에너지
    {"grade": "S1", "keyword": "발전 비용 구조", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "전력 거래 시장 분석", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    # 국방·방산 (TS 미만 일반)
    {"grade": "S1", "keyword": "납품 단가 협상", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "방산 입찰 전략", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 헬스케어·의료
    {"grade": "S1", "keyword": "환자 식별 데이터", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "의료 영상 데이터셋", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "보험 청구 데이터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    # 콘텐츠·게임
    {"grade": "S1", "keyword": "게임 밸런스 파라미터", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "콘텐츠 라이선스 단가", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "유저 행동 로그", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    # 보안 운영
    {"grade": "S1", "keyword": "취약점 분석 보고서", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "침투 테스트 결과", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "보안 점검 결과", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "사고 대응 보고서", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "비공개 패치 노트", "weight": 0.75, "factor": "NON_PUBLICITY"},
    # AI/데이터
    {"grade": "S1", "keyword": "fine-tuning 데이터셋", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "프롬프트 템플릿", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "embedding 모델 구성", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "RAG 검색 인덱스 구조", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    # 식품·물류
    {"grade": "S1", "keyword": "공급사 단가표", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "물류 비용 분석", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    # 인사·HR
    {"grade": "S1", "keyword": "임원 평가 결과", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "성과급 산정 모델", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "스톡옵션 부여 명단", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    # 영업·고객
    {"grade": "S1", "keyword": "주요 고객 ARPU", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "고객 이탈률", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "B2B 가격표", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "할인 정책 매트릭스", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    # 법무 확장
    {"grade": "S1", "keyword": "비공개 합의서", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "분쟁 합의 조건", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S1", "keyword": "라이선스 협상 초안", "weight": 0.75, "factor": "NON_PUBLICITY"},
    # 등급 표기 확장 — 본 시스템은 TS(특급) > S1(1급) > S2(2급) > S3(공개) 순.
    # "2급 비밀"은 S2 시드와 충돌 가능해 의도적으로 S1에 두지 않음.
    {"grade": "S1", "keyword": "Restricted", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "Confidential High", "weight": 0.8, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "1급비밀", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "내부 1급", "weight": 0.85, "factor": "LEAK_IMPACT"},

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

    # === v3 확장 (W12+ 블록 1) ===
    # 자동차
    {"grade": "S2", "keyword": "차종별 판매 실적", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "딜러 인센티브", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    # 통신·네트워크
    {"grade": "S2", "keyword": "기지국 위치 목록", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "회선 운영 현황", "weight": 0.6, "factor": "MANAGEMENT_LEVEL"},
    # 에너지
    {"grade": "S2", "keyword": "발전소 운영 일지", "weight": 0.6, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "전력 수급 계획", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    # 식품·물류
    {"grade": "S2", "keyword": "거래처별 매출", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "물류 거점 운영 현황", "weight": 0.6, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "배송 비용 구조", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    # 콘텐츠
    {"grade": "S2", "keyword": "광고 단가표", "weight": 0.65, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "콘텐츠 사용 실적", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    # 헬스케어
    {"grade": "S2", "keyword": "진료 통계", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "병상 운영률", "weight": 0.55, "factor": "MANAGEMENT_LEVEL"},
    # IT·운영
    {"grade": "S2", "keyword": "용량 산정 보고서", "weight": 0.6, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "시스템 모니터링 결과", "weight": 0.55, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "장애 보고서", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "변경 관리 대장", "weight": 0.55, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "릴리즈 노트 내부판", "weight": 0.55, "factor": "NON_PUBLICITY"},
    # 보안·감사
    {"grade": "S2", "keyword": "감사 결과 요약", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "내부 통제 보고서", "weight": 0.65, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "권한 점검 결과", "weight": 0.65, "factor": "MANAGEMENT_LEVEL"},
    # 영업·마케팅 확장
    {"grade": "S2", "keyword": "캠페인 성과", "weight": 0.55, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "프로모션 일정", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "고객 세그먼트 분석", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    # HR 확장
    {"grade": "S2", "keyword": "팀별 KPI", "weight": 0.6, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "근태 통계", "weight": 0.55, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "교육 이수율", "weight": 0.5, "factor": "MANAGEMENT_LEVEL"},
    # 재무 확장
    {"grade": "S2", "keyword": "원가 분석 내부판", "weight": 0.7, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "예산 집행 현황", "weight": 0.6, "factor": "ECONOMIC_VALUE"},
    # 등급 표기 확장
    {"grade": "S2", "keyword": "Internal Use", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "Internal Only", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "사내 전용", "weight": 0.6, "factor": "NON_PUBLICITY"},
    {"grade": "S2", "keyword": "비공개 (대외)", "weight": 0.7, "factor": "NON_PUBLICITY"},

    # ============================================================
    # S3 3급 공개 — 일반 사내자료 또는 공개 가능
    # ============================================================
    # [pilot fix] 바 "공개" 시드 제거 — "외부 공개 시(유출 위험)" 경고문·"비공개"(substring)에 오매칭해
    # 비밀문서를 S3로 과소분류시킴. 공개 판정은 구체 마커(보도자료·공시·공개특허 등)로만.
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

    # === v3 확장 (W12+ 블록 1) ===
    # 정부·공공 공개
    {"grade": "S3", "keyword": "공공입찰 공고", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "정부 통계", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "정책 자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공공데이터 포털", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "오픈데이터", "weight": 0.75, "factor": "NON_PUBLICITY"},
    # IR·재무 공개
    {"grade": "S3", "keyword": "분기 IR 자료", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "주주총회 공고", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "전자공시", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "DART 공시", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "EDGAR Filing", "weight": 0.8, "factor": "NON_PUBLICITY"},
    # 학술·연구 공개
    {"grade": "S3", "keyword": "학회 발표 자료", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 논문", "weight": 0.85, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "프리프린트", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "오픈소스 라이선스", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "GitHub 공개 저장소", "weight": 0.8, "factor": "NON_PUBLICITY"},
    # 미디어·홍보
    {"grade": "S3", "keyword": "기자회견 자료", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공식 SNS 게시물", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "유튜브 공식", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "블로그 공식", "weight": 0.7, "factor": "NON_PUBLICITY"},
    # 안전·환경
    {"grade": "S3", "keyword": "MSDS 공개판", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "환경 영향 평가 공개", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "안전 인증서", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "ISO 인증 안내", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "ISMS 인증 안내", "weight": 0.75, "factor": "NON_PUBLICITY"},
    # 제품·기술 공개
    {"grade": "S3", "keyword": "공개 데이터시트", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 사양서", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 API 문서", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "사용자 매뉴얼 공개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "지원 문서", "weight": 0.65, "factor": "NON_PUBLICITY"},
    # 행사·이벤트
    {"grade": "S3", "keyword": "개최 안내", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "참가 안내", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "포럼 자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 컨퍼런스", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "데모데이 자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    # 인사·채용 공개
    {"grade": "S3", "keyword": "공개 모집 공고", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 채용 일정", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "인턴 모집 공고", "weight": 0.75, "factor": "NON_PUBLICITY"},
    # 통계·트렌드
    {"grade": "S3", "keyword": "산업 트렌드 리포트", "weight": 0.65, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 시장 보고서", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "오픈 통계자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    # 등급 표기 확장 (Public은 위에 이미 존재)
    {"grade": "S3", "keyword": "Unclassified", "weight": 0.8, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공개 가능", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "공시 대상", "weight": 0.75, "factor": "NON_PUBLICITY"},

    # ============================================================
    # v4 신규 도메인 추가 (해양·우주·항공정밀·정유석유화학·원자력/신재생)
    # 출처: 산업기술보호법 시행령 별표(국가핵심기술), KISTEP 산업동향
    # ============================================================
    # --- TS (국가핵심기술 / 국가안보 연계) ---
    {"grade": "TS", "keyword": "잠수함 추진체 설계", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "심해 무인잠수정 항법", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "함정 음향 스텔스 설계", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "발사체 추진기관 설계", "weight": 0.95, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "고체연료 추진제 조성비", "weight": 0.95, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "위성 추력기 설계", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "재진입 캡슐 열차폐", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "정밀유도 알고리즘", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "관성항법장치 설계", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "능동위상배열 레이더", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "원자로 핵심 설계자료", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "핵연료 농축 공정", "weight": 0.95, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "SMR 노심 설계", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "방사성 동위원소 분리법", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "원전 사고 시나리오 분석", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "TS", "keyword": "FPSO 핵심 설계도", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "심해 시추 핵심기술", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "촉매 조성 노하우", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "정밀 광학 시스템 설계", "weight": 0.9, "factor": "ECONOMIC_VALUE"},
    {"grade": "TS", "keyword": "스텔스 도료 조성", "weight": 0.95, "factor": "LEAK_IMPACT"},

    # --- S1 (1급 비밀 / 산업기술보호 대상) ---
    {"grade": "S1", "keyword": "선급 검사 미공개 도면", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "선체 블록 공정도", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "수중 음향센서 사양", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "위성 자세제어 SW", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "지상국 통신 프로토콜", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "위성 영상 처리 알고리즘", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "항공기 풍동시험 데이터", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "엔진 블레이드 설계", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "항공정밀 부품 공차", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "비행제어 펌웨어", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "원전 운영 절차서 비공개", "weight": 0.85, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S1", "keyword": "원전 비상 운전 시나리오", "weight": 0.85, "factor": "LEAK_IMPACT"},
    {"grade": "S1", "keyword": "수소 저장 핵심 소재", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "양자점 태양전지 조성", "weight": 0.85, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "전해질 첨가제 배합", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "촉매 활성 데이터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "납사 분해 공정 파라미터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "정유 catalyst 수명 데이터", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "정밀 가공 노하우", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S1", "keyword": "심해 케이블 매설 공법", "weight": 0.8, "factor": "ECONOMIC_VALUE"},

    # --- S2 (2급 대외비 / 사업·운영 정보) ---
    {"grade": "S2", "keyword": "조선소 생산 일정", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "수주 가격 협상안", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "선박 발주 협의록", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "발사 일정 사전 정보", "weight": 0.8, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "위성 운용 스케줄", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "지상국 운영 매뉴얼 내부본", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "항공사 정비 매뉴얼 내부판", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "MRO 정비 단가표", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "항공정밀 수율 데이터", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "원전 유지보수 계획", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "신재생 발전 입찰 견적", "weight": 0.8, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "수소 사업 사업계획서", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "ESS 운영 데이터 내부본", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "정유 가동 효율 보고서", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "석유화학 설비 가동률", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "원유 수송 일정", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "탱크 운영 점검 기록", "weight": 0.7, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "해상 풍력 평가 데이터", "weight": 0.75, "factor": "ECONOMIC_VALUE"},
    {"grade": "S2", "keyword": "위성 영상 내부 카탈로그", "weight": 0.75, "factor": "MANAGEMENT_LEVEL"},
    {"grade": "S2", "keyword": "항공 노선 수익성 분석", "weight": 0.8, "factor": "ECONOMIC_VALUE"},

    # --- S3 (3급 공개 / 일반 공시) ---
    {"grade": "S3", "keyword": "조선업 시장 동향 공개", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "해운 운임 지수 공개", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "해양수산부 공시", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "발사 성공 보도자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "위성 발사 공식 발표", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "한국항공우주연구원 공개자료", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "항공산업 백서 공개판", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "국토부 항공 통계 공개", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "원전 가동률 공시", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "신재생에너지 통계 공개", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "전력 거래량 공개자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "에너지경제연구원 공개 보고서", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "정유사 분기 IR 자료", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "석유협회 공개 통계", "weight": 0.75, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "국제유가 공개 동향", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "OPEC 공시자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "해양플랜트 전시회 자료", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "에어쇼 공개 카탈로그", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "수소경제 백서 공개판", "weight": 0.7, "factor": "NON_PUBLICITY"},
    {"grade": "S3", "keyword": "원자력안전위원회 공시", "weight": 0.75, "factor": "NON_PUBLICITY"},
]
