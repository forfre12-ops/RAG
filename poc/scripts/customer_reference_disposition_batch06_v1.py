"""Bounded, authored review of 64 ORIGINAL batch06 bodies and synthetic premises.

These are source-disposition recommendations, not source edits, customer GOLD,
human approval, technical certification, or training/evaluation authorization.
Alias-removal experimental views are deliberately not used as review inputs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.customer_benchmark import FLAGS
from koipa.customer_guide_reference import POLICY_SHA256
from koipa.policy_facts import require, text_digest, value_digest

PARENT_MANIFEST = "fbcc96d012a3b87f4bd3fd6ba6fea9f62ef1d9c7f5e39b5a416fec7e91285a19"
DEFAULT_PARENT = POC / "reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6"
SCHEMA = "customer-source-disposition-v1"


def _review(key, scope, boundary, body, context, action, defect=None):
    """Literal authored judgments; this helper makes no grade or content decision."""
    return {
        "family_id": "family-" + key, "quotes": (scope, boundary),
        "body_review_note": body, "context_review_note": context,
        "required_action": action, "defect": defect,
    }


# Read against original complete body, premises/rationale and authored claims.
# Each entry selects an actual target/application limit and an exception or
# inference boundary. This registry is not generated from the previous status.
REVIEWS = [
    _review("ceramic-ramp-knee",
        "분말 R은 710도부터 760도까지 분당 2도로 올리고 그 밖의 구간은 기존 속도를 유지한다.",
        "밀도가 다른 성형체로 그대로 옮기지 않는다.",
        "분말 R의 특정 승온 구간과 같은 방향의 42−36=6mm 수축을 연결한다. 다른 밀도의 성형체와 파손 전 길이가 없는 시편을 구별하므로 R을 지우면 적용 대상을 넓힐 위험이 있다.",
        "직접 소결·단면 분석비만 귀속하고 소결로와 다른 분말 연구비를 뺀 가상 전제다. 보유자 경유와 개별 계정 제한을 본문만으로 입증한 것이 아니다.",
        "분말 R 식별자와 밀도·파손 제외조건을 원문대로 유지한다. 별칭 교차균형은 별도 개정에서 검토한다."),
    _review("echo-gate-separation",
        "첫 피크의 꼬리가 둘째 창에 들어온 기록은 독립 반사 두 개로 세지 않는다.",
        "이 시간차만으로 재료 내부 거리를 적으려면 해당 재료의 음속이 추가로 필요하다.",
        "동일 지연 보정의 두 창에서 19−12=7μs를 비교하며 겹친 꼬리를 독립 반사로 세지 않는다. 결합재 재도포는 새 추적으로 나누고 시간차를 미확인 거리로 바꾸지 않는다.",
        "접촉 조건·창 경계 분석에만 투입을 귀속하고 장비 구입을 제외한 설정이다. 고투입이어도 업무 필요 역할 제한이며 개인 추가 승인 전제는 없다는 차이를 유지한다.",
        "시간차와 거리의 구별, 재도포 후 추적 분리를 보존한다. 실제 장비 성능 검증으로 발표하지 않는다."),
    _review("strain-gauge-offset",
        "두 값은 같은 하중 단계에서 읽었다.",
        "이 비교는 지정 지그의 변위 경계이며 재료의 허용 하중을 결정하는 문서는 아니다.",
        "같은 하중 단계의 표시 3.8mm에서 지그 이동 0.6mm를 뺀 값만 시편 변위로 다룬다. 다시 조인 지그와 미끄러운 표점을 구분하므로 보정을 모든 시편에 일반화하지 않는다.",
        "계약 공동 시험기관의 동일 판본 취득과 직접 시험비 귀속이 명시된 합성 전제다. 업무 필요자 제한을 유지하며 지그 본문이 실제 외부 취득 가능성을 증명하지는 않는다.",
        "지정 지그·같은 하중·허용 하중 판단 제외 문장을 유지하며 외부 시험 사실로 인증하지 않는다."),
    _review("compass-table-demo",
        "두 방향은 같은 기준선에서 읽는다.",
        "이 실습판은 방향 변화 관찰용이며 실제 길 찾기의 정확도를 보장하지 않는다.",
        "같은 기준선에서 70−25=45도의 변화만 계산한다. 금속 이동 후 안정된 위치와 탁자 이동 뒤 새 묶음을 구분하여 시연 각도를 실제 항법 정확도로 확장하지 않는다.",
        "공개된 관찰판 전체와 시연·안내 작성비만 전제한다. 공개 범위와 제한 없음은 가상 맥락에서 주어진 것으로 나침반이라는 제목에서 도출한 판단이 아니다.",
        "교육용 범위와 기준선 변경 기록을 보존한다. 안내의 공개 전제를 실제 제품 사용 승인으로 해석하지 않는다."),
    _review("droplet-hysteresis",
        "두 값은 같은 액체와 표면 배치에서 얻었다.",
        "증발로 액량이 줄어든 구간은 펌프 회수량과 구분하여 해석한다.",
        "표면 P에서 동일 액체·배치의 전진 104도와 후퇴 76도를 비교한다. P가 두 번 같은 대상을 지칭하며 기울기와 증발을 별도로 남겨 28도 차이를 다른 표면의 상수로 만들지 않는다.",
        "표면 P의 속도별 실험·영상 분석 직접비와 개인 승인 제한을 가정한다. 장비 자산은 제외되며 2μL의 L은 표면 별칭이 아닌 단위이므로 그대로 보존해야 한다.",
        "표면 P의 두 지시와 μL 단위를 보존한다. 증발과 펌프 회수의 구별을 삭제하지 않는다."),
    _review("pipette-sample-map",
        "시료 식별자는 유지하고 실제 분주 순서만 별도 열에 적는다.",
        "팁 교체 시각이 빠진 칸은 확인 대상으로 남긴다.",
        "한 실험판의 표준액 8칸과 시험액 20칸만 채운 칸으로 합하고 빈 대조 칸을 구분한다. 순서 변경은 시료 정체성 변경이 아니며 방향 전환·미확인 시각·수정 이력을 따로 남긴다.",
        "이번 판의 위치 대응·순서 확인에 든 비용만 포함하고 원 시료 개발비를 제외한다. 낮은 귀속 투입과 실제 개별 승인 계정 제한이 함께 주어진 합성 조건이다.",
        "시료 식별과 분주 순서의 별도 이력을 유지한다. 기존 유사 식별 문서와의 평가 분할 연결도 유지한다."),
    _review("rotor-weight-log",
        "내용물을 넣은 질량은 다음 칸에서 확인한다.",
        "이 기록만으로 장착이나 운전을 승인하지 않고 장비 담당 절차를 따른다.",
        "18.6g 용기와 2.4g 덮개의 합은 내용물이 없는 조립체에만 적용된다. 로터 위치의 실제 표기 대조와 운전 승인 분리가 있어 이 질량 확인을 회전 안전 검증으로 읽지 않는다.",
        "지정 용기 식별·인계비만 귀속하고 운전·전체 시험 비용을 뺀 가정이다. 업무 필요자 제한은 있으나 개인 추가 승인은 없으며 안전 승인 전제도 만들어 내지 않는다.",
        "빈 조립체 범위와 운전 승인 제외 문구를 보존한다. 실제 장비 조작용 절차로 배포하지 않는다."),
    _review("humidity-exhibit-span",
        "하루 안의 결측 시간은 별도 표에 남긴다.",
        "이 값은 전시 공간의 결과이며 주변 도시의 습도 추세로 확대하지 않는다.",
        "계획 30일에서 정지한 4일을 빼 관측일 26일을 세며 하루 안의 결측 시간은 별도 항목이다. 환기 사건과 센서 결측을 구분하므로 관측일을 완전한 24시간 측정일이나 도시 기후로 해석하지 않는다.",
        "공간의 계절별 직접 관측·사건 대조비는 높지만 그래프와 설명 전체가 공개된 조건이다. 높은 비용이 공개 판본의 비공개 근거가 되지 않는 정책 경계 사례로 읽는다.",
        "일 단위 관측일과 시간 단위 결측의 분리를 유지한다. 완전 관측시간이나 도시 추세로 바꾸지 않는다."),
    _review("powder-charge-decay",
        "분말 W가 접지된 받침에 모두 도착한 뒤 12분을 세며 이송 중 시간은 넣지 않습니다.",
        "받침 재질을 바꾸면 기존 조건을 그대로 적용하지 않습니다.",
        "감소 25는 이번 계측기의 상대 눈금 64−39이며 물리 전하량 단위를 새로 붙이지 않는다. 분말 W의 도착 뒤부터 12분을 세고 받침 변경과 벽면 잔류를 구분해 대상·시간 범위가 있다.",
        "분말 W의 이송 시험·감쇠 분석 직접비와 개별 제한을 전제하고 생산 설비 전체 비용은 제외한다. W의 특정 조건을 보존해야 그 귀속 설명과 본문의 대상이 이어진다.",
        "분말 W와 상대 눈금·도착 기준 시계를 유지한다. 별칭 제거로 모든 분말의 조건으로 넓히지 않는다."),
    _review("bleaching-exposure",
        "첫 노출 18초와 추가 노출 27초를 합하면 누적은 45초입니다.",
        "초점을 맞추며 비춘 시간은 따로 남깁니다.",
        "동일 시편 영역의 18+27=45초 계산은 맞고 서로 다른 이력을 합치지 않는 제한도 있다. 다만 초점 조명 시간이 별도인데 '누적'이 두 기록 구간만인지 모든 광노출인지 명시되지 않아 총량의 범위가 모호하다.",
        "계약 공동 분석실 취득·직접 노출 분석비·개별 승인 전제를 넣으면 원래 정책 등급은 계산된다. 그 사실이 누적 노출이라는 본문 계량값의 범위까지 확정해 주지는 않는다.",
        "별도 개정에서 45초가 두 노출 구간의 합임을 명시하고 초점 조명을 포함한 전체 노출은 별도임을 구별한다. 이번에는 원문을 수정하지 않는다.",
        ("exposure_total_scope_unresolved", "초점 맞춤 조명 시간을 별도로 둔 상태에서 누적이라는 값의 포함 범위를 하나로 확정할 수 없다. 산술 오류가 아니라 참조본문의 총량 범위 명료화가 필요하다.")),
    _review("radiograph-envelope",
        "이번 정리는 한 봉투의 식별 연결만 다룹니다.",
        "노출 조건의 적합성은 이 메모에서 판단하지 않았습니다.",
        "한 봉투의 31장 중 번호 보류 5장을 제외한 26장은 식별 대응 완료이지 결함 판독 완료가 아니다. 촬영 위치 누락을 추정하지 않고 재촬영도 별도 번호로 남겨 필름과 부재 정체성을 구별한다.",
        "번호·위치 연결과 요청 목록 작성비만 귀속하며 전문 판독·촬영비를 제외한 설정이다. 업무 역할 제한 전제를 문서의 기술적 용어만으로 더 높은 관리제한으로 바꾸지 않는다.",
        "봉투 단위 식별 연결과 전문 판독 제외를 유지한다. 식별 계열 문서의 분할 중복 주의도 유지한다."),
    _review("polarized-crystal-demo",
        "재촬영은 같은 결정의 새 관측으로 남겼습니다.",
        "색상만으로 물질 종류를 확정할 수 없다는 설명도 유지해 주세요.",
        "16+9=25는 사진 수이며 같은 결정의 재촬영을 새로운 결정 수로 세지 않는다. 필터 방향에 따른 어두워짐과 물질 소멸을 구별하고 색상·촬영조건으로 종류나 실제 크기를 확정하지 않는다.",
        "방향별 관측·시연 구간 도출에 직접 든 고투입이지만 결과와 안내 전체를 공개한 설정이다. 개별 결정의 비밀 관리나 숨은 부록을 가정해 등급을 올릴 근거는 없다.",
        "사진 수와 결정 수, 필터 방향과 물질 판정의 구별을 보존한다. 공개 가정은 실제 공개 인증이 아니다."),
    _review("enzyme-addition-lag",
        "효소 E는 억제제를 먼저 넣은 군과 함께 넣은 군을 같은 초기 조건으로 합치지 않습니다.",
        "이번 결과는 기질 배치 M에 한정하며 다른 농도의 반응 속도로 그대로 환산하지 않습니다.",
        "혼합 기준 대기 14초와 지연 6초는 접촉 시작 20초이며 반응시간은 접촉부터 따로 센다. 효소 E와 기질 배치 M은 서로 다른 정체성이며 M 한정과 농도 외삽 금지가 적용범위를 직접 고정한다.",
        "효소 E의 순서·지연별 직접 반응 분석비만 포함하고 재고가는 제외한다. 보유자 경유·개별 제한은 가상 입력이며 기질 M 삭제 실험뷰는 대상 한정이 약해져 이번 유지 판단의 입력이 아니다.",
        "효소 E와 기질 배치 M을 둘 다 보존한다. 원본의 조건부 참조만 유지하며 별칭삭제뷰는 미채택으로 남긴다."),
    _review("conductivity-label",
        "내용물의 성분을 재분석했다는 뜻은 아닙니다.",
        "개봉일과 제조일을 같은 칸에 적지 말아 주세요.",
        "받은 병 19개 중 재확인 3개를 제외한 16개는 병·기록지 연결 수다. 미확인 온도를 추정하지 않고 제조·개봉 날짜와 단위를 분리하므로 라벨 확인을 성분 분석이나 교정 완료로 바꾸지 않는다.",
        "이번 병 묶음의 판본·날짜 확인에 직접 든 비용이고 제조·교정 비용은 제외된다. 작은 투입이어도 개인 승인 제한이 실제 적용된다는 합성 조건을 별도로 읽었다.",
        "병 식별과 성분 검증의 경계 및 제조일·개봉일 구별을 유지한다. 라벨만으로 실제 품질을 인증하지 않는다."),
    _review("settling-window",
        "용기 아래에서 잰 눈금입니다.",
        "이 두 시점의 이동만으로 일정한 침강 속도를 가정하지 않습니다.",
        "92−65=27mm는 용기 아래 기준으로 읽은 층 경계의 두 시점 이동이다. 벽면 부착·부유층·흐린 판독을 구분하고 흔든 뒤 새 관측으로 시작하므로 일정 속도나 전체 입자의 경로로 확장하지 않는다.",
        "계약 공동 시험소의 같은 규칙 취득과 농도별 경계 분석 직접비를 전제한다. 역할 제한은 있지만 개인 추가 승인이 없다는 맥락과 본문의 공동 비교가 충돌하지 않는다.",
        "용기 기준점과 두 시점 이동 한정을 유지한다. 속도·침강 법칙 확정 자료로 전환하지 않는다."),
    _review("lens-screen-demo",
        "같은 자의 시작점에서 읽었습니다.",
        "관찰 그림의 화살표 길이를 실제 물체 길이로 사용하지 않습니다.",
        "같은 자 기준의 화면 이동 29−18=11cm만 확인하고 선명도와 크기 교정을 구별한다. 렌즈 교체 시 새 번호를 남기며 그림의 화살표를 실제 길이로 쓰지 않아 교육용 범위가 분명하다.",
        "공개 렌즈 수업의 준비·안내 투입과 판본 전체 공개가 명시된 합성 조건이다. 저비용이라는 이유가 아니라 공개·관리제한 없음이라는 전제로 낮은 정책등급이 정해진다.",
        "화면 이동·크기 교정의 분리와 렌즈 교체 식별을 유지한다. 실제 광학 성능 보증으로 제시하지 않는다."),
    _review("snapshot-cut-index",
        "번호가 연속인 시험 로그의 값이다.",
        "파일이 존재한다는 사실과 내용 검증 완료는 별개다.",
        "연속 로그에서 840−765=75개의 후속 항목을 세는 범위가 명시되어 있다. 저장소 Q는 새 검증 표식 전에 이전 복구 묶음을 유지하고 늦은 로그도 실행 번호에 연결해 파일 존재와 검증을 구별한다.",
        "저장소 Q의 장애 시점별 직접 시험·로그 분석비만 귀속하고 서버 자산은 제외한다. 개별 승인과 비공개는 가상 전제이며 이 메모가 실제 복구 구현의 안전성을 증명하지는 않는다.",
        "저장소 Q, 연속 번호 조건과 이전 복구 묶음 보존을 유지한다. 복구 소프트웨어 인증과 분리한다."),
    _review("decimal-roundtrip",
        "수치가 같은지 여부는 다른 검사에서 확인했다.",
        "빈 문자열과 숫자 0은 같은 입력으로 합치지 않는다.",
        "46개 중 표기 차이 7개를 뺀 39는 문자열 표기의 동일성이다. 끝의 0·지수 표기·내부 반올림을 구분하고 빈 문자열과 0을 합치지 않아 형식과 값의 동등성을 혼동하지 않는다.",
        "해당 소수 예제 왕복 대조와 인계비만 포함하며 제품 전체 개발비는 제외한 조건이다. 본문의 흔한 소수 용어와 별개로 개별 승인 계정 제한이 주어진다.",
        "표기 동일성과 수치 동일성의 별도 검사를 유지한다. 전체 제품 정밀도 검증으로 확대하지 않는다."),
    _review("feature-flag-cohort",
        "같은 사용자의 여러 요청도 각각 센 값이다.",
        "기능 선택과 처리 성공은 다른 결과이므로 실패 요청을 분모에서 빼지 않는다.",
        "20/80=25%는 사용자 비율이 아닌 요청 비율이며 같은 사용자의 중복 요청도 포함한다. 선택 키·설정 판본·군 이동을 남기고 실패 요청을 제외하지 않아 선택 효과와 성공률을 분리한다.",
        "계약 시험 협력팀의 동일 판본 취득과 선택 키 분포 재현의 직접 투입을 가정한다. 업무 역할 제한과 외부 계약 취득 가능성을 동시에 보존하며 공개 자료로 간주하지 않는다.",
        "요청 단위 분모와 실패 포함 조건을 유지한다. 참여자·사건 계수 계열과 평가 분할 연결도 보존한다."),
    _review("markdown-link-lab",
        "같은 주소를 가리켜도 표시 위치는 각각 남긴다.",
        "이번 안내는 공개 문법 예제이며 실제 업무 문서의 권한을 검사한 결과가 아니다.",
        "내부 11개와 외부 8개는 링크 표시 위치 수로 합하며 같은 목적지도 별도로 남긴다. 존재 검사·목적지 확인·권한 검사를 구별하고 알 수 없는 파일의 자동 실행을 지시하지 않는다.",
        "공개 문법 예제 전체와 확인·안내 작성비만 전제한다. 실제 업무 문서의 접근권한이나 보안등급을 확인했다는 추가 사실은 포함하지 않은 조건부 사례다.",
        "표시 위치·주소 구별과 권한 검증 제외를 유지한다. 예제 밖 문서 검사 결과로 사용하지 않는다."),
    _review("bloom-probe-order",
        "분할 K는 필터 판본과 데이터 판본이 일치할 때만 음성 결과로 후속 탐색을 생략한다.",
        "양성 결과는 실제 존재의 확정이 아니다.",
        "조회 500건 중 저장소 접근 85건의 비율은 캐시 종료 요청도 포함한 17%다. 분할 K에서 판본 일치가 생략의 필요조건이며 양성 확정·삭제 혼합·비용 통합을 경계한다. 일반 필터 정확성 증명은 아니다.",
        "분할 K의 판본 교체 경계에 대한 직접 부하·오류 분석비를 귀속하고 서버 구입은 제외한다. 개별 제한을 가정하므로 K의 식별을 지워 일반 분할 정책처럼 만들 이유가 없다.",
        "분할 K와 판본 일치 필요조건을 보존한다. 이 짧은 메모를 필터 구현 전체의 무오류 증명으로 읽지 않는다."),
    _review("event-dedup-expiry",
        "새로 들어오는 키는 이번 계산에서 분리했다.",
        "키 만료와 업무 사건의 재처리 허가는 같은 상태로 두지 않는다.",
        "기존 260키에서 만료 45키를 뺀 215는 신규 키를 제외한 계산이다. 만료와 재처리 허가를 분리하고 삭제 예정 표식만으로 조회 제외하지 않아 데이터 상태와 업무 권한의 의미가 구분된다.",
        "만료·재처리 경계의 지연·재전송 시험에 직접 든 고투입이며 전체 개발비는 제외된다. 보유자 경유·업무 역할 제한이 전제이고 개인별 추가 승인이라고 읽지 않는다.",
        "신규 유입 제외와 만료·재처리 허가 분리를 유지한다. 실제 사건을 재처리하라는 허가로 전환하지 않는다."),
    _review("plural-resource-check",
        "문구 개수는 화면 개수와 다르다.",
        "한 언어에서 확인한 복수형 조건을 다른 언어에 자동 복사하지 않는다.",
        "기본 13개와 복수형 18개는 문구 수 31이며 화면 수가 아니다. 수량 0과 언어별 조건을 확인하고 미확인 칸을 비우며 변수·호출 예제를 연결해 언어 간 조건의 무단 전이를 막는다.",
        "해당 번역 묶음의 조건·변수 대조에만 직접 비용을 귀속하고 번역 제품 전체 비용은 제외한다. 업무 필요자 제한을 가정한 내부 자료로 공개 문법 지식과 동일시하지 않는다.",
        "문구 단위와 언어별 조건 확인을 유지한다. 한 언어의 결과를 모든 언어 품질로 확장하지 않는다."),
    _review("screen-zoom-guide",
        "이 안내는 모든 직원이 같은 판본으로 보는 사용 설명이다.",
        "화면 크기만 보고 글자 대비나 접근성이 모두 적합하다고 표시하지 않는다.",
        "기본 7개와 펼친 5개 버튼을 합한 12개는 화면 밖 버튼도 포함한 목록이다. 줄바꿈과 읽기 순서를 같다고 보지 않고 키보드·낭독을 따로 확인해 확대 설정을 접근성 인증으로 만들지 않는다.",
        "전 직원이 알아야 하고 사내 직무·개인 제한은 없는 안내라는 맥락이 본문과 맞는다. 외부 배포는 미승인이므로 관리요소 0의 정책 결과를 공개 허가로 바꿀 수 없다.",
        "전 직원용 범위와 외부 배포 미승인을 함께 유지한다. 낮은 등급을 공개·접근성 승인으로 사용하지 않는다."),
    _review("multipart-commit-map",
        "업로드 완료라는 뜻은 아닙니다.",
        "이름 존재와 전체 내용 검증을 같은 상태로 합치지 않습니다.",
        "48조각 중 재전송 대기 6개를 뺀 42개는 이번 검증된 조각 수라는 관측이다. 저장 경로 M의 해시·순서표 동일 판본과 교체 이력을 확인하고 최종 이름 존재를 전체 업로드 완료로 오인하지 않는다.",
        "저장 경로 M의 중단·조각 교체 시험 직접비만 귀속하며 저장장치 자산을 제외한다. 비공개·개별 승인 전제에서 경로 M은 적용 대상 식별이고 삭제 실험뷰로 대체하지 않는다.",
        "저장 경로 M과 조각 검증·전체 완료 분리를 유지한다. 실제 업로드 서비스의 완료 보증은 별도 검증한다."),
    _review("null-sort-order",
        "공백 문자열은 또 다른 상태로 남깁니다.",
        "같은 정렬 값이 반복되면 보조 키를 명시하고 우연히 나온 순서를 정답으로 고정하지 말아 주세요.",
        "입력 54행 중 빈 값 8행을 뺀 46행을 값 있는 행으로 세되 0과 공백을 다른 상태로 남긴다. 방향 변경·빈 값 위치·동률 보조 키를 명시하여 우연한 행 순서를 정답으로 삼지 않는다.",
        "이번 표의 빈 값과 정렬 옵션 확인에만 비용을 귀속하고 데이터베이스 전체 개발비는 제외한다. 낮은 투입과 개별 승인 제한이 함께 설정되어 제목만으로 등급을 고정하지 않는다.",
        "빈 값·공백·0의 구별과 동률 보조 키 요구를 유지한다. 전체 데이터베이스 동작 보증으로 확대하지 않는다."),
    _review("schedule-calendar-gap",
        "실행 성공일이라는 뜻은 아닙니다.",
        "서버가 꺼져 빠진 실행과 달력상 제외된 실행은 다른 이유로 기록해 주세요.",
        "계획 22일에서 제외 3일을 뺀 19일은 실행 후보일이다. 없는 날짜를 월말로 당기지 않고 계약 처리와 대조하며 서버 중단·달력 제외·미관측 0을 구분해 후보를 성공으로 세지 않는다.",
        "계약 운영 보조자의 동일 달력 취득과 해당 날짜 대조비만 귀속한 가정이다. 보조자 취득 가능과 개별 승인 계정 적용은 모순이 아니라 서로 다른 판단요소다.",
        "후보일·성공일 및 달력 제외·운영 중단의 구별을 유지한다. 일정 계약 내용을 추정해 채우지 않는다."),
    _review("relative-path-demo",
        "같은 상대 경로라도 시작 폴더가 달라지면 같은 파일을 가리키지 않을 수 있습니다.",
        "이 기록은 경로 해석 연습이며 실제 문서의 접근 권한 점검이 아닙니다.",
        "문서 9개와 그림 12개를 합한 파일 21개에서 바로가기는 별도 표시한다. 시작·해석 위치를 함께 남기고 이름만으로 형식을 확정하지 않아 경로 예제를 권한 검증이나 파일 조작 허가로 읽지 않는다.",
        "공개 경로 실습의 예제·기록 전체와 작성 투입을 전제한다. 예제 밖 실제 파일의 공개 여부나 삭제 권한을 추가로 가정하지 않은 낮은 정책등급 사례다.",
        "시작 위치의 정체성과 예제 밖 이동·삭제 금지를 보존한다. 실제 문서의 권한 시험으로 집계하지 않는다."),
    _review("candidate-prune-order",
        "분할 V는 남은 상한값이 현재 경계보다 낮고 계산 판본이 같을 때만 탐색을 종료합니다.",
        "후보가 줄었다는 사실과 최종 결과가 같다는 사실을 따로 보고해 주세요.",
        "180−42=138과 미검사 후보 제외 금지는 명확하며 분할 V라는 적용 대상도 있다. 다만 상한값이 무엇의 허용 상한인지, 현재 경계가 어떤 목적함수의 기준인지 없어 낮다는 비교만으로 종료 의미를 하나로 고정하기 어렵다.",
        "분할 V의 직접 탐색 시험·비교 분석비와 개별 승인 맥락이면 기존 정책 등급은 계산된다. 합성 투입 전제는 목적함수 방향이나 상한의 유효성을 대신 정의하지 않으므로 본문 수용을 보류한다.",
        "별도 개정에서 최대화 등 목적 방향, 현재 경계의 정의, 남은 후보의 유효 상한 범위를 명시한다. 분할 V와 판본 조건은 보존하고 이번 원문은 수정하지 않는다.",
        ("pruning_bound_semantics_unresolved", "상한값과 현재 경계의 목적 방향 및 비교 대상이 명시되지 않아 종료 규칙의 한 가지 의미를 고정하기 어렵다. 오류라고 단정하지 않고 조건 명료화 전 내부 참조 수용을 보류한다.")),
    _review("lease-clock-origin",
        "이 계산은 같은 시계 기준의 시험값입니다.",
        "다른 호스트의 표시 시각을 그대로 빼서 임대 만료를 판단하지 않습니다.",
        "90−35=55초는 같은 시계 기준 시험값이며 다른 호스트 표시시각을 직접 빼지 않는다. 남은 시간이 양수여도 갱신 실패를 별도 상태로 남기고 완료와 반납 사건을 구분해 소유권 의미가 유지된다.",
        "계약·자격을 갖춘 검증기관의 취득과 시계 차이별 직접 검증비를 가정한다. 개인 승인 제한이 별도로 주어지며 실제 분산 시스템의 시계 안전성을 이 합성 메모로 보증하지 않는다.",
        "같은 시계 기준과 갱신 실패·반납 사건 구별을 유지한다. 운영 임대 만료 알고리즘 인증과 분리한다."),
    _review("export-permission-list",
        "내보내기 허용 개수는 아닙니다.",
        "조회 뒤 정책 판본이 바뀌면 이전 응답을 새 요청의 근거로 쓰지 않습니다.",
        "37대상 중 미수신 4개를 뺀 33개는 응답 수이지 허용 수가 아니다. 없거나 판본이 맞지 않는 응답을 미확인으로 남기고 정책 변경 뒤 재사용하지 않으며 실제 반출을 수행하지 않았다.",
        "이번 목록의 응답·판본 대조 비용만 귀속하고 권한 시스템 개발비는 제외한다. 문서의 권한 확인 절차와 문서 자체에 주어진 업무 역할 제한은 별개의 사실로 읽었다.",
        "미수신은 미확인이라는 경계와 정책 판본 재검증을 보존한다. 반출 허가나 실행으로 전환하지 않는다."),
    _review("printer-queue-guide",
        "인쇄된 종이 장수와는 다릅니다.",
        "계정이나 문서 본문은 안내 예제에 넣지 않았습니다.",
        "대기 12개와 정지 3개는 표시 작업 15개이며 종이 장수가 아니다. 목록에서 사라짐과 실제 수령을 구분하고 타인 작업 재시작을 금지해 안내와 실행 권한이 분리된다.",
        "전 직원이 알아야 하는 공용 안내이며 사내 직무·개인 제한이 없다는 합성 전제다. 예제에 계정·원문은 없고 외부 공개는 허가하지 않았으므로 낮은 등급과 외부 공유는 분리된다.",
        "작업 수·용지 수와 취소·수령 상태를 보존한다. 외부 공개 미허가를 낮은 정책등급으로 덮지 않는다."),
    _review("bundle-exit-clause",
        "거래군 C에는 묶음 해제 가능 시점과 잔여 지원 사용 규칙을 같은 화면에서 설명한다.",
        "이번 표본의 문의 감소를 다른 상품의 해지율 감소로 해석하지 않는다.",
        "240+36=276만원은 기본 이용과 추가 지원의 합이며 실제 청구 기간은 따로 확인한다. 거래군 C의 설명 순서와 요청·종료 사건을 구별하고 문의 감소를 다른 상품의 해지 효과로 외삽하지 않는다.",
        "설명 순서·전환 혼동 조사에 직접 든 비용만 포함하고 이용료·예상 매출은 취득비에서 제외한다. 개별 승인 가정과 거래군 C의 한정을 함께 유지해야 본문과 귀속이 이어진다.",
        "거래군 C와 표본 한정, 청구 기간 확인을 보존한다. 계약의 법적 효력이나 타 상품 효과로 확장하지 않는다."),
    _review("discount-base-recheck",
        "이 합계는 할인 전 금액이다.",
        "할인율을 적용할 대상이 확인되지 않으면 전체 합계에 임의로 곱하지 않는다.",
        "상품 180만원과 별도 배송 20만원의 200만원은 할인 전 합계다. 배송료 포함 여부와 적용 대상 판본이 미확인이면 곱하지 않고 변경 이력도 남기므로 할인 후 금액을 임의 생성하지 않는다.",
        "해당 발주안의 범위 확인·질의 정리비만 귀속하며 발주 총액을 취득비로 넣지 않는다. 작은 확인 업무라도 개별 승인 계정 제한을 가정한 내부 판본이라는 맥락이다.",
        "할인 전 합계와 할인 적용 범위를 구분한 원문을 보존한다. 미확인 배송료 조건을 정답으로 채우지 않는다."),
    _review("quota-sample-shift",
        "모집에 실패한 후보는 응답 표본에 넣지 않았다.",
        "표본 구성 변경을 남기지 않은 채 전체 비율 차이를 행동 변화로 부르지 않는다.",
        "동쪽 34곳과 서쪽 46곳의 80곳은 실제 응답 표본이며 모집 실패 후보가 아니다. 구역 비중·모집 경로·비응답을 함께 읽고 빈 구역에 0을 채우지 않아 구성 변화와 행동 변화를 구분한다.",
        "계약 조사기관의 동일 판본 취득과 차수별 모집·비응답 직접 분석비를 전제한다. 업무 필요자 제한 아래 공유 가능한 기관 경로를 공개 경로와 혼동하지 않는다.",
        "응답 표본 분모와 구성 변경 기록을 보존한다. 전체 모집단의 행동 변화로 단정하지 않는다."),
    _review("public-response-scale",
        "보통 응답은 이 합계에 넣지 않는다.",
        "표본 밖 이용자의 결과나 향후 만족도를 이 표만으로 예측하지 않는다.",
        "매우 만족 29명과 만족 41명만 긍정 70명으로 합하며 보통을 제외한다. 질문·범주 판본의 변화와 전체 이용자 수를 구별하고 이름을 싣지 않아 공개 집계를 개인 원자료나 예측으로 바꾸지 않는다.",
        "응답 척도·문구 대조의 직접 고투입에도 안내와 집계 판본 전체가 공개된 가정이다. 공개된 정확한 판본을 비용이 높다는 이유만으로 비공개 자료처럼 취급할 수 없다.",
        "긍정 범주·기간·표본 한정을 유지한다. 공개 집계와 응답자 원자료를 동일한 승인 범위로 보지 않는다."),
    _review("supplier-delay-window",
        "준비 완료 시각에 운송이 시작한 이번 기록의 값이다.",
        "이 조건은 해당 부품군의 관측에서 얻었으며 모든 공급자에게 그대로 적용하지 않는다.",
        "준비 완료와 운송 시작이 이어진 이번 기록이므로 8+5=13일이 성립한다. 공급군 N의 예정일 두 번 변경을 조회 조건으로 삼되 실제 주문 전환과 구분하고 모든 공급자로 확대하지 않는다.",
        "공급군 N의 예정일·대체 경로 조건 분석에 직접 든 비용이며 부품 구매액은 제외한다. 보유자 경유·개별 계정 제한이라는 가상 전제가 특정 부품군 한정과 연결된다.",
        "공급군 N과 연속 시간 구간, 조회·주문 전환의 분리를 유지한다. 다른 공급자 규칙으로 일반화하지 않는다."),
    _review("license-renewal-seats",
        "확인 대상이 모두 유료 갱신된 것은 아니다.",
        "좌석과 실제 사용자 식별자를 다른 열에 남긴다.",
        "120좌석에서 종료 확정 18좌석을 뺀 102는 갱신 확인 대상이다. 미접속을 종료로 보지 않고 좌석·사용자를 다른 식별로 유지하며 계약 판본·이용 기간을 연결해 매출이나 갱신 성공으로 세지 않는다.",
        "계약별 좌석·사용자 변경의 직접 이력 분석비에만 귀속하고 이용료·예상 매출을 제외한다. 비공개지만 업무 역할 제한이지 개인 추가 승인이 아니란 합성 맥락이다.",
        "좌석·사용자·갱신 확인의 경계를 보존한다. 미접속이나 후보 수로 계약 종료·매출을 확정하지 않는다."),
    _review("interview-consent-ledger",
        "공개 가능한 건수는 아니다.",
        "요약 사용 동의를 원음 전체 공개 동의로 넓혀 읽지 않는다.",
        "24면담에서 범위 미확인 5건을 뺀 19건은 동의 범위 확인 수이지 공개 가능 수가 아니다. 목적별 동의·서명 누락·철회를 구별하고 법률 판단을 대신하지 않는 한정이 있다.",
        "이번 묶음 동의서 식별·범위 대조비만 포함하고 전체 조사 계약금은 제외한다. 업무 필요자 제한은 문서 자체의 합성 관리조건이며 면담 원음 공개 허가와는 무관하다.",
        "범위 확인·공개 허가·법률 판단의 분리를 유지한다. 서명이나 동의 내용을 만들어 보완하지 않는다."),
    _review("staff-meal-demand",
        "이 메모에는 개인별 이름이나 식사 정보가 없다.",
        "마감 후 변경은 다음 차수와 구분하며 신청 합계를 참석 인원으로 바꾸지 않는다.",
        "일반식 37건과 채식 12건은 신청 49건이지 배식이나 참석 수가 아니다. 취소·변경 차수를 남기고 개인별 자료가 없는 전 직원 신청 안내라서 합계와 개인 기록의 범위가 분리된다.",
        "전 직원이 알아야 하는 신청 방법이며 사내 직무·개인 제한은 없다는 전제다. 안내 작성비만 귀속하고 외부 배포는 미승인으로, 낮은 등급을 외부 제공 권한으로 읽지 않는다.",
        "개인 기록 미포함과 신청·참석 구별을 보존한다. 전 직원 열람을 외부 배포 허가로 바꾸지 않는다."),
    _review("channel-incentive-return",
        "경로 F는 인수 확인과 취소 가능 기간 종료를 함께 확인한 뒤 지원 검토로 넘깁니다.",
        "경로별 고객 구성 차이를 지원금 효과로 단정하지 않습니다.",
        "210판매에서 철회 26건을 뺀 184는 검토 대상이며 지급 완료가 아니다. 경로 F의 인수와 취소 기간을 함께 확인하고 철회·회수를 분리하므로 고객 구성 차이를 인센티브 효과로 오인하지 않는다.",
        "경로 F의 지원·철회 경계 직접 분석비를 귀속하고 판매액·지원금 자체는 제외한다. 개별 승인 가정을 유지하며 경로 F를 지워 모든 판매 채널의 규칙으로 만들지 않는다.",
        "경로 F와 두 가지 검토 조건, 지급 미확정 범위를 보존한다. 실제 지급을 승인하는 문서로 사용하지 않는다."),
    _review("consignment-location",
        "소유권이 언제 바뀌는지는 계약 기준으로 따로 봅니다.",
        "같은 장소에 있어도 계약이 다른 물량은 분리합니다.",
        "창고에 보관된 160상자의 판매 상태에서 확정 45를 뺀 115를 미판매로 세고 소유권 이전과는 분리한다. 이동 지시와 출고·계약별 물량을 구분하며 계약 해석을 확정하는 절차라고 주장하지 않는다.",
        "자격·계약 확인을 거친 협력 정산팀의 취득과 귀속·이동 직접 분석비가 전제다. 개별 승인 접근은 유지되므로 외부 협력 취득과 누구나 공개를 같은 조건으로 보지 않는다.",
        "보관·판매·소유권·이동 상태의 구별과 계약별 분리를 보존한다. 법적 권리 확정으로 확대하지 않는다."),
    _review("freight-weight-unit",
        "운임 적용 중량이 72kg이라고 확정한 것은 아닙니다.",
        "부피 환산 중량이 필요한 계약에는 실제 질량만 옮겨 계산하지 않습니다.",
        "86−14=72kg는 내용물 질량이며 운임에 적용할 중량은 아니다. 부피 환산 기준·원래 단위·반올림 전 값을 따로 남기고 다른 포장 개수를 합치지 않아 물리량과 청구 기준이 구분된다.",
        "해당 명세 단위·계약 범위 대조에 직접 든 비용만 귀속하고 운임 청구액은 취득비에서 제외한다. 업무 역할 제한 가정에서 본문 금액·질량만으로 경제성 점수를 새로 만들지 않는다.",
        "내용물 질량과 청구 중량의 경계를 유지한다. 미확인 환산 계약값을 임의 정답으로 채우지 않는다."),
    _review("fair-leaflet-count",
        "안내지를 여러 장 받은 방문자도 있어 방문 인원과 다릅니다.",
        "이전 판본의 안내지는 남은 수량에 섞지 않고 따로 표시합니다.",
        "300장 입고에서 85장 배포를 뺀 215장은 같은 판본 안내지 수다. 방문자 수와 다중 수령을 구별하며 오류·구판을 따로 기록하고 연락처·상담이 없는 공개 배포 기록의 범위를 설명한다.",
        "안내지 판본 대조·기록 작성비만 귀속하고 인쇄 사업 전체 비용은 제외한다. 안내지뿐 아니라 기록 설명 전체의 공개를 명시해 원문과 공개 맥락의 범위가 맞는다.",
        "판본별 안내지 수와 방문자 수의 분리를 유지한다. 사건·참여자 계수 계열의 분할 연결도 유지한다."),
    _review("subscription-downgrade",
        "두 구간이 겹치지 않는 이번 기록의 값입니다.",
        "이 절차를 다른 계약의 권리 판단에 그대로 적용하지 않습니다.",
        "요청 후 확인 3일과 적용 대기 12일은 이번 기록의 비중첩 구간으로 15일을 이룬다. 상품 L의 이용권 이전과 적용일·실제 변경을 따로 남기고 빠른 응답만 남기거나 타 계약에 일반화하지 않는다.",
        "상품 L의 이용권·축소 시점 조사 직접비만 포함하고 이용료·환급액은 제외한다. 개별 승인 제한 가정과 상품 L 한정을 보존해야 다른 계약의 권리를 추정하는 문서가 되지 않는다.",
        "상품 L, 비중첩 시간 구간과 다른 계약 적용 금지를 유지한다. 안내를 실제 변경 완료로 세지 않는다."),
    _review("tender-revision-map",
        "문구만 고친 항목은 새 항목 수에 넣지 않았습니다.",
        "항목 순서가 바뀌면 행 번호만으로 답변을 덮어쓰지 말아 주세요.",
        "원본 33항목에 신규 6항목을 더한 39는 문구 수정과 구별된 항목 수다. 삭제 요청도 식별자를 남기는 이력 범위이며 응답을 원래 판본에 연결하므로 행 순서를 정체성으로 대신하지 않는다.",
        "해당 요청서의 수정 항목·응답 대응 작업에만 직접 비용을 귀속하고 발주·사업 금액은 제외한다. 개별 승인 제한을 가정한 내부 판본으로 업체가 본 판본 확인과 구별된다.",
        "항목 식별·삭제 상태·응답 판본을 보존한다. 유사 수정 이력 계열 문서와 평가 분할 연결도 유지한다."),
    _review("nonresponse-followup",
        "연락처 확인 실패는 미응답 이유에서 따로 표시했습니다.",
        "같은 곳에 여러 번 연락해도 대상 수는 늘리지 않습니다.",
        "90곳 중 미응답 27곳을 뺀 63곳은 응답한 대상 수다. 연락처 실패·거절·이탈을 구분하고 재연락 횟수를 새 대상처럼 세지 않으며 응답 표본의 변화율을 최초 전체의 변화로 부르지 않는다.",
        "계약 조사기관의 같은 판본 취득과 장기 표본 이탈·연락 추적 직접비를 전제한다. 역할 제한은 있으나 개인 추가 승인은 없는 상태로, 익숙한 조사 용어가 정책요소를 대신하지 않는다.",
        "대상·연락 횟수와 표본 이탈 경계를 보존한다. 미응답 사유나 전체 모집단의 변화를 추정하지 않는다."),
    _review("catalogue-range-open",
        "주문 가능한 재고 수량과는 다릅니다.",
        "이 목록에는 개별 고객 조건을 넣지 않았습니다.",
        "소형 24개와 대형 18개는 규격 항목 42개이며 재고 수량이 아니다. 규격·공급 상태의 갱신 시점과 단종을 나누고 고객 조건이 없는 공개 목록이라 수록을 납품 가능으로 바꾸지 않는다.",
        "규격·공급 이력 대조에 직접 든 고투입이지만 목록 판본 전체를 공개한 조건이다. 상품 판매액은 비용에서 제외하며 고객별 조건이 숨겨진다고 가정하지 않는다.",
        "규격 수록·현재 공급의 분리와 고객 조건 미포함을 유지한다. 실제 납품 약속으로 사용하지 않는다."),
    _review("cleanroom-return-slot",
        "이 설정은 빈 작업대 조건이며 실제 작업 중의 상태는 별도 검증한다.",
        "회수 비율만으로 공간의 청정도나 안전 적합성을 승인하지 않는다.",
        "154/200=77%는 방출 표식의 관측 회수율이고 미검출을 전부 외부 유출로 세지 않는다. 구역 D의 빈 작업대·환류구 설정에 한정하며 보간·관측과 실제 작업 조건·안전 승인을 구분한다.",
        "구역 D의 배치별 관측·모사 직접비만 귀속하고 시설 공사비는 제외한다. 개별 승인 가정과 빈 작업대 범위를 유지해야 실제 작업장의 청정도나 공사 승인으로 확대되지 않는다.",
        "구역 D와 빈 작업대 한정을 보존한다. 표식 회수율을 실제 공간 안전·청정 인증으로 바꾸지 않는다."),
    _review("parcel-seal-handover",
        "내용물 검수 완료를 뜻하지 않는다.",
        "봉인이 온전해도 내부 파손이 없다고 판단하지 않는다.",
        "도착 38상자에서 봉인 보류 6상자를 뺀 32는 봉인 판독 수다. 상자와 봉인 번호·교체 이력을 각각 유지하고 젖은 라벨을 임의 이전하지 않아 외관 확인을 내부 검수로 오인하지 않는다.",
        "이번 반송 묶음의 외관 식별·봉인 인계비만 포함하고 운송·내용물 검수비를 제외한다. 낮은 귀속 투입과 개별 승인 제한이 함께 주어졌으며 개봉 권한은 별도 담당 절차다.",
        "상자·봉인 식별과 내용물 검수 제외를 유지한다. 개봉 권한이나 파손 없음의 보증을 만들지 않는다."),
    _review("pump-spectrum-window",
        "이 값은 진동 크기가 아니라 분석 구간이다.",
        "순간적인 신호 증가만으로 고장 원인을 확정하지 않고 센서 고정 상태도 별도로 남긴다.",
        "58−36=22Hz는 주파수 분석 구간 폭이지 진동 크기가 아니다. 속도가 달라지면 같은 칸을 같은 기계 성분으로 연결하지 않고 센서 고정·시간을 함께 보아 고장 원인 확정을 피한다.",
        "계약 계측기관의 동일 분석 판본 취득과 속도별 대역 관측 직접비를 전제한다. 업무 역할 제한과 기관 경로를 유지하고 실제 고장 진단의 정확성을 조건부 등급으로 인증하지 않는다.",
        "대역 폭·진폭 및 주파수 칸·기계 성분의 구별을 보존한다. 고장 원인 자동 확정에 사용하지 않는다."),
    _review("visitor-route-sign",
        "되돌아가는 구간은 이번 합계에 넣지 않는다.",
        "견학 경로 표시를 비상 대피 경로와 같은 표지로 쓰지 않는다.",
        "45+30=75m는 돌아오는 길을 제외한 공개 견학 경로다. 비상 경로·임시 차단·출입 허용 구역을 구분하고 길이로 이동 시간이나 권한을 판단하지 않아 안내 범위가 한정된다.",
        "공개 견학 안내판 전체와 경로 확인·설명 작성비만 전제한다. 공개 안내의 등급을 시설 전체의 접근 권한이나 비상 운영절차 승인으로 해석할 근거는 없다.",
        "견학·대피 경로와 편도 합계 한정을 보존한다. 실제 비상 운영·출입 허가 자료로 바꾸지 않는다."),
    _review("slot-replenish-wave",
        "두 범주가 겹치지 않는 기록이다.",
        "이 규칙을 품목 규격이 다른 창고에 그대로 적용하지 않는다.",
        "이번 관측에서 교차 통로 이동과 같은 구역 이동 두 범주를 대비하여 420−96=324회를 읽는다. 비중첩을 명시하고 구역 S의 반납 대기품을 분리한다. 다만 이 짧은 본문으로 실제 창고 사건의 모든 분류 규약을 검증한 것은 아니다.",
        "구역 S의 보충 파동·역방향 이동 직접 분석·모사비만 귀속하고 재고가·설비비는 제외한다. 개별 승인 조건은 명시적 가정이며 다른 창고 적용 금지와 충돌하지 않는다.",
        "원문의 두 범주·이번 관측 한정과 구역 S를 유지한다. 다음 작성 규약에는 계수 분모의 포괄 범주를 더 명시하되 현재 오류로 단정하거나 본문을 수정하지 않는다."),
    _review("standby-load-history",
        "전환 중 빈 기록은 별도 표시했다.",
        "이 문답은 담당자가 수행한 시험의 기록 해석만 다룬다.",
        "예열 12분과 부하 관측 28분은 기록이 있는 두 구간의 합 40분이며 전환 결측은 별도다. 연결 순서가 다른 시험을 같은 출력 하나로 합치지 않고 기록 해석을 설비 조작 승인과 분리한다.",
        "부하 순서·회복 이력의 직접 관측 분석비만 포함하고 설비 자산·조작 교육비는 제외한다. 비공개 업무 역할 제한 가정이며 실제 부하 투입 승인을 이 참고 답안으로 만들지 않는다.",
        "관측 구간 합과 전환 결측을 구분한 원문을 유지한다. 장비 조작 또는 부하 투입 승인으로 쓰지 않는다."),
    _review("linen-lot-return",
        "처리 완료 장수와는 다르다.",
        "수량·식별 확인은 세척 성능이나 위생 적합성 인증이 아니다.",
        "84장에서 별도 보관 9장을 뺀 75장은 일반 처리 대상이지 완료 수다. 포장 훼손 뒤 기존 계수값을 승계하지 않고 재계수 사유를 남기며 식별 확인과 위생 인증을 분리한다.",
        "해당 묶음 수량·식별·인계 비용만 귀속하고 설비와 실제 처리비를 제외한다. 업무 필요자 제한이라는 가상 맥락이며 세척 성능 검증을 했다는 추가 사실은 없다.",
        "재계수 사유·처리 대상·성능 인증의 분리를 유지한다. 사용 적합성 승인 자료로 확대하지 않는다."),
    _review("parking-common-guide",
        "지금 비어 있는 공간 수가 아니다.",
        "개인 차량 번호나 출입 이력은 포함하지 않는다.",
        "일반 32곳과 임시 8곳의 40곳은 표시 구획 수이지 빈자리 수가 아니다. 장기 배정·임시 사용·차단 기간을 구분하며 개인 차량·출입 원자료가 없는 직원 안내 범위다.",
        "전 직원용 공용 안내로 직무·개인 제한 없이 같은 판본을 읽는 조건이다. 작성비만 귀속하며 외부 배포 미승인이므로 낮은 관리요소를 실제 외부 공개 권한으로 바꾸지 않는다.",
        "구획·빈자리·배정 상태와 개인정보 미포함을 유지한다. 외부 배포와 지정 밖 주차를 승인하지 않는다."),
    _review("furnace-standby-chain",
        "단계 사이 전환 시간은 따로 남겼습니다.",
        "이 문답은 기록된 순서의 의미를 설명하며 가열 설비의 실제 조작을 승인하지 않습니다.",
        "18분과 24분의 합 42분은 두 대기 단계이며 전환 시간은 따로 남긴다. 라인 T의 후단 수용 기록은 전단 해제 후보 조건이고 실제 인수 완료와 구분하여 결측을 준비 완료로 바꾸지 않는다.",
        "라인 T의 대기 해제·막힘 전파 직접 분석·모사비만 포함하고 가열 설비비·생산액을 제외한다. 개별 승인 가정을 실제 가열 조작 승인이나 전환 시간 포함 총 경과시간으로 확장하지 않는다.",
        "라인 T와 두 단계·전환 시간 분리, 해제 후보·실제 인수의 구별을 보존한다. 설비를 조작하지 않는다."),
    _review("work-permit-dates",
        "작업 허가 건수가 아닙니다.",
        "승인자 서명이나 현장 점검을 대신 만들지 않습니다.",
        "29건에서 기간 미확인 7건을 뺀 22건은 기간 확인 수다. 구역이 다르면 날짜만 맞춰 연결하지 않고 판본·시점·만료·철회를 구분하며 실제 허가와 서명·현장 점검을 대신하지 않는다.",
        "해당 확인서의 구역·기간·판본 대조비만 귀속하고 현장 검사·공사비를 제외한다. 개별 승인 제한은 문서 열람의 합성 조건이며 현장 작업을 승인했다는 뜻이 아니다.",
        "문서 항목 확인과 작업 허가·서명의 분리를 유지한다. 원문을 실제 승인 증빙으로 사용하지 않는다."),
    _review("coldchain-clock-gap",
        "적정 온도로 확인된 시간은 별도 판정합니다.",
        "도착지의 한 번 측정으로 이동 중 상태를 대신하지 않습니다.",
        "240−35=205분은 관측된 시간이며 적정 온도 유지 시간과 다르다. 정지 구간을 이전 온도로 채우지 않고 시계 보정·실제 정지·도착 관측을 구분하여 물품 적합성 승인을 하지 않는다.",
        "계약 운송사의 동일 판본 취득과 차수별 시계·정지 이력 검증비를 전제한다. 업무 역할 제한을 유지하며 관측 이력의 가상 고투입으로 물품 사용 적합성을 인증하지 않는다.",
        "관측·적온 시간과 시계 보정·정지 구분을 보존한다. 실제 물품 출고·사용 승인으로 전환하지 않는다."),
    _review("atrium-energy-display",
        "이 값에는 표시된 로비 회로만 포함됩니다.",
        "시설 전체 절감량이나 비용으로 환산하려면 추가 범위와 요금 조건이 필요합니다.",
        "18+27=45kWh는 두 구간 에너지이며 순간 전력 합이 아니다. 로비 회로만 포함하고 계량기 교체·보정과 관측을 구별하여 시설 전체 절감량이나 요금을 추가 조건 없이 계산하지 않는다.",
        "로비 회로의 장기 계측·교체 이력 분석비는 높지만 그림과 설명 전체가 공개된 설정이다. 고투입과 공개를 함께 읽어 시설 전체의 비공개 요금 자료를 포함한다고 추정하지 않는다.",
        "로비 회로 범위와 에너지·전력 및 관측·보정 구별을 유지한다. 시설 전체 비용·절감량으로 확장하지 않는다."),
    _review("hoist-transfer-window",
        "구역 G는 반출 지점의 인수 완료가 확인되기 전에는 다음 이송창을 예약하지 않습니다.",
        "이 문서는 모사 결과 비교용이며 실제 인양·이송을 지시하거나 안전 적합성을 승인하지 않습니다.",
        "8/64=12.5%는 작업 관측의 대기창 충돌 비율이고 중단 실행은 원시 목록에 남긴다. 구역 G의 인수 완료와 다음 예약을 연결하되 모사에 한정하며 다른 현장 규격과 실제 인양 승인을 제외한다.",
        "구역 G의 대기 전파 직접 모사·이력 분석비만 귀속하고 장비 자산·공사비를 제외한다. 개별 승인 가정과 모사 범위를 유지해야 대상 G 삭제로 실제 일반 이송 지시가 되지 않는다.",
        "구역 G와 모사 한정·현장별 별도 검증을 유지한다. 실제 인양·이송 지시나 안전 승인에 쓰지 않는다."),
    _review("dock-contact-mark",
        "압력을 직접 측정한 값은 아닙니다.",
        "이 메모는 흔적 해석 범위를 설명하며 접안 장치의 사용 승인이 아닙니다.",
        "표시 면적 180cm2에서 그 안의 겹친 면적 35cm2를 뺀 145cm2를 비중복으로 읽는다. 색·번짐·촬영 시점과 표면 상태를 남기고 흔적 면적을 압력이나 최대 하중으로 확정하지 않는다.",
        "계약·자격 시험기관의 동일 판본 취득과 표면 흔적 직접 분석비를 전제한다. 개별 승인 제한은 합성 조건이며 표면 관측을 실제 장치 사용 승인으로 대신할 수 없다.",
        "표시·겹침 면적과 압력 측정의 경계를 유지한다. 접안 장치 사용 또는 하중 승인으로 집계하지 않는다."),
    _review("spare-part-alias",
        "고유 부품 수는 아닙니다.",
        "서로 다른 별칭이 같은 부품을 가리키는 경우도 원래 표현을 남깁니다.",
        "52별칭 중 번호 미확인 9개를 뺀 43은 연결된 별칭 수이지 부품 수가 아니다. 비슷한 이름을 규격 없이 합치지 않고 이름 변경·부품 교체·여러 별칭을 구분하므로 대상 정체성을 직접 보존한다.",
        "해당 부품 목록의 별칭·규격번호 대조와 인계에 든 직접비만 귀속하고 재고·수리비를 제외한다. 업무 필요자 제한 가정과 장착 가능 여부의 별도 규격 확인이 충돌하지 않는다.",
        "별칭·실제 부품·교체 판본의 구별을 보존한다. 식별 계열 분할 연결을 유지하고 이름만으로 장착을 승인하지 않는다."),
    _review("fountain-public-sign",
        "현재 사용 가능한 수량은 별도 상태판에서 확인합니다.",
        "급수 동작과 수질 확인을 같은 완료 표시로 합치지 않습니다.",
        "실내 6곳과 실외 4곳은 표시 위치 10곳이지 현재 사용 가능 수가 아니다. 급수 동작·수질 확인·임시 중지 기간을 분리하고 배관도·계정이 없는 공개 위치 안내에 한정된다.",
        "공개 위치 안내의 표기 대조·작성 투입과 판본 전체 공개가 주어진 조건이다. 안내의 공개 등급을 실제 수질 검사나 시설 내부 배관 공개 승인으로 확대할 근거가 없다.",
        "표시 위치·사용 가능·수질 확인 구별과 공개 범위를 유지한다. 현장 점검 결과를 만들어 채우지 않는다."),
]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _rows(path):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line]
    require(bool(rows), "disposition_empty_source")
    return rows


def _read_parent(parent_pack):
    root = Path(parent_pack).resolve()
    require(_sha(root / "manifest.json") == PARENT_MANIFEST, "disposition_parent_manifest_changed")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    paths = ("authoring/documents.jsonl", "answers/answers.candidate.jsonl", "answers/evidence.jsonl",
             "authoring/batch06_metadata.jsonl")
    for name in paths:
        require(_sha(root / name) == manifest["files"][name], "disposition_parent_payload_changed")
    records, answers, details, metadata = (_rows(root / name) for name in paths)
    require(len(records) == len(answers) == len(details) == 260, "disposition_parent_count")
    require(len(metadata) == len({r["doc_id"] for r in metadata}) == 64, "disposition_panel_count")
    require({r["family_id"] for r in metadata} == {r["family_id"] for r in REVIEWS},
            "disposition_panel_family_binding")
    by_id = {r["input"]["doc_id"]: r for r in records}
    for row in metadata:
        original = by_id[row["doc_id"]]
        require(row["family_id"] == original["family_id"] and
                row["body_sha256"] == text_digest(original["input"]["text"]), "disposition_panel_body_binding")
    return records, answers, details


def _quote(body, quote):
    require(body.count(quote) == 1, "disposition_quote_missing_or_ambiguous")
    start = body.index(quote)
    return {"quote": quote, "start": start, "end": start + len(quote), "sha256": text_digest(quote)}


def compile_dispositions(parent_pack=DEFAULT_PARENT):
    """Bind 64 authored judgments to the pinned original pack, never repair views."""
    records, answers, details = _read_parent(parent_pack)
    by_family = {row["family_id"]: row for row in records}
    answers = {row["doc_id"]: row for row in answers}
    details = {row["doc_id"]: row for row in details}
    require(len(REVIEWS) == len({r["family_id"] for r in REVIEWS}) == 64, "disposition_review_coverage")
    result = []
    for review in REVIEWS:
        record = by_family[review["family_id"]]
        source = record["input"]
        doc_id, body = source["doc_id"], source["text"]
        answer, detail = answers[doc_id], details[doc_id]
        require(record["input_sha256"] == value_digest(source), "disposition_input_binding")
        require(detail["policy_sha256"] == POLICY_SHA256, "disposition_policy_binding")
        defect = review["defect"]
        bias_reason = (
            "원본 64건 중 TS 16건에만 단독 영문 대상명이 집중하는 알려진 편향이 있다. "
            "개별 원문의 조건부 답안 유지와 독립 분류 성능 평가의 적합성은 다르므로 전체 묶음을 보류한다."
        )
        findings = [{"code": "batch06_grade_correlated_alias_bias", "scope": "benchmark",
                     "reason": bias_reason}]
        if defect:
            findings.append({"code": defect[0], "scope": "conditional_reference", "reason": defect[1]})
        evidence_quotes = list(dict.fromkeys([c["quote"] for c in record["claims"]] + list(review["quotes"])))
        context = {row["name"]: row for row in source["context"]}
        result.append({
            **FLAGS, "schema_version": SCHEMA, "source_manifest_sha256": PARENT_MANIFEST,
            "doc_id": doc_id, "input_sha256": record["input_sha256"], "body_sha256": text_digest(body),
            "answer_sha256": value_digest(answer), "evidence_sha256": value_digest(detail),
            "policy_sha256": POLICY_SHA256, "reference_grade": answer["reference_grade"],
            "source_disposition": "revise" if defect else "keep",
            "conditional_reference_decision": "hold" if defect else "accept", "benchmark_decision": "hold",
            "reviewer_kind": "ai_internal_review", "review_scope": "body_and_synthetic_context",
            "source_unchanged": True, "body_review_note": review["body_review_note"],
            "context_review_note": review["context_review_note"],
            "decision_reason": (
                ("수치와 기존 정책 전제의 계산을 오류로 판정한 것은 아니지만 다음 범위가 미확정이므로 원문 참조 수용을 보류한다. "
                 + defect[1]) if defect else
                ("해당 대상·적용범위·예외를 읽었을 때 원문의 조건부 참조 답안을 깨는 충돌은 발견하지 못했다. "
                 + review["body_review_note"] + " 다만 가상 맥락의 사실성·고객 정책 승인·실제 기술 성능을 인증하지 않는다.")
            ),
            "required_action": review["required_action"],
            "body_evidence": [_quote(body, quote) for quote in evidence_quotes],
            "context_evidence": [{"name": name, "quote": context[name]["value"],
                                  "sha256": value_digest(context[name]["value"])}
                                 for name in ("reader_scope", "impact_description", "management_controls")],
            "findings": findings,
        })
    require(len({row["doc_id"] for row in result}) == 64, "disposition_duplicate_id")
    _read_parent(parent_pack)
    return result


def _payload(records):
    summary = {
        **FLAGS, "schema_version": SCHEMA, "source_manifest_sha256": PARENT_MANIFEST,
        "reviewed_documents": len(records), "source_dispositions": dict(Counter(r["source_disposition"] for r in records)),
        "conditional_reference_decisions": dict(Counter(r["conditional_reference_decision"] for r in records)),
        "benchmark_decisions": dict(Counter(r["benchmark_decision"] for r in records)),
        "source_unchanged": True, "alias_removal_view_adopted": False,
        "customer_gold_created": False, "human_review_certified": False,
        "scope": "64 original bodies, synthetic premises and authored evidence; bounded AI source review only",
    }
    lines = ["# 원본 batch06 문서별 처분 검토", "",
             "원문·정답·정책은 바꾸지 않았다. 내부 조건부 참조 수용 권고와 고객 GOLD·학습허가·독립 평가를 분리한다.",
             "별칭 삭제 실험뷰는 미채택이며 TS 전용 별칭 편향 때문에 원본 64건 모두 benchmark HOLD다.",
             "이 기록은 기술·법률·안전 인증 또는 사람 검수 서명이 아니다. 가상 맥락의 실제 사실성은 인증하지 않는다.", ""]
    for r in records:
        lines += [f"## {r['doc_id']} — {r['reference_grade']} / {r['source_disposition']}", "",
                  f"- input SHA: `{r['input_sha256']}`",
                  f"- 본문: {r['body_review_note']}", f"- 맥락: {r['context_review_note']}",
                  f"- 판정: {r['decision_reason']}", f"- 조치: {r['required_action']}",
                  "- 근거:"] + [f"  - `{e['start']}:{e['end']}` {e['quote']}" for e in r["body_evidence"]] + [""]
    return {
        "dispositions.jsonl": "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in records),
        "summary.json": json.dumps(summary, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        "REVIEW.md": "\n".join(lines) + "\n",
    }


def _output_guard(out, parent_pack):
    out, parent = Path(out).resolve(), Path(parent_pack).resolve()
    require(not out.exists(), "disposition_output_exists")
    require(out != parent and parent not in out.parents, "disposition_output_inside_parent")
    require(all(not (p / "manifest.json").exists() for p in out.parents), "disposition_output_inside_frozen_pack")
    return out


def _check_written_payload(out, payload):
    require({p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} == set(payload),
            "disposition_unlisted_output")
    for name, content in payload.items():
        require((out / name).read_bytes() == content.encode("utf-8"), "disposition_output_changed_before_manifest")


def write_report(out, parent_pack=DEFAULT_PARENT):
    source_sha = _sha(__file__)
    out = _output_guard(out, parent_pack)
    records = compile_dispositions(parent_pack)
    payload = _payload(records)
    require(_sha(__file__) == source_sha, "disposition_source_drift")
    _read_parent(parent_pack)
    out.mkdir(parents=True)
    for name, content in payload.items():
        with (out / name).open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    _check_written_payload(out, payload)
    _read_parent(parent_pack)
    require(_sha(__file__) == source_sha, "disposition_source_drift")
    manifest = {**FLAGS, "schema_version": SCHEMA, "source_manifest_sha256": PARENT_MANIFEST,
                "source_sha256": source_sha, "files": {name: text_digest(content) for name, content in payload.items()}}
    # Complete only after all exact payloads, source and original parent recheck.
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    verify_report(out, parent_pack)
    return json.loads(payload["summary.json"])


def verify_report(root, parent_pack=DEFAULT_PARENT):
    root = Path(root).resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    require(manifest.get("source_sha256") == _sha(__file__), "disposition_source_drift")
    require(manifest.get("source_manifest_sha256") == PARENT_MANIFEST, "disposition_parent_manifest_changed")
    require(all(manifest.get(name) is False for name in FLAGS), "disposition_manifest_authority")
    expected = _payload(compile_dispositions(parent_pack))
    require(set(manifest["files"]) == set(expected), "disposition_report_files")
    require({p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()} ==
            set(expected) | {"manifest.json"}, "disposition_unlisted_output")
    for name, content in expected.items():
        require((root / name).read_bytes() == content.encode("utf-8"), "disposition_report_replay")
        require(_sha(root / name) == manifest["files"][name], "disposition_report_hash")
    return json.loads(expected["summary.json"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pack", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    require(bool(args.out) != bool(args.verify), "disposition_choose_build_or_verify")
    result = verify_report(args.verify, args.parent_pack) if args.verify else write_report(args.out, args.parent_pack)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
