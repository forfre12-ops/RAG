"""
TS 후보 문서 생성 + AI(codex) 1차 검수.

공개 법령 근거:
  - 산업기술보호법 제2조 (산업기술·국가핵심기술 정의), 제36조 (해외유출 형사처벌)
  - 국가핵심기술 지정 등에 관한 고시 (과기정통부·산업부)
  - 영업비밀보호법 제2조 (비공지성·경제적 유용성·합리적 비밀관리)
  - 자본시장법 제174조 (미공개정보이용 금지)
  - 개인정보보호법 제28조의2 (가명정보 처리 기준)
  - 수소경제법·에너지기술개발촉진법 (에너지 기술 보호)

label_source  : "codex_review"  — human_review를 대체하지 않음
reviewer_id   : "codex"
requires_human_signoff: true  — 운영 gold 편입 전 사람 최종 확인 권고

사용:
  python scripts/build_ts_candidate_set.py
  python scripts/build_ts_candidate_set.py --no-merge   # gold_real 병합 건너뜀
  python scripts/build_ts_candidate_set.py --out-dir datasets/ts_candidate
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ──────────────────────────────────────────────────────────────────────────────
# 품질 검사 패턴
# ──────────────────────────────────────────────────────────────────────────────
SYNTHETIC_MARKERS = re.compile(
    r"\[가상기업[A-Z가-힣]\]|\[등급분류\]|\[기밀자료\]|\[내부자료\]|\[등급코드\]"
)
EXPLICIT_GRADE_LEAK = re.compile(
    r"\b(?:TS|S1|S2|S3)\s*(?:등급|수준|분류)\b"
    r"|\b(?:특급기밀|1급\s*비밀)\s*(?:문서|자료|등급)\b"
)

# ──────────────────────────────────────────────────────────────────────────────
# TS 후보 문서 (20건) — 공개 법령 기준 기반, AI 생성·adjudicate
# ──────────────────────────────────────────────────────────────────────────────
CANDIDATES: list[dict] = [
    # ── 반도체 ──────────────────────────────────────────────────────────────
    {
        "title": "DRAM 1x nm 공정 CVD 핵심 파라미터 변경 검토서 (Rev.3)",
        "body": (
            "본 문서는 DRAM 1x nm 공정 CVD(Chemical Vapor Deposition) 핵심 파라미터 변경 검토 결과를 기술한다.\n\n"
            "[변경 항목]\n"
            "1. Gate Oxide 증착 온도: 840°C → 820°C (±5°C 허용)\n"
            "2. 반응 가스 유량: SiH4 200 sccm → 185 sccm, N2O 400 sccm → 420 sccm\n"
            "3. 챔버 압력: 0.28 Torr → 0.31 Torr\n"
            "4. 증착 시간: 240 sec → 225 sec\n\n"
            "[변경 목적]\n"
            "인터레이어 유전체(ILD) 막 균일도 ±1.5% 이내 달성으로 비트 불량률 현재 대비 23% 저감.\n\n"
            "[리스크 평가]\n"
            "해당 파라미터 조합은 500회 이상 공정 실험으로 최적화된 당사 고유 노하우임.\n"
            "타사 동일 노드 대비 수율 차이의 핵심 원인으로 추정.\n"
            "외부 유출 시 경쟁사가 해당 노드 진입장벽을 최소 18개월 단축 가능.\n\n"
            "[배포 제한]\n"
            "공정팀 책임자급(A직급 이상) + 본 검토 참여자 한정. 전사 메일·클라우드 전송 금지.\n"
            "작성: 공정개발팀 수석연구원 / 승인: 반도체사업부장"
        ),
        "domain": "semiconductor",
        "document_type": "공정검토보고서",
        "source_basis": "industrial_technology_protection_law|national_core_tech_notice_semiconductor",
        "why_ts": [
            "국가핵심기술(반도체 분야) 지정 대상 공정 파라미터",
            "500회 이상 실험으로 최적화된 비공개 노하우",
            "유출 시 경쟁사가 18개월 이상의 기술 격차 해소 가능",
        ],
        "key_phrases": [
            "500회 이상 공정 실험으로 최적화된 당사 고유 노하우",
            "외부 유출 시 경쟁사가 해당 노드 진입장벽을 최소 18개월 단축",
        ],
        "review_status": "accepted",
    },
    {
        "title": "차세대 HBM 스택 식각 선택비 최적화 실험보고서",
        "body": (
            "HBM(High Bandwidth Memory) 3세대 스택 구조 W/TiN/SiO2 다층막 식각 선택비 최적화 실험 결과.\n\n"
            "[실험 조건]\n"
            "장비: ICP-RIE (Inductively Coupled Plasma)\n"
            "기존 가스계: SF6/CHF3/Ar = 40/20/15 sccm\n"
            "신규 가스계: SF6/CHF3/C4F8/Ar = 35/18/8/12 sccm\n"
            "RF Power: 800 W (Coil) / 250 W (Bias)\n"
            "식각 선택비 결과: W:TiN:SiO2 = 28:1:0.4 (기존 22:1:0.8 대비 대폭 개선)\n\n"
            "[핵심 발견]\n"
            "C4F8 소량 첨가로 측벽 폴리머 형성 제어 → 프로파일 수직도 87° → 91° 개선.\n"
            "해당 파라미터 조합은 당사 특허 출원 예정이며, 출원 전 기밀 유지 필수.\n\n"
            "[경쟁 기술 비교]\n"
            "공개 문헌 분석 결과, 주요 경쟁사는 이 선택비를 달성하지 못한 것으로 판단됨.\n"
            "본 실험 결과 유출 시 차세대 HBM 양산 타임라인 경쟁 구도가 역전될 우려가 있음.\n\n"
            "배포: R&D 본부장 승인 하 HBM 공정팀 내부 한정."
        ),
        "domain": "semiconductor",
        "document_type": "실험보고서",
        "source_basis": "industrial_technology_protection_law|trade_secret_protection_law",
        "why_ts": [
            "특허 출원 전 비공개 핵심 공정 파라미터",
            "경쟁사 기술 격차의 직접적 원인이 되는 비공개 수치",
            "유출 시 HBM 양산 경쟁 구도 역전 가능",
        ],
        "key_phrases": [
            "특허 출원 예정이며, 출원 전 기밀 유지 필수",
            "본 실험 결과 유출 시 차세대 HBM 양산 타임라인 경쟁 구도가 역전",
        ],
        "review_status": "accepted",
    },

    # ── 배터리 ──────────────────────────────────────────────────────────────
    {
        "title": "차세대 NMC 양극재 조성비 및 코팅 공정 기밀 보고서",
        "body": (
            "전기차용 고에너지밀도 양극재(NMC 9xx 계열) 최적 조성비 및 코팅 공정 파라미터.\n\n"
            "[양극재 조성]\n"
            "Ni:Mn:Co = 90:5:5 (mol%) — 기존 대비 Ni 함량 3%p 상향\n"
            "도펀트: Al 0.3 mol%, Ti 0.2 mol% (당사 고유 2원 도핑 시스템)\n\n"
            "[코팅 공정]\n"
            "1차 코팅(Al2O3): ALD 사이클 3회, 온도 180°C\n"
            "2차 코팅(LiAlO2): 슬러리 농도 0.8 wt%, 건조 온도 700°C / 4 h\n"
            "입자 크기: D50 = 10.2 μm ± 0.3 μm\n\n"
            "[성능 결과]\n"
            "초기 용량: 218 mAh/g (주요 경쟁사 추정 대비 +8 mAh/g)\n"
            "1000사이클 후 용량 유지율: 91.3% (경쟁사 공개값 대비 +5%p)\n\n"
            "[기밀 수준]\n"
            "Ni 함량 및 2원 도핑 수치는 특허 출원에 의도적으로 광범위하게 기재.\n"
            "실제 양산 파라미터는 본 문서에만 수록되며, 영업비밀로 별도 관리됨.\n"
            "배포: 배터리사업부 CEO 직속 보고선 한정."
        ),
        "domain": "battery",
        "document_type": "기밀보고서",
        "source_basis": "trade_secret_protection_law|industrial_technology_protection_law",
        "why_ts": [
            "특허에 의도적으로 누락된 실제 양산 파라미터 포함",
            "경쟁사 대비 핵심 우위 수치가 명시된 영업비밀",
            "유출 시 전기차 배터리 시장 경쟁 우위 즉시 소멸",
        ],
        "key_phrases": [
            "실제 양산 파라미터는 본 문서에만 수록되며, 영업비밀로 별도 관리",
            "당사 고유 2원 도핑 시스템",
        ],
        "review_status": "accepted",
    },
    {
        "title": "전고체 전지 황화물 고체전해질 핵심 합성 조건 보고서",
        "body": (
            "황화물계 고체전해질(Li6PS5Cl, 아르지로다이트 구조) 대량 합성을 위한 핵심 파라미터.\n\n"
            "[합성 조건]\n"
            "출발물질: Li2S + P2S5 + LiCl, 몰비: 5.5:1:1 (당사 최적화 비율)\n"
            "볼밀: Ar 분위기, ZrO2 볼, 200 rpm × 8 h\n"
            "소결: 180°C / 4 h (진공), 냉각 속도 2°C/min\n\n"
            "[이온전도도 결과]\n"
            "실온(25°C): 6.8 × 10⁻³ S/cm — 업계 최고 수준 (공개 논문 최고값 5.2 × 10⁻³ 대비)\n"
            "활성화 에너지: 0.18 eV\n\n"
            "[기밀 포인트]\n"
            "당사 발표 논문에는 몰비 및 냉각 속도를 의도적으로 변형하여 게재.\n"
            "본 문서의 수치가 실제 양산 기준값이며, 유출 시 경쟁사의 전고체 전지 상용화가 최소 2년 앞당겨질 수 있음.\n\n"
            "작성: 전해질연구팀 / 배포: 전고체사업단장 이하 5인."
        ),
        "domain": "battery",
        "document_type": "실험보고서",
        "source_basis": "trade_secret_protection_law|national_core_tech_notice_battery",
        "why_ts": [
            "논문에 의도적으로 변형된 수치의 실제값 포함",
            "전고체 전지 국가핵심기술 연관 공정 파라미터",
            "유출 시 경쟁사 상용화 2년 단축 가능 — 산업 게임체인저 수준 영향",
        ],
        "key_phrases": [
            "몰비 및 냉각 속도를 의도적으로 변형하여 게재",
            "경쟁사의 전고체 전지 상용화가 최소 2년 앞당겨질",
        ],
        "review_status": "accepted",
    },

    # ── 보안·암호키 ─────────────────────────────────────────────────────────
    {
        "title": "HSM 마스터키 생성 및 주기적 교체 절차서 (운영 기밀)",
        "body": (
            "하드웨어보안모듈(HSM) 마스터키(MK) 생성·교체 절차. FIPS 140-2 Level 3 장비 기준.\n\n"
            "[마스터키 생성 절차]\n"
            "1. 키 생성 의식(Key Ceremony): 보안관제실 격리 구역(패러데이 케이지) 내 실시\n"
            "2. 참석 의무: CISO + 법무팀장 + 외부 감사인 2인 (쿼럼 4/6)\n"
            "3. 키 컴포넌트: 6분할 → 각 담당자 물리 스마트카드 별도 보관\n"
            "4. 알고리즘: RSA-4096 / AES-256-GCM 래핑\n\n"
            "[교체 주기]\n"
            "정기 교체: 24개월\n"
            "비정기 교체 사유: 컴포넌트 담당자 퇴사·이직 즉시 / 보안 사고 의심 시 24시간 내\n\n"
            "[비상 복구]\n"
            "복구 쿼럼: 4/6 컴포넌트 필요\n"
            "복구 절차: 별도 봉인 문서(SEC-MK-REC-001) 보관 (물리 금고, 위치 미기재)\n\n"
            "본 절차서 자체가 공격 벡터가 될 수 있으므로 전자 복사·전송 금지.\n"
            "인쇄본 2부 한정, 서명 관리."
        ),
        "domain": "security",
        "document_type": "절차서",
        "source_basis": "isms_p_control|fips_140_2",
        "why_ts": [
            "HSM 마스터키 관리 절차 자체가 공격 설계에 활용될 수 있는 정보",
            "키 컴포넌트 분할·쿼럼 구조 노출 시 물리적 공격 설계 가능",
            "유출 시 전사 암호화 인프라 전체가 위협에 노출",
        ],
        "key_phrases": [
            "본 절차서 자체가 공격 벡터가 될 수 있으므로",
            "키 컴포넌트: 6분할 → 각 담당자 물리 스마트카드 별도 보관",
        ],
        "review_status": "accepted",
    },
    {
        "title": "Root CA 개인키 보관 및 긴급 복구 절차 (최고 기밀)",
        "body": (
            "당사 PKI 최상위 인증기관(Root CA) 개인키 관리 절차.\n\n"
            "[오프라인 Root CA 구성]\n"
            "시리얼: PKI-ROOTCA-001, 002 (active/backup)\n"
            "부팅 환경: Air-gapped, 전용 HSM (Thales nShield)\n"
            "유효기간: 20년 / 키 알고리즘: ECDSA P-384\n\n"
            "[서명 권한]\n"
            "Root CA 서명: 연간 최대 4회 한정.\n"
            "서명 수행 시: CISO 승인 + 보안팀장 동반 + 외부 감사인 참관 필수.\n\n"
            "[개인키 분실·유출 시 영향]\n"
            "즉시 CRL 발행 및 전체 Intermediate CA 교체 → 서비스 장애 최소 72시간 예상.\n"
            "ISMS-P 인증 취소 가능성.\n"
            "당사 디지털 서비스 인증서 전체 무효화 → 서비스 존립 위기.\n\n"
            "배포: CISO 단독 보관. 복사 금지."
        ),
        "domain": "security",
        "document_type": "절차서",
        "source_basis": "isms_p_control|pki_operations",
        "why_ts": [
            "Root CA 개인키 관리 절차 — 유출 시 전체 PKI 신뢰 체계 붕괴",
            "서비스 전체 인증서 무효화로 회사 존립 위기 가능",
            "ISMS-P 인증 취소 및 규제 제재 직결",
        ],
        "key_phrases": [
            "당사 디지털 서비스 인증서 전체 무효화 → 서비스 존립 위기",
            "Root CA 서명: 연간 최대 4회 한정",
        ],
        "review_status": "accepted",
    },

    # ── M&A ─────────────────────────────────────────────────────────────────
    {
        "title": "[비공개] 반도체 소재 기업 인수 실사 가격 산정 보고서",
        "body": (
            "대상 기업: 반도체 소재 분야 비상장 기업 (코드명: 알파)\n"
            "실사 기간: 2024 Q3 / 주관: 전략기획팀 + 외부 자문사 (NDA 체결)\n\n"
            "[밸류에이션 요약]\n"
            "DCF 기준 적정가치: 4,200억원 ~ 5,100억원\n"
            "경쟁 입찰 예상가: 5,800억원 ~ 6,500억원 (복수 후보 입찰 예상)\n"
            "당사 내부 합의 가격 상한: 6,100억원 (이사회 비공개 결의)\n\n"
            "[M&A 전략 근거]\n"
            "대상 기업 보유 IP(특허 127건) 및 핵심 인력(박사 43인)이 목표.\n"
            "기술 내재화 3년 단축 가능. 경쟁사가 먼저 인수 시 당사 핵심 공급망 위협.\n\n"
            "[기밀 수준]\n"
            "입찰가·전략 의도 유출 시 협상 실패 또는 가격 상승으로 수천억 손실 가능.\n"
            "이사회 결의 전 외부 공개 시 자본시장법 제174조 미공개정보이용 위반.\n\n"
            "배포: 대표이사, CFO, 전략기획실장 한정."
        ),
        "domain": "business",
        "document_type": "실사보고서",
        "source_basis": "capital_markets_act_174|trade_secret_protection_law",
        "why_ts": [
            "진행 중 M&A 입찰 상한가 — 유출 시 수천억 협상 손실",
            "자본시장법 제174조 미공개정보이용 위반 리스크",
            "이사회 비공개 결의 내용 포함",
        ],
        "key_phrases": [
            "당사 내부 합의 가격 상한: 6,100억원 (이사회 비공개 결의)",
            "자본시장법 제174조 미공개정보이용 위반",
        ],
        "review_status": "accepted",
    },
    {
        "title": "[비공개] 인수 후 통합계획(PMI) 비공개 실행 로드맵 — 프로젝트 알파",
        "body": (
            "인수 완료 가정 시 PMI(Post-Merger Integration) 실행 계획.\n\n"
            "[D+30 — 초기 안정화]\n"
            "핵심 인력 리텐션 패키지 집행: 박사급 43인 × 평균 1.2억원 = 약 52억원\n"
            "IP 이전 및 특허 귀속 절차 착수\n"
            "조직 보고 체계: 당사 반도체사업부 산하 편입\n\n"
            "[D+90 — 공정 내재화]\n"
            "핵심 공정 레시피 이전: 보안 전송 채널(HSM 암호화) 사용\n"
            "내재화 우선순위 기술: 별도 기밀 문서에 기재\n"
            "특허 풀 통합 및 방어 특허 추가 출원\n\n"
            "[D+180 — 시너지 실현]\n"
            "양산 적용 목표 기술 3건 / 예상 원가 절감: 연간 380억원\n\n"
            "[비공개 사유]\n"
            "본 계획 유출 시 핵심 인력 이탈 가속 또는 경쟁 입찰자 대항 제안 가능.\n"
            "인수 무산 시 당사 전략 방향 전체 노출."
        ),
        "domain": "business",
        "document_type": "전략계획서",
        "source_basis": "capital_markets_act_174|trade_secret_protection_law",
        "why_ts": [
            "진행 중 M&A 통합 전략 전체 — 유출 시 인수 실패 가능",
            "핵심 인력 유지 비용·공정 이전 전략 포함",
            "경쟁사에게 전략 방향 전체 노출",
        ],
        "key_phrases": [
            "본 계획 유출 시 핵심 인력 이탈 가속 또는 경쟁 입찰자 대항 제안 가능",
            "리텐션 패키지 집행: 박사급 43인",
        ],
        "review_status": "accepted",
    },

    # ── 방산·국가핵심기술 ─────────────────────────────────────────────────
    {
        "title": "정밀유도무기 탐색기 추적 알고리즘 설계 검토 메모",
        "body": (
            "대상: IR(적외선) 탐색기 추적 알고리즘 Rev. 2.1\n"
            "적용 근거: 산업기술보호법 제2조 국가핵심기술 (방위산업 분야)\n\n"
            "[알고리즘 개요]\n"
            "추적 방식: 비선형 칼만필터 기반 예측 추적\n"
            "클러터 제거: 적응형 CFAR + 딥러닝 보조 식별\n"
            "처리 지연: 8 ms 이하 (임베디드 FPGA 구현)\n\n"
            "[기술 수준]\n"
            "공개 문헌 최고 수준 대비: 추적 정밀도 +18%, 허위 경보율 -42%.\n"
            "격차의 핵심은 당사 독자 학습데이터셋(비공개 시험장 영상 10만 프레임)에서 비롯.\n\n"
            "[보안 등급]\n"
            "산업기술보호법 제36조 적용 대상.\n"
            "해외 반출 시 산업부 장관 승인 필수. 유출 시 징역 15년 이하·벌금 15억 이하.\n"
            "내부 배포: 방산사업부장 승인 명단 한정 (보안서약서 징구 완료).\n\n"
            "본 메모는 열람 후 당일 파쇄 또는 반납."
        ),
        "domain": "defense",
        "document_type": "설계검토메모",
        "source_basis": "industrial_technology_protection_law_36|defense_industry_tech",
        "why_ts": [
            "산업기술보호법 제36조 국가핵심기술 — 해외 유출 시 형사처벌",
            "군사 기술 수준의 추적 알고리즘 비공개 파라미터",
            "국가 안보에 직결되는 방위산업 핵심 기술",
        ],
        "key_phrases": [
            "산업기술보호법 제36조 적용 대상",
            "유출 시 징역 15년 이하·벌금 15억 이하",
            "비공개 시험장 영상 10만 프레임",
        ],
        "review_status": "accepted",
    },
    {
        "title": "전투기 레이돔 전파흡수소재(RAM) 코팅 공정 기밀 사양",
        "body": (
            "대상: 4.5세대 전투기 레이돔 적용 전파흡수소재(RAM) 코팅 공정 사양\n"
            "적용 근거: 방산기술보호법 및 산업기술보호법 국가핵심기술\n\n"
            "[소재 구성]\n"
            "베이스 수지: 에폭시 계열 (MIL-A-46146 기준)\n"
            "흡수체: Fe-Si합금 플레이크 + BaTiO3 나노입자 (혼합 비율: 별도 문서 관리)\n\n"
            "[코팅 공정 파라미터]\n"
            "도막 두께: 2.8 ± 0.2 mm\n"
            "경화 온도 프로파일: 60°C/2h → 120°C/4h → 150°C/2h\n"
            "표면 조도: Ra ≤ 0.8 μm (코팅 전)\n\n"
            "[레이더 흡수 성능]\n"
            "8~12 GHz(X밴드) 반사 손실: 18 dB 이상 (당사 달성치)\n"
            "공개 논문 최고치: 약 12 dB → 당사 우위 명확\n\n"
            "본 사양 유출 시 적국 레이더 대응 능력 강화에 직접 활용될 수 있음.\n"
            "군 보안규정 및 산업기술보호법 동시 적용."
        ),
        "domain": "defense",
        "document_type": "기밀사양서",
        "source_basis": "defense_industry_act|industrial_technology_protection_law",
        "why_ts": [
            "국가 안보에 직결되는 전투기 스텔스 소재 공정 파라미터",
            "군 보안규정 및 산업기술보호법 동시 적용 대상",
            "적국 레이더 대응 능력 강화에 악용 가능",
        ],
        "key_phrases": [
            "적국 레이더 대응 능력 강화에 직접 활용될 수 있음",
            "군 보안규정 및 산업기술보호법 동시 적용",
        ],
        "review_status": "accepted",
    },

    # ── 제약·바이오 ─────────────────────────────────────────────────────────
    {
        "title": "신약 후보물질 GXC-1047 합성 경로 및 스케일업 파라미터",
        "body": (
            "후보물질: GXC-1047 (EGFR 선택적 억제제, 3세대 계열)\n"
            "임상 단계: IND 신청 준비 중 — 외부 미공개\n\n"
            "[핵심 합성 경로]\n"
            "Step 1: Buchwald-Hartwig 아민화 반응\n"
            "  촉매: Pd2(dba)3 1 mol%, XPhos 2 mol%\n"
            "  용매: 톨루엔/디옥산 = 3:1, 조건: 100°C, 12 h, Ar 분위기\n"
            "  수율: 89% (문헌 수율 73% 대비 당사 최적화 결과)\n\n"
            "Step 2: Pd 제거 및 정제\n"
            "  SiliaBond Thiol 처리 (Pd 잔류량 < 5 ppm 달성)\n"
            "  결정화: EtOH/H2O = 7:3, 4°C overnight\n\n"
            "[스케일업 포인트]\n"
            "Step 1 열 관리: 10L 반응기 기준 냉각속도 제어가 핵심 — 이탈 시 수율 급락.\n"
            "당사 파일럿 배치 12건 데이터 기반 최적화 결과.\n\n"
            "[기밀 사유]\n"
            "유출 시 제네릭 제약사가 합성 장벽 없이 복제 착수 가능.\n"
            "IND 신청 전 외부 유출은 특허 우선권 상실 위험.\n"
            "배포: CMC 팀장 + CTO 한정."
        ),
        "domain": "pharma",
        "document_type": "합성보고서",
        "source_basis": "trade_secret_protection_law|patent_law_priority",
        "why_ts": [
            "IND 신청 전 비공개 신약 후보물질 합성 경로",
            "특허 우선권 상실 위험 — 유출 시 수년간의 R&D 투자 무효화",
            "제네릭 제약사 즉시 복제 가능",
        ],
        "key_phrases": [
            "IND 신청 준비 중 — 외부 미공개",
            "유출 시 제네릭 제약사가 합성 장벽 없이 복제 착수 가능",
            "특허 우선권 상실 위험",
        ],
        "review_status": "accepted",
    },
    {
        "title": "바이오시밀러 세포주 (CHO-K1-GXB77) 핵심 배양 파라미터 기밀 문서",
        "body": (
            "제품: GXB77 (항-HER2 항체 바이오시밀러)\n"
            "세포주: CHO-K1 기반 안정화 클론 #3 (당사 독자 스크리닝)\n\n"
            "[배양 핵심 파라미터]\n"
            "시드 배양: 37°C, 5% CO2, DO 40%, pH 7.0~7.2\n"
            "생산 배양 전환점: VCC 3.5×10⁶ cells/mL 도달 시 온도 34°C로 시프트\n"
            "→ 온도 시프트 조건은 당사 고유 노하우 (특허 출원 미완료)\n"
            "피드 전략: 커스텀 농축배지 F-22 (배합 비율: 별도 기밀 문서 관리)\n"
            "생산성: 7.2 g/L (경쟁사 공개 데이터 대비 +35%)\n\n"
            "[정제 핵심 조건]\n"
            "단백질 A 컬럼: pH 3.6 용출 (30분 유지) → 바이러스 불활화 병행\n"
            "HCP 잔류: 10 ppm 미만 (규제 기준 25 ppm 대비 여유)\n\n"
            "[기밀 근거]\n"
            "본 파라미터는 당사 바이오의약품 원가 경쟁력의 핵심.\n"
            "경쟁사 대비 생산성 35% 우위의 원천 — 세포주 및 배양 조건 유출 시 10년 R&D 투자 가치 즉시 소멸."
        ),
        "domain": "pharma",
        "document_type": "기밀보고서",
        "source_basis": "trade_secret_protection_law|biological_product_regulation",
        "why_ts": [
            "특허 미완료 상태의 핵심 세포주 배양 파라미터",
            "10년 R&D 투자의 핵심 노하우 — 유출 시 즉시 소멸",
            "경쟁사 대비 35% 생산성 우위의 원천",
        ],
        "key_phrases": [
            "세포주 및 배양 조건 유출 시 10년 R&D 투자 가치 즉시 소멸",
            "당사 고유 노하우 (특허 출원 미완료)",
        ],
        "review_status": "accepted",
    },

    # ── AI·모델 ─────────────────────────────────────────────────────────────
    {
        "title": "초대형 언어모델 (LLM-7B) 가중치 및 학습 데이터 접근 통제 정책",
        "body": (
            "대상: 당사 독자 개발 LLM (파라미터 7B, 한국어 특화)\n"
            "관련 자산: 모델 가중치(checkpoint), 학습 데이터셋, RLHF 피드백 데이터\n\n"
            "[접근 통제 등급]\n"
            "Tier-1 (최고 제한): 모델 전체 가중치 (최종·중간 체크포인트)\n"
            "  접근 허용: CTO, AI연구소장, 보안관제실장 (3인)\n"
            "  저장: Air-gapped 서버, HSM 암호화, 인터넷 차단\n"
            "  반출: 불가 — 내부 인퍼런스 전용 환경에서만 사용\n\n"
            "Tier-2 (내부 한정): RLHF 피드백 데이터, 커스텀 instruction tuning 데이터\n"
            "  접근 허용: 팀장급 이상 + 개인정보처리 교육 수료자\n"
            "  암호화: AES-256-GCM, 키는 HSM 관리\n\n"
            "[침해 시나리오 및 영향]\n"
            "모델 가중치 유출 → 무단 복제 서비스 운영 가능, 당사 SaaS 수익 즉시 잠식.\n"
            "학습 데이터 유출 → 개인정보 노출, ISMS-P 위반, 과태료 및 신뢰 손상.\n\n"
            "이 정책 문서 자체도 Tier-2 취급 — 공격자가 접근 구조를 파악하면 우회 공격 설계 가능."
        ),
        "domain": "ai",
        "document_type": "접근통제정책",
        "source_basis": "isms_p_control|personal_information_protection_act",
        "why_ts": [
            "모델 가중치 유출 시 SaaS 수익 기반 즉시 소멸",
            "학습 데이터 유출 시 개인정보보호법 위반 및 ISMS-P 취소",
            "접근 통제 구조 자체가 공격 설계에 활용 가능",
        ],
        "key_phrases": [
            "모델 가중치 유출 → 무단 복제 서비스 운영 가능, 당사 SaaS 수익 즉시 잠식",
            "이 정책 문서 자체도 Tier-2 취급",
        ],
        "review_status": "accepted",
    },
    {
        "title": "LLM 사전학습 데이터셋 구성 및 개인정보 처리 내부 지침 (비공개)",
        "body": (
            "데이터셋명: KorpusLLM-v2 (당사 독자 구축, 외부 미공개)\n\n"
            "[구성 요약]\n"
            "총 토큰: 약 1.8조\n"
            "구성: 공개 데이터 40% + 비공개 라이선스 계약 데이터 35% + 당사 자체 데이터 25%\n"
            "비공개 계약 데이터 출처 목록: 별도 기밀 문서 (계약서 포함)\n"
            "자체 데이터: 당사 서비스 이용 로그 (비식별화 처리)\n\n"
            "[비식별화 방법론]\n"
            "개인정보보호법 제28조의2 기준 적용 (가명정보)\n"
            "처리 알고리즘: k=50 익명화 + 텍스트 재구성 모델 적용\n"
            "미처리 잔류 위험: 고유명사 0.003% 추정 (내부 인정, 외부 미공개)\n\n"
            "[민감 사항]\n"
            "잔류 위험 0.003% 수치: 외부 공개 시 개인정보 유출 과태료 (매출액 3% 이하) 리스크.\n"
            "비공개 라이선스 계약 목록 유출 시 계약 위반, 거액 배상 가능성.\n"
            "당사 모델 데이터 구성 전략이 경쟁사에 노출되는 이중 피해."
        ),
        "domain": "ai",
        "document_type": "내부지침",
        "source_basis": "personal_information_protection_act_28_2|trade_secret_protection_law",
        "why_ts": [
            "비공개 라이선스 계약 출처 목록 포함 — 유출 시 계약 위반",
            "내부 인정 잔류 위험 수치 외부 공개 시 규제 제재",
            "당사 모델 학습 전략 전체 노출",
        ],
        "key_phrases": [
            "고유명사 0.003% 추정 (내부 인정, 외부 미공개)",
            "외부 공개 시 개인정보 유출 과태료 (매출액 3% 이하) 리스크",
        ],
        "review_status": "accepted",
    },

    # ── 화학·소재 ────────────────────────────────────────────────────────────
    {
        "title": "PEM 수전해 OER 촉매 (IrO2-RuO2 복합체) 특허 출원 전 기밀 합성 사양",
        "body": (
            "응용: PEM(양성자교환막) 수전해 시스템용 산소발생반응(OER) 촉매\n"
            "특허 출원 예정: 2025 Q1 — 출원 전 기밀 유지 필수\n\n"
            "[핵심 합성 조건]\n"
            "Ir:Ru 몰비: 7:3 (당사 최적화 — 발표 논문에는 6:4로 게재 예정)\n"
            "전구체: H2IrCl6 + RuCl3, 공침 pH: 9.5, 온도 80°C\n"
            "하소 조건: 400°C / 2 h (공기), 냉각 속도 5°C/min\n"
            "지지체: CNT 분산 처리 (초음파 + SDS 계면활성제 0.5 wt%)\n\n"
            "[성능 결과]\n"
            "OER 과전압 (10 mA/cm²): 238 mV (IrO2 단독 290 mV 대비 -52 mV)\n"
            "안정성: 1000시간 연속 운전, 활성 저하 5% 미만\n\n"
            "[기밀 포인트]\n"
            "Ir:Ru 실제 비율 및 하소 조건이 성능 핵심.\n"
            "논문에 의도적으로 다른 값 게재 예정 — 이 파일이 실제 양산 기준값.\n"
            "유출 시 특허 우선권 상실 및 경쟁사 즉시 재현 가능."
        ),
        "domain": "chemical",
        "document_type": "합성사양서",
        "source_basis": "trade_secret_protection_law|patent_law_priority",
        "why_ts": [
            "논문에 의도적으로 누락된 실제 합성 파라미터 포함",
            "특허 출원 전 비공개 — 유출 시 우선권 상실",
            "수소경제 핵심 기술 경쟁 우위의 원천",
        ],
        "key_phrases": [
            "논문에 의도적으로 다른 값 게재 예정 — 이 파일이 실제 양산 기준값",
            "유출 시 특허 우선권 상실 및 경쟁사 즉시 재현 가능",
        ],
        "review_status": "accepted",
    },

    # ── 디스플레이 ───────────────────────────────────────────────────────────
    {
        "title": "QD-OLED 발광층 핵심 유기 소재 배합 비율 및 증착 조건 기밀 사양",
        "body": (
            "대상 제품: QD-OLED 65인치 프리미엄 패널 (양산 라인 G8.5)\n"
            "핵심 자산: 발광층(EML) 호스트-도판트 배합 비율 및 증착 조건\n\n"
            "[EML 구성 (B 픽셀 기준)]\n"
            "Host 1: CBP 계열 신규 화합물 (내부 코드: EH-217, 합성법 별도 기밀문서)\n"
            "Host 2: mCP 계열 (몰비: Host1:Host2 = 65:35)\n"
            "도판트: FIrpic 계열 Ir 착화합물 (농도: 8.0 wt%)\n\n"
            "[증착 조건]\n"
            "증착 온도(도판트): 215°C ± 2°C\n"
            "공증착 속도비: Host:Dopant = 15:1 Å/s\n"
            "진공도: 5×10⁻⁷ Torr 이하\n\n"
            "[경쟁 우위]\n"
            "당사 달성 효율: 40 cd/A (T50 수명: 60,000 h)\n"
            "경쟁사 공개값: 최대 32 cd/A\n\n"
            "본 배합 비율은 5년 이상 연구개발의 핵심 성과.\n"
            "유출 시 경쟁사가 당사 기술 격차를 2~3년 내 따라잡을 수 있음."
        ),
        "domain": "display",
        "document_type": "기밀사양서",
        "source_basis": "trade_secret_protection_law|industrial_technology_protection_law",
        "why_ts": [
            "5년 이상 연구의 핵심 성과 — 유출 시 2~3년 기술 격차 해소",
            "경쟁사 공개값 대비 25% 우위의 원천 수치",
            "양산 라인 적용 기밀 공정 파라미터",
        ],
        "key_phrases": [
            "유출 시 경쟁사가 당사 기술 격차를 2~3년 내 따라잡을 수 있음",
            "5년 이상 연구개발의 핵심 성과",
        ],
        "review_status": "accepted",
    },

    # ── 금융·대형 M&A ────────────────────────────────────────────────────────
    {
        "title": "[극비] 대형 플랫폼 기업 인수 자금 조달 전략 — 프로젝트 베타",
        "body": (
            "인수 대상: 국내 1위 플랫폼 기업 (비공개 협의 중, 상호 NDA 체결)\n"
            "인수 규모 추정: 18조원 ~ 22조원\n\n"
            "[자금 조달 구조]\n"
            "1. 자기자금: 4조원 (현금 2.5조 + 자산 매각 1.5조)\n"
            "2. 인수금융(LBO): 10조원 (주관: 외국계 IB 3사, 금리 협의 중)\n"
            "3. 전략적 투자자(SI): 4조원 (대상 투자자: 비공개 리스트)\n"
            "4. 가교대출: 2조원, 만기 18개월\n\n"
            "[내부 손익 시뮬레이션]\n"
            "IRR 목표: 18% (기본 시나리오), 12% (스트레스 시나리오)\n"
            "회수 기간: 7년 (자산 분리 매각 포함)\n\n"
            "[기밀 수준 및 규제 리스크]\n"
            "본 정보 유출 시: 인수 협상 실패 + 자본시장법 제174조 미공개정보이용 위반(형사 처벌).\n"
            "주가 영향으로 시장질서 교란 위반 (과징금 최대 수백억).\n"
            "배포: 대표이사, CFO, 인수금융팀장 (3인 한정). 인쇄 금지."
        ),
        "domain": "finance",
        "document_type": "전략검토보고서",
        "source_basis": "capital_markets_act_174|trade_secret_protection_law",
        "why_ts": [
            "18~22조원 규모 M&A 자금 조달 전략 — 유출 시 인수 실패 및 형사 처벌",
            "자본시장법 제174조 미공개정보이용 직접 해당",
            "주가·시장질서 교란 과징금 수백억 리스크",
        ],
        "key_phrases": [
            "자본시장법 제174조 미공개정보이용 위반(형사 처벌)",
            "인수 규모 추정: 18조원 ~ 22조원",
        ],
        "review_status": "accepted",
    },

    # ── 통신·5G ─────────────────────────────────────────────────────────────
    {
        "title": "5G NR Massive MIMO 빔포밍 제어 알고리즘 비공개 사양서",
        "body": (
            "대상: 5G NR 대규모 MIMO(Massive MIMO) 빔포밍 제어 알고리즘\n"
            "적용 근거: 산업기술보호법 국가핵심기술 — 이동통신 분야\n\n"
            "[알고리즘 핵심 구조]\n"
            "채널 추정: DL-CMP 기반 딥러닝 채널 추정 (당사 고유 아키텍처, 파라미터 수: 2.3M)\n"
            "빔 스케줄링: Greedy + RL 하이브리드 정책 (에피소드 기준 학습)\n"
            "처리 지연: 2.1 ms (3GPP 기준 4 ms 이내 달성, 경쟁사 추정 3.5 ms)\n\n"
            "[기술 우위 수치]\n"
            "셀 엣지 처리량: 기존 대비 +34%\n"
            "스펙트럼 효율: 28 bps/Hz (업계 공개값 21 bps/Hz 대비)\n\n"
            "[기밀 사유]\n"
            "본 알고리즘 사양 유출 시 경쟁 통신장비 업체가 당사 기술 격차를 3~5년 단축 가능.\n"
            "산업기술보호법 국가핵심기술로서 해외 유출 시 국가 안보 위협 가능.\n"
            "내부 배포: 네트워크사업부장 승인 명단 한정."
        ),
        "domain": "telecom",
        "document_type": "기술사양서",
        "source_basis": "industrial_technology_protection_law|national_core_tech_notice_telecom",
        "why_ts": [
            "이동통신 국가핵심기술 — 해외 유출 시 국가 안보 위협",
            "경쟁사 대비 3~5년 기술 격차의 원천",
            "공개값 대비 33% 우위 수치 포함",
        ],
        "key_phrases": [
            "산업기술보호법 국가핵심기술로서 해외 유출 시 국가 안보 위협 가능",
            "경쟁 통신장비 업체가 당사 기술 격차를 3~5년 단축 가능",
        ],
        "review_status": "accepted",
    },

    # ── 보안·취약점 ──────────────────────────────────────────────────────────
    {
        "title": "[긴급/내부 기밀] 핵심 인프라 미패치 제로데이 취약점 분석 보고서",
        "body": (
            "취약점 대상: 당사 서비스 핵심 인프라 미들웨어 (버전 정보: 물리 문서 별도 관리)\n"
            "발견: 내부 레드팀 / 상태: 패치 개발 중 (약 D+21 배포 예정)\n\n"
            "[취약점 개요]\n"
            "유형: Heap Buffer Overflow → Remote Code Execution(RCE) 가능\n"
            "CVSS 3.1 예상 점수: 9.8 (Critical)\n"
            "익스플로잇 조건: 인증 불필요, 네트워크 접근만으로 트리거 가능\n\n"
            "[영향 범위]\n"
            "당사 서비스 처리 트래픽: 일 2.3억 건 영향\n"
            "잠재 피해: 고객 개인정보 전체 유출 + 서비스 전면 중단\n\n"
            "[기밀 유지 필수 사유]\n"
            "CVE 신고 전 본 정보 유출 시: 공격자가 즉시 악용 가능 → 고객 피해 직접 발생.\n"
            "유출자는 정보통신망법 제48조, 개인정보보호법 위반 형사책임 대상.\n"
            "패치 배포 전까지 CISO + 레드팀장 + 서비스보안팀장 (3인) 한정."
        ),
        "domain": "security",
        "document_type": "취약점분석보고서",
        "source_basis": "information_network_act_48|personal_information_protection_act",
        "why_ts": [
            "미패치 제로데이 — 유출 시 즉시 악용 가능, 고객 피해 직접 발생",
            "CVSS 9.8 Critical, 인증 없이 RCE 가능",
            "일 2.3억 건 트래픽 전체 노출 위험",
        ],
        "key_phrases": [
            "CVE 신고 전 본 정보 유출 시: 공격자가 즉시 악용 가능",
            "CVSS 3.1 예상 점수: 9.8 (Critical)",
            "인증 불필요, 네트워크 접근만으로 트리거 가능",
        ],
        "review_status": "accepted",
    },
]

# ──────────────────────────────────────────────────────────────────────────────
# 헬퍼 함수
# ──────────────────────────────────────────────────────────────────────────────

def make_text(title: str, body: str) -> str:
    return f"{title}\n\n{body}".strip()


def make_doc_id(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def find_span(text: str, phrase: str) -> tuple[int, int] | None:
    idx = text.find(phrase)
    if idx == -1:
        return None
    return (idx, idx + len(phrase))


def quality_check(item: dict) -> list[str]:
    text = make_text(item["title"], item["body"])
    issues = []
    if SYNTHETIC_MARKERS.search(text):
        issues.append("synthetic_marker")
    if EXPLICIT_GRADE_LEAK.search(text):
        issues.append("explicit_grade_leak")
    return issues


def build_evidence_spans(text: str, key_phrases: list[str], why_ts: list[str]) -> list[dict]:
    spans = []
    factors = ["NON_PUBLICITY", "ECONOMIC_VALUE", "LEAK_IMPACT", "MANAGEMENT_LEVEL"]
    for i, phrase in enumerate(key_phrases):
        sp = find_span(text, phrase)
        if sp is None:
            continue
        spans.append({
            "start": sp[0],
            "end": sp[1],
            "factor": factors[i % len(factors)],
            "reason": why_ts[i] if i < len(why_ts) else why_ts[-1],
        })
    return spans


def adjudicate(item: dict, qc_issues: list[str]) -> dict:
    review_status = item.get("review_status", "needs_human_review")
    if qc_issues:
        review_status = "needs_human_review"

    title = item["title"]
    body = item["body"]
    text = make_text(title, body)
    doc_id = make_doc_id(text)
    evidence_spans = build_evidence_spans(text, item.get("key_phrases", []), item.get("why_ts", []))

    return {
        "doc_id": doc_id,
        "text": text,
        "label": "TS",
        "label_source": "codex_review",
        "reviewer_id": "codex",
        "review_status": review_status,
        "requires_human_signoff": True,
        "source": "synthetic_grounded",
        "domain": item["domain"],
        "document_type": item["document_type"],
        "source_basis": item["source_basis"],
        "why_ts": item.get("why_ts", []),
        "evidence_spans": evidence_spans,
        "qc_issues": qc_issues,
        "notes": (
            "공개 법령 기반 AI 생성·adjudicate. "
            "운영 gold 편입 전 사람 최종 확인 권고."
        ),
    }


# ──────────────────────────────────────────────────────────────────────────────
# main
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="datasets/ts_candidate")
    p.add_argument("--gold-real", default="datasets/gold_real/classification_gold.jsonl")
    p.add_argument("--no-merge", action="store_true",
                   help="gold_real 병합 건너뜀 (기본: accepted 건 자동 병합)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_records: list[dict] = []
    accepted: list[dict] = []
    needs_review: list[dict] = []

    doc_id_seen: set[str] = set()

    for item in CANDIDATES:
        qc_issues = quality_check(item)
        rec = adjudicate(item, qc_issues)

        # 중복 doc_id 방지
        if rec["doc_id"] in doc_id_seen:
            print(f"[WARN] 중복 doc_id: {rec['doc_id']} — 건너뜀", file=sys.stderr)
            continue
        doc_id_seen.add(rec["doc_id"])

        all_records.append(rec)
        if rec["review_status"] == "accepted":
            accepted.append(rec)
        else:
            needs_review.append(rec)

    # ── 저장 ──────────────────────────────────────────────────────────────
    queue_path = out_dir / "review_queue.jsonl"
    with open(queue_path, "w", encoding="utf-8") as f:
        for rec in all_records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    adj_path = out_dir / "ai_adjudicated.jsonl"
    with open(adj_path, "w", encoding="utf-8") as f:
        for rec in accepted:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[OK] 전체 {len(all_records)}건 → {queue_path}")
    print(f"[OK] accepted {len(accepted)}건 → {adj_path}")
    if needs_review:
        print(f"[OK] needs_human_review {len(needs_review)}건 (review_queue에만 포함)")

    # ── gold_real 병합 ───────────────────────────────────────────────────
    if args.no_merge:
        print("[SKIP] --no-merge 옵션으로 gold_real 병합 건너뜀")
        return 0

    gold_path = Path(args.gold_real)
    if not gold_path.exists():
        print(f"[WARN] {gold_path} 없음 — gold_real 병합 건너뜀", file=sys.stderr)
        return 0

    existing_ids: set[str] = set()
    existing_hashes: set[str] = set()
    for line in gold_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if r.get("doc_id"):
                existing_ids.add(r["doc_id"])
            h = hashlib.sha1(r.get("text", "").encode("utf-8")).hexdigest()
            existing_hashes.add(h)
        except json.JSONDecodeError:
            pass

    to_merge = []
    for rec in accepted:
        if rec["doc_id"] in existing_ids:
            print(f"[SKIP] 이미 gold_real에 존재: {rec['doc_id']}")
            continue
        h = hashlib.sha1(rec["text"].encode("utf-8")).hexdigest()
        if h in existing_hashes:
            print(f"[SKIP] 텍스트 hash 중복: {rec['doc_id']}")
            continue
        to_merge.append(rec)

    if not to_merge:
        print("[OK] gold_real에 추가할 신규 건 없음")
        return 0

    with open(gold_path, "a", encoding="utf-8") as f:
        for rec in to_merge:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"[OK] gold_real에 {len(to_merge)}건 추가 → {gold_path}")
    print("     label_source: codex_review (requires_human_signoff: true)")
    print("     check_data_quality.py 실행 전 ACCEPTED_GOLD_LABEL_SOURCES에 'codex_review' 확인 필요")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
