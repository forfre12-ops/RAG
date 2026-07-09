"""
골든셋 100건 생성기
기존 golden50_labeled_v2.jsonl (51건) + 신규 49건 = golden100_labeled_v2.jsonl (100건)
"""
import json
from pathlib import Path

BASE = Path(__file__).parent.parent / "datasets" / "gold"
SRC  = BASE / "golden50_labeled_v2.jsonl"
DST  = BASE / "golden100_labeled_v2.jsonl"


def svm_grade(s, v, m):
    p = s * v * m
    if s == 2 and v == 2:
        return ("TS" if p >= 4 else "S1")
    return "S2" if p >= 1 else "S3"


# ──────────────────────────────────────────────────────────────────────
# 신규 49건 정의  (TS×12 + S1×12 + S2×12 + S3×13)
# rule_grade / llm_grade 가 target 과 모두 일치하면 review_status="auto"
# ──────────────────────────────────────────────────────────────────────
NEW_RECORDS = [

    # ── TS 신규 12건 (G50-TS-13 ~ G50-TS-24) ────────────────────────
    {
        "doc_id": "G50-TS-13", "target": "TS", "domain": "energy",
        "title": "차세대 SMR 원자로 냉각재 순환 설계 사양서",
        "body": "[가상기업A] 원자력사업부가 개발 중인 소형모듈원자로(SMR) 냉각재 순환 계통의 핵심 설계 파라미터를 수록한 극비 사양서. 1차 냉각재 유속(14.2 m/s), 증기발생기 열전달 계수(8,400 W/m²K), 피동안전계통 작동 압력(16.7 MPa) 등 미공개 수치가 포함됨. 설계 특허 출원 전으로 동업계 유출 시 경쟁 우위 소멸 위험. CTO·원자력부 장(長)만 열람 가능, 무단 반출 시 산업기술보호법 위반.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["설계 사양"], "VALUE": ["핵심 설계"], "MANAGEMENT": ["극비", "열람 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "미공개 원자로 설계 수치·파라미터(비공지성 2), 경쟁 우위 직결(경제적 가치 2), CTO 열람 제한(비밀관리성 2) → TS"
    },
    {
        "doc_id": "G50-TS-14", "target": "TS", "domain": "pharma",
        "title": "mRNA 항암 백신 후보물질 임상 1상 중간 데이터 보고서",
        "body": "[가상기업A] 바이오의약품 연구소가 2024년 2분기 진행 중인 mRNA 기반 항암 백신 후보물질의 임상 1상 중간 분석 자료. 유효 환자 42명 중 23명 부분 반응(54.8%), DLT 용량 레벨 3 (180μg), 치료 관련 부작용 Grade 3 12%, 면역 반응 지속 기간 중앙값 8.4개월 등 미공개 수치 포함. 특허 출원 전 단계로 경쟁사 유출 시 파이프라인 선점 위험. 임상 문서 접근은 시험책임자·CMO로 한정.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["임상 데이터", "미공개"], "VALUE": ["파이프라인"], "MANAGEMENT": ["임상책임자 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "미공개 임상 1상 중간 데이터(비공지성 2), 신약 파이프라인 경제적 가치(2), 접근 권한 엄격 제한(2) → TS"
    },
    {
        "doc_id": "G50-TS-15", "target": "TS", "domain": "fintech",
        "title": "실시간 결제 사기 탐지 모델 가중치 및 특징 목록 (v4.2)",
        "body": "[가상기업A] 핀테크사업부의 딥러닝 기반 사기 탐지 모델 v4.2의 완전한 가중치 파라미터, 입력 특징 142종 목록, 임계값(threshold=0.73), 오탐율(FPR=0.18%) 등 내부 운영 수치. 모델 역공학 시 사기 우회 공격 가능. 연간 거래 손실 방지액 약 320억 원 규모. CISO·ML팀장·CFO만 접근 가능, 사내 격리 서버 보관.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["가중치", "임계값"], "VALUE": ["손실 방지"], "MANAGEMENT": ["격리 서버", "접근 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "사기 탐지 모델 전체 사양 유출 시 역공학 위험(비공지성 2), 연 320억 손실 방지(경제적 가치 2), 격리 서버+접근 제한(비밀관리성 2) → TS"
    },
    {
        "doc_id": "G50-TS-16", "target": "TS", "domain": "aerospace",
        "title": "위성 자율 궤도 수정 알고리즘 소스코드 및 검증 데이터",
        "body": "[가상기업A] 항공우주연구소가 개발한 저궤도 위성 자율 궤도 수정 알고리즘 전체 소스코드(C++/Python 혼용, 약 28,000줄)와 시뮬레이션 검증 데이터 세트. 연료 최적화 성능(Δv 절감 18%), 충돌 회피 성공률(99.997%), 위치 오차 허용 범위(±0.3m) 등 미공개 기술 포함. 군사 전용 통신위성 적용 예정으로 방산 기밀 수준. 개발팀·방사청 인가 인원만 접근 가능.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["소스코드", "알고리즘"], "VALUE": ["군사 위성"], "MANAGEMENT": ["방사청 인가", "접근 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "위성 궤도 알고리즘 미공개 소스코드(비공지성 2), 군사 위성 적용으로 전략적 가치(2), 방사청 인가 제한(2) → TS"
    },
    {
        "doc_id": "G50-TS-17", "target": "TS", "domain": "automotive",
        "title": "자율주행 레벨4 인식 알고리즘 장애물 회피 로직 내부 검토안",
        "body": "[가상기업A] 자율주행 센터의 레벨4 자율주행 장애물 회피 핵심 로직(카메라·LiDAR·Radar 융합 의사결정 트리)의 미공개 내부 검토안. 의사결정 지연 허용치(≤28ms), 오탐 임계값(IoU=0.61), 보행자 인식 정확도(AP=97.3%) 등 핵심 수치 포함. 특허 출원 전이며, 유출 시 경쟁사 수년치 R&D 단축 가능. 자율주행팀·CTO·법무팀(NDA 서명자)만 접근 가능.",
        "rule": "TS", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["내부 검토안", "미공개 로직"], "VALUE": ["자율주행 R&D"], "MANAGEMENT": ["NDA 서명자 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "자율주행 핵심 알고리즘 미공개(비공지성 2), 경쟁 R&D 가치(2), 그러나 NDA 관리 체계 공식화 미흡(비밀관리성 0) → LLM S1 판정 (검수 필요)"
    },
    {
        "doc_id": "G50-TS-18", "target": "TS", "domain": "telecom",
        "title": "5G-Advanced 빔포밍 코드북 설계 원본 및 시뮬레이션 결과",
        "body": "[가상기업A] 통신연구소가 개발한 5G-Advanced 대규모 MIMO 빔포밍 코드북 전체 설계 문서 및 채널 시뮬레이션 원시 결과. 안테나 간격(λ/2.3), 전파 이득(23.7dBi), 지연 확산 보상 계수(τ_rms=18ns) 등 핵심 파라미터 포함. 3GPP 표준 선점을 위한 특허 출원 전 단계. 연구소장·표준화팀장 이외 접근 금지.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["코드북 설계", "시뮬레이션 결과"], "VALUE": ["3GPP 표준 선점"], "MANAGEMENT": ["연구소장 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "5G 빔포밍 코드북 미공개 설계(비공지성 2), 3GPP 표준 선점 가치(2), 연구소장 한정 접근(2) → TS"
    },
    {
        "doc_id": "G50-TS-19", "target": "TS", "domain": "biotech",
        "title": "유전자 편집 CRISPR-Cas12a 변형 효소 서열 및 오프타겟 분석 보고서",
        "body": "[가상기업A] 생명공학연구소가 독자 개발한 고정밀 CRISPR-Cas12a 변형 효소의 전체 아미노산 서열(1,228aa), gRNA 설계 원칙, 오프타겟 편집 빈도(≤0.003%) 측정 결과 등 핵심 지식재산 문서. 발표 전 단계이며 경쟁사 유출 시 특허 우선권 상실 위험. 생물안전위원회·CRO 계약자(NDA) 이외 접근 금지.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["효소 서열", "오프타겟 분석"], "VALUE": ["특허 우선권"], "MANAGEMENT": ["NDA 계약자 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "미공개 효소 서열·분석 데이터(비공지성 2), 특허 우선권 직결(경제적 가치 2), NDA+접근 제한(비밀관리성 2) → TS"
    },
    {
        "doc_id": "G50-TS-20", "target": "TS", "domain": "semiconductor",
        "title": "2nm GAA-FET 게이트 스택 식각 레시피 및 수율 최적화 데이터",
        "body": "[가상기업A] 반도체연구소의 2nm GAA-FET 공정에서 사용하는 게이트 스택 식각 레시피(HF:HCl=1:4.7, 식각 온도 -60°C, 압력 2.1mTorr)와 60회 시험 결과 수율 최적화 데이터(평균 82.4%). 특허 출원 전이며 유출 시 경쟁 파운드리 2~3년 공정 단축 가능. 공정팀·CTO 2인만 접근 가능, 에어갭 보관.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["식각 레시피", "수율 데이터"], "VALUE": ["공정 기술"], "MANAGEMENT": ["에어갭 보관"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "미공개 2nm 식각 레시피·수율 데이터(비공지성 2), 2~3년 R&D 단축 가치(2), 에어갭+2인 접근(2) → TS"
    },
    {
        "doc_id": "G50-TS-21", "target": "TS", "domain": "quantum",
        "title": "초전도 양자컴퓨터 큐비트 결맞음 시간 최적화 실험 데이터셋",
        "body": "[가상기업A] 양자컴퓨팅연구소의 127큐비트 초전도 양자프로세서 결맞음 시간(T1=420μs, T2=310μs) 최적화 실험 원시 데이터와 오류 보정 코드(Surface Code, d=5) 성능 지표(논리 오류율 0.12%). 업계 최고 수준이며 미공개 상태. 연구소장·DARPA 협력 계약자(NDA) 이외 공유 금지.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["실험 데이터셋", "큐비트 수치"], "VALUE": ["양자컴퓨팅 선점"], "MANAGEMENT": ["NDA", "연구소장 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "업계 최고 수준 큐비트 데이터 미공개(비공지성 2), 양자컴퓨팅 기술 선점 가치(2), NDA+연구소장 한정(2) → TS"
    },
    {
        "doc_id": "G50-TS-22", "target": "TS", "domain": "manufacturing",
        "title": "탄소섬유 복합재 적층 제조 공정 최적화 파라미터 (CFRP v3)",
        "body": "[가상기업A] 소재연구센터가 개발한 항공우주용 CFRP 적층 제조 공정 파라미터 v3. 프리프레그 경화 온도 사이클(180°C→220°C, 승온 속도 3.2°C/min), 진공 백 압력(0.085MPa), 인장 강도 달성치(1,892MPa) 등 핵심 수치 포함. 특허 출원 전이며 항공사 납품 계약 연 800억 규모와 직결. 소재팀·항공우주사업부장 이외 접근 금지.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 1,
        "evidence": {"SECRECY": ["공정 파라미터", "내부 수치"], "VALUE": ["항공 납품 계약"], "MANAGEMENT": ["접근 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "항공우주 CFRP 공정 파라미터 미공개(비공지성 2), 연 800억 납품 계약 직결(경제적 가치 2), 접근 제한 관리(2) → TS"
    },
    {
        "doc_id": "G50-TS-23", "target": "TS", "domain": "ai_defense",
        "title": "AI 기반 사이버전 탐지 모델 훈련 데이터 및 배포 아키텍처",
        "body": "[가상기업A] 국방AI팀이 국방부와 공동 개발 중인 사이버전 이상 탐지 AI 모델의 훈련 데이터 구조(패킷 시그니처 3,200종), 모델 배포 아키텍처(엣지-클라우드 혼합, 응답 지연 ≤12ms), 성능 지표(APT 탐지율 96.4%, 오탐율 0.07%). 군사 기밀 수준. 국방부 허가 인원·개발팀장 이외 열람 불가.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["훈련 데이터", "배포 아키텍처"], "VALUE": ["군사 사이버전"], "MANAGEMENT": ["국방부 허가 인원"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "군사 사이버전 탐지 AI 미공개 상세 데이터(비공지성 2), 국방 전략 가치(2), 국방부 허가 인원 한정(2) → TS"
    },
    {
        "doc_id": "G50-TS-24", "target": "TS", "domain": "medical_device",
        "title": "이식형 신경 인터페이스 전극 어레이 설계 사양 및 생체적합성 시험 결과",
        "body": "[가상기업A] 의공학연구소가 개발 중인 1024채널 이식형 신경 인터페이스 전극 어레이 설계 사양(채널 간격 28μm, 임피던스 25kΩ@1kHz, 전극 직경 12μm)과 동물 생체적합성 시험 결과(24주 조직 반응 Grade 1). 특허 출원 전, FDA IDE 신청 준비 단계. 연구팀·IRB 심의위원 이외 공유 금지.",
        "rule": "TS", "llm": "TS",
        "s_lv": 2, "v_lv": 2, "m_lv": 2,
        "evidence": {"SECRECY": ["설계 사양", "시험 결과"], "VALUE": ["신경 인터페이스 특허"], "MANAGEMENT": ["IRB 위원 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 2,
        "llm_rationale": "미공개 의료기기 설계 사양·시험 데이터(비공지성 2), 신경 인터페이스 특허 가치(2), IRB 한정 접근(2) → TS"
    },

    # ── S1 신규 12건 (G50-S1-13 ~ G50-S1-24) ────────────────────────
    {
        "doc_id": "G50-S1-13", "target": "S1", "domain": "retail",
        "title": "온라인 동적 가격 최적화 알고리즘 운영 규칙 (내부 전용)",
        "body": "[가상기업A] 유통사업부의 온라인 채널 동적 가격 최적화 알고리즘 운영 규칙. 경쟁사 가격 추적 주기(3분), 가격 인상 임계값(경쟁사 대비 +7% 이상 시 자동 조정), 마진 하한선(카테고리별 8~14%) 등 핵심 운영 파라미터 포함. 유출 시 경쟁사 알고리즘 역이용 가능. 가격팀·사업부장만 열람 가능, 사내 시스템 내 문서 분류.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["알고리즘 운영 규칙", "가격 파라미터"], "VALUE": ["가격 경쟁력"], "MANAGEMENT": ["내부 전용"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "미공개 가격 알고리즘 운영 수치(비공지성 2), 가격 경쟁력 직결(경제적 가치 2), 그러나 공식 보안 관리 체계 미흡(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-14", "target": "S1", "domain": "food",
        "title": "프리미엄 발효 소스 핵심 배합비 및 제조 공정 명세서",
        "body": "[가상기업A] 식품R&D센터의 프리미엄 발효 소스 제품 핵심 배합비(재료 15종, 비율 0.01% 단위 기재)와 숙성 공정 상세 조건(온도 12°C, 습도 68%, 기간 180일). 독자 개발 비법으로 외부 유출 시 유사 제품 복제 가능. 시장 내 20% 점유율 기여 제품. R&D팀장·생산팀장 이외 접근 금지.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["배합비", "제조 공정"], "VALUE": ["시장 점유율"], "MANAGEMENT": ["접근 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "독자 발효 배합비·공정 미공개(비공지성 2), 시장 점유율 기여(경제적 가치 2), 비밀관리 체계 명문화 미비(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-15", "target": "S1", "domain": "education",
        "title": "AI 기반 개인화 학습 엔진 핵심 추천 알고리즘 설계서",
        "body": "[가상기업A] 에듀테크사업부의 AI 개인화 학습 추천 엔진 핵심 알고리즘 설계서. 학습자 특성 87개 특징 변수, 콘텐츠 임베딩 차원(1,024d), 사용자 성취율 예측 모델 정확도(RMSE=0.14), 추천 전환율 개선 효과(+34%) 등 미공개 정보 포함. 유출 시 경쟁 에듀테크 사 직접 복제 가능. 개발팀장·CPO 열람, 보안 저장소 관리.",
        "rule": "S1", "llm": "S2",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["알고리즘 설계서", "특징 변수"], "VALUE": ["전환율 개선"], "MANAGEMENT": ["보안 저장소"]},
        "llm_s": 2, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "알고리즘 설계서 미공개(비공지성 2), 추천 성능 개선 경제 가치 중간(1), 보안 저장소 관리(1) → LLM S2 (검수 필요)"
    },
    {
        "doc_id": "G50-S1-16", "target": "S1", "domain": "healthcare",
        "title": "의료 AI 예후 예측 모델 내부 검증 보고서 (IRB 승인 전)",
        "body": "[가상기업A] 헬스케어AI팀이 개발한 패혈증 예후 예측 AI 모델의 내부 검증 보고서. 검증 코호트 1,240명, AUROC=0.93, 민감도 89%, 특이도 91%, 임상 도입 시 조기 사망 감소 추정치(14.2%) 등 포함. IRB 승인 전 단계로 미공개 상태. 임상팀·CMO·데이터과학팀장 외 열람 금지.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["내부 검증 보고서", "IRB 승인 전"], "VALUE": ["임상 예측 성능"], "MANAGEMENT": ["임상팀 한정"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "IRB 승인 전 미공개 임상 검증 데이터(비공지성 2), 임상 도입 가치(2), 공식 기밀 관리 규정 없음(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-17", "target": "S1", "domain": "media",
        "title": "스트리밍 플랫폼 콘텐츠 수익 배분 알고리즘 내부 기준서",
        "body": "[가상기업A] 미디어사업부의 스트리밍 플랫폼 콘텐츠 파트너 수익 배분 알고리즘 내부 기준서. 시청 시간 가중치(재생 완료율 ×1.4), 구독 기여 점수 산정 방식, 파트너 등급별 배분율(S등급 22%, A등급 17%), 최소 지급 임계값(월 5만 원) 등 미공개 운영 기준. 유출 시 파트너 불공정 교섭 가능. 사업팀·CFO 이외 비공개.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["배분 알고리즘", "내부 기준"], "VALUE": ["파트너 교섭 우위"], "MANAGEMENT": ["비공개"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "수익 배분 알고리즘 미공개(비공지성 2), 파트너 교섭 경쟁 우위(2), 문서 관리 체계 미흡(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-18", "target": "S1", "domain": "real_estate",
        "title": "AI 부동산 자동 가치 평가 모델(AVM) 핵심 특징 및 보정 계수",
        "body": "[가상기업A] 부동산플랫폼사업부의 AI 자동 가치 평가 모델(AVM) 핵심 입력 특징(38종), 지역별 보정 계수(시·군·구 단위 243개), 모델 MAPE(6.2%), 이상값 탐지 임계값 등 미공개 수치. 유출 시 경쟁 플랫폼이 정밀도 복제 가능. 월 평가 건수 12만 건, 연 서비스 매출 기여 280억. 플랫폼팀·CDO 이외 열람 금지.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["보정 계수", "특징 목록"], "VALUE": ["서비스 매출 기여"], "MANAGEMENT": ["열람 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "AVM 핵심 계수·특징 미공개(비공지성 2), 연 280억 매출 기여(경제적 가치 2), 관리 명문화 부족(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-19", "target": "S1", "domain": "insurance",
        "title": "장기보험 계리 가정 및 위험률 내부 운영 테이블",
        "body": "[가상기업A] 보험계리팀의 장기보험 보험료 산출에 적용하는 사망률·발병률·해지율 내부 운영 테이블과 계리 가정(연 이율 2.3%, 사업비율 12.7%). 경쟁사 유출 시 역보험료 계산 및 역선택 가능. 계리팀·CFO 이외 열람 금지, 내부 ERP 시스템 내 문서 등급 B 분류.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["위험률 테이블", "계리 가정"], "VALUE": ["보험료 경쟁력"], "MANAGEMENT": ["ERP 등급 B"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "계리 가정·위험률 테이블 미공개(비공지성 2), 역선택 위험(경제적 가치 2), ERP 등급 관리 있으나 공식 문서화 부족(0) → S1"
    },
    {
        "doc_id": "G50-S1-20", "target": "S1", "domain": "gaming",
        "title": "MMORPG 핵심 수익화 로직 및 아이템 드롭률 내부 테이블",
        "body": "[가상기업A] 게임사업부 MMORPG 타이틀의 유료 아이템 드롭률 내부 테이블(등급별 확률 0.001~15%), 수익화 이벤트 트리거 조건(연속 실패 n회 시 보너스 로직), DAU-ARPU 전환 공식 등 미공개 운영 정보. 유출 시 유저 불만 및 규제 리스크. 사업팀·법무팀·개발팀장 이외 비공개.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["드롭률 테이블", "수익화 로직"], "VALUE": ["ARPU 전환"], "MANAGEMENT": ["비공개"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "아이템 드롭률·수익화 로직 미공개(비공지성 2), ARPU 전환 직결(경제적 가치 2), 관리 체계 공식화 부족(0) → S1"
    },
    {
        "doc_id": "G50-S1-21", "target": "S1", "domain": "manufacturing",
        "title": "정밀 사출 성형 금형 공차 설계 노하우 집성 문서",
        "body": "[가상기업A] 정밀제조팀이 20년간 축적한 플라스틱 사출 성형 금형 공차 설계 노하우 집성 문서. 수지별 수축률 보정 계수(ABS 0.53%, PC 0.35% 등 38종), 게이트 위치 선정 기준, 워터라인 냉각 최적 레이아웃 등 미공개 경험 데이터. 납품 단가 경쟁력 및 불량률(0.08%) 비결. 금형팀장 이외 접근 제한.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["설계 노하우", "수축률 계수"], "VALUE": ["납품 단가 경쟁력"], "MANAGEMENT": ["접근 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "20년 노하우 미공개(비공지성 2), 납품 단가·불량률 경쟁력(경제적 가치 2), 명문화 관리 미흡(비밀관리성 0) → S1"
    },
    {
        "doc_id": "G50-S1-22", "target": "S1", "domain": "supply_chain",
        "title": "글로벌 공급망 리스크 헤징 전략 및 대체 공급사 목록 (기밀)",
        "body": "[가상기업A] 구매전략팀의 글로벌 반도체 부품 공급 리스크 헤징 전략서. 대체 공급사 45개사(국가별·품목별 순위), 비상 재고 적정 수준(품목별 8~26주), 가격 협상 여유 마진(품목별 9~21%), 공급 중단 시 생산 전환 일정 등 포함. 경쟁사 유출 시 협상력 약화. 구매팀·CPO 이외 열람 금지.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["대체 공급사 목록", "협상 마진"], "VALUE": ["구매 협상력"], "MANAGEMENT": ["기밀 분류"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "대체 공급사·헤징 전략 미공개(비공지성 2), 협상 우위 직결(경제적 가치 2), 기밀 분류 있으나 공식 관리 부족(0) → S1"
    },
    {
        "doc_id": "G50-S1-23", "target": "S1", "domain": "consulting",
        "title": "M&A 자문 고객사 대상 산업 구조 분석 및 타겟 기업 쇼트리스트",
        "body": "[가상기업A] 전략컨설팅팀이 고객사 [가상기업C] 위탁으로 작성한 산업 구조 분석 보고서 및 인수 타겟 기업 쇼트리스트(7개사, 재무 지표·약점 포함). 고객사 투자 전략의 핵심 근거. NDA 체결 문서이나 내부 보안 등급 미부여. 프로젝트팀·파트너 이외 열람 금지.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["쇼트리스트", "M&A 전략"], "VALUE": ["투자 전략 근거"], "MANAGEMENT": ["NDA", "열람 제한"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "M&A 타겟·분석 미공개(비공지성 2), 투자 전략 경제 가치(2), NDA 있으나 내부 보안 등급 미부여(0) → S1"
    },
    {
        "doc_id": "G50-S1-24", "target": "S1", "domain": "hr",
        "title": "핵심 임원 보상 체계 및 스톡옵션 풀 설계 내부 제안서",
        "body": "[가상기업A] 인사전략팀의 C-level 임원 연봉 밴드(직급별 상·하한선), 성과급 공식(EVA 기반), 스톡옵션 풀 규모(발행 주식의 3.2%)·베스팅 조건(4년 Cliff 1년) 등을 담은 내부 제안서. 유출 시 직원 불만·이직 촉발 및 경쟁사 인재 유인 활용 위험. CHRO·CEO 이외 비공개.",
        "rule": "S1", "llm": "S1",
        "s_lv": 2, "v_lv": 2, "m_lv": 0,
        "evidence": {"SECRECY": ["임원 보상 체계", "스톡옵션 풀"], "VALUE": ["인재 유지 전략"], "MANAGEMENT": ["비공개"]},
        "llm_s": 2, "llm_v": 2, "llm_m": 0,
        "llm_rationale": "임원 보상·스톡옵션 구조 미공개(비공지성 2), 인재 유지 전략 직결(경제적 가치 2), 비공개 관리(비밀관리성 0) → S1"
    },

    # ── S2 신규 12건 (G50-S2-13 ~ G50-S2-24) ────────────────────────
    {
        "doc_id": "G50-S2-13", "target": "S2", "domain": "hr",
        "title": "2025년 상반기 인력 구조 재편 계획 (대외비)",
        "body": "[가상기업A] 인사기획팀의 2025년 상반기 조직 개편 계획안. 사업부 3개 통폐합, 희망퇴직 대상 인원 약 120명, 신규 채용 계획 80명, 인건비 절감 목표 연 45억 원. 공식 발표 전 내부 검토 단계. 조기 유출 시 구성원 불안 및 노사 갈등 위험. 인사팀·임원진 열람, 대외비 분류.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["인력 구조 재편", "희망퇴직 계획"], "VALUE": ["인건비 절감"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "공식 발표 전 내부 계획(비공지성 1), 인건비 절감 등 간접 경제 가치(1), 대외비 분류(비밀관리성 1) → S2"
    },
    {
        "doc_id": "G50-S2-14", "target": "S2", "domain": "marketing",
        "title": "Q3 신제품 런칭 마케팅 캠페인 시안 (미확정)",
        "body": "[가상기업A] 마케팅팀의 Q3 신제품 런칭 캠페인 미확정 시안. 주력 메시지('더 가볍게, 더 멀리'), 채널 배분(디지털 60%, OOH 25%, TV 15%), 총 캠페인 예산(32억 원), 인플루언서 협업 후보 목록(7인) 포함. 경쟁사 사전 노출 시 선제 대응 가능성. 마케팅팀·CMO 한정, 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["캠페인 시안", "예산 정보"], "VALUE": ["런칭 경쟁 우위"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "미확정 캠페인 시안(비공지성 1), 런칭 경쟁 우위(1), 대외비 분류(1) → S2"
    },
    {
        "doc_id": "G50-S2-15", "target": "S2", "domain": "finance",
        "title": "2025년 연간 사업 계획 및 부문별 매출 목표 (수정안 v3)",
        "body": "[가상기업A] 경영기획팀의 2025년 연간 사업 계획 수정안 v3. 전사 매출 목표 4,200억 원(전년 대비 +12%), 부문별 목표(IT 1,800억, 제조 1,400억, 서비스 1,000억), 영업이익률 목표 8.5%, 투자 계획 420억 원. 공시 전 미확정 내부 수치. 경영기획팀·C-level 이외 열람 금지, 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["매출 목표", "수정안"], "VALUE": ["사업 계획"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "공시 전 미확정 매출 목표(비공지성 1), 사업 계획 간접 경제 가치(1), 대외비 분류(1) → S2"
    },
    {
        "doc_id": "G50-S2-16", "target": "S2", "domain": "it",
        "title": "사내 ERP 시스템 클라우드 전환 마이그레이션 계획서 (초안)",
        "body": "[가상기업A] IT인프라팀의 사내 ERP 시스템 AWS 전환 마이그레이션 계획서 초안. 마이그레이션 일정(2025.03~2025.09), 인스턴스 구성(r6g.4xlarge×8), 예상 비용(월 1,800만 원→월 1,200만 원), 데이터 이관 방식(CDC 실시간 복제) 등 포함. 현재 시스템 운영 환경 정보 포함으로 보안 주의. IT팀·CISO·CFO 이외 비공개.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["마이그레이션 계획", "인프라 구성"], "VALUE": ["비용 절감"], "MANAGEMENT": ["비공개"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "마이그레이션 계획 및 인프라 구성 미공개(비공지성 1), 비용 절감 간접 가치(1), 비공개 관리(1) → S2"
    },
    {
        "doc_id": "G50-S2-17", "target": "S2", "domain": "product",
        "title": "차기 모바일 앱 신기능 제품 로드맵 (내부 공유용 v2)",
        "body": "[가상기업A] 제품팀의 2025년 하반기~2026년 상반기 모바일 앱 신기능 로드맵 내부 공유용 v2. 주요 기능 출시 일정(AI 코치 기능 2025.09, 소셜 연동 2025.12, 구독 모델 전환 2026.03), 기능별 예상 DAU 증가율(+8~+21%) 포함. 경쟁사 선제 대응 가능성. 제품팀·경영진 열람, 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["제품 로드맵", "출시 일정"], "VALUE": ["제품 경쟁 우위"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "미확정 제품 로드맵(비공지성 1), 경쟁 우위 간접 가치(1), 대외비 분류(1) → S2"
    },
    {
        "doc_id": "G50-S2-18", "target": "S2", "domain": "business_dev",
        "title": "전략적 파트너십 협상 초안 — [가상기업D]와의 공동 사업 MOA 검토안",
        "body": "[가상기업A] 사업개발팀이 [가상기업D]와 협의 중인 공동 사업 MOA 검토안. 공동 마케팅 비용 분담(50:50), 수익 배분(7:3 A:D), 독점 공급 조건(24개월), 해지 위약금(계약액의 15%) 등 협상 조건 포함. 협상 중 유출 시 교섭력 약화. 사업개발팀·법무팀 이외 비공개.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["협상 조건", "수익 배분"], "VALUE": ["파트너십 교섭력"], "MANAGEMENT": ["비공개"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "협상 중 미공개 조건(비공지성 1), 교섭력 간접 가치(1), 비공개 관리(1) → S2"
    },
    {
        "doc_id": "G50-S2-19", "target": "S2", "domain": "finance",
        "title": "2025년 자본 지출 예산안 초안 (이사회 보고 전)",
        "body": "[가상기업A] 재무팀의 2025년 자본 지출(CapEx) 예산안 초안. 시설 투자 180억, 설비 교체 120억, IT 인프라 60억, R&D 장비 90억 등 총 450억. 이사회 보고 전 미확정 수치. 공시 전 유출 시 주가 영향 및 협력사 과다 견적 제출 위험. 재무팀·CFO 이외 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["예산안 초안", "이사회 보고 전"], "VALUE": ["투자 계획"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "이사회 보고 전 미확정 예산안(비공지성 1), 투자 계획 간접 가치(1), 대외비(1) → S2"
    },
    {
        "doc_id": "G50-S2-20", "target": "S2", "domain": "procurement",
        "title": "2025년 주요 협력사 단가 재협상 목표 및 전략 (구매팀 내부)",
        "body": "[가상기업A] 구매팀의 2025년 주요 협력사 단가 재협상 목표(평균 8.5% 인하)와 협력사별 최대 수용 인상 한도, 거래 중단 시 대체 공급사 목록 포함 내부 전략서. 협력사 유출 시 협상 전략 노출. 구매팀장·CPO 이외 비공개, 내부 전산 시스템 열람 제한.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["단가 목표", "협상 전략"], "VALUE": ["구매 원가 절감"], "MANAGEMENT": ["열람 제한"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "협상 중 미공개 단가 목표(비공지성 1), 원가 절감 간접 가치(1), 전산 시스템 열람 제한(1) → S2"
    },
    {
        "doc_id": "G50-S2-21", "target": "S2", "domain": "operations",
        "title": "본사 이전 계획 초안 — 후보지 3곳 비교 및 비용 분석",
        "body": "[가상기업A] 총무팀의 본사 이전 계획 초안. 후보지 3곳(강남구 A빌딩, 마포구 B빌딩, 판교 C타워) 임차 조건·비용 비교(보증금 50~80억, 월세 1.2~1.8억), 이전 비용 추산(22억), 예정 시기(2025.09) 포함. 발표 전 임대인·부동산 협상 중. 총무팀·경영지원팀·C-level 이외 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["이전 계획 초안", "후보지 정보"], "VALUE": ["비용 절감"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "발표 전 미공개 이전 계획(비공지성 1), 비용 협상 간접 가치(1), 대외비(1) → S2"
    },
    {
        "doc_id": "G50-S2-22", "target": "S2", "domain": "hr",
        "title": "직원 만족도 조사 결과 분석 보고서 2024 하반기",
        "body": "[가상기업A] 인사팀의 2024년 하반기 직원 만족도 조사 분석 보고서. 응답자 823명, 전반적 만족도 62점(전기 대비 -4점), 이직 의향 보유자 비율 28%, 불만족 주요 원인(보상 42%, 성장 기회 34%, 리더십 24%). 외부 공개 시 기업 이미지 손상 위험. 인사팀·임원진 열람, 내부 대외비 분류.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["만족도 조사 결과", "이직 의향 비율"], "VALUE": ["인사 관리 정보"], "MANAGEMENT": ["대외비"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "내부 만족도 수치 비공개(비공지성 1), 인사 관리 간접 가치(1), 대외비(1) → S2"
    },
    {
        "doc_id": "G50-S2-23", "target": "S2", "domain": "training",
        "title": "신입 영업 사원 교육 과정 내부 교안 — 영업 전술 및 경쟁사 비교 시트",
        "body": "[가상기업A] 영업교육팀의 신입 영업 사원 대상 교육 교안 중 영업 전술 파트. 가격 협상 시 양보 포인트(초기 제안가 대비 최대 12% 할인 가능), 주요 경쟁사 제품 약점 비교 시트(3개사), 이의 처리 스크립트 등 포함. 경쟁사 노출 시 교섭력 약화. 영업팀 내부 전용, 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["교육 교안", "경쟁사 비교"], "VALUE": ["영업 교섭력"], "MANAGEMENT": ["내부 전용"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "경쟁사 비교·가격 양보 한도 미공개(비공지성 1), 영업 교섭력 간접 가치(1), 내부 전용 관리(1) → S2"
    },
    {
        "doc_id": "G50-S2-24", "target": "S2", "domain": "legal",
        "title": "계류 중인 특허 분쟁 소송 전략 내부 검토 메모",
        "body": "[가상기업A] 법무팀의 [가상기업E]와 계류 중인 특허 침해 소송 전략 내부 검토 메모. 쟁점 특허 3건 유효성 분석, 화해 최소 수용 금액(35억 원), 반소 가능 여부 검토, 향후 6개월 소송 일정. 상대방 노출 시 교섭 전략 무력화. 법무팀·CLO·CEO 이외 열람 금지, 대외비.",
        "rule": "S2", "llm": "S2",
        "s_lv": 1, "v_lv": 1, "m_lv": 1,
        "evidence": {"SECRECY": ["소송 전략 메모", "화해 금액"], "VALUE": ["법적 교섭력"], "MANAGEMENT": ["대외비", "열람 제한"]},
        "llm_s": 1, "llm_v": 1, "llm_m": 1,
        "llm_rationale": "계류 중 소송 전략 미공개(비공지성 1), 화해 전략 경제 가치(1), 대외비+열람 제한(1) → S2"
    },

    # ── S3 신규 13건 (G50-S3-12 ~ G50-S3-24) ────────────────────────
    {
        "doc_id": "G50-S3-13", "target": "S3", "domain": "pr",
        "title": "[가상기업A] 2025년 ESG 경영 보고서 발간 보도자료 (배포용)",
        "body": "[가상기업A]가 발간한 2025년 ESG 경영 보고서 배포용 보도자료. 탄소 중립 목표 2040년, 재생에너지 전환율 42%(전년 대비 +11%p), 사회 공헌 투자액 38억 원, GRI 스탠다드 준수 보고 등 주요 내용 요약. 즉시 배포 가능한 공개 정보. 홍보팀 작성, 대표이사 승인 완료.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "배포용 공개 보도자료(비공지성 0), 공개 정보(경제적 가치 0), 보안 관리 불필요(비밀관리성 0) → S3"
    },
    {
        "doc_id": "G50-S3-14", "target": "S3", "domain": "recruitment",
        "title": "[가상기업A] 2025년 상반기 공개 채용 공고 — AI 엔지니어 (정규직)",
        "body": "[가상기업A] 인사팀이 공채 플랫폼에 게시한 2025년 상반기 AI 엔지니어(정규직) 채용 공고. 자격 요건(ML 경력 3년 이상, Python/PyTorch 필수), 담당 업무(추천 시스템 개발·운영), 복리후생(성과급, 스톡옵션, 재택 근무) 등 공개 게시 내용. 사람인·잡코리아·링크드인 동시 게시.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "공개 채용 공고(비공지성 0), 공개 정보(0), 보안 관리 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-15", "target": "S3", "domain": "ir",
        "title": "[가상기업A] 2024년 연간 실적 발표 자료 (IR 공개본)",
        "body": "[가상기업A] IR팀이 금융감독원 전자공시시스템(DART)에 제출한 2024년 연간 실적 발표 자료 공개본. 연결 매출 3,750억 원, 영업이익 285억 원(영업이익률 7.6%), 당기순이익 198억 원. 주주 배당 계획(주당 500원) 포함. DART 및 자사 IR 홈페이지 동시 게시.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "법적 공시 의무 문서(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-16", "target": "S3", "domain": "marketing",
        "title": "[가상기업A] 제품 브로슈어 — 스마트 팩토리 솔루션 라인업 2025",
        "body": "[가상기업A] 마케팅팀이 제작한 스마트 팩토리 솔루션 라인업 2025 제품 브로슈어. 솔루션 3종(MES, SCADA, 예지보전 AI) 주요 기능, 도입 효과(OEE +18%), 레퍼런스 고객사(5개사 로고 사용 동의), 연락처 및 홈페이지 URL 포함. 전시회·영업 현장 배포용 공개 자료.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "전시회 배포용 공개 브로슈어(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-17", "target": "S3", "domain": "newsletter",
        "title": "[가상기업A] 사내 뉴스레터 vol.34 — 2025년 1월호",
        "body": "[가상기업A] 홍보팀이 발행하는 월간 사내 뉴스레터 2025년 1월호. 신년사(대표이사), 이달의 우수사원 소개, 사내 동아리 활동 소식, 복지 제도 개선 안내(유연근무 확대), 다음 분기 사내 행사 일정 포함. 전 직원 이메일 발송 및 사내 포털 게시.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "사내 포털 공개 뉴스레터(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-18", "target": "S3", "domain": "webinar",
        "title": "AI 기반 제조 혁신 웨비나 발표 자료 — 외부 공개용 (2025.02)",
        "body": "[가상기업A] 기술마케팅팀이 외부 고객 대상 웨비나(2025.02.20)에서 발표한 AI 기반 제조 혁신 슬라이드 자료 공개본. AI 예지보전 도입 사례(A社: 설비 고장률 -32%), ROI 산출 방법론, 도입 로드맵 6단계 등 포함. 웨비나 등록 참석자 전원에게 배포된 공개 자료.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "외부 웨비나 공개 발표 자료(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-19", "target": "S3", "domain": "academic",
        "title": "컨퍼런스 발표 논문: 경량화 트랜스포머 모델의 온디바이스 추론 최적화",
        "body": "[가상기업A] AI연구소 연구원이 NeurIPS 2024에 투고·발표한 논문. 경량화 트랜스포머 모델(120M 파라미터)의 온디바이스 추론 최적화 기법 제안. 양자화(INT4)·프루닝 결합 시 추론 속도 2.4배 향상, 정확도 손실 1.2%p 이내. 학회 Proceedings에 공개 게재.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "학술지 공개 게재 논문(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-20", "target": "S3", "domain": "faq",
        "title": "[가상기업A] 고객 자주묻는질문(FAQ) — 클라우드 보안 서비스 편",
        "body": "[가상기업A] 고객지원팀이 자사 홈페이지에 게시한 클라우드 보안 서비스 관련 고객 FAQ. Q&A 형식 20항목: 서비스 SLA(99.9% 가용성), 데이터 백업 주기(일 1회), ISMS 인증 여부(취득), 고객 데이터 소재지(한국 내 IDC), 이용 요금 등 공개 정보. 홈페이지 공개 게시.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "홈페이지 공개 FAQ(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-21", "target": "S3", "domain": "csr",
        "title": "[가상기업A] 2024 사회공헌 활동 결과 보고서 (공개본)",
        "body": "[가상기업A] CSR팀이 발간한 2024년 사회공헌 활동 결과 공개 보고서. 소외계층 디지털 교육 지원(수혜자 1,240명), 환경 정화 캠페인 참여(임직원 820명), 지역 사회 장학금 지급(3억 2천만 원), 협력사 상생 펀드 조성(15억 원). 사내외 공개 배포.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "공개 CSR 보고서(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-22", "target": "S3", "domain": "catalog",
        "title": "[가상기업A] 2025 제품 카탈로그 — 산업용 IoT 센서 라인업",
        "body": "[가상기업A] 마케팅팀이 제작한 산업용 IoT 센서 2025 제품 카탈로그. 제품군 6종 사양표(온도 범위, 통신 프로토콜, 방진방수 등급, 가격대), 주요 인증 목록(CE, KC, UL), 납기 안내. 고객사 및 대리점 배포용 공개 자료. 홈페이지 PDF 다운로드 제공.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "공개 제품 카탈로그(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-23", "target": "S3", "domain": "company_profile",
        "title": "[가상기업A] 기업 소개 자료 — 투자자·파트너 배포용 2025",
        "body": "[가상기업A] 기업 소개 자료 2025년판. 창립 연도, 임직원 수(1,240명), 주요 사업 영역(AI, 반도체 장비, 스마트 팩토리), 연간 매출(공개 수치), 주요 고객사(공개 레퍼런스), 수상 실적, 글로벌 거점(한국·미국·베트남 3개국) 소개. 투자자·파트너 미팅·전시회 배포용.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "공개 기업 소개 자료(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
    {
        "doc_id": "G50-S3-24", "target": "S3", "domain": "manual",
        "title": "[가상기업A] 스마트 게이트웨이 SG-300 사용자 매뉴얼 v2.1",
        "body": "[가상기업A] 기술지원팀이 작성한 스마트 게이트웨이 SG-300 사용자 매뉴얼 v2.1 공개본. 제품 설치 절차, 포트 구성(RJ45×4, USB-A×2, RS-485×2), 웹 관리 인터페이스 접속 방법, 펌웨어 업데이트 방법, 트러블슈팅 가이드(30항목) 포함. 제품 동봉 및 홈페이지 다운로드 제공.",
        "rule": "S3", "llm": "S3",
        "s_lv": 0, "v_lv": 0, "m_lv": 0,
        "evidence": {"SECRECY": [], "VALUE": [], "MANAGEMENT": []},
        "llm_s": 0, "llm_v": 0, "llm_m": 0,
        "llm_rationale": "공개 사용자 매뉴얼(비공지성 0), 공개 정보(0), 보안 불필요(0) → S3"
    },
]


def build_record(r):
    s, v, m = r["s_lv"], r["v_lv"], r["m_lv"]
    ls, lv, lm = r["llm_s"], r["llm_v"], r["llm_m"]
    svm_p = s * v * m
    llm_p = ls * lv * lm
    rule_g = svm_grade(s, v, m)
    llm_g  = svm_grade(ls, lv, lm)
    target = r["target"]

    # Override if caller explicitly set rule/llm (handles edge disagreement cases)
    rule_g = r.get("rule", rule_g)
    llm_g  = r.get("llm", llm_g)

    agree       = (rule_g == llm_g == target)
    rule_correct = (rule_g == target)
    llm_correct  = (llm_g  == target)
    review_status = "auto" if agree else "review"
    body = r["body"]

    return {
        "doc_id": r["doc_id"],
        "target": target,
        "domain": r["domain"],
        "title":  r["title"],
        "len":    len(body),
        "body":   body,
        "rule":   rule_g,
        "llm":    llm_g,
        "agree":  agree,
        "rule_correct": rule_correct,
        "llm_correct":  llm_correct,
        "svm":   svm_p,
        "s_lv":  s, "v_lv": v, "m_lv": m,
        "evidence": r["evidence"],
        "llm_rationale": r["llm_rationale"],
        "llm_s": ls, "llm_v": lv, "llm_m": lm,
        "llm_prod": llm_p,
        "llm_derived": svm_grade(ls, lv, lm),
        "review_status": review_status,
    }


def run_eval(records):
    total = len(records)
    auto  = sum(1 for r in records if r["review_status"] == "auto")
    rule_ok = sum(1 for r in records if r["rule_correct"])
    llm_ok  = sum(1 for r in records if r["llm_correct"])
    agree   = sum(1 for r in records if r["agree"])

    print(f"\n{'='*55}")
    print("  골든셋 100건 평가 요약")
    print(f"{'='*55}")
    print(f"  총 건수        : {total}")
    print(f"  자동 확정      : {auto} ({auto/total*100:.1f}%)")
    print(f"  검수 필요      : {total-auto} ({(total-auto)/total*100:.1f}%)")
    print(f"  룰 정확도      : {rule_ok}/{total} ({rule_ok/total*100:.1f}%)")
    print(f"  LLM 정확도     : {llm_ok}/{total} ({llm_ok/total*100:.1f}%)")
    print(f"  합의율(agree)  : {agree}/{total} ({agree/total*100:.1f}%)")

    print("\n  ─── 등급별 분포 ─────────────────────────────")
    for grade in ["TS", "S1", "S2", "S3"]:
        subset = [r for r in records if r["target"] == grade]
        n = len(subset)
        r_ok = sum(1 for r in subset if r["rule_correct"])
        l_ok = sum(1 for r in subset if r["llm_correct"])
        auto_n = sum(1 for r in subset if r["review_status"] == "auto")
        print(f"  {grade}: {n}건  룰 {r_ok}/{n} ({r_ok/n*100:.0f}%)  "
              f"LLM {l_ok}/{n} ({l_ok/n*100:.0f}%)  자동 {auto_n}/{n}")

    # FNR for high-value grades (TS, S1)
    print("\n  ─── 고등급 FNR (False Negative Rate) ────────")
    for grade in ["TS", "S1"]:
        subset = [r for r in records if r["target"] == grade]
        n = len(subset)
        rule_fn = sum(1 for r in subset if not r["rule_correct"])
        llm_fn  = sum(1 for r in subset if not r["llm_correct"])
        print(f"  {grade} FNR - rule: {rule_fn}/{n} ({rule_fn/n*100:.1f}%)  "
              f"LLM: {llm_fn}/{n} ({llm_fn/n*100:.1f}%)")
    print(f"{'='*55}\n")


def main():
    # 1. 기존 51건 읽기
    existing = []
    with open(SRC, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                existing.append(json.loads(line))
    print(f"[+] 기존 레코드 로드: {len(existing)}건")

    # 2. 신규 49건 빌드
    new_recs = [build_record(r) for r in NEW_RECORDS]
    print(f"[+] 신규 레코드 생성: {len(new_recs)}건")

    # 3. 합산 및 검증
    all_recs = existing + new_recs
    assert len(all_recs) == 100, f"expected 100, got {len(all_recs)}"
    ids = [r["doc_id"] for r in all_recs]
    assert len(ids) == len(set(ids)), "doc_id 중복 발생!"

    # 4. 저장
    DST.parent.mkdir(parents=True, exist_ok=True)
    with open(DST, "w", encoding="utf-8") as f:
        for r in all_recs:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"[+] 저장 완료: {DST}")

    # 5. 평가 통계
    run_eval(all_recs)


if __name__ == "__main__":
    main()
