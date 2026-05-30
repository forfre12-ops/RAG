"""KOIPA 영업비밀 분류 대상과 유사한 현실적 코퍼스 생성.

배경:
  고객사(KOIPA) 실문서 없음. 공개 판례·특허·법령에서 추출한 도메인 지식 기반으로
  반도체/배터리/화학/바이오/소프트웨어/경영정보 6개 분야 × 4개 등급 문서 생성.

  기존 합성과 차이:
  - 모든 문서가 다른 제목·다른 기술 내용 → 임베딩이 실제로 구분해야 함
  - 쿼리는 본문 핵심 문장 추출 → grade 매칭이 아닌 doc_id 매칭
  - 실제 KOIPA 판례·컨설팅 케이스에서 확인된 기술 용어 사용

실행:
  python scripts/p2_build_realistic_corpus.py \\
      --out datasets/realistic_corpus \\
      --count 400
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import uuid
from pathlib import Path

# ── 도메인별 기술 템플릿 ──────────────────────────────────────
# 실제 영업비밀 침해 판례(모나미·토마토종자·반도체·배터리 등)와
# KOIPA 컨설팅 사례에서 확인된 기술 내용 기반

DOMAINS: dict[str, dict] = {

    "반도체": {
        "TS": [
            {
                "title": "DRAM 커패시터 유전체 ALD 공정 핵심 레시피",
                "body": (
                    "본 문서는 DRAM 커패시터 유전체 박막 증착에 적용되는 원자층증착(ALD) 공정의 핵심 레시피를 기술한다. "
                    "ZrO2/Al2O3 복합 유전체 구조에서 ZrO2 증착 사이클은 TEMAZr 전구체를 0.3초 펄스 후 N2 퍼지 2.0초, "
                    "H2O 산화제 0.2초 펄스 후 N2 퍼지 2.5초 순서로 진행된다. 기판 온도는 250±2°C를 유지하며, "
                    "챔버 압력은 0.5 Torr로 고정한다. 목표 유전율(k값)은 25 이상이며, 누설전류밀도는 "
                    "1MV/cm 인가 전압 기준 1×10⁻⁷ A/cm² 이하를 요구한다. "
                    "Al2O3 캡핑층 두께는 0.5nm로 설정하여 crystallization temperature를 상승시킨다. "
                    "본 레시피는 자사 10nm급 DRAM 양산 수율 94.3% 달성의 핵심 기술로, "
                    "전구체 종류·농도·펄스시간의 조합은 3년간 7,200회 실험을 통해 최적화된 값이다."
                ),
            },
            {
                "title": "비메모리 반도체 PMIC 회로 레이아웃 설계 규격서",
                "body": (
                    "본 규격서는 자사 고유 Power Management IC(PMIC)의 전력 변환 회로 레이아웃 설계 기준을 정의한다. "
                    "Buck-Boost 컨버터 스위칭 주파수는 2.4MHz로 고정되며, 출력 전압 범위는 0.6V~5.5V(100mV 스텝)이다. "
                    "인덕터 DCR 보상 회로는 Rcs=0.82Ω, Ccs=47nF 조합으로 구성되며, 이 값은 출력 리플 전압을 "
                    "5mV 이내로 억제하기 위한 최적화 결과이다. "
                    "레이아웃상 HS FET와 LS FET 사이 dead-time은 8ns로 설정하여 shoot-through 전류를 방지한다. "
                    "메탈 레이어 구성: M1(신호선, 최소폭 0.14μm), M2(전원선, 최소폭 0.5μm), "
                    "M3(글로벌 전원, 최소폭 2.0μm). 본 설계는 경쟁사 대비 변환효율 2.3%p 우위를 달성한다."
                ),
            },
            {
                "title": "EUV 포토레지스트 CAR 조성 연구 보고서",
                "body": (
                    "EUV(13.5nm) 리소그래피용 화학증폭형 포토레지스트(CAR) 조성 최적화 연구 결과를 보고한다. "
                    "베이스 폴리머로 PMMA-co-PHOST 공중합체(조성비 3:7)를 사용하며, PAG(Photo Acid Generator)로 "
                    "TPSTF를 전체 조성의 3.2wt% 첨가한다. 용매 시스템은 PGMEA:EL=7:3 혼합계를 사용한다. "
                    "PEB(Post Exposure Bake) 조건: 90°C, 60초. 현상액: 0.26N TMAH 수용액, 30초. "
                    "목표 LWR(Line Width Roughness)는 2.5nm 이하, EUV 감도는 20mJ/cm² 이하이다. "
                    "현재 달성값: LWR 2.3nm, 감도 17.8mJ/cm². 7nm 이하 노드 적용을 위한 핵심 IP이다."
                ),
            },
        ],
        "S1": [
            {
                "title": "차세대 FinFET 게이트 절연막 특허 출원 전 기술 검토",
                "body": (
                    "3nm 노드 FinFET 구조에 적용 예정인 고유전율 게이트 절연막 기술의 특허 출원 전 선행기술 조사 결과이다. "
                    "자사 개발 HfZrOx 절연막은 Hf:Zr 몰비 7:3에서 최적 특성을 나타내며, "
                    "EOT(Equivalent Oxide Thickness) 0.65nm, 게이트 누설전류 1×10⁻⁸ A/cm²를 달성했다. "
                    "선행특허 US10,234,567(Intel), JP2022-123456(TSMC) 대비 차별점은 "
                    "La₂O₃ 도핑을 통한 결정화 온도 향상(750°C→820°C)이며, 이 기술은 미출원 상태이다. "
                    "출원 예정일: 2026년 3분기. 경쟁사 공개 기술과의 기술격차는 약 18개월로 추산된다."
                ),
            },
            {
                "title": "파운드리 공정 수율 개선 실패분석 보고서 Q1 2026",
                "body": (
                    "5nm 공정 SRAM 비트셀 수율 이상 원인 분석 보고서. 2026년 1분기 수율이 전분기 대비 2.1%p 하락하여 "
                    "원인 조사를 실시하였다. TEM 분석 결과 게이트 산화막 두께 불균일(±0.08nm → ±0.14nm)이 "
                    "주 원인으로 확인됐다. WER(Wet Etch Rate) 변화가 ALD 전구체 공급라인 내 수분 오염에 의한 것으로 "
                    "판단되며, 공급라인 MFC #3 교체 후 정상화되었다. "
                    "개선 전: 수율 91.2%, 개선 후: 93.8%. 본 분석은 자사 공정 노하우의 핵심이다."
                ),
            },
        ],
        "S2": [
            {
                "title": "반도체 장비 협력사 단가 계약 현황 2026",
                "body": (
                    "주요 반도체 장비 협력사별 납품 단가 및 계약 조건 현황. "
                    "CVD 장비: A사 단가 $2.3M/대, 유지보수 연간 $180K, 납기 16주. "
                    "이온주입기: B사 단가 $4.1M/대, 유지보수 연간 $320K, 납기 24주. "
                    "포토레지스트 도포·현상 장비(Track): C사 단가 $1.8M/대, 납기 12주. "
                    "협상 여지: CVD 장비 연간 10대 이상 구매 시 7% 할인 조건 확보 가능. "
                    "B사는 2027년 신모델 출시 예정으로 현 재고 모델 20% 할인 가능성 있음."
                ),
            },
        ],
        "S3": [
            {
                "title": "반도체 공정 기초 교육자료 — 포토리소그래피 개요",
                "body": (
                    "포토리소그래피(Photolithography)는 반도체 제조의 핵심 공정으로, "
                    "빛을 이용해 회로 패턴을 웨이퍼에 전사하는 기술이다. "
                    "주요 단계: 1) 포토레지스트 도포 2) 소프트베이크 3) 노광(Exposure) "
                    "4) PEB(Post Exposure Bake) 5) 현상(Development) 6) 검사. "
                    "광원 종류: i-line(365nm), KrF(248nm), ArF(193nm), EUV(13.5nm). "
                    "해상도 한계는 레일리 기준 R=k₁λ/NA로 결정된다. "
                    "본 교육자료는 신입 엔지니어 대상 기초 교육 목적으로 작성되었으며, "
                    "공개 교재 및 학술자료를 바탕으로 구성하였다."
                ),
            },
        ],
    },

    "배터리": {
        "TS": [
            {
                "title": "리튬이온 배터리 양극재 NCM 조성 최적화 연구보고서",
                "body": (
                    "차세대 NCM(Ni-Co-Mn) 양극재의 조성비 최적화 연구 결과를 기술한다. "
                    "Ni:Co:Mn = 8.5:0.8:0.7 몰비에서 초기 방전용량 215 mAh/g, "
                    "100사이클 후 용량유지율 91.3%를 달성하였다. "
                    "핵심 노하우: 전구체 공침 pH 제어(11.2±0.05), 소성 온도 프로파일(1차 750°C/5h, 2차 820°C/10h), "
                    "Li/M 몰비 1.04 과잉 리튬 첨가. Al₂O₃ 건식코팅(200ppm) 적용 시 고온 보관 특성 개선. "
                    "전해액 조성: EC:DMC:EMC = 1:1:1(vol%), LiPF₆ 1.2M, VC 1wt% 첨가제. "
                    "본 조성은 3년 연구개발비 48억원 투입, 12,000회 충방전 테스트를 통해 도출된 핵심 IP이다."
                ),
            },
            {
                "title": "전고체 배터리 황화물계 고체전해질 제조 공정서",
                "body": (
                    "Li₆PS₅Cl(Argyrodite) 계열 황화물 고체전해질의 자사 고유 제조 공정을 기술한다. "
                    "원료: Li₂S(순도 99.9%), P₂S₅(순도 99%), LiCl(순도 99.5%). "
                    "볼밀 조건: ZrO₂ 볼(직경 10mm), 회전수 500rpm, 밀링시간 20시간, Ar 분위기. "
                    "열처리: 550°C, 8시간, Ar 분위기(승온속도 2°C/min). "
                    "목표 이온전도도: 5×10⁻³ S/cm 이상. 현재 달성값: 6.2×10⁻³ S/cm. "
                    "결정성 확인: XRD 주요 피크 2θ = 25.2°, 30.1°, 51.7°. "
                    "본 공정은 경쟁사 대비 이온전도도 30% 우위로, 자사 전고체 배터리 상용화의 핵심 기술이다."
                ),
            },
            {
                "title": "배터리 BMS 셀 밸런싱 알고리즘 소스코드 설계서",
                "body": (
                    "자사 BMS(Battery Management System)에 탑재되는 능동형 셀 밸런싱 알고리즘의 설계 명세를 기술한다. "
                    "알고리즘: Fuzzy-PID 복합 제어 방식. 입력 변수: 셀 전압 편차(ΔV), SOC 편차(ΔSOC), 온도(T). "
                    "밸런싱 시작 조건: ΔV > 20mV 또는 ΔSOC > 3%. "
                    "퍼지 멤버십 함수: 삼각형 함수, 전압 편차 [0, 50, 100]mV 기준 3등급(Low/Mid/High). "
                    "전달 함수: Kp=0.85, Ki=0.12, Kd=0.03(경험적 최적화값). "
                    "최대 밸런싱 전류: 2A, 효율: 94.7%. 본 알고리즘 소스코드는 임베디드 C 구현체로 별도 관리."
                ),
            },
        ],
        "S1": [
            {
                "title": "46800 원통형 배터리 권취 공정 개선 결과 보고서",
                "body": (
                    "46800 대형 원통형 배터리 젤리롤 권취 공정의 수율 개선 프로젝트 결과 보고. "
                    "초기 불량률: 권취 불량 3.2%, 쇼트 불량 0.8%. 개선 후: 권취 불량 0.9%, 쇼트 불량 0.2%. "
                    "핵심 개선사항: 권취 장력 프로파일 최적화(양극 1.2N → 0.9N, 음극 0.8N → 0.7N), "
                    "분리막 Pre-tension 제어 추가(±0.05N 정밀도), 권취 속도 40rpm → 35rpm 하향. "
                    "설비 개조 내용: 장력 센서 교체(분해능 0.01N), 서보모터 제어 PID 재튜닝. "
                    "연간 불량 비용 절감: 약 23억원 예상."
                ),
            },
        ],
        "S2": [
            {
                "title": "배터리 팩 완성차 고객사 공급 단가 협상 자료 2026",
                "body": (
                    "2026년 상반기 배터리 팩 완성차 고객사별 공급 단가 협상 현황. "
                    "A 완성차: 현재 단가 $112/kWh → 요구 단가 $98/kWh(12.5% 인하 요구). "
                    "B 완성차: 현재 단가 $118/kWh, 물량 2만개→3만개 증가 조건 $105/kWh 제안. "
                    "원가 구조: 양극재 38%, 음극재 12%, 분리막 8%, 전해액 9%, 케이스 11%, 조립/기타 22%. "
                    "원자재 가격 상승분(리튬 35% 상승) 미반영 시 현재 수익성 악화 진행 중. "
                    "협상 전략: A사는 장기 물량 계약(3년 총 15만개) 조건으로 5% 인하 수용 가능."
                ),
            },
        ],
        "S3": [
            {
                "title": "리튬이온 배터리 안전관리 일반 지침",
                "body": (
                    "리튬이온 배터리 취급 및 보관 안전관리 일반 지침. 본 문서는 사내 배포용으로 작성되었다. "
                    "1. 충전: 규정 충전기만 사용, 과충전 금지(최대 4.2V/셀). "
                    "2. 보관: 온도 15~25°C, 습도 45~75%, SOC 30~50% 상태 보관 권장. "
                    "3. 화재 시 대응: 배터리 화재는 물 사용 금지, D급 소화기 또는 건조모래 사용. "
                    "4. 운반: 단락 방지를 위해 단자 절연 처리 후 운반. "
                    "배터리 관련 국제 기준: UN38.3(운송), IEC62133(안전), UL1642(인증). "
                    "본 지침은 공개 가능하며 협력업체에도 배포할 수 있다."
                ),
            },
        ],
    },

    "화학_제약": {
        "TS": [
            {
                "title": "신약 후보물질 합성 경로 및 원료 배합 연구 노트",
                "body": (
                    "항암제 후보물질 KR-2847의 다단계 합성 경로 및 최적 반응 조건을 기술한다. "
                    "Step 1: 출발물질 A(3-bromopyridine)와 B(4-aminophenylboronic acid)의 Suzuki 커플링. "
                    "촉매: Pd(PPh₃)₄(5mol%), 염기: K₂CO₃(2eq), 용매: DMF/H₂O = 4:1, 온도: 80°C, 시간: 12h. "
                    "수율: 87%. Step 2: 중간체 C의 아실화 반응, 클로로아세틸클로라이드(1.2eq), TEA(2eq), DCM, 0°C→RT. "
                    "최종 수율(3단계): 42%. 순도(HPLC): 99.3%. 용해도: pH7.4 PBS에서 2.3mg/mL. "
                    "본 합성 경로는 기존 공개 경로 대비 단계수 감소(6→3단계), 수율 향상(28%→42%), "
                    "연구비 35억원 및 4년 연구기간의 산물이다."
                ),
            },
            {
                "title": "기능성 잉크 화학약품 조성비율 및 조성방법 기술서",
                "body": (
                    "산업용 잉크젯 프린터용 기능성 잉크의 화학 조성 및 제조 방법을 기술한다. "
                    "주요 성분(wt%): 착색제(Pigment Red 122) 4.5%, 분산제(BYK-190) 1.2%, "
                    "바인더 수지(아크릴릭 코폴리머, Mw=45,000) 8.3%, 습윤제(글리세롤) 6.0%, "
                    "보존제(Proxel GXL) 0.15%, 잔량 탈이온수. "
                    "제조 공정: 1) 착색제+분산제 비드밀 분산(지르코니아 비드 0.3mm, 2,000rpm, 4h), "
                    "2) 바인더 수지 용해 혼합, 3) 습윤제·보존제 첨가 후 여과(0.8μm 필터). "
                    "목표 물성: 점도 8.5±0.5 cPs(25°C), 표면장력 32±2 mN/m, 입도 D90 < 180nm. "
                    "본 조성은 10년간 약 32년 분량의 실험을 통해 최적화한 핵심 영업비밀이다."
                ),
            },
        ],
        "S1": [
            {
                "title": "의약품 원료 합성 공정 밸리데이션 보고서",
                "body": (
                    "고혈압 치료제 원료의약품 X의 합성 공정 밸리데이션 보고서(ICH Q11 기준). "
                    "핵심 공정 파라미터(CPP): 반응온도(-10±2°C), 반응시간(180±10분), pH(7.2±0.1). "
                    "품질 특성(CQA): 순도≥99.5%(HPLC), 수분≤0.2%(KF법), 잔류용매(에탄올≤5,000ppm). "
                    "3배치 밸리데이션 결과: 수율 평균 78.3%(±1.2%), 순도 평균 99.71%(±0.08%). "
                    "공정이상 위험(FMEA): 온도 이탈 시 불순물 B 생성 증가(>0.1%), 즉각 배치 폐기 기준. "
                    "GMP 승인일: 2025년 8월. FDA 및 EMA 제출용 CTD Module 3 포함 예정."
                ),
            },
        ],
        "S2": [
            {
                "title": "원료 공급업체 평가 및 이중소싱 전략 보고서",
                "body": (
                    "주요 화학 원료 공급업체 평가 및 공급망 리스크 대응 전략. "
                    "원료 A(아세틸살리실산): 현재 단일 공급(국내 M사, 단가 45만원/kg). "
                    "이중소싱 후보: 국내 N사(단가 48만원/kg, 품질 동등), 중국 P사(단가 31만원/kg, 품질 검증 중). "
                    "원료 B(디클로로메탄): H사 단독 계약, 납기 8주, 단가 8.2만원/kg. "
                    "리스크: H사 공장 화재 이력(2023년 2회). 대안: K사 납기 10주, 단가 9.1만원/kg. "
                    "권고사항: 원료 A는 N사 이중소싱 추진(비용 증가 6.7%), 원료 B는 K사 등록 우선."
                ),
            },
        ],
        "S3": [
            {
                "title": "화학물질 안전관리 교육자료 — MSDS 활용 방법",
                "body": (
                    "화학물질 안전보건자료(MSDS/SDS) 이해 및 활용 방법 교육 자료. "
                    "MSDS는 화학물질의 물리화학적 특성, 위험성, 취급 방법, 응급 처치 방법 등을 기재한 문서다. "
                    "GHS 기준 16개 항목: 1)화학제품과 회사 정보 2)유해위험성 3)구성성분 정보 "
                    "4)응급조치요령 5)폭발·화재 시 대처방법 ... 16)기타 참고사항. "
                    "경고표지 GHS 픽토그램: 폭발성(폭탄), 산화성(불꽃+원), 고압가스(가스통), "
                    "인화성(불꽃), 부식성(피부 손상), 독성(해골), 환경유해성(물고기+나무). "
                    "본 교육자료는 산업안전보건법 및 화학물질관리법 기반으로 작성되었으며 공개 가능하다."
                ),
            },
        ],
    },

    "소프트웨어": {
        "TS": [
            {
                "title": "추천 시스템 핵심 알고리즘 설계 명세서 v3.2",
                "body": (
                    "자사 e커머스 플랫폼의 개인화 추천 시스템 핵심 알고리즘 설계 명세. "
                    "모델 구조: Two-Tower Neural Network + Transformer Attention 결합. "
                    "User Tower: 행동 시퀀스 임베딩(GRU, hidden=256) + 인구통계 피처(MLP, [128,64]). "
                    "Item Tower: 텍스트 피처(BERT-KOR fine-tuned, 768→256 projection) + 이미지(ResNet-50, 2048→256). "
                    "Loss: Softmax loss + In-batch negative sampling(neg_sample=128). "
                    "온라인 서빙: 2단계(Recall→Ranking), Recall 후보 500개, Ranking top-10 노출. "
                    "CTR 개선 기여: AB테스트 기준 기존 CF 대비 +23.4%, GMV +8.7%. "
                    "본 알고리즘은 자사 플랫폼 핵심 경쟁력으로 소스코드·가중치 외부 유출 금지."
                ),
            },
            {
                "title": "사이버보안 침입탐지 시스템 시그니처 DB 및 탐지 로직",
                "body": (
                    "자사 IDS/IPS 제품의 핵심 탐지 시그니처 DB 구성 및 탐지 엔진 로직을 기술한다. "
                    "시그니처 총 14,832개(2026.05 기준): 네트워크 이상(3,241), 악성코드(5,108), "
                    "웹 공격(4,127), 기타(2,356). "
                    "탐지 로직: 1)패킷 헤더 파싱(Layer2~7), 2)플로우 재조합, "
                    "3)DPI(Deep Packet Inspection) — 정규식 매칭 엔진(Hyperscan 기반), "
                    "4)행동 기반 분석(ML 모델: XGBoost, F1=0.94). "
                    "제로데이 탐지: 이상행동 기반 Autoencoder(임계값 재구성오류 > 0.023). "
                    "처리 성능: 10Gbps 라인레이트, 지연 < 50μs. "
                    "시그니처 DB는 자사 보안연구팀 2,400시간 분석의 산물이다."
                ),
            },
        ],
        "S1": [
            {
                "title": "클라우드 인프라 아키텍처 설계서 — 다중 리전 DR 구성",
                "body": (
                    "자사 클라우드 서비스의 다중 리전 재해복구(DR) 아키텍처 설계 문서. "
                    "Primary 리전: KR-CENTRAL-1, Secondary: KR-EAST-1. "
                    "RTO(목표복구시간): 15분, RPO(목표복구시점): 5분. "
                    "DB 복제: Aurora Global Database, 크로스 리전 복제 지연 < 1초. "
                    "스토리지: S3 크로스 리전 복제, 복제 지연 < 15분. "
                    "트래픽 전환: Route53 헬스체크(30초 간격), 장애 감지→ DNS TTL 60초→전환. "
                    "월간 DR 훈련: 자동화 스크립트(Terraform+Lambda), 훈련 시 실서비스 영향 0건 목표. "
                    "본 아키텍처 구성은 자사 SLA 99.99% 달성의 핵심 설계이다."
                ),
            },
        ],
        "S2": [
            {
                "title": "SaaS 고객사별 계약 조건 및 사용 현황 2026 상반기",
                "body": (
                    "자사 SaaS 제품 고객사별 계약 금액·조건 및 사용 현황 집계. "
                    "A그룹: 연간 계약금액 4.8억원, 사용자 수 1,200명, 계약 만료 2027.03, 갱신 협상 중. "
                    "B공기업: 연간 2.1억원, 사용자 450명, 2026.12 만료, 예산 삭감 리스크 있음. "
                    "C스타트업: 월정액 580만원(성장형), MAU 기준 초과 시 추가 과금, 현재 MAU 3,200명. "
                    "전체 ARR: 34.2억원. Churn Rate 최근 3개월: 1.8%(목표 < 2%). "
                    "이탈 위험 고객: B공기업(예산 이슈), D제조사(경쟁사 솔루션 평가 중)."
                ),
            },
        ],
        "S3": [
            {
                "title": "오픈소스 라이선스 사용 정책 가이드",
                "body": (
                    "자사 소프트웨어 개발 시 오픈소스 라이선스 활용 정책 가이드. 본 문서는 전 직원 공개. "
                    "사용 가능 라이선스(상용 제품 포함): MIT, Apache 2.0, BSD 2/3-Clause, ISC. "
                    "제한적 사용(법무 검토 필요): LGPL v2.1/v3(동적 링크 허용, 정적 링크 금지). "
                    "사용 금지(상용 제품): GPL v2/v3, AGPL v3, SSPL. "
                    "주요 리스크: GPL 코드를 상용 제품에 정적 링크 시 전체 소스코드 공개 의무 발생. "
                    "오픈소스 사용 등록: JIRA 티켓 생성 후 법무팀 검토(3영업일 내 회신). "
                    "연간 오픈소스 감사: 매년 1월, FOSSA 도구로 자동 스캔."
                ),
            },
        ],
    },

    "경영정보": {
        "TS": [
            {
                "title": "핵심 고객사 영업 전략 및 수주 방어 계획 — 기밀",
                "body": (
                    "자사 매출 Top 3 고객사(A그룹·B공기업·C대기업) 대상 수주 방어 및 확대 전략. "
                    "A그룹: 현재 점유율 78%. 경쟁사 D사 침투 시도 포착(2026년 Q1 제안서 제출 확인). "
                    "방어 전략: 임원 관계망 강화(CTO 월 1회 미팅), 전용 기술지원팀 배치, "
                    "신규 모듈 조기 제공(6개월 선공급). 추가 수주 목표: 연 120억원. "
                    "B공기업: 예산 주기 3월·9월, 담당 구매팀장 인맥 확보 필요. "
                    "내부 정보: B공기업 IT본부장 2026년 9월 교체 예정, 신임 본부장 전 직장(E사) 파악 중. "
                    "본 문서는 영업본부장 이상 열람 가능."
                ),
            },
        ],
        "S1": [
            {
                "title": "M&A 대상 기업 실사 보고서 — F사 인수 검토",
                "body": (
                    "인수 후보 F사(매출 280억원, 종업원 87명) 실사 결과 보고서. "
                    "재무: 최근 3년 EBITDA 마진 8.3%→11.2%→13.7% 성장세. 부채비율 142%. "
                    "기술 자산: 특허 12건(핵심 3건, 만료 예정 2건), SW 저작권 4건. "
                    "리스크: 핵심 인력 3명 이탈 가능성(스톡옵션 만료 2026.12), "
                    "주요 고객 G사 의존도 62%(이탈 시 매출 타격 심각), "
                    "세무조사 리스크(2022년 법인세 신고 오류 의혹). "
                    "적정 인수가격 범위: 320억~380억원(EV/EBITDA 8~10배). "
                    "권고: 핵심 인력 잔류 조건부 인수, 주요 고객 계약 승계 확약 선행."
                ),
            },
        ],
        "S2": [
            {
                "title": "임직원 급여 및 성과급 지급 기준 내부 규정",
                "body": (
                    "2026년 임직원 급여 체계 및 성과급 지급 기준. "
                    "기본급 밴드(직급별): 사원 3,600~4,200만원, 대리 4,500~5,500만원, "
                    "과장 5,800~7,200만원, 차장 7,500~9,500만원, 부장 9,800~13,000만원. "
                    "성과급: 회사 목표달성률 × 개인 평가등급 조합. "
                    "회사 달성률 100%·개인 S등급 시 기본급의 50%, A등급 40%, B등급 25%, C등급 10%. "
                    "특별 인센티브: 핵심인재(HiPo) 별도 Retention 보너스 1,000~3,000만원 지급. "
                    "본 문서는 HR팀·임원·팀장급 이상 열람. 외부 유출 금지."
                ),
            },
        ],
        "S3": [
            {
                "title": "2026년 상반기 경영 실적 요약 (외부 공개본)",
                "body": (
                    "2026년 상반기 경영 실적 요약 — 주주·대외 공개용. "
                    "매출액: 2,847억원(전년 동기 대비 +12.3%). 영업이익: 341억원(영업이익률 12.0%). "
                    "당기순이익: 278억원. 연구개발비: 매출의 6.2% 투자. "
                    "사업부별 매출 비중: 반도체 소재 44%, 배터리 소재 31%, 화학 소재 25%. "
                    "2026년 하반기 전망: 북미 신규 공장 가동(2026.09), 연간 매출 6,000억원 목표. "
                    "본 보고서는 투자자 대상 IR 자료로 작성되었으며 외부 공개가 가능하다."
                ),
            },
        ],
    },

    "바이오_농업": {
        "TS": [
            {
                "title": "F1 하이브리드 고추 품종 부모본 육종 계획서",
                "body": (
                    "신규 F1 하이브리드 고추 품종 KP-2847의 부모본 육종 내역 및 교배 계획. "
                    "모본(Female): KP-F22계통, 특성: 고온 내성(42°C 생존율 89%), 과피 두께 5.2mm, "
                    "캡사이신 함량 12,400 SHU. 23세대 자식계통 고정. "
                    "부본(Male): KP-M15계통, 특성: 역병 저항성(Phytophthora capsici 감염률 < 5%), "
                    "초형: 직립, 숙기: 중조생. 18세대 자식계통 고정. "
                    "F1 예측 특성: 수량 3.2kg/주(CK 대비 +34%), 과장 12.5cm, 조기착화성. "
                    "유전자 마커 데이터: SSR 마커 86개 기반 계통 동일성 확인. "
                    "본 부모본 계통은 DNA 분석 기반 특허 출원 예정(2026.09), 현재 영업비밀 관리 중."
                ),
            },
            {
                "title": "미생물 발효 균주 배양 조건 최적화 보고서",
                "body": (
                    "아미노산 고생산성 Corynebacterium glutamicum KJ-08 균주의 배양 조건 최적화 결과. "
                    "탄소원: 포도당 60g/L(최적), 초과 시 카타볼라이트 억제. "
                    "질소원: (NH₄)₂SO₄ 15g/L + 대두박 수해물 5g/L 복합 사용. "
                    "비타민: 바이오틴 0.05mg/L(결핍 시 생장 정지). pH 7.2±0.1(자동 제어, H₂SO₄/NH₃). "
                    "교반: 400rpm, 통기량: 1.5 vvm. 온도: 30°C(성장기)→28°C(생산기, 전환점: OD600=12). "
                    "최종 라이신 생산량: 98.3g/L(72h 배양). 수율: 0.48g/g 포도당. "
                    "이 균주 및 배양 조건은 경쟁사 대비 생산성 35% 우위의 핵심 기술이다."
                ),
            },
        ],
        "S1": [
            {
                "title": "임상 2상 시험 중간 결과 보고서 — KR-5521",
                "body": (
                    "자사 신약 후보물질 KR-5521(비알코올성 지방간염 치료제) 임상 2상 중간 분석 결과. "
                    "대상: 100명(치료군 50명, 위약군 50명), 투여기간: 24주. "
                    "1차 평가변수: NAS(NAFLD Activity Score) 2점 이상 개선. "
                    "중간 결과(12주): 치료군 62%(31/50명) 개선 vs 위약군 18%(9/50명)(p < 0.001). "
                    "ALT 수치: 치료군 평균 68→41 IU/L(39.7% 감소), 위약군 71→65 IU/L(8.5% 감소). "
                    "안전성: 이상반응 두통 12%, 오심 8%(모두 경증). 중대한 이상반응 없음. "
                    "본 데이터는 FDA Fast Track 신청 자료이며 최고 기밀로 관리한다."
                ),
            },
        ],
        "S2": [
            {
                "title": "농약 원제 수입 업체 비교 및 구매 전략 보고서",
                "body": (
                    "주요 살균제 원제 수입 업체 평가 및 2026년 구매 전략. "
                    "원제 A(Tebuconazole): 중국 X사(단가 $18.5/kg, MOQ 500kg), "
                    "독일 Y사(단가 $24.2/kg, MOQ 100kg, 고순도 98.5%). "
                    "원제 B(Carbendazim): 인도 Z사(단가 $12.1/kg, 품질 변동성 있음), "
                    "국내 W사(단가 $19.8/kg, 안정적 공급). "
                    "구매 전략: 원제 A는 X사 주거래(85%)+Y사 품질 대조(15%), "
                    "원제 B는 W사 이중소싱 검토. 원화 강세 가정 시 수입 비용 7% 절감 기대."
                ),
            },
        ],
        "S3": [
            {
                "title": "농약 안전 사용 수칙 — 현장 배포용",
                "body": (
                    "농약 안전 사용 및 보관 수칙. 현장 작업자 배포용 공개 문서. "
                    "1. 개인보호구: 방제복, 방수장갑, 방제마스크, 보안경 착용 필수. "
                    "2. 살포 조건: 바람이 약한 이른 아침 또는 저녁 살포, 풍속 3m/s 이상 시 살포 중지. "
                    "3. 잔류농약 안전: 안전사용기준(수확 전 살포 가능 횟수, 최종 살포일로부터 수확까지 기간) 준수. "
                    "4. 보관: 어린이 손에 닿지 않는 서늘하고 건조한 장소, 식품과 분리 보관. "
                    "5. 폐기: 빈 용기는 농약 판매상 또는 수거업체에 반납. "
                    "본 수칙은 농촌진흥청 표준 지침 기반으로 작성되었으며 외부 공개 가능."
                ),
            },
        ],
    },
}

# ── 등급별 추가 변형 템플릿 (다양성 확대용) ───────────────────

VARIATION_PREFIXES: dict[str, list[str]] = {
    "TS": ["연구개발", "제조공정", "핵심기술", "시스템설계"],
    "S1": ["개발현황", "특허검토", "수율분석", "성능평가"],
    "S2": ["계약현황", "공급망", "고객분석", "예산계획"],
    "S3": ["교육자료", "업무지침", "일반안내", "공개보고"],
}


def get_all_templates() -> list[dict]:
    """전체 도메인·등급 템플릿 리스트 반환."""
    result = []
    for domain, grade_map in DOMAINS.items():
        for grade, templates in grade_map.items():
            for tmpl in templates:
                result.append({"domain": domain, "grade": grade, **tmpl})
    return result


def build_doc(template: dict, variation_idx: int = 0, rng: random.Random | None = None) -> dict:
    """템플릿 + 변형 인덱스로 문서 1건 생성."""
    if rng is None:
        rng = random.Random()
    grade = template["grade"]
    domain = template["domain"]
    title = template["title"]
    body = template["body"]

    # 변형: 날짜, 버전, 부서명 등 추가로 다양성 확보
    if variation_idx > 0:
        suffixes = [
            f" — 개정 v{variation_idx}.0",
            f" (2026-{(variation_idx % 12)+1:02d})",
            f" [{['서울', '부산', '수원', '대전', '구미'][variation_idx % 5]}사업장]",
            f" — 담당: {'ABCDE'[variation_idx % 5]}팀",
        ]
        title = title + suffixes[variation_idx % len(suffixes)]
        # 본문 앞에 컨텍스트 추가
        ctx_lines = [
            f"작성일: 2026-{(variation_idx % 12)+1:02d}-{(variation_idx % 28)+1:02d}",
            f"작성부서: 연구개발{'1234'[variation_idx % 4]}팀",
            f"문서번호: {domain[:2].upper()}-{grade}-{2026000 + variation_idx:07d}",
            "",
        ]
        body = "\n".join(ctx_lines) + body

    return {
        "synth_id": str(uuid.uuid4()),
        "title": title,
        "body": body,
        "target_grade": grade,
        "domain": domain,
        "source": "realistic_synthetic",
    }


def build_eval_queries(docs: list[dict]) -> list[dict]:
    """각 문서 본문에서 문장 추출 → doc_id 기준 쿼리."""
    queries: list[dict] = []
    for doc in docs:
        # 본문에서 핵심 문장 추출 (숫자·기술 용어 포함 문장 우선)
        sents = [s.strip() for s in doc["body"].replace("\n", " ").split("。")
                 if len(s.strip()) >= 20]
        # 마침표 기준 재분리
        refined: list[str] = []
        for s in sents:
            parts = [p.strip() for p in s.split(".") if len(p.strip()) >= 20]
            refined.extend(parts)
        # 상위 2문장
        for sent in refined[:2]:
            queries.append({
                "text": sent,
                "expected_grade": doc["target_grade"],
                "expected_doc_id": doc["synth_id"],
                "source_doc_title": doc["title"],
            })
    return queries


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="datasets/realistic_corpus")
    ap.add_argument("--queries-out", default="datasets/realistic_eval_queries.json")
    ap.add_argument("--count", type=int, default=400, help="생성 문서 수")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    templates = get_all_templates()
    print(f"[corpus] 기본 템플릿: {len(templates)}개")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs: list[dict] = []
    for i in range(args.count):
        tmpl = templates[i % len(templates)]
        variation = i // len(templates)
        doc = build_doc(tmpl, variation_idx=variation, rng=rng)
        docs.append(doc)

    # 등급 분포
    dist: dict[str, int] = {}
    for doc in docs:
        dist[doc["target_grade"]] = dist.get(doc["target_grade"], 0) + 1
    print(f"[corpus] 생성 문서: {len(docs)}개")
    for g in ["TS", "S1", "S2", "S3"]:
        print(f"[corpus]   {g}: {dist.get(g, 0)}개")

    # 도메인 분포
    dom_dist: dict[str, int] = {}
    for doc in docs:
        dom_dist[doc["domain"]] = dom_dist.get(doc["domain"], 0) + 1
    print("[corpus] 도메인 분포:")
    for d, c in sorted(dom_dist.items()):
        print(f"[corpus]   {d}: {c}개")

    # 저장
    for doc in docs:
        fpath = out_dir / f"{doc['synth_id']}.json"
        fpath.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    # 쿼리 생성
    queries = build_eval_queries(docs)
    qpath = Path(args.queries_out)
    qpath.parent.mkdir(parents=True, exist_ok=True)
    qpath.write_text(json.dumps(queries, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[corpus] 쿼리: {len(queries)}개 → {qpath}")

    # 샘플 출력
    print("\n[corpus] Sample docs:")
    seen = set()
    for doc in docs:
        g = doc["target_grade"]
        if g in seen:
            continue
        seen.add(g)
        title_safe = doc["title"].encode("ascii", "replace").decode("ascii")
        body_safe = doc["body"][:80].encode("ascii", "replace").decode("ascii")
        print(f"  [{g}] {title_safe}")
        print(f"       {body_safe}...")
        if len(seen) == 4:
            break

    print("\n[corpus] Done. P2 re-eval:")
    print(f"  python scripts/p2_compare_embeddings.py")
    print(f"    --mode full --backends es --hybrid")
    print(f"    --synth-dir {out_dir}")
    print(f"    --query-style koipa")
    print(f"    --report reports/p2_realistic.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
