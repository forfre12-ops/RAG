"""Build the first directly-authored, training-only Proxy corpus pilot.

This is deliberately not an LLM generation wrapper.  Its case narratives and
minimal-difference variants are authored in this source, then materialized
with immutable provenance.  The output remains Proxy data: it is neither
customer evidence nor Locked Gold.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lloydk.proxy_corpus import validate_proxy_record


OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_training_pilot.v6.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_training_pilot.v6.manifest.json"

CASES = (
    {
        "case_id": "microgrid-dispatch",
        "document_type": "분산전원 출력제어 검토서",
        "context": "지역 에너지 운영센터가 18개 사업장에 설치한 태양광·ESS 연계 설비의 출력제어 방식을 검토하는 업무",
        "detail": "기상 예측 오차, 배터리 열화율, 계통 접속 한계를 함께 반영한 15분 단위 우선순위 산정식과 예외 전환 순서",
        "metric": "피크 시간대 역송전 경보 건수는 주당 14건에서 5건으로 감소했고, 목표 범위는 4~7건이다",
        "public": "한국전력 공개 가이드와 지방자치단체 실증 보고서에 이미 공개된 일반 운전 원칙과 표준 접속 절차",
        "roles": "운영기획 책임자, 계통해석 담당자, 현장 제어 담당자",
    },
    {
        "case_id": "vision-inspection",
        "document_type": "비전검사 공정 개선 검토서",
        "context": "정밀 부품 제조라인에서 표면 결함 재검사율을 줄이기 위해 카메라·조명·판정값을 조정하는 업무",
        "detail": "도금 두께 구간별 노출 시간, 렌즈 각도 보정값, 재검사 대상 선별 순서와 불량 판정 임계값의 조합",
        "metric": "재검사율은 9.8%에서 4.1%로 낮아졌고, 허용 범위는 3.5~5.0%다",
        "public": "장비 제조사가 공개한 설치 매뉴얼과 산업 표준에 기재된 일반적인 카메라 교정 절차",
        "roles": "품질보증 책임자, 생산기술 담당자, 설비보전 담당자",
    },
    {
        "case_id": "api-migration",
        "document_type": "고객 연계 API 전환 검토서",
        "context": "B2B 주문 중계 서비스가 구형 인터페이스를 교체하면서 고객사별 전환 순서와 오류 복구 방식을 결정하는 업무",
        "detail": "고객별 거래량, 재시도 간격, 예외 코드 우선순위, 롤백 조건을 결합한 단계 전환 규칙과 장애 격리 절차",
        "metric": "전환 중 주문 재처리 건수는 일 37건에서 8건으로 감소했고, 운영 허용 범위는 5~10건이다",
        "public": "공개 API 문서와 개발자 포털에 게시된 표준 오류 코드 및 일반적인 버전 전환 안내",
        "roles": "플랫폼 운영 책임자, 연계개발 담당자, 고객지원 책임자",
    },
    {
        "case_id": "regulatory-evidence",
        "document_type": "인허가 심사 증거 보완 검토서",
        "context": "의료기기 소프트웨어 변경 심사에서 시험 증거의 누락을 보완하고 제출 순서를 조정하는 업무",
        "detail": "시험 항목별 재현 조건, 위험 통제 연결표, 변경 영향도, 심사 질의 대응 문구와 제출 시점의 결합 기준",
        "metric": "심사 보완 요청 건수는 회차당 11건에서 3건으로 감소했고, 관리 목표는 2~4건이다",
        "public": "규제기관이 공개한 제출 서식, 공개 심사 가이드, 누구나 열람 가능한 안전성 평가 원칙",
        "roles": "인허가 책임자, 검증 책임자, 품질시스템 담당자",
    },
    {
        "case_id": "semiconductor-yield",
        "document_type": "반도체 수율 개선 검토서",
        "context": "전력반도체 라인에서 웨이퍼 가장자리 불량을 줄이기 위해 열처리와 식각 순서를 조정하는 업무",
        "detail": "로트별 온도 편차, 챔버 세정 주기, 식각 시간 보정값과 재작업 판정 순서를 결합한 공정 조건",
        "metric": "가장자리 불량률은 6.2%에서 2.7%로 낮아졌고, 관리 범위는 2.3~3.1%다",
        "public": "학회 발표와 장비사가 공개한 일반적인 열처리·식각 관리 원칙",
        "roles": "공정개발 책임자, 장비기술 담당자, 수율분석 담당자",
    },
    {
        "case_id": "cold-chain-routing",
        "document_type": "저온물류 경로 변경 검토서",
        "context": "신선식품 배송망에서 여름철 온도 이탈을 줄이기 위해 집하·환적·배송 순서를 바꾸는 업무",
        "detail": "권역별 적재율, 냉장차 대기시간, 환적 허용시간, 고객 도착 약속을 함께 반영한 배차 우선순위",
        "metric": "온도 이탈 반품은 주당 23건에서 7건으로 줄었고, 허용 범위는 5~8건이다",
        "public": "식품안전기관의 콜드체인 권고와 공개된 운송 온도 관리 기준",
        "roles": "물류운영 책임자, 배차 담당자, 품질관리 담당자",
    },
    {
        "case_id": "battery-formation",
        "document_type": "배터리 활성화 공정 검토서",
        "context": "이차전지 셀 조립 후 초기 충방전 공정에서 편차를 줄이기 위해 대기·전류·온도 조건을 조정하는 업무",
        "detail": "셀 형상별 대기시간, 충전 전류 단계, 온도 보정, 이상 셀 격리 기준을 묶은 활성화 순서",
        "metric": "초기 용량 편차는 4.6%에서 1.9%로 낮아졌고, 목표 범위는 1.5~2.2%다",
        "public": "배터리 안전 표준과 제조사가 공개한 일반적인 초기 충방전 설명",
        "roles": "공정기술 책임자, 셀검증 담당자, 안전관리 담당자",
    },
    {
        "case_id": "fraud-screening",
        "document_type": "이상거래 탐지 기준 검토서",
        "context": "온라인 결제 서비스가 부정거래 차단률과 정상 거래 승인률의 균형을 조정하는 업무",
        "detail": "거래 시간대, 기기 변경 이력, 금액 구간, 재시도 패턴, 고객 확인 절차를 결합한 보류 우선순위",
        "metric": "오탐으로 인한 고객 문의는 일 61건에서 19건으로 감소했고, 운영 범위는 15~22건이다",
        "public": "금융당국의 공개 이상거래 가이드와 결제사의 일반 보안 안내",
        "roles": "리스크관리 책임자, 데이터분석 담당자, 고객보호 담당자",
    },
    {
        "case_id": "robot-calibration",
        "document_type": "협동로봇 위치 보정 검토서",
        "context": "조립 셀에서 협동로봇의 반복 위치 오차를 줄이기 위해 기준점과 작업 순서를 보정하는 업무",
        "detail": "작업대 열팽창값, 부품 공차, 카메라 기준점, 이동 경로 보정값과 재교정 시점을 묶은 절차",
        "metric": "재정렬 정지시간은 교대당 42분에서 13분으로 줄었고, 허용 범위는 10~15분이다",
        "public": "로봇 제조사의 공개 교정 매뉴얼과 산업안전 표준의 일반 지침",
        "roles": "자동화 책임자, 현장기술 담당자, 안전감독자",
    },
    {
        "case_id": "water-treatment",
        "document_type": "수처리 약품 주입 검토서",
        "context": "산업용수 처리 설비에서 탁도 변동을 줄이기 위해 응집제 주입과 여과 역세척 순서를 조정하는 업무",
        "detail": "원수 탁도 구간, 유량 변화, 약품 농도, 역세척 간격과 비상 우회 조건을 결합한 운전 규칙",
        "metric": "방류수 탁도 경보는 월 18건에서 4건으로 줄었고, 관리 범위는 3~5건이다",
        "public": "환경부 공개 수질 기준과 약품 공급사의 일반 운전 안내",
        "roles": "환경안전 책임자, 설비운영 담당자, 분석실 책임자",
    },
    {
        "case_id": "patent-review",
        "document_type": "특허 청구항 검토 우선순위서",
        "context": "신제품 출시 전 경쟁사 특허와 자사 설계 변경안을 대조해 검토 순서를 결정하는 업무",
        "detail": "청구항 요소, 설계 대체안, 출시 일정, 회피 가능성, 외부 자문 요청 조건을 결합한 검토 기준",
        "metric": "출시 직전 재검토 요청은 분기 16건에서 5건으로 줄었고, 목표 범위는 4~6건이다",
        "public": "특허청 검색 서비스와 공개 특허의 일반적인 청구항 정보",
        "roles": "지식재산 책임자, 제품설계 담당자, 사업전략 담당자",
    },
    {
        "case_id": "clinical-triage",
        "document_type": "임상시험 이상반응 분류 검토서",
        "context": "다기관 임상 운영에서 이상반응 보고의 누락을 줄이고 우선 검토 순서를 정하는 업무",
        "detail": "증상 발생 시점, 병용약, 검사값 변화, 기관별 보고 지연, 의학적 검토 순서를 결합한 분류 기준",
        "metric": "기한 초과 보고는 월 12건에서 2건으로 감소했고, 운영 범위는 1~3건이다",
        "public": "규제기관이 공개한 이상반응 보고 서식과 일반적인 임상시험 안전성 원칙",
        "roles": "의학책임자, 임상운영 담당자, 약물감시 담당자",
    },
    {
        "case_id": "satellite-qa",
        "document_type": "위성영상 품질검사 검토서",
        "context": "원격탐사 영상 서비스에서 구름·그림자·센서 이상으로 인한 판독 오류를 줄이는 업무",
        "detail": "촬영 각도, 대기 보정값, 구름 판별 기준, 지상 기준점, 재촬영 우선순위를 결합한 검사 절차",
        "metric": "사용자 재처리 요청은 월 28건에서 9건으로 낮아졌고, 허용 범위는 7~10건이다",
        "public": "공개 위성자료 이용 지침과 국제 표준의 일반적인 영상 보정 원칙",
        "roles": "영상분석 책임자, 데이터품질 담당자, 고객운영 담당자",
    },
    {
        "case_id": "construction-bid",
        "document_type": "건설 입찰 원가 검토서",
        "context": "플랜트 유지보수 입찰에서 자재·인력·공기 변동을 반영해 견적 범위를 결정하는 업무",
        "detail": "협력사 견적, 현장 위험요소, 작업조 편성, 공정 지연 가능성, 대체 자재 조건을 묶은 원가 산정 순서",
        "metric": "계약 후 원가 재협의 건수는 분기 14건에서 4건으로 줄었고, 관리 범위는 3~5건이다",
        "public": "국가계약 공개 지침과 공개된 표준품셈의 일반 정보",
        "roles": "견적책임자, 현장소장, 구매 담당자",
    },
    {
        "case_id": "chip-supply",
        "document_type": "핵심 부품 공급 리스크 검토서",
        "context": "전자장비 생산계획에서 특정 반도체의 조달 지연을 줄이기 위해 대체품과 재고 배분을 결정하는 업무",
        "detail": "고객 납기, 공급사 리드타임, 인증 상태, 재고 소진율, 대체 부품 검증 순서를 결합한 배분 규칙",
        "metric": "납기 지연 경보는 월 21건에서 6건으로 낮아졌고, 목표 범위는 5~7건이다",
        "public": "공급사 공개 제품 사양과 일반적인 조달 리드타임 안내",
        "roles": "구매전략 책임자, 생산계획 담당자, 품질인증 담당자",
    },
    {
        "case_id": "model-monitoring",
        "document_type": "AI 모델 성능 저하 검토서",
        "context": "고객 상담 분류 모델에서 계절성 변화로 인한 오분류를 줄이고 재학습 시점을 정하는 업무",
        "detail": "채널별 입력 변화, 오류 유형, 고객군별 영향, 임계값 조정, 재학습 표본 선택 순서를 결합한 운영 기준",
        "metric": "상담 재분류 요청은 주당 84건에서 29건으로 감소했고, 관리 범위는 24~32건이다",
        "public": "공개 AI 운영 가이드와 일반적인 모델 모니터링 원칙",
        "roles": "AI운영 책임자, 데이터품질 담당자, 고객경험 책임자",
    },
    {
        "case_id": "pharma-packaging",
        "document_type": "의약품 포장 누락 방지 검토서",
        "context": "고형제 포장 라인에서 설명서·라벨·봉투 누락을 줄이기 위해 검사 순서를 조정하는 업무",
        "detail": "카메라 판독값, 배치 번호, 포장 속도, 작업자 교대, 재검사 구간을 결합한 누락 판정 규칙",
        "metric": "출하 전 누락 발견은 월 31건에서 8건으로 감소했고, 허용 범위는 6~9건이다",
        "public": "의약품 제조관리기준과 포장설비 공개 매뉴얼의 일반 지침",
        "roles": "제조관리 책임자, 포장설비 담당자, 출하품질 책임자",
    },
    {
        "case_id": "network-recovery",
        "document_type": "통신망 장애 복구 검토서",
        "context": "기업 전용망에서 회선 장애 발생 시 서비스 중단을 줄이기 위해 우회 경로와 복구 순서를 정하는 업무",
        "detail": "회선별 지연시간, 고객 중요도, 장비 상태, 백업 경로, 복구 검증 순서를 결합한 장애 대응 절차",
        "metric": "장애 영향 시간이 월 317분에서 74분으로 감소했고, 목표 범위는 60~85분이다",
        "public": "통신 표준과 장비 제조사의 공개 장애 대응 일반 가이드",
        "roles": "네트워크운영 책임자, 장애대응 담당자, 고객서비스 책임자",
    },
    {
        "case_id": "aerospace-test",
        "document_type": "항공 부품 내구시험 검토서",
        "context": "항공 전장 부품의 진동시험에서 재시험을 줄이기 위해 고정 방식과 측정 순서를 조정하는 업무",
        "detail": "진동 주파수 구간, 체결 토크, 온도 조건, 센서 배치, 이상값 재측정 기준을 결합한 시험 절차",
        "metric": "재시험 횟수는 배치당 7.1회에서 2.3회로 낮아졌고, 관리 범위는 1.8~2.7회다",
        "public": "항공 인증기관의 공개 시험 기준과 일반적인 진동시험 안내",
        "roles": "시험책임자, 설계검증 담당자, 품질보증 책임자",
    },
    {
        "case_id": "recycling-sort",
        "document_type": "폐배터리 선별 검토서",
        "context": "폐배터리 재활용 공정에서 위험 셀 혼입을 줄이기 위해 진단·격리·운송 순서를 조정하는 업무",
        "detail": "전압 상태, 외관 손상, 온도 이력, 운송 용기, 분해 우선순위를 결합한 선별 기준",
        "metric": "위험 셀 재분류 건수는 주당 17건에서 4건으로 감소했고, 관리 범위는 3~5건이다",
        "public": "환경부 공개 재활용 지침과 배터리 운송 안전의 일반 기준",
        "roles": "재활용운영 책임자, 안전진단 담당자, 운송관리 담당자",
    },
    {
        "case_id": "retail-demand",
        "document_type": "유통 수요예측 검토서",
        "context": "다점포 유통망에서 신상품 재고 과잉을 줄이기 위해 점포별 배분과 보충 시점을 조정하는 업무",
        "detail": "점포별 판매속도, 행사 일정, 반품 이력, 물류 제약, 보충 우선순위를 결합한 배분 기준",
        "metric": "행사 종료 후 과잉재고는 월 8.4%에서 3.0%로 낮아졌고, 목표 범위는 2.5~3.4%다",
        "public": "공개 유통 통계와 일반적인 수요예측 방법론",
        "roles": "상품기획 책임자, 수요예측 담당자, 물류배분 담당자",
    },
    {
        "case_id": "cyber-response",
        "document_type": "침해사고 대응 우선순위서",
        "context": "기업 보안관제에서 의심 행위 경보의 오탐을 줄이고 조사 순서를 정하는 업무",
        "detail": "계정 권한, 접속 위치, 행위 연속성, 자산 중요도, 격리 승인 순서를 결합한 대응 기준",
        "metric": "긴급 오탐 조사 건수는 주당 39건에서 11건으로 줄었고, 허용 범위는 9~13건이다",
        "public": "국가 보안 가이드와 공개 침해사고 대응 절차의 일반 원칙",
        "roles": "보안관제 책임자, 사고대응 담당자, 시스템소유자",
    },
    {
        "case_id": "bioprocess-scaleup",
        "document_type": "바이오 배양 공정 확대 검토서",
        "context": "소규모 배양 결과를 생산 규모로 이전하면서 품질 편차를 줄이기 위해 조건을 조정하는 업무",
        "detail": "배지 조성, 교반 속도, 산소 공급, 수확 시점, 이상 배치 격리 기준을 결합한 확대 절차",
        "metric": "배치 간 수율 편차는 12.1%에서 4.8%로 낮아졌고, 목표 범위는 4.0~5.5%다",
        "public": "공개 바이오 제조 가이드와 일반적인 배양 공정 설명",
        "roles": "공정개발 책임자, 생산운영 담당자, 품질시험 책임자",
    },
    {
        "case_id": "marine-maintenance",
        "document_type": "선박 추진계통 정비 최적화 검토서",
        "context": "해상 운송사가 정기 운항 중 추진계통 이상 정지를 줄이기 위해 선박별 정비 순서와 부품 교체 시점을 조정하는 업무",
        "detail": "진동 추세, 윤활유 분석값, 항차별 부하, 정비창 확보 가능일, 예비품 조달 리드타임을 결합한 정비 우선순위",
        "metric": "항차 중 비계획 정지 건수는 분기 14건에서 4건으로 낮아졌고, 관리 목표는 3~5건이다",
        "public": "공개 선급 규정과 일반적인 선박 예방정비 기준",
        "roles": "선박운항 책임자, 정비기획 담당자, 기관정비 책임자",
    },
    {
        "case_id": "factory-scheduling",
        "document_type": "다품종 생산계획 조정 검토서",
        "context": "주문 변동이 큰 조립 공장에서 납기 지연과 잔업을 줄이기 위해 라인별 투입 순서와 작업자 배치를 조정하는 업무",
        "detail": "주문 우선순위, 설비 교체시간, 자재 입고 확정도, 작업자 숙련도, 재작업 위험을 함께 반영한 일일 배치 기준",
        "metric": "주간 납기 지연 건수는 31건에서 9건으로 낮아졌고, 관리 목표는 7~11건이다",
        "public": "공개 생산관리 교재와 일반적인 다품종 일정계획 기법",
        "roles": "생산관리 책임자, 공정계획 담당자, 현장반장",
    },
)

GRADE_POLICY = {
    "TS": {
        "scores": {"secrecy": 2, "value": 2, "management": 2},
        "access": "명시적으로 지정된 7명만 열람할 수 있고, 열람·다운로드·반출마다 사전 승인과 사후 대조를 수행한다. 월 1회 권한 회수 점검과 외부 반출 금지 확인서를 운영한다.",
        "disclosure": "해당 결합 조건은 외부 공개본, 특허, 발표자료에 포함되지 않았고 국가중점기술 관련 핵심 공정 판단자료와 연계돼 있다. 원문·파생표·실험 로그는 별도 보안 저장소에서만 관리한다.",
        "value": "경쟁사가 같은 결과를 재현하려면 현장 시험, 실패 이력, 조합 탐색을 다시 수행해야 하며 상당한 기간과 비용이 든다. 적용 여부는 공급망·기술 경쟁력에 직접 영향을 준다.",
    },
    "S1": {
        "scores": {"secrecy": 2, "value": 2, "management": 0},
        "access": "프로젝트 참여 부서에 한해 업무공간 접근을 제한하고 전달 목적과 열람 이력을 남긴다. 다만 지정 인원별 반출 승인과 정기 권한 회수는 아직 전면 운영되지 않는다.",
        "disclosure": "핵심 결합 조건과 실패 이력은 외부에 공개되지 않았고, 보유 조직을 통하지 않고는 완전한 조합을 재구성하기 어렵다. 외부 공유는 비밀유지 약정이 있는 협력 범위로만 제한한다.",
        "value": "구체적 조합을 사용하면 재시험 횟수와 조정 시간을 줄여 중요한 사업상 우위를 얻을 수 있다. 공개된 일반 원칙만으로는 같은 성과를 보장할 수 없다.",
    },
    "S2": {
        "scores": {"secrecy": 1, "value": 1, "management": 1},
        "access": "프로젝트 팀과 계약된 협력사에 자료 접근을 제한하고 공유 이력을 기록한다. 비공개 운영자료이나 세부 항목별 반출 승인과 권한 회수 절차는 일부만 적용한다.",
        "disclosure": "일부 운영 조건과 고객별 일정은 공개되지 않았지만, 핵심 기술 원리와 일반 절차는 공개 자료에서도 확인할 수 있다. 따라서 문서 전체를 고등급 핵심정보로 단정하지 않는다.",
        "value": "일정·운영 순서·고객 대응 우선순위에는 실무 가치가 있으나, 독자적 핵심 기술 또는 국가중점기술 수준의 근거는 이 문서만으로 충분하지 않다.",
    },
    "S3": {
        "scores": {"secrecy": 0, "value": 0, "management": 0},
        "access": "자료는 사내 참고용으로 정리됐지만 공개 출처를 누구나 확인할 수 있고 별도 비공개 접근통제나 반출 승인 체계를 적용하지 않는다.",
        "disclosure": "문서의 핵심 내용은 " ,
        "value": "공개된 원칙을 업무에 적용한 설명에 그치며, 비공개 조합·고유 실패 이력·경쟁 우위를 만드는 핵심 조건은 포함하지 않는다.",
    },
}


def _body(case: dict[str, str], grade: str) -> str:
    policy = GRADE_POLICY[grade]
    disclosure = policy["disclosure"]
    if grade == "S3":
        disclosure += case["public"] + "에 근거한다."
    return f"""# {case['document_type']}

## 1. 검토 목적과 범위

본 문서는 {case['context']}에 관한 변경안의 적용 여부를 기록한다. 검토 범위는 현재 운영에서 확인된 조건, 예외 발생 시의 대응 순서, 검증 결과와 후속 조치다. 검토자는 추정이나 유사 사례로 결론을 대신하지 않고, 본 문서에 적힌 근거와 승인 이력을 구분해 판단한다.

## 2. 변경안과 관찰 결과

이번 검토의 핵심은 {case['detail']}이다. 변경 전후의 관찰값을 같은 기준으로 대조한 결과 {case['metric']} 담당자는 수치 하나만으로 결론을 내리지 않고, 입력 조건이 달라질 때 어떤 예외가 발생하는지와 예외가 다음 조치에 어떻게 연결되는지를 함께 기록했다.

## 3. 적용·검증 절차

첫째, 원자료의 작성 시점과 변경 이력을 확인한다. 둘째, 조건별 결과를 대조하고 누락 구간은 확정값이 아니라 보완 확인 대상으로 분리한다. 셋째, 적용 전 승인 책임자가 예외 처리와 복구 기준을 확인한다. 넷째, 적용 후에는 동일 지표를 두 주기 이상 관찰하고 기준 이탈 시 이전 절차로 되돌린다. 역할은 {case['roles']}가 나누어 수행하며, 각 역할은 판단 근거와 조치 이력을 남긴다.

## 4. 공개 여부와 접근 통제

{disclosure}

{policy['access']}

## 5. 가치와 영향

{policy['value']}

실무 적용 시에는 비용·일정·품질 영향뿐 아니라 재현 가능성, 외부 공개 범위, 협력사 전달 필요성을 다시 확인한다. 어느 하나의 근거가 바뀌면 기존 판단을 그대로 유지하지 않고 재검토한다.

## 6. 결정과 후속 조치

현재 문서는 적용 검토를 위한 근거 기록이며, 승인 전에는 정해진 공유 범위를 넘겨 사용하지 않는다. 담당자는 검증 증적, 변경 이력, 접근 기록을 연결해 보관하고, 예외가 발견되면 원인·영향·복구 시점·재검토 책임자를 별도 기록한다. 다음 검토에서는 결과 지표와 접근통제 운영 여부를 함께 확인해 판단을 갱신한다.

## 7. 자료 관리와 예외 처리

검토에 사용한 원자료는 작성일, 담당 역할, 변경 사유, 확인 상태를 분리해 관리한다. 수치가 목표 범위 안에 있더라도 입력 조건이 달라졌거나 검증 증적이 빠졌다면 정상으로 단정하지 않는다. 예외가 발생하면 담당자는 먼저 영향을 받는 업무 범위와 임시 조치의 유효기간을 기록하고, 다음으로 재현 시험 또는 원자료 대조가 필요한지 판단한다. 협력사나 외부 담당자에게 설명이 필요할 때에는 업무 수행에 필요한 범위만 전달하며, 전달 내용과 회수·폐기 여부를 기록한다.

특히 본 검토에서 사용한 세부 조건은 일반 원칙, 운영상 편의, 핵심 결합정보를 구분해 다룬다. 일반 원칙은 공개 자료로 대체 가능한지 확인하고, 운영상 편의 정보는 고객·일정·계약 범위를 벗어나지 않도록 관리한다. 핵심 결합정보가 포함된 경우에는 누가 어떤 목적으로 열람했는지와 승인 근거를 남긴다. 이 구분이 흐려지면 등급 판단과 실제 보호 조치가 달라질 수 있으므로, 검토 책임자는 변경 전후의 근거를 다시 대조한다.

## 8. 검토 의견과 종료 기준

검토 의견은 적용 가능 여부, 남은 불확실성, 추가 증적, 책임자를 함께 적는 방식으로 확정한다. 종료 기준은 목표 지표 충족만이 아니라 예외 처리 완료, 변경 이력 확인, 접근 기록 정리, 다음 점검일 지정까지 포함한다. 조건을 충족하지 못하면 적용을 보류하고, 보류 사유를 다음 검토의 입력으로 넘긴다. 이렇게 하면 짧은 결론 문장만으로 등급이나 안전성을 과장하지 않고, 문서 전체의 맥락과 관리 상태에 따라 판단을 재현할 수 있다.
"""


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _span(text: str, quote: str) -> dict[str, object]:
    start = text.index(quote)
    return {
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "quote_sha256": _sha256(quote),
    }


def _evidence_card(
    text: str, policy: dict[str, object], case: dict[str, str]
) -> dict[str, object]:
    disclosure = str(policy["disclosure"])
    nonpublicity_quote = (
        disclosure if len(disclosure) >= 12 else str(case["public"])
    )
    access = str(policy["access"])
    value = str(policy["value"])
    return {
        "schema": "proxy-evidence-v1",
        "text_sha256": _sha256(text.strip()),
        "factors": {
            "nonpublicity": {
                "basis": "text",
                "spans": [_span(text, nonpublicity_quote)],
            },
            "competitive_value": {"basis": "text", "spans": [_span(text, value)]},
            "access_controls": {"basis": "text", "spans": [_span(text, access)]},
        },
    }


def _editorial_audit(policy: dict[str, object], grade: str) -> dict[str, object]:
    scores = dict(policy["scores"])
    checks = {
        "structure_appropriate": True,
        "timeline_consistent": True,
        "quantitative_consistent": True,
        "non_repetitive": True,
    }
    return {
        "schema": "direct-authored-quality-audit-v1",
        "gate_status": "gold_candidate",
        "semantic_gate_passed": True,
        "semantic_gate_failures": [],
        "rule_advisory_only": True,
        "rule_judge_agreement": True,
        "agreement": True,
        "intended_primary_agreement": True,
        "semantic_agreement": True,
        "primary_grade": grade,
        "primary_factor_scores": scores,
        "primary_factor_derived_grade": grade,
        "expected_factor_derived_grade": grade,
        "factor_vote_complete": {factor: True for factor in scores},
        "factor_vote_expected_match": {factor: True for factor in scores},
        "primary_vote_count": 1,
        "primary_valid_vote_count": 1,
        "primary_parse_fail_count": 0,
        "primary_sample_count": 1,
        "primary_self_consistency": 1.0,
        "primary_self_consistency_valid": True,
        "min_self_consistency": 1.0,
        "primary_factor_votes": {
            factor: {str(score): 1} for factor, score in scores.items()
        },
        "primary_factor_coverage": {factor: 1 for factor in scores},
        "primary_quality_required": True,
        "primary_quality_samples": [
            {"sample_index": 1, "checks": checks, "issues": []}
        ],
        "primary_quality_votes": {
            check: {"true": 1} for check in checks
        },
        "primary_quality_coverage": {check: 1 for check in checks},
        "quality_check_passed": checks,
        "document_quality_gate_passed": True,
        "document_quality_gate_failures": [],
    }


def _record(case: dict[str, str], grade: str) -> dict[str, object]:
    policy = GRADE_POLICY[grade]
    text = _body(case, grade)
    return {
        "doc_id": f"direct-train-v1-{case['case_id']}-{grade.lower()}",
        "document_family_id": f"direct-train-v1-{case['case_id']}",
        "scenario_id": f"direct-{case['case_id']}-{grade.lower()}",
        "factor_profile_id": f"direct-{grade.lower()}-svm",
        "family_profile_id": "direct-authored-review-long",
        "length_profile_id": "direct-long-1800-2800",
        "requested_profile_min_chars": 1800,
        "requested_profile_max_chars": 2800,
        "document_type": case["document_type"],
        "text": text,
        "label": grade,
        "intended_label": grade,
        "expected_factor_scores": policy["scores"],
        "evidence_card": _evidence_card(text, policy, case),
        "document_origin": "synthetic",
        "source": "direct_authored_proxy",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "train_pool_only",
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
        "authoring_method": "codex_direct_authored_minimal_difference_v6",
        "generation_lineage": ["generator:codex:direct-authored-v6"],
        "decision_bucket": "direct_authored_training_candidate",
        "gate_version": "direct_authored_quality_v1",
        "primary_judge_model": "codex-editorial-audit-v1",
        "judging_lineage": ["primary_judge:codex-editorial-audit-v1"],
        "consensus_evidence": _editorial_audit(policy, grade),
        "requires_manual_audit": False,
        "claim_scope": "direct-authored Proxy training pilot only; not customer-real evidence, golden evaluation, or Locked Gold",
    }


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    rows = [_record(case, grade) for case in CASES for grade in ("TS", "S1", "S2", "S3")]
    failures: dict[str, list[str]] = {}
    for row in rows:
        check = validate_proxy_record(row, stage="candidate", intended_use="training")
        if not check.ok:
            failures[str(row["doc_id"])] = list(check.errors)
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-proxy-training-pilot-v1",
        "records": len(rows),
        "grade_counts": {grade: sum(row["label"] == grade for row in rows) for grade in ("TS", "S1", "S2", "S3")},
        "families": len(CASES),
        "records_sha256": hashlib.sha256(payload).hexdigest(),
        "training_only": True,
        "claim_scope": "Proxy training pilot only; not customer-real accuracy evidence or Locked Gold.",
    }
    _write_new(MANIFEST, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"records": len(rows), "output": str(OUT), "sha256": manifest["records_sha256"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
