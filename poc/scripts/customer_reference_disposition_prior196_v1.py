"""Bounded authored review of the original prior 196, not an automatic grader.

REVIEW_NOTES was written after reading every body, synthetic context and
rationale. Code binds those decisions to the pinned source; it does not infer
semantic correctness from phrases, arithmetic, prior labels or CV scores.
"""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

import build_customer_guide_batch06 as source_batch
from koipa.customer_benchmark import FLAGS, strict_loads
from koipa.customer_reference_audit_v1 import audit_reference
from koipa.policy_facts import require, text_digest, value_digest

SOURCE_MANIFEST_SHA256 = (
    "fbcc96d012a3b87f4bd3fd6ba6fea9f62ef1d9c7f5e39b5a416fec7e91285a19"
)
POLICY_SHA256 = "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"
DEFAULT_PARENT = POC / "reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6"
SCHEMA = "customer-source-disposition-v1"
BENCHMARK_HOLD = "이 원문은 이미 개발·진단에 노출됐고 문체·제목·주제 단서 및 계열 독립성의 품질 경고를 해소하지 않았으므로 시험자료 채택은 보류한다."

# Columns: family key | body-specific reading | acquisition-attribution reading.
# These are human-readable authored review notes, not generated from labels.
REVIEW_NOTES = """
acoustic-joint|46·41dB 차이를 지정 수음점의 5dB로 한정하고 전 대역으로 확장하지 않는다.|이음부 비교와 대역 분석을 얻은 시험 투입이며 제3자 시험소 취득 경로와 양립한다.
action-log|담당 배정과 완료 결과물을 구별하고 날짜 변경·취소 이력을 남기는 안내다.|전체 직원용 후속 조치 안내 작성 투입이고 외부 공개와 사내 공지를 구별한다.
adhesion-window|10mm 폭과 동일 경과시간 아래 습도별 하중을 비교하고 기재 파손을 분리한다.|반복 접합·환경 계측 취득비이며 완료 후 전문 공개 설정과 고비용은 모순되지 않는다.
alignment-fit|잔차 평균0.04와 반대 부호 보정 후 네 잔차가 일치하며 다른 렌즈에는 복사하지 않는다.|네 위치 잔차를 확정한 정밀 계측비를 산술식 작성비와 명시적으로 분리한다.
archive-box|38개 중 라벨 교체6개를 뺀32개는 빈 상자의 배치 대상이며 도착 전 완료를 막는다.|위치·라벨 대조 작성비만 귀속하고 상자 속 문서 취득비나 시설비를 제외한다.
archive-listing|파일14와 디렉터리5의 목록19개를 파일 내용 확인이나 임시공간 보장과 구별한다.|공개 교육 예제 목록과 안내 작성비이고 기록 전체 공개라는 가정이다.
assembly-log|두 시제품의 간격재·간섭 조치와 열 번 재확인을 적고 설계 확정을 분리한다.|이번 조립 관찰 정리의8인시만 귀속하고 설계 전체 개발비를 합산하지 않는다.
attendance-count|14명과12명의 겹침5명을 빼21명, 참석 사건26건을 구별하고 미확인을 보존한다.|익명 중복 대조·조건 정리비이며 제한된 제3자 취득과 역할별 열람 가정이다.
baffle-splash|같은 액량·5분 포집 비교36g과22g의 차이는14g이며 벽면 잔류는 제외한다.|해당 배플 비교·질량 분석의 직접 시험비이고 공동 시험소 취득 설명이 있다.
batch-resume|75행 중 저장 완료28행을 빼47행이며 메모리 처리와 영속 완료를 구별한다.|이 재개 경계의 소규모 검증비만 계상하고 응용 제품 전체 개발비는 제외한다.
beam-aperture|125와103의 감소22를 빔 잘림·산란으로 분해하지 않으며 광원 설정을 고정한다.|개구 위치별 손실과 정렬 영향을 얻은 광학 시험비이며 장비 구매비와 다르다.
bearing-preload|왼쪽 고정 뒤 회전·오른쪽 고정을 순서화하고 걸림 시 중지하며 조립판B에 한정한다.|이번 순서와 간격재 비교의 소규모 투입이고 인명별 승인 접근 설정이다.
bid-capacity|겹치는 주간의360+280인시가600보다40 부족하며 미계약 지원을 넣지 않는다.|실행 자원·이행 조건을 도출한 조사비로 본문 가용 인시 자체를 개발 투입으로 쓰지 않는다.
binder-tabs|파란34장과회색26장의 합60이며 탭 색을 문서 상태나 보안등급으로 쓰지 않는다.|계수·위치 정리비와 외부 편집업체의 제한 취득 가정이 본문 인계와 양립한다.
bushing-clearance|동일 예압에서0.18-0.11=0.07mm이며 영점 재설정 판독을 다른 차수로 둔다.|부시 간격·접촉 위치 시험비이고 협력 시험소에서도 같은 판본을 얻는 설정이다.
cache-expiry|10초 저장+30초 유효의40초 경계를 포함해 만료시키며 재시도가 만료를 늘리지 않는다.|시험 시계 경계 재현·기록 작성의 한정된 비용이며 인명 승인 접근 가정이다.
cache-victim-trace|240요청 중36재읽기15%와 구간Q의 두 창·쓰기 대기 제외 조건을 분리한다.|선택 규칙 도출을 위한 추적·부하 재현비이며 서버 자산·제품 전체 개발비를 제외한다.
calibration-label|19대 중5대 표기 교체이고 날짜 형식 수정은 교정일이나 적합성 갱신이 아니다.|식별자·상태표 작성비만 귀속하고 기준기 가격·이전 교정사업비를 제외한다.
capacity-allocation|1200시간 중900배정 후300잔여이며 두 후보380은80초과하고 미승인 교대를 제외한다.|가용 시간·기여액 조합을 얻은 설비 검증·원가 조사비이며 거래대금을 비용으로 세지 않는다.
capsule-hold|동일 침지시간의31-19=12mg과 조성C의 습도45%·경화 후 시간 기준이 결합된다.|조성C의 숙성·방출 대응을 얻은 반복 제조·성분 분석비이며 포장 설비비를 제외한다.
cart-wheel|빈 카트 바퀴24중5교체로19나머지이며 적재 상태까지 점검됐다고 확장하지 않는다.|빈 카트 점검·정리비로 아직 미수행 적재 시험비나 카트 구매가를 더하지 않는다.
case-reopening|동일 증상의 재접수를 동일 원인이나 실패 확정으로 보지 않고 이전 완료 이력을 보존한다.|익명 문의 이력과 재접수 조건 대조비이며 제한 취득·업무 역할 접근 가정이다.
cash-timing|900만원의30%270과잔금630은 조건부 지급 계획이며 송금 실적과 구별한다.|지급구간·계약 조건 비교 작성비이며 계약 대금900만원을 V 투입으로 쓰지 않는다.
catalyst-pulse|촉매L의120초 공급·45초 정지 차이75와215도·초기 두 주기 분리 조건이 있다.|펄스·안정화·분획 조건 도출의 반응 시험비로 촉매 판매가·반응기 자산을 제외한다.
cell-rest-window|35+25=60분 휴지는 충전과 분리하며 중간 부하가 있으면 연속 휴지로 합산하지 않는다.|이번 전지 묶음 시각 대조비이며 전지 개발·수명시험 전체 비용을 제외한다.
ceramic-shrink|50mm에서46mm로4mm 수축하며1180도22분 군과 판독 불가 시편을 구별한다.|성형체C의 유지·수축 대응을 얻은 소성 반복·치수 분석비이며 소결로 자산가와 다르다.
channel-adsorption|80-68=12mg 미회수를 전부 흡착으로 보지 않고 유로C·처리배치K에 제한한다.|처리면·유속·분획별 손실 시험·분석비이며 유로 제조 설비비를 제외한다.
channel-interview|40곳 중18요청45%를 계약과 구별하고 거래처를 겹치지 않게 배정한다.|두 접촉 순서의 표본 설계·실행·응답 검증비이며 예상 수주액을 넣지 않는다.
channel-mix|60×50=3000분과100×20=2000분을 수익 우열로 바꾸지 않고 시나리오로 둔다.|지원 패턴을 확보한 공동 시장 조사비이며 시나리오 매출과 구분하고 제3자 경로가 있다.
coldstore-defrost|48+22=70분 구간과 구역B의1.5도 아래8분 조건을 분리하고 문 개방은 제외한다.|적재·제상·순환 복귀 조건 취득을 위한 반복 계측비이고 냉장고·상품 가액은 제외한다.
colorimeter-check|무광면을 고정하고 재판독 덮어쓰기를 막으며 오염·세척 여부를 측정 조건에 남긴다.|측정면 불일치 확인·작업 지시 정리비이며 실제 측정 전체 개발비를 주장하지 않는다.
common-board|30칸 중4수리로26사용이며 전 직원 위치표 갱신과 게시물 외부 반출을 구별한다.|전 직원용 위치 안내 작성비이고 외부 배포 미승인이라는 M0 예외가 명시된다.
common-locker|52칸 중8수리로44대상이며 개인 칸과 잔존 물품의 변경 보류를 명시한다.|공용 번호 안내는 전 직원 열람이나 외부 공개는 아니며 번호 정리비만 귀속한다.
configuration-origin|기본18과덮어쓰기7의25는 사건수이며 고유키수가 아니고 빈 문자열을 누락으로 바꾸지 않는다.|조건부 상속·빈 값 출처 규명 재현비이며 계약 검증기관의 취득 설명이 있다.
control-spec|간격100·120·150에서26·30·36명령이 맞고20~40제한과 센서 단절 시험 제외가 있다.|정상값 보정 정보의16인시·32만원이며 단순 수식을 고비용으로 부풀리지 않는다.
coolant-poster|동시 유입21도·유출29도의8도 차이를 그림·원표 시간축 차이와 함께 남긴다.|순환수 응답 장기 계측비이며 결과와 회신 전문 공개 설정은 고비용과 양립한다.
course-effect|서로 다른20명 군의 중앙값4회·2회 차이를 개인 향상량이나 인과효과로 확정하지 않는다.|교육 순서 비교를 얻은 공동 실험비이고 교육 매출이 아니며 연구기관 취득 경로가 있다.
delivery-dedup|48+35-12=71의 산술은 맞지만 목록별 고유수 전제가 없고 내부 반복행 처리도 뒤에 남아 있다.|목록 식별자 검토비라는 귀속은 정합하나 비용 맥락이 고유수 보장 전제를 대신하지 않는다.
delivery-window|26요청 중17회신 뒤9대기이며 취소·도착 실적과 구별하고 복수 시간대를 확인한다.|회신 취합·대조비이며 운송팀의 동일 일정 취득 경로와 양립한다.
demo-reservation|대기와 좌석 확정, 참석과 구매를 구별하고 설명용 모형의 공급 조건은 별도다.|예약 안내·문구 점검 작성비이며 안내 전문 공개 설정이다.
desk-allocation|28석에서 서로 안 겹치는3수리·5장비를 빼20석이고 잠깐 이석과 반납을 구별한다.|전 직원용 공간 문답 작성비이며 사내 공유와 외부 미승인을 구별한다.
dielectric-fatigue|40개 중6단락15%와 막D의휴지·온도·두께 조건을 기록하고 다른 두께로 확장하지 않는다.|막D의 휴지·균열 경로 취득 시험·단면 분석비이며 시험기 구매비를 제외한다.
dispatch-planner|병렬40분과C지연 시25+5+15=45분을 구별하고 장비 이동시간0 가정을 명시한다.|작업 시간·가용 구역을 도출한 현장 조사비이며 몇 줄 산술 자체의 비용이 아니다.
dispense-delay|목표80ms에 개방지연12ms를 더해92ms 명령이며 재료 점도 변경에는 재사용하지 않는다.|밸브·재료 조합의 지연 계측·반복 검증비이며 밸브 구매가와 구별한다.
dock-arrival-clock|14+26=40분 하역 전 대기는 실제 하역과 다르고 재진입·시계 보정을 구별한다.|다중 도크 시계·재진입 추적 분석비이며 계약 창고의 동일 규칙 취득 설명이 있다.
downtime-window|13:10~14:00의50분 중대기15를 빼35작업이지만 설비 중단에는 대기도 포함한다.|시작·종료·대기와 중복구간 대조비이며 설비 전체 정비비가 아니다.
drawing-revision|구멍 위치만 바뀐3판을 전달하되 완료품 재작업·현장 적용 승인은 메일에서 결정하지 않는다.|변경 항목 대조·전달 문안비만 귀속하고 도면 전체 개발비를 제외한다.
drying-ramp|10분 동시 센서64·59도의5도차를 내부 건조 완료로 읽지 않고 냉각 전후를 구별한다.|두 위치 온도·단계 조건의 공동 계측비이며 제한된 제3자 경로와 양립한다.
energy-load|20분 적산1.2·1.8·2.4kWh를3.6·5.4·7.2kW로 계산하고 예열을 임의 차감하지 않는다.|계측 환경 조성·시험비로 실제 관측값 취득에 귀속되며 공동기관도 제한 제공한다.
envelope-sort|64중창봉투22를 뺀42는 유형 구분이며 들뜬 접착면 제외 시점은 더 명확히 할 수 있다.|계수·인계 작성비와 위탁 우편실의 제한 취득 가정은 내용상 양립한다.
event-sequence|68알림 중11재전송 제외57최초발송을 사건수와 구별하며 미표시 중복은 별도 확인한다.|알림 순서·사건 대응의 소규모 시험비이며 전체 알림 서비스 개발비를 제외한다.
exhibit-power|3×12+24=60W의 표시전력 합이며 충전기·현장 전기안전 승인은 별도다.|배치 확인·안내 작성비만 귀속하고 회신 전문 외부 공개를 가정한다.
expense-duplicate|같은 금액만으로 중복을 확정하지 않고 식별번호·지급 상태·원접수를 보존한다.|익명 증빙 대조·절차 정리비이며 개별 승인된 저장소 접근 가정이다.
expense-template|실제 거래가 없는 양식에서 미확인을0으로 채우지 않고 세금 기준·수식 변경을 남긴다.|빈 비교표 안내 작성비이며 실제 거래 정보의 가치나 권한을 승계하지 않는다.
export-columns|열 이름과 순서의 매핑, 날짜 결측의 원인, 양식 버전·파일 해시를 따로 확인한다.|수신 양식·내보내기 열 대응 대조비이며 명시된 인명별 접근 가정이다.
failure-spectrum|800Hz대역5dB 감소와1600Hz불변을 구별하고dB차이를 선형 퍼센트로 바꾸지 않는다.|대역별 소음·원인 조건을 취득한 공동시험비이며 계약기관의 동일 요약 제공 가정이다.
fair-order-guide|유효 신청을 센다고 하면서28+17=45접수에 취소를 별도 남겨 유효와 접수 경계가 불명확하다.|전 직원용 접수 안내 작성비는 정합하지만 취소 포함 여부를 정하는 본문 조건은 아니다.
fermentation-feed|18+7=25mL보정은 산소35%미만6분에 구간당1회이며 배양조F·배지4에 한정한다.|배양조·배지의 산소 조건·보정 규칙 도출비이고 매출·시설비를 제외한다.
film-pinhole|같은 면적23-9=14핀홀 차이와 조성R의90초교반·7분정지를 인장강도와 구별한다.|조성R의 제조·현미경 분석으로 얻은 대응 비용이며 개수 차이 산술비가 아니다.
filter-pressure-line|동시68-51=17kPa와 송풍·필터 판본을 묶고 차압만으로 교체를 승인하지 않는다.|오염·송풍별 해석 경계 취득의 반복 계측비이며 필터 구매·시설 설치비를 제외한다.
financing-window|12억원 지급구간20/50/30을20/30/20/30으로 나눠도 총액은 같고 수락은 미정이다.|지급 조건 실사·대안 검증비이며 설비 가격12억원을 취득비로 쓰지 않는다.
fixture-clean|세척·건조·조립 완료를 나누고 미확인 부품을 이상 없음으로 채우지 않는다.|세척 인계 순서 정리·현장 확인비이며 업무 역할 접근 가정이다.
flex-cycle|편 상태32→35mm의3mm차이와2분대기를 묶으며 표점 재표시·피로수명을 구별한다.|굴곡 후 판독 상태·대기·균열 대응의 직접 시험비이며 시험기 자산가를 제외한다.
flow-pulse-fit|96/88=12/11계수는120~180mL/분 구간 한정이며 액체 변경 시 재계량한다.|센서U의 유속별 응답·보정 구간 취득비로 단순 비례식이나 센서 가격이 아니다.
folding-stand|양쪽 걸쇠·평탄 바닥 조건을 요구하고 한쪽 잠금이나 변형 부품 사용을 금한다.|전시대 사용 안내·설치 순서 대조 작성비와 전문 공개 가정이다.
freight-break|두 번4만원씩8만원은 한 번6만원보다2만원 높고 보관비 미포함을 명시한다.|동일 발주 배송 조건 수집·검산비로 본문 운송비와 별개의 귀속이다.
freight-option|840+65=905만원 선지급은 지연 정산과 별개이고 거래군R의48시간 조건을 명시한다.|항로·거래 기록 검증으로 슬롯 조건을 얻은 비용이며 운송료·선지급액이 아니다.
garden-water|150+200=350mL급수는 배출수와 구별하고 한 번 급수로 생장 효과를 단정하지 않는다.|공개 체험 시연·질문 작성비만 귀속한 가정이다.
gauge-offset|25.00기준 대비25.06의+0.06mm를 전 범위 보정으로 확대하지 않는다.|게이지 영점·적용 이력 확인비이며 인명 승인 접근 가정이다.
gel-cure-window|42-27=15mg차이와조성W의32도8분→38도 전환을 묶고 다른 조성에는 쓰지 않는다.|전환·잔류·기공 대응을 얻은 직접 연구비이며 생산라인 전체 구축비를 제외한다.
helpdesk-hours|접수와 답변·조치 완료를 구별하고 기존 접수번호로 추가하며 무관 개인정보를 제외한다.|공개 접수 상태 안내 작성비로 실제 문의 원문의 가치나 권한을 주장하지 않는다.
idea-session|개인 작성·조별 공유·묶기 순서에서 투표를 사업 채택이나 예산 승인으로 보지 않는다.|외부 배포하는 모임 진행 안내 작성비이며 운영권한 부여가 아니다.
image-rejection|서로 겹치지 않는37·19를160에서 빼104후보이며6픽셀·15%조건과 실제결함을 구별한다.|검사판N·배율2의 영상·단면 대응 검증비이며 범용 영상 제품 개발비를 제외한다.
import-reconcile|150-8-12=130은 제외 목록이 겹치지 않는 조건이고 행과 고유 문서를 구별한다.|해당 파일의 오류·중복·집계 대조 직접 투입과 인명 승인 가정이다.
index-layout|동일200질의의중앙값42→31ms와저장18→24GB를 구별하고 모든 질의 개선으로 보지 않는다.|준비된 자료에서 두 색인 반복 시험비만 귀속하고 검색제품 전체 개발비를 제외한다.
ink-wetting|지름5→8mm의3mm변화는 표면처리P·20초 조건이고 희미한 고리·흐린사진은 제외한다.|기재P의 처리·퍼짐 대응 반복 계측비이며 인쇄기·잉크 전체 개발비를 제외한다.
inspection-sampling|상자 상단 편중을 재추출로 고치되 이전표본을 보존하고 표본증가를 합격으로 읽지 않는다.|실제 제품 식별자가 없는 요청 전문을 교육 사례로 공개한 작성·확인비다.
interview-codebook|58문장 중11맥락부족을 빼47코딩하며 복수 이유와 발언시점·인원수를 구별한다.|해당 문장묶음 코드 대조비만 귀속하고 계약 코딩 보조자의 동일판본 취득 가정이다.
invoice-lines|21+8=29행은 수정본 없는 추가 묶음이며 취소행 번호·금액합계를 따로 둔다.|행수·취소 처리 문의 정리비이고 외부 기장업체의 제한 취득 가정이다.
keyboard-focus|6+3=9입력칸을 접힌구역과 구별하나 이동 횟수 질문과 대상 칸수 답은 다를 수 있다.|전 직원 공용 화면 안내 점검비이며 사내 공유가 외부 공개를 뜻하지 않는다.
keyboard-help|검색·선택 저장값과Esc의저장전취소를 구별하고 닫은뒤 목록 초점복귀를 명시한다.|화면 안내 작성·키보드 이동 점검비이며 공개 도움말 판본 한정이다.
label-proof|36교정지 중4재출력 제외32채택이며 잘림2·방향오류2와 이전색상판을 구별한다.|교정 검토·재출력 사유 정리비이고 본 인쇄 발주액은 제외한다.
lead-status|10+5+3=18은 상담당 현재1단계이며 제안과 계약·문의횟수와상담ID를 구별한다.|상담 단계 취합·익명 집계 규칙 확인비와 제한 취득·역할 접근 가정이다.
lift-call-events|22+17=39호출사건은 사람수가 아니며 반복버튼 원시사건과 집계규칙을 보존한다.|수집된 작은 호출 묶음의 단위 대조·메모비이며 제어 작업 비용이 아니다.
lighting-zones|24개를3구역8개로 나누어도 점등시간·전력량·밝기 균일성을 보장하지 않는다.|해당 배치안·운영 조건 비교 투입이며 설치공사 전체 비용을 주장하지 않는다.
line-idle|9~11시와13~15시예약 사이 정리구간을 제외하고 예약이 조작자격을 주지 않는다.|두 시험반 일정 취합비만 귀속하여 공용이라는 표현과 업무필요자 제한이 양립한다.
maintenance-board|복도조명·가구 점검 일정만 담고 고장원인·수리조건·취소 확정을 배제한다.|공간 담당자 일정 안내비이며 계약 점검업체의 제한된 동일 일정 제공과 양립한다.
margin-sensitivity|400×12000+300×18000=1020만원은 주문 확정이 아닌 시나리오이며 고정비 중복을 금한다.|단위기여액·원가 가정 확정 조사비로 판매대금과 구별한다.
market-panel-range|52/80=65%는 해당상권 점포표본이며 신규점포·방문횟수·지속참여율을 구별한다.|계절별 표본 유지·방문 검증비이며 이 문답 전문 공개와 고액 투입이 양립한다.
material-exhibit|반사 비교에서 광원각도를 고정하고 관찰위치만 바꾸며 전시느낌으로 내구성을 판단하지 않는다.|전시 문답·시연 순서 확인비이며 공개 전문 가정이다.
meeting-audio-check|유선4+무선3=7은 준비 마이크이며 연결표시·실제송출·녹음 여부를 구별한다.|전 직원 공용 준비 안내 작성비이고 외부 공개 승인과 구별한다.
membrane-backwash|분당42L와35L의 유량차를7L로 써 문맥상 생략을 추정해야 하므로 차이 단위를 명시해야 한다.|막R의 반복 오염·회복 시험비는 정합하며 단순 뺄셈비나 설비·다른막 개발비가 아니다.
mesh-tension|같은방향26-21=5N/cm 비교이며 뒤집힌 위치를 대응시키고 두점으로 전체균일성을 보지 않는다.|위치별 장력·뒤집기 대응 취득 시험비이며 시험소 제한 취득 경로가 있다.
microbalance-tare|12.48-10.02=2.46g순질량이며 영점버튼 이후값과 받침변경을 분리한다.|이 묶음 영점 처리·인계 기록비이고 장비 전범위 교정비가 아니다.
milestone-slack|순차3+4+2=9일과마감12일의3일여유를 실제사용일로 바꾸지 않고 휴무일을 별도 확인한다.|해당 일정 의존관계·여유구간 대조비로 작업 전체 실행비를 주장하지 않는다.
minutes-vote|14+19=33응답에 중복 제출의 마지막1회만 남기고 불참·선호·출석확정을 구별한다.|설문 중복 제거·유효응답 정리비와 업무필요자 열람 가정이다.
mixture-record|850+140+10=1000g 한 배치와 혼합·거품 관찰만 기록하며 장기안정성은 미검증이다.|한 차례 관찰·정리8인시12만원만 귀속하고 장기 개발비를 주장하지 않는다.
molding-vent|동일재료80개씩의16-7=9미충전차이는 금형T·홈0.04mm에 한정하며 내부기공과 구별한다.|홈·미충전·기공 대응 취득비이며 전체 금형 자산가를 제외한다.
nozzle-standoff|620-545=75μm차이는노즐Z·조성H·간격과속도 복합조건이며 한 조건 효과로 읽지 않는다.|간격·속도·선폭 대응 반복 도포·계측비이고 장비 구매가를 제외한다.
offer-order|27/60=45%신청을 결제와 구별하고 회사지점 중복배정을 금하며 업종 외삽을 막는다.|제안 순서 표본설계·신청 검증 조사비이며 시험상품 판매액을 제외한다.
office-move|18상자 중11이동 뒤7잔여이며 빈 공용 바인더·위치표갱신·외부반출을 구별한다.|전 직원 공용 위치 안내 작성비이고 외부 배포는 미승인이라는 설정이다.
onboarding-form|빈 관리번호·확인란 양식이며 미확인을 완료로 채우지 않고 장비별·일자별 기록을 나눈다.|빈 양식 구성비만 귀속하며 공개권한이 이후 작성된 장비 기록에 이어지지 않는다.
open-balance|40+15=55g의질량합산이며 흔들리는 접시를 평형으로 보거나 저울 정확도를 인증하지 않는다.|공개 교육 절차·시연 준비비이며 기기 교정 또는 실제 정확도 보증은 아니다.
open-fan-map|두점3·5m/s의평균4는 단면평균이 아니며 면적별 합산과 관측·보간을 구별한다.|풍로 분포 장기 측정·교정 취득비이며 해당 일지 전문 공개 가정이다.
open-fiber-test|84-69=15N파단하중차를 단면적당 강도비로 바꾸지 않고 시연값과분포평균을 나눈다.|섬유다발 인장·파단 분석비이며 결과와 안내 전체 공개 가정이다.
open-foam-curve|24-15=9mm압축변형과하중제거 후회복을 다른곡선으로 두고30초유지를 명시한다.|반복 압축·회복 곡선 취득 시험비이며 곡선과 메모 전문 공개 가정이다.
open-heat-map|동시47-35=12도차이이며 범례구간·보간·결측을 구별하고 다른두께로 확장하지 않는다.|해당시험판 온도분포·응답 취득비이며 결과와 절차 공개 가정이다.
open-prism|동일수평축12~38mm의26mm간격이며 두빛점으로 모든파장을 구별하지 않는다.|공개 수업 그림·시연 메모 작성비이며 실제 분광 정확도 인증은 아니다.
open-shadow-show|36-24=12cm차이는전시장 배치·동일물체에 한정하며 흐린경계를 임의 생성하지 않는다.|공개 전시 안내·체험 준비의 한정된 직접 투입이다.
open-sieve|28+44=72구슬과 시작·통과·잔류를 대조하며 체험 비율을 생산수율로 설명하지 않는다.|공개 체험 메모·시연 확인비이고 실제 분말 공정 연구비와 다르다.
open-spring-demo|동일표점14→19cm의5cm변형이며 복원값을 따로 두고 전 하중 비례성을 일반화하지 않는다.|공개 실험 안내·시연 작성비와 실제 공학성능 인증을 구별한다.
open-volume-demo|120+35=155mL는 예상합이며 실제눈금차와흘림을 그대로 기록하고 용기정확도를 보증하지 않는다.|공개 실습 절차·시연 준비비만 귀속한다.
open-weather|09시 시작눈금 확인과13시 첫판독 뒤 오전4mm를 언급하여 기준4의 관측시점이 불명확하다.|공개 체험 일지 작성비는 정합하지만 시간·기준눈금의 연결을 대신하지 않는다.
optical-gate|210-48-27=135는겹치지않는제외이며0.82·3픽셀조건은재검대상이지실제결함확정이 아니다.|렌즈M 영상·절단면 대응으로 재검조건을 취득한 비용이며 범용영상 전체비가 아니다.
oven-atmosphere|각50장8·3결함차5와산소0.8·0.3%를비교하며전환4분시편을 안정군과 분리한다.|분위기·결함 대응의 소성·단면분석비이고 공정 역할 접근 설정이다.
packing-slip|수정본없는39+12=51행을주문수로읽지않고상자번호·정정번호를별도로 유지한다.|명세행·상자 대응 정리비와 외부 포장업체의 제한 취득 가정이다.
page-fold-check|40쪽중18대조뒤22미대조이며빈쪽·물리인접면·읽는순서를구분한다.|접지·페이지 대응 대조비이고 계약 인쇄팀의 동일연결표 취득 설명이 있다.
pallet-layout|4×3×2=24배치수는맞지만본문은시험적재후확인을요청하고안정조건의획득결과가보이지않는다.|rationale의층별안정조건 도출 시험비와본문의후속시험 지시를잇는기존결과범위가 더필요하다.
pantry-stock|46잔중회의실14를빼32휴게실이며세척대기분제외·이동과신규구매를구별한다.|전 직원 공용 위치 안내비이며 외부 배포 미승인 가정이다.
particle-classifier|800-612=188g나머지와입도군B·회전1460·유량38·초기40초를묶고질량과순도를구별한다.|회전·유량·분획경계 취득의 반복분리·입도분석비이며 분리기 가격을 제외한다.
photo-naming|58파일중23확인뒤35이며노출별파일·이름대응·열기실패·촬영묶음을구별한다.|사진 이름·대응표 정리비이고 촬영프로젝트 전체비를 제외한다.
photo-sequence|93사진중17재촬영을빼76최초촬영이며시편번호·차수·이름변경을서로구별한다.|한묶음 사진·차수·경로 대조비이고 시편 연구비·전체촬영비를 제외한다.
procurement-calendar|수요일15시마감뒤수정은자동반영되지않으며접수·구매승인·입고를분리한다.|각반 신청담당 역할의 일정취합·안내비라 공용소모품이라는 제목과 열람제한이 모순되지 않는다.
product-help|표시기호는사용자수동변경이며자동감지가아니고안내는전시모형에한정한다.|문답 전문 공개와3인시작성비이며 공급일정·거래조건을 포함한 자료가 아니다.
proof-return|47접수중29반영확인뒤18미확인이며거절·같은문장·마감후차수를구별한다.|교정의견 상태·회신일정 정리비이고 본문원고의 전체가치와 다르다.
public-kiln|180→120도의60도하강은센서두시각이며표면접촉안전이나직선냉각을보장하지않는다.|해당냉각곡선 반복시험 취득비이며 공개 문답·결과 전문이라는 가정이다.
quality-cause|같은10시료의이물제거전3이탈·후0이탈을생산분품질개선으로확대하지않는다.|이번 재측정12인시20만원이고 계약점검업체에서도 같은익명요약을 제한 취득한다.
quality-symbols|검사표빈칸은미확인이고재검은이력추가이며실제제품번호·출하결정은포함하지않는다.|실적없는 교육기호풀이 작성비와 전문공개 가정이다.
query-cardinality|100행과10000행에서경로우세가바뀌지만두점으로전환경계·실패지연0을확정하지않는다.|분포·경로성능 구간을 취득한 반복실험비이며 공개 연구요약 설정이다.
quote-exclusion|180+45=225만원은설치·교육의합이며세금·횟수미정·유지보수포함을추정하지않는다.|두견적 범위확인·질의작성비이며 견적총액·발주예상액을 제외한다.
rainwater-display|120+85=205L유입에서배수량을빼지않고탱크잔량·절수량과구별한다.|우량·유입·청소이력 대조로 전시구간을 얻은 비용이고 전문공개 가정이다.
rebate-window|96납품중12취소를빼84정산검토대상이며계약기준일과지급완료를구별한다.|정산경계·수정명세 재처리원인 분석비이고 정산액·지급예정액을 제외한다.
receipt-route|80개와120개예정입고를하역계수·외관확인으로나누고지연이신청수량을바꾸지않는다.|계약운송사 제한취득·전직원 사내공유·외부미승인이 함께 있는 일정작성비다.
receiving-shortfall|120요청과116실물차4이며요청값을고치지않고후속입고·외관검사를별도처리한다.|실수량계수·차이확인 기록비이며 상품가액을 가치귀속으로 대체하지 않는다.
renewal-interview|동일24계정9→15의6증가이나설명순서가달라가격효과로단정하지않는다.|반복면담 설계·실시·코딩 취득비이며 계약총액·잠재매출을 제외한다.
renewal-pricing|10대×5만원×12개월600만원과일괄지급5%할인570만원이며출장·선택미정은별도다.|제안산출6인시15만원이며 실제 고객선택·수락을 가정하지 않는다.
renewal-routing|42/70=60%면담과비교25를기록하고계정중복배정·예약과참석·인력차이를구별한다.|실험 표본설계·운영·면담검증비이며 계약액이 아닌 비교·배정절차 취득비다.
replay-offset|90요청중실행전12취소제외78실행이고출력없는3회도실행수에남긴다.|해당 재생로그 회귀시험비이고 자동화프레임워크 전체개발비를 제외한다.
resin-recovery|240-198=42mg미회수에용기잔류를남기고수지J·첫15mL·3mL/분을한정한다.|재생분획·물질수지 취득 반복실험비이며 수지재고·설비가격을 제외한다.
retry-budget|최초전송포함5시도중2수행뒤3잔여여도사용자취소시재전송하지않고원격미실행을추정하지않는다.|작은호출예제 취소·횟수처리 확인비이고 원격서비스 전체개발비를 제외한다.
retry-report|12문서중3회추가시도로15시도이며문서수12와마지막상태·실패이력을분리한다.|가상수량 재현안내 전문을 공개한 작성비이고 실제 운영데이터의 공개주장이 아니다.
return-case-close|철회분리후43접수중25완료뒤18진행중이며운송장·실수령·부분도착을구별한다.|접수·완료상태 경계 정리비와 업무필요 역할 접근 가정이다.
return-photo-reason|43요청중8미첨부제외35첨부이며사진장수·반품승인·손상사유를구별한다.|해당자료 유무·사유정리비이고 상품가액·보상액을 제외한다.
return-triage|8접수의3이물·2방향조치·3휨의심을구별하며의심3건을불량확정으로세지않는다.|8건 점검정리비와 협력수리점 제한취득·업무역할 열람 가정이다.
reuse-station|젖은상자·마른상자·빈내용물을구별하고회수구역도착을재사용적합으로확정하지않는다.|외부참가자용 회수·정리안내 작성비이며 전문공개 가정이다.
room-observation|23→25도의2도차이와40분·문개방을기록하나냉방고장을단정하지않는다.|회의전후 판독·출입시각 정리비이며 시설장치 전체개발비를 주장하지 않는다.
route-crossover|40상자와160상자에서경로우세가바뀌나두점으로모든물량최적경로를확정하지않는다.|실제작업조건·물량별시간을 얻은 가상현장계측비이며 산술비로 설명하지 않는다.
salinity-reference|상대눈금31·34차3은실제해수염분단위가아니며온도·관측점·구간평균을구별한다.|온도별 상대눈금 반복관측·전시구간 도출비와 전문공개 가정이다.
sample-courier|45시료중13현장보관제외32발송이며빈용기·보류시료·도착확인을구별한다.|시료수량·발송명세 대조비이며 시료개발·물품가액을 제외한다.
sample-inventory|48입고-13사용-5폐기=30은실물30으로대조하며예약·위치이동을차감·입고로세지않는다.|시편계수·사용폐기 대조비만 귀속하며 시편연구 전체비를 주장하지 않는다.
scan-rotate|62쪽중9회전제외53쪽은방향기준이고글자잘림·빈뒷면삭제·수정횟수는별도다.|해당스캔묶음 방향·페이지대응비이며 원문개발비를 제외한다.
scanner-crop|210-8-12=190mm남는폭이며상하여백은불변이고접힘·흔들림은개별확인한다.|이번스캔 여백·질의정리비와 인명별 열람제한 가정이다.
seal-friction|28-19=9N은정지30분뒤시작하중이며연속마찰·반복사용시편을분리한다.|시작저항 대응표 시험·분석비이고 협력기관의 제한된 동일결과 제공 설명이 있다.
search-help|검색결과0을자료전체부재로보지않고선택범위·기간·요약과원문을구별한다.|공개 검색도움말 범위·요약설명 작성비만 귀속한다.
seat-allocation|36석중진행자2·장비4를비워30석이며대기·참석확정·미승인보조의자를구별한다.|해당회차 좌석조건·신청상태 대조비이며 인명 승인 접근 가정이다.
sensor-observation|세군평균19·12·9ms가맞고순서역전미실시·일반식추정·보간결측을구별한다.|아홉관측값 취득24인시45만원이고 유료심사서비스와 인명등록 접근 가정이다.
separator-angle|500-430=70g미회수는통밖잔류포함이며17도·32Hz복합조건과2~4mm입도를한정한다.|경사·진동 조합과회수량을 얻은 반복시험비이며 선별라인 설치비를 제외한다.
service-bundle|다음달실험이라는적용범위바로뒤에80곳중44예약실적을적어그결과의이전차수시점이불명확하다.|표본운영·예약대조 취득비라는설명은있으나미래적용과기존취득결과를명확히연결해야한다.
service-interval|400시간과600시간0.18mm관측으로전체교체주기를확정하지않고부품별실운전시간을나눈다.|누적운전조건·마모구간 취득의 장기계측비이며 달력시간 자체가 투입인시가 아니다.
service-load-map|80×25+50×40=4000분이며같은요청중복·대기재가산·미계약가용인력을제외한다.|지원패턴·처리시간 가정 도출의 조사검증비로 지원업무 전체수행비와 구별한다.
service-quote|2×9만원+한번3만원=21만원이며방문2회가이동비2회를뜻하지않고부품·수락은별도다.|한제안의 작업·이동조건 확인·검산비이며 실제방문대금과 다른 귀속이다.
servo-notch|180-125=55ms정착단축과축Q의73Hz·9Hz억제를같은오차띠·부하검증에한정한다.|부하별응답·억제대역 검증비이며 제어기구매·전체제품개발비를 제외한다.
shadow-distance|동일시작선16→23cm의7cm이동이며판이동·흐린경계·하루전체직선예측을구별한다.|관찰판안내·수업시연 작성비이며 안내전문공개 가정이다.
shift-assignment|설치와복구의요일·반별인원만쓰고실물공동확인과개인연락처제외를명시한다.|편성2인시와 운영사제한취득·전직원공지·외부미승인 가정이 함께 성립한다.
shift-overlap|16+21-7=30고유지원자는신청기준이고출근·교대사건·시간합계를구별한다.|지원신청중복·시간구간 대조비와 업무역할 접근 가정이다.
spare-part-count|57실물-16예약=41미예약이나실물은57유지하며규격·반출시각·추가입고를구별한다.|계수·예약대응 작성비이고 부품재고가가 아니며 정비팀 제한취득 설명이 있다.
spectrometer-dark|84-17=67상대신호보정이며같은적분시간과온도·음수바닥분포를보존한다.|적분시간·온도별 바닥신호 취득분석비이고 공동시험소도 제한 취득한다.
staff-survey|48찬성+17반대=65유효응답이며무응답·질문범위·부서별미수집·분모차이를구별한다.|익명설문 취합비이고 전직원 결과공유는 외부 배포 허가와 별개다.
stockout-panel|21/30=70%는품절문의 표본수락이며반품후속과전체거래처 선호를구별한다.|표본설계·실행·반품대조 취득비이며 재고가·실험매출을 제외한다.
storage-signs|입고대기·점검중·사용가능표지를소유자·구매승인으로해석하지않는다.|교육용 구역표지·동선안내 작성비이고 실제 개별물품 승인정보가 아니다.
strain-marker|80-64=16mm간격감소와고정구대칭배치를묶고이전값재계산에는원시좌표를요구한다.|표점배치별 판독오차·반복성 시험비이고 계약시험소도 같은판본을 제한 취득한다.
substitute-part|규격명같아도접점·높이가달라혼용을보류하며입고수량과사용가능수량을구별한다.|신청규격·실물차이·사진정리비이고 전체부품개발비를 주장하지 않는다.
supplier-terms|300×8200+40000=250만원과300×8450=253.5만원이며500개조건을300개에쓰지않는다.|견적조건취득·정리4인시이고 공급업체 제한취득과 인명승인저장소 가정이다.
supplier-yield|210/250=84%는공급원가의실사용비율이며포장질량·미집계나공급원0채움을제외한다.|가공·실수율 조건 확보비이고 원자재 발주총액과 구별한다.
tender-capacity|320확보-185예약=135가용이며예상반품·거래군H의확정납기·장소조건을분리한다.|예약·취소·인수조건 슬롯순서 취득의 이력분석비이고 판매액·재고가를 제외한다.
test-protocol|h1/h0×100의자체복원율정의와h0=0오류·500g60초·제거후120초조건을명시한다.|공개 교육절차4인시작성비이며 실제 납품 적합성 인증을 주장하지 않는다.
thermal-storage|같은22도시작에서27·25종료의5·3도상승이며온도차를에너지차로바꾸지않는다.|축열조건별 응답취득 가상계측비이며 요약전문을 이후 공개했다는 가정이다.
thermal-window|12+18+10=40분유지에승온냉각은제외하고4도이하90초조건·치구묶음을구별한다.|열처리·치구조건 반복접합시험 취득비이며 다른제품개발비를 제외한다.
tile-coordinate|960-128-192=640픽셀높이는폭과분리되며경계표식ID·원점이동·미리보기축척을구별한다.|다중축척·경계표식 좌표연결 검증비로 명시되어 단순빼기비로 읽지 않으며 검증팀 경로도 있다.
timezone-migration|지역없는9행을빼63행전부변환가능이라고하나뒤에서중복지역시각은추가검토로남긴다.|작은시각표 원본형식·경계검토비는정합하지만63행의추가모호시각 부재를보장하지않는다.
tool-custody|공구함빈칸을분실로단정하지않고작업대발견후반납을추가하며반출원시각을유지한다.|공구위치·반출이력대조 작성비이며 제한취득·업무역할 접근 가정이다.
translation-merge|32+27-9=50식별자병합과동형다의3쌍을구별하고설명빈칸을자동덮어쓰지않는다.|두용어표 병합·충돌확인 작성비이며 번역사업 전체비는 제외한다.
translation-page|84쪽중전날31확인뒤53이며빈쪽·그림불일치·원본개정차수·실제판번호를구별한다.|페이지대응·불일치정리비이고 계약편집업체의 동일연결표 취득 가정이다.
tray-verification|54트레이중6파손제외48외관정상에젖은것을포함하되즉시사용가능과분리한다.|반입상태·계수인계 소규모비이며 트레이재고가를 제외한다.
trial-onboarding|34/50=68%설정완료는유료전환과다르고다음차수·회사단일배정·추가지원효과를구별한다.|실험배정·운영·완료검증 취득비이며 구독매출을 제외한다.
valve-handover|27표식중4보류제외23판독이며위치대조를조작승인·에너지차단확인으로쓰지않는다.|위치표식·인계기록 대조비이고 실제설비조작·전면안전검사비를 제외한다.
vendor-question|35질문중24연결답변뒤11미회신을파일수가아닌질문번호로세며정정·사양승인을구별한다.|질문답변대응 정리비와 구매대행팀 제한취득 가정이다.
vibration-map|600·900·1200회전수와진폭세점을최대진동위치로확정하지않지만회전수단위는빠져있다.|기존프레임 세지점 소규모시험비이고 프레임전체개발비를 제외한다.
visitor-orientation|체험빈상자·회수상자를구별하고방문이동안내가실제생산절차·시설출입권한을주지않는다.|공개 안내전문2인시작성비와 실제 시설권한을 명시적으로 구별한다.
volume-rebate|150개중첫100은정가200만원·초과50은90만원으로290만원이며전체할인을금한다.|한견적 구간조건 확인·검산비이고 견적합계·배송비와 구별한다.
voucher-reconcile|140발급-18취소=122를유효라부르지만사용완료·만료도별도상태로남겨유효의정의가모호하다.|발급·취소·사용경계 검토비는정합하나만료분포함유효수의뜻을대신정의하지않는다.
wash-flow|동일유량·시간17-11=6mg잔류차이며끝단회수·중앙회수·소요액절감·다른오염을구별한다.|분사폭별 잔류성분·회수액 취득시험비이며 설비설치비를 제외한다.
water-sampling|12측정병+3공시료병=15와예비병을분리하며채수시각·지점표기·누수보충금지를명시한다.|채수준비·표기확인비이고 수질조사 전체용역비를 제외한다.
watermark-window|18+7=25초여유는시험스트림한정이고분할P복구·다른분할·수정사건을구별한다.|복구·마감전진 상호작용 장애주입·재처리 검증비이고 제품전체비를 제외한다.
workshop-guide|관측차이를지우지않고조건·순서차이를기록하며재료목록·회수·다음조인계를구분한다.|공개 실습안내·진행순서 확인비이며 실제 결과의 진위 인증이 아니다.
yard-crossing|24/150=16%는통행사건이지차량수아니며구역J의창분리와현장승인·우천을구별한다.|교차막힘·배차창 조건 취득 관측재현비이며 차량구매·운송매출을 제외한다.
""".strip()

# These unresolved issues are authored judgments on exact source passages.
# They do not assert that the policy product or the fictional grade is wrong.
FIXES = {
    "delivery-dedup": (
        "UNIQUE_CARDINALITY_PREMISE_MISSING",
        "48개와35개가 각 목록 내부 중복을 제거한 고유수인지 명시되지 않았는데 고유71개를 단정한다. 내부 반복행을 뒤에서 따로 처리하므로 산술 일치만으로 고유수 전제가 보장되지 않는다.",
        "다음 판본에서48·35가 내부 중복 제거 후 고유 식별자 수임을 명시하고 공통12도 동일 단위임을 고정한다.",
        (
            "A목록 48개와 B목록 35개에서 공통 12개를 빼면 고유 항목은 71개다.",
            "한 목록 안에서 반복된 행은 목록 사이 공통 항목과 구분해서 처리한다.",
        ),
    ),
    "fair-order-guide": (
        "VALID_VERSUS_GROSS_SUBMISSIONS_UNDEFINED",
        "유효 신청을 센다는 지시 뒤에 첫 마감28과추가17의접수합45를 두고 취소를 별도 표로 남긴다.45가 취소 제외 후인지 접수 사건 합인지 불명확해 유효라는 이름을 고정할 수 없다.",
        "다음 판본에서45를 총접수 사건으로 이름 붙이거나 두 입력수가 취소 제외 후 유효 신청임을 명시한다.",
        (
            "공동구매 신청표의 유효 신청을 셉니다.",
            "첫 마감 28건과 추가 접수 17건을 합하면 신청은 45건입니다. 취소는 별도 표에 남깁니다.",
        ),
    ),
    "membrane-backwash": (
        "FLOW_DIFFERENCE_UNIT_NOT_EXPLICIT",
        "분당42L와35L의 유량 차이를7L로 표기해 시간 단위가 빠졌다. 문맥상 L/분 생략으로 이해할 여지는 있으나 참조 원고에서는 부피와 유량을 독자가 보충 추정하지 않게 해야 한다.",
        "다음 판본의35와차이7에 L/분 단위를 명시하고 유량 인용·산술 근거를 새 해시로 결합한다.",
        (
            "역세 간격 18분에서 유량은 분당 42L, 24분에서는 35L로 차이는 7L였다.",
            "다음 운전은 18분 간격으로 시작하고 차압이 28kPa를 넘으면 시료를 따로 받습니다.",
        ),
    ),
    "open-weather": (
        "BASELINE_READING_TIME_UNBOUND",
        "09시에 빈 통의 시작눈금을 확인하고13시에 첫 판독을 남겼다고 한 뒤 오전4mm를 비교 기준으로 쓴다.4mm가09시 기준 눈금인지 이후 관측인지 불분명하며 빈 통의 오프셋인지도 설명되지 않는다.",
        "다음 판본에서 오전4mm의 판독 시각과 빈 통 기준값 관계를 명시하고13시를 첫 추후 판독 등으로 구별한다.",
        (
            "09:00 빈 관측통을 받침에 놓고 시작 눈금을 확인했다. 13:00 첫 판독을 남겼다.",
            "오전 판독 4mm에서 오후 판독 11mm로 눈금 차이는 7mm였다.",
        ),
    ),
    "pallet-layout": (
        "ACQUIRED_STABILITY_SCOPE_NOT_LINKED",
        "작성 근거는 층별 안정 조건을 도출한 반복 시험 투입이라 설명하지만 본문에는 배치 수량과 앞으로 시험 적재 후 확인할 지시만 있다. 기존 시험이 있을 수는 있으나 어느 취득 결과가 이 배치안에 귀속됐는지 연결이 충분히 명시되지 않았다.",
        "다음 판본에서 기존 관측으로 정한 해당 상자·팔레트 배치 조건과 아직 미수행인 후속 적재 검증을 구별한다. 금액을 줄이거나 결과를 임의 창작하지 않는다.",
        (
            "빈 팔레트의 한 층에는 상자를 가로 4개, 세로 3개 놓는 안을 비교한다.",
            "시험 적재 후에는 모서리 돌출 여부를 네 방향에서 확인하고 사진을 남긴다.",
        ),
    ),
    "service-bundle": (
        "OBSERVED_COHORT_VERSUS_FUTURE_SCOPE_UNBOUND",
        "다음달 신규 문의 시험을 적용 범위로 제시한 뒤80곳 중44곳이라는 관측 예약 결과를 바로 적었다. 과거 차수의 참고 결과로 읽힐 수 있지만 원고가 그 시점을 명시하지 않아 미래 계획과 이미 취득한 실적의 연결이 모호하다.",
        "다음 판본에서80·44가 이전 차수의 관측임을 명시하거나 관측 시점을 별도 가상 전제로 고정하고 미래 적용 지시와 분리한다.",
        (
            "적용 범위: 다음 달 신규 문의의 제안 순서 시험.",
            "묶음 제안군 80곳 중 후속 상담 예약 44곳으로 예약 비율은 55%다.",
        ),
    ),
    "timezone-migration": (
        "CONVERTIBLE_COUNT_IGNORES_OTHER_AMBIGUITY",
        "지역 누락9행을 제외한63행을 변환 가능하다고 부르지만 뒤에서 시계가 뒤로 바뀌는 중복 지역시각을 별도 검토 대상으로 둔다. 나머지63행에 그러한 모호값이 없는지 또는63은 지역정보 보유수에 불과한지 명시하지 않았다.",
        "다음 판본에서63을 지역값이 있는 후보행으로 한정하거나 모호·존재하지 않는 시각의 별도 제외 후 변환가능 수임을 고정한다.",
        (
            "검사한 72행에서 지역 누락 9행을 빼면 변환 가능한 행은 63행이다.",
            "시간이 뒤로 바뀌는 날의 중복 시각은 별도 검토 목록에 둔다.",
        ),
    ),
    "vibration-map": (
        "ROTATION_RATE_TIME_UNIT_MISSING",
        "600·900·1200을 회전수라 부르지만 회/분인지 회/초인지 누적 회전 횟수인지 단위를 명시하지 않는다.900부근 진동 봉우리 해석에는 회전 조건의 단위가 필요하며 통상 rpm이라는 추정을 정답 전제로 고정할 수 없다.",
        "다음 판본에서 세 회전 조건의 단위를 명시하고 회전속도와 누적 회전 횟수를 구별한다.",
        (
            "회전수 600에서 진폭 0.3mm, 900에서 0.7mm, 1200에서 0.4mm를 읽었다.",
            "세 점만으로 최대 진동이 정확히 900에서 발생한다고 확정하지 않는다.",
        ),
    ),
    "voucher-reconcile": (
        "VALID_COUPON_STATE_UNDEFINED",
        "발급140에서 취소18만 빼122를 유효 쿠폰이라 정의하지만 사용 완료와 만료를 별도 상태로 남긴다. 유효가 미취소라는 뜻인지 사용 가능이라는 뜻인지 명시되지 않아 만료·기사용분 포함 여부를 독자가 추정해야 한다.",
        "다음 판본에서122를 미취소 발급건으로 이름 붙이거나 유효의 상태 정의와 만료·사용완료의 포함 관계를 명시한다.",
        (
            "발급 140개 중 취소 18개를 제외한 유효 쿠폰은 122개다.",
            "사용 완료는 유효 쿠폰의 별도 상태이며 발급 수량에서 다시 차감하지 않는다. 발급 후 만료된 것은 만료 열에 표시한다.",
        ),
    ),
}

MINOR_BENCHMARK_FINDINGS = {
    "envelope-sort": "접착면이 들뜬 봉투를 수량에서 제외했다고 하나64장 계수가 제외 전후 어느 시점인지 더 명확히 쓰면 인계 수량 해석을 개선할 수 있다. 조건부 정책 답의 공개·가치·관리 전제 오류로 확정하지는 않는다.",
    "keyboard-focus": "다음 입력칸까지 몇 번 이동하는지라는 질문에 이동 대상 칸9개로 답해 키 입력 횟수와 대상 개수의 차이가 남는다. 조건부 등급 전제는 변하지 않으나 실제 시험 원고 문답의 명확성은 보완해야 한다.",
}


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(path):
    result = [
        strict_loads(line)
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    require(bool(result), "disposition_zero_rows")
    return result


def _notes():
    result = {}
    for line in REVIEW_NOTES.splitlines():
        key, body_note, context_note = line.split("|")
        require(
            key not in result and min(len(body_note), len(context_note)) >= 18,
            "disposition_authored_note_invalid",
        )
        result[key] = (body_note, context_note)
    require(
        len(result) == 196 and set(FIXES) <= set(result),
        "disposition_review_coverage_invalid",
    )
    return result


def _verify_source(parent_pack):
    require(
        _sha(parent_pack / "manifest.json") == SOURCE_MANIFEST_SHA256,
        "disposition_source_pin_changed",
    )
    source_batch.verify(parent_pack)
    require(
        _sha(parent_pack / "manifest.json") == SOURCE_MANIFEST_SHA256,
        "disposition_source_changed_during_verify",
    )


def _evidence(body, quote):
    require(
        type(quote) is str and len(quote) >= 10 and body.count(quote) == 1,
        "disposition_quote_not_unique",
    )
    start = body.index(quote)
    return {
        "quote": quote,
        "start": start,
        "end": start + len(quote),
        "sha256": text_digest(quote),
    }


def compile_dispositions(parent_pack=DEFAULT_PARENT):
    """Bind authored decisions to immutable originals; never rewrite source."""
    parent_pack = Path(parent_pack).resolve()
    source_sha = _sha(__file__)
    _verify_source(parent_pack)
    originals = _rows(parent_pack / "authoring/documents.jsonl")
    answers = _rows(parent_pack / "answers/answers.candidate.jsonl")
    details = _rows(parent_pack / "answers/evidence.jsonl")
    new_ids = {
        r["doc_id"] for r in _rows(parent_pack / "authoring/batch06_metadata.jsonl")
    }
    require(
        len(originals) == 260 and len(new_ids) == 64, "disposition_source_count_invalid"
    )
    # Structural policy cross-check supplements, not generates, authored review.
    audit_reference(originals, answers, details)
    a_by_id = {a["doc_id"]: a for a in answers}
    d_by_id = {d["doc_id"]: d for d in details}
    prior = [r for r in originals if r["input"]["doc_id"] not in new_ids]
    notes = _notes()
    require(
        len(prior) == 196
        and {r["family_id"].removeprefix("family-") for r in prior} == set(notes),
        "disposition_exact_prior_subset_invalid",
    )
    records = []
    for raw in sorted(prior, key=lambda r: r["input"]["doc_id"]):
        inp = raw["input"]
        doc_id, body, key = (
            inp["doc_id"],
            inp["text"],
            raw["family_id"].removeprefix("family-"),
        )
        answer, detail = a_by_id[doc_id], d_by_id[doc_id]
        body_note, context_note = notes[key]
        require(
            answer["policy_sha256"] == detail["policy_sha256"] == POLICY_SHA256,
            "disposition_policy_changed",
        )
        findings = [
            {
                "code": "development_exposure_not_blind",
                "scope": "benchmark",
                "reason": "원본196건은 기존 개발·진단에서 노출된 자료이므로 현재 해시 또는 파생본을 최종 미노출200건으로 배정할 수 없다.",
            },
            {
                "code": "authoring_bias_gate_unresolved",
                "scope": "benchmark",
                "reason": "문체·제목·주제 단서와 문서 계열의 품질 경고를 이 본문 검토로 해소하지 않았다. 문자 진단 수치만으로 정답 오류나 품질 합격을 선언하지 않는다.",
            },
        ]
        quote_values = [c["quote"] for c in raw["claims"]]
        require(len(quote_values) == 2, "disposition_claim_count_changed")
        if key in FIXES:
            code, reason, required_action, selected = FIXES[key]
            source_disposition, decision = "revise", "hold"
            findings.append(
                {
                    "code": code.lower(),
                    "scope": "conditional_reference",
                    "reason": reason,
                }
            )
            quote_values = list(selected)
            decision_reason = (
                reason
                + " 이는 기존 정책 산식의 등급 오답 확정이 아니라 명확한 참조 원고 채택의 보류 사유다. "
                + BENCHMARK_HOLD
            )
        else:
            source_disposition, decision = "keep", "accept"
            required_action = "원문을 보존하고 문체·주제·계열 품질 경고를 별도 해소한 뒤 시험자료 채택을 다시 검토한다. 이번에는 학습·평가 배포하지 않는다."
            decision_reason = (
                body_note
                + " 명시한 가상 맥락과 직접 취득 설명을 함께 읽었을 때 내부 조건부 참조 답의 모순을 발견하지 않아 이 원문에 한해 채택한다. "
                + BENCHMARK_HOLD
            )
        if key in MINOR_BENCHMARK_FINDINGS:
            findings.append(
                {
                    "code": "presentation_clarity_remains",
                    "scope": "benchmark",
                    "reason": MINOR_BENCHMARK_FINDINGS[key],
                }
            )
        context = {c["name"]: c for c in inp["context"]}
        records.append(
            {
                **FLAGS,
                "schema_version": SCHEMA,
                "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                "doc_id": doc_id,
                "input_sha256": raw["input_sha256"],
                "body_sha256": text_digest(body),
                "answer_sha256": value_digest(answer),
                "evidence_sha256": value_digest(detail),
                "policy_sha256": POLICY_SHA256,
                "reference_grade": answer["reference_grade"],
                "source_disposition": source_disposition,
                "conditional_reference_decision": decision,
                "benchmark_decision": "hold",
                "reviewer_kind": "ai_internal_review",
                "review_scope": "body_and_synthetic_context",
                "source_unchanged": True,
                "body_review_note": body_note
                + " 본문 전체의 대상·조건·예외를 제한적으로 읽은 판단이며 실제 공학 성능이나 안전성을 인증하는 것은 아니다.",
                "context_review_note": context_note
                + " 공개·취득 경로와 업무/개인 접근 가정을 대조했으며 실제 비용의 현실 진위는 미검증이다. 작성 근거: "
                + detail["rationale"],
                "decision_reason": decision_reason,
                "required_action": required_action,
                "body_evidence": [_evidence(body, q) for q in quote_values],
                "context_evidence": [
                    {
                        "name": name,
                        "quote": context[name]["value"],
                        "sha256": value_digest(context[name]["value"]),
                    }
                    for name in (
                        "reader_scope",
                        "impact_description",
                        "management_controls",
                    )
                ],
                "findings": findings,
            }
        )
    require(
        len(records) == 196 and len({r["doc_id"] for r in records}) == 196,
        "disposition_compilation_count_invalid",
    )
    require(_sha(__file__) == source_sha, "disposition_source_script_changed")
    _verify_source(parent_pack)
    return records


def _source_hashes():
    names = (
        "scripts/customer_reference_disposition_prior196_v1.py",
        "src/koipa/customer_reference_audit_v1.py",
        "src/koipa/customer_benchmark.py",
        "src/koipa/policy_facts.py",
    )
    return {
        **source_batch.source_hashes(),
        **{name: _sha(POC / name) for name in names},
    }


def _json(value):
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        + "\n"
    )


def _out_guard(out, parent_pack):
    require(
        not out.exists() and not out.is_relative_to(parent_pack),
        "disposition_output_exists_or_source",
    )
    require(
        not any((p / "manifest.json").exists() for p in out.parents),
        "disposition_output_frozen_ancestor",
    )


def _summary(records):
    require(
        type(records) is list and len(records) == 196,
        "disposition_summary_count_invalid",
    )
    counts = Counter(r["conditional_reference_decision"] for r in records)
    return {
        **FLAGS,
        "status": "bounded_prior196_disposition_not_release",
        "documents_reviewed": len(records),
        "conditional_reference": {k: counts[k] for k in ("accept", "hold", "reject")},
        "source_disposition": dict(Counter(r["source_disposition"] for r in records)),
        "benchmark_decision": dict(Counter(r["benchmark_decision"] for r in records)),
        "new_documents": 0,
        "training_released": 0,
        "evaluation_released": 0,
        "model_inference_performed": False,
        "new_cv_performed": False,
        "policy_arithmetic_cross_check_is_only_auxiliary": True,
        "authored_individual_body_and_rationale_notes": len(_notes()),
        "findings": dict(Counter(f["code"] for r in records for f in r["findings"])),
        "by_reference_grade": {
            g: dict(
                Counter(
                    r["conditional_reference_decision"]
                    for r in records
                    if r["reference_grade"] == g
                )
            )
            for g in ("TS", "S1", "S2", "S3")
        },
        "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
        "policy_sha256": POLICY_SHA256,
        "limitations": [
            "AI 작성 내부 검토이며 사람 서명이나 외부 정답 권위가 아니다.",
            "실제 비용·관측·관리통제의 현실 진위는 검증하지 않았다.",
            "인용·해시는 변경 감지와 결합 검증이지 검토 내용의 진실 인증이 아니다.",
            "이전 문자진단·노출·작성 편향 경고를 해소하지 않았고 최종 블라인드 평가에 사용할 수 없다.",
        ],
    }


def _review_markdown(records):
    lines = [
        "# 이전196건 본문·가상 맥락별 내부 참조 처분",
        "",
        BENCHMARK_HOLD,
        "",
        "본문 전체·가상 맥락·작성 귀속 설명을 읽고 문서별 노트를 새로 작성했다. 정책 계산 검산은 보조다. 원문·답안·정책은 변경하지 않았다.",
    ]
    for r in records:
        lines += [
            "",
            "## " + r["doc_id"],
            "",
            f"원문 {r['source_disposition']} / 내부 참조 {r['conditional_reference_decision']} / 시험자료 {r['benchmark_decision']}",
            "",
            r["body_review_note"],
            "",
            r["context_review_note"],
            "",
            r["decision_reason"],
            "",
            "후속 조치: " + r["required_action"],
        ]
        lines += [
            "",
            *["> " + e["quote"].replace("\n", "\n> ") for e in r["body_evidence"]],
        ]
    return "\n".join(lines) + "\n"


def write_report(out, *, parent_pack=DEFAULT_PARENT):
    out, parent_pack = Path(out).resolve(), Path(parent_pack).resolve()
    _out_guard(out, parent_pack)
    sources = _source_hashes()
    records = compile_dispositions(parent_pack)
    summary = _summary(records)
    payload = {
        "dispositions.jsonl": "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            for r in records
        ),
        "summary.json": _json(summary),
        "REVIEW.md": _review_markdown(records),
    }
    _verify_source(parent_pack)
    require(_source_hashes() == sources, "disposition_dependencies_changed")
    _out_guard(out, parent_pack)
    out.mkdir(parents=True, exist_ok=False)
    for name, content in payload.items():
        with (out / name).open("x", encoding="utf-8", newline="\n") as target:
            target.write(content)
    _verify_source(parent_pack)
    require(
        _source_hashes() == sources, "disposition_dependencies_changed_during_output"
    )
    require(
        all(
            (out / name).read_bytes() == content.encode("utf-8")
            for name, content in payload.items()
        ),
        "disposition_output_changed",
    )
    # Only this last file marks a complete report. Failed late output is not complete.
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as target:
        target.write(
            _json(
                {
                    **FLAGS,
                    "schema_version": "customer-prior196-disposition-report-v1",
                    "source_manifest_sha256": SOURCE_MANIFEST_SHA256,
                    "policy_sha256": POLICY_SHA256,
                    "source_files_sha256": sources,
                    "files": {
                        name: text_digest(content) for name, content in payload.items()
                    },
                }
            )
        )
    return summary


def verify_report(out, *, parent_pack=DEFAULT_PARENT):
    out, parent_pack = Path(out).resolve(), Path(parent_pack).resolve()
    snapshot = (out / "manifest.json").read_bytes()
    manifest = strict_loads(snapshot.decode("utf-8"))
    require(
        manifest.get("schema_version") == "customer-prior196-disposition-report-v1"
        and manifest.get("source_manifest_sha256") == SOURCE_MANIFEST_SHA256
        and manifest.get("policy_sha256") == POLICY_SHA256
        and all(manifest.get(k) is False for k in FLAGS),
        "disposition_manifest_invalid",
    )
    require(
        manifest.get("source_files_sha256") == _source_hashes(),
        "disposition_report_source_drift",
    )
    records = compile_dispositions(parent_pack)
    expected = {
        "dispositions.jsonl": "".join(
            json.dumps(r, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
            for r in records
        ),
        "summary.json": _json(_summary(records)),
        "REVIEW.md": _review_markdown(records),
    }
    require(
        type(manifest.get("files")) is dict and set(manifest["files"]) == set(expected),
        "disposition_report_file_list_invalid",
    )
    for name, content in expected.items():
        require(
            (out / name).read_bytes() == content.encode("utf-8")
            and manifest["files"][name] == text_digest(content),
            "disposition_report_replay_mismatch",
        )
    require(
        {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
        == set(expected) | {"manifest.json"},
        "disposition_report_unlisted_file",
    )
    require(
        (out / "manifest.json").read_bytes() == snapshot, "disposition_report_changed"
    )
    return _summary(records)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("write", "verify"))
    parser.add_argument("--parent-pack", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = (write_report if args.command == "write" else verify_report)(
            args.out, parent_pack=args.parent_pack
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        print(json.dumps({"status": "failed", "code": "prior196_disposition_failed"}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
