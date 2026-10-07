"""Eleven separately versioned clarifications, not replacement authority or GOLD.

The original 260 and their former dispositions remain immutable. Newly supplied
synthetic scope assumptions are explicit, never represented as recovered facts.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "src"))

from koipa.customer_benchmark import FLAGS
from koipa.customer_guide_reference import (
    POLICY_SHA256, build_case, decode_context, validate_reference,
)
from koipa.policy_facts import require, text_digest, value_digest

PARENT_MANIFEST = "fbcc96d012a3b87f4bd3fd6ba6fea9f62ef1d9c7f5e39b5a416fec7e91285a19"
ADOPTION_MANIFEST = "7c1d0f2f7004e33fd7ac7225fa1788f908973146725562dcfb2a34f32b326ce1"
DEFAULT_PARENT = POC / "reports/CUSTOMER_GUIDE_PARALLEL02_20260915/reference_v0_6"
DEFAULT_ADOPTION = POC / "reports/CUSTOMER_REFERENCE_ADOPTION_20260915/adoption_v1"
SCHEMA = "customer-reference-clarifications-v1"
ORIGIN = "authored_for_revised_synthetic_edition"


def _spec(key, edits, claims, rationale, assumptions, exclusions, reason):
    return {"family_id": "family-" + key, "edits": edits, "claim_quotes": claims,
            "rationale_append": rationale, "additional_assumptions": assumptions,
            "excluded_scope": exclusions, "decision_reason": reason,
            "remaining_issues": []}


# Literal, document-specific edits written after rereading all 11 original
# bodies, contexts, claims and rationales. No label drives these edits.
REVISIONS = [
    _spec("membrane-backwash", [
        ("역세 간격 18분에서 유량은 분당 42L, 24분에서는 35L로 차이는 7L였다.",
         "역세 간격 18분에서 유량은 42L/분, 24분에서는 35L/분으로 유량 차이는 7L/분이었다."),
    ], {"statement-1": "역세 간격 18분에서 유량은 42L/분, 24분에서는 35L/분으로 유량 차이는 7L/분이었다."},
        "이번 합성 판본에서는 원문의 '유량은 분당'이라는 양을 두 조건과 그 차이 모두에 L/분으로 표시했다. 새 유량이나 부피 측정값을 추가하지 않았으며 기존 막 모듈 R·원수 배치·중단 조건과 직접 투입의 범위는 유지했다.",
        [], [("유량 비교는 같은 원수 배치에서 세척 후 첫 시료를 제외한 원래 시험 범위를 유지한다.",
              "원수 배치는 같고 세척 후 첫 시료는 버렸습니다.")],
        "원문에서 시간 분모가 생략된 두 유량 표기를 명시했다. 42−35=7은 부피 차이가 아니라 L/분의 유량 차이이며 새 실측값이나 운전 승인을 만들지 않았다."),
    _spec("bleaching-exposure", [
        ("먼저 같은 시편 영역의 누적 노출을 확인합니다.",
         "먼저 같은 시편 영역의 두 기록 노출 구간을 확인합니다."),
        ("첫 노출 18초와 추가 노출 27초를 합하면 누적은 45초입니다.",
         "첫 노출 18초와 추가 노출 27초를 합하면 두 기록 구간의 합은 45초입니다."),
        ("초점을 맞추며 비춘 시간은 따로 남깁니다.",
         "초점을 맞추며 비춘 시간은 이 합계에서 제외하고 따로 남깁니다. 전체 광노출 시간을 45초로 확정하지 않습니다."),
    ], {"statement-1": "첫 노출 18초와 추가 노출 27초를 합하면 두 기록 구간의 합은 45초입니다."},
        "이번 합성 판본은 45초를 본문에 기록된 두 노출 구간의 합으로만 정의한다. 원래 근거의 누적 노출은 이 두 구간 대조를 뜻하도록 범위를 좁히며, 초점 조명을 포함한 전체 광노출량을 검증했다는 뜻으로 사용하지 않는다. 원래 직접 분석비·영역 이동 비교 가정은 유지한다.",
        [], [("45초는 초점 조명까지 포함한 전체 광노출 시간이 아니다.",
              "초점을 맞추며 비춘 시간은 이 합계에서 제외하고 따로 남깁니다. 전체 광노출 시간을 45초로 확정하지 않습니다.")],
        "18+27=45초의 숫자는 그대로 두고 합산 대상만 두 기록 구간으로 좁혔다. 초점 조명을 빼고도 전체 노출을 확정하는 오해를 본문과 작성 근거 모두에서 제거했다."),
    _spec("open-weather", [
        ("09:00 빈 관측통을 받침에 놓고 시작 눈금을 확인했다. 13:00 첫 판독을 남겼다. 오전 판독 4mm에서 오후 판독 11mm로 눈금 차이는 7mm였다.",
         "09:00 빈 관측통을 받침에 놓고 시작 눈금 4mm를 확인했다. 이 값은 빈 통의 표시 오프셋이다. 13:00 후속 판독 11mm를 남겼다. 09:00 시작 눈금 4mm에서 13:00 판독 11mm로 표시 차이는 7mm였다."),
    ], {"statement-1": "09:00 시작 눈금 4mm에서 13:00 판독 11mm로 표시 차이는 7mm였다."},
        "이번 합성 판본의 추가 작성 가정: 빈 통의 09:00 표시에는 4mm 오프셋이 있고, 13:00의 11mm는 그 뒤의 판독이다. 원본에서 이 관계가 명시되었다고 주장하지 않는다. 7mm는 같은 눈금의 표시 차이이며 교정된 강수량이나 실제 기상 관측으로 인증하지 않는다. 공개 체험 일지와 직접 작성 투입의 전제는 유지한다.",
        [("이번 합성 판본에서 오전 4mm는 09:00 빈 통의 표시 오프셋이고 11mm는 13:00 후속 판독이라고 추가로 정했다.",
          "09:00 빈 관측통을 받침에 놓고 시작 눈금 4mm를 확인했다. 이 값은 빈 통의 표시 오프셋이다. 13:00 후속 판독 11mm를 남겼다.")],
        [("표시 눈금의 차이를 공식 강수량 관측값으로 해석하지 않는다.",
          "이번 눈금은 체험 통의 판독이며 공식 강수량 관측값이 아니다.")],
        "빈 통·오전 판독·첫 판독의 연결을 명시하기 위해 오프셋과 시점 관계를 새 합성 가정으로 정했다. 기존 원고에 있던 사실을 발견한 것이 아니며 숫자 4·11·7은 동일 눈금의 표시 차이로만 남는다."),
    _spec("service-bundle", [
        ("적용 범위: 다음 달 신규 문의의 제안 순서 시험. 묶음 제안군 80곳 중 후속 상담 예약 44곳으로 예약 비율은 55%다.",
         "적용 범위: 다음 달 신규 문의의 제안 순서 시험. 아래 예약 수치는 이미 종료된 직전 차수의 관측이다. 직전 차수의 묶음 제안군 80곳 중 후속 상담 예약 44곳으로 예약 비율은 55%다."),
    ], {"statement-1": "직전 차수의 묶음 제안군 80곳 중 후속 상담 예약 44곳으로 예약 비율은 55%다."},
        "이번 합성 판본의 추가 작성 가정: 80·44·29의 예약 관측은 종료된 직전 차수이며 다음 달 시험은 후속 적용 계획이다. 원래 작성 근거의 표본 운영·예약 대조 직접비는 이 과거 차수 결과에 귀속하는 것으로 명료화했다. 원본에 그 시점이 명시되어 있었다거나 실제 고객 실적을 확인했다고 주장하지 않는다.",
        [("이미 종료된 직전 차수의 결과를 다음 달 시험 계획에 참고한다는 시간 관계를 이번 합성 판본에서 추가했다.",
          "아래 예약 수치는 이미 종료된 직전 차수의 관측이다.")],
        [("예약 관측은 계약 성공률이나 향후 시험 결과가 아니다.",
          "이 비교는 예약까지의 결과이며 계약 성공률이 아니다.")],
        "미래 계획 앞에 과거 관측을 연결하는 시점을 새 가정으로 드러냈다. 44/80=55%는 지난 차수의 예약률이며 다음 달 결과를 미리 만들거나 계약 성공으로 바꾸지 않았다."),
    _spec("delivery-dedup", [
        ("A목록 48개와 B목록 35개에서 공통 12개를 빼면 고유 항목은 71개다.",
         "A목록의 고유 식별자 48개와 B목록의 고유 식별자 35개에서 양쪽 공통 고유 식별자 12개를 빼면 합집합은 71개다. 두 목록 모두 내부 반복행을 묶고 식별자 누락행을 보류한 뒤 센 값이다."),
    ], {"statement-1": "A목록의 고유 식별자 48개와 B목록의 고유 식별자 35개에서 양쪽 공통 고유 식별자 12개를 빼면 합집합은 71개다."},
        "이번 합성 판본의 추가 작성 가정: 48과 35는 각각 내부 반복행을 묶고 식별자 누락을 보류한 뒤의 고유 식별자 수이고, 공통 12도 같은 단위다. 원래 목록에 실제로 이 사전 정리가 완료됐었다고 소급 주장하지 않는다. 두 목록의 규칙을 검토한 직접비와 발송·수신 동의 분리는 유지한다.",
        [("48·35·12를 내부 중복 정리와 누락 보류 뒤의 같은 고유 식별자 단위로 해석하는 집계 가정을 추가했다.",
          "두 목록 모두 내부 반복행을 묶고 식별자 누락행을 보류한 뒤 센 값이다.")],
        [("목록 정리는 발송 허가 또는 수신 동의가 아니다.",
          "발송 여부는 정리 완료 뒤 따로 결정하며, 목록에 들어 있다는 사실을 수신 동의로 해석하지 않는다.")],
        "포함·배제식 48+35−12=71이 성립하는 고유 식별자 단위를 본문에 넣었다. A와 B는 서로 다른 실제 비교 대상이므로 보존하고 누락행·내부 반복행을 공동 항목과 구별한다."),
    _spec("voucher-reconcile", [
        ("발급 140개 중 취소 18개를 제외한 유효 쿠폰은 122개다.",
         "발급 140개 중 취소 18개를 제외한 미취소 발급 쿠폰은 122개다."),
        ("사용 완료는 유효 쿠폰의 별도 상태이며 발급 수량에서 다시 차감하지 않는다.",
         "사용 완료는 미취소 발급 쿠폰의 별도 상태이며 이 집계에서 다시 차감하지 않는다. 미취소 수는 현재 사용 가능한 쿠폰 수를 뜻하지 않는다."),
    ], {"statement-1": "발급 140개 중 취소 18개를 제외한 미취소 발급 쿠폰은 122개다.",
        "statement-2": "사용 완료는 미취소 발급 쿠폰의 별도 상태이며 이 집계에서 다시 차감하지 않는다."},
        "이번 합성 판본에서는 원문의 유효라는 이름을 취소만 제외한 미취소 발급 집계로 좁혔다. 122에서 사용·만료를 추가 차감한 현재 사용 가능 수는 제시하지 않는다. 발급·취소·사용·만료의 상태 대조에 대한 원래 직접 검토비와 동일 마감 시각은 유지하며 새 쿠폰 실적을 만들지 않았다.",
        [], [("122개는 사용·만료 상태를 반영한 현재 사용 가능 수가 아니다.",
              "미취소 수는 현재 사용 가능한 쿠폰 수를 뜻하지 않는다.")],
        "140−18=122를 실제로 계산한 미취소 집계의 이름으로 한정했다. 사용 완료와 만료는 별도 상태 그대로이며 유효 또는 사용 가능 수라는 더 넓은 결론을 제거했다."),
    _spec("candidate-prune-order", [
        ("분할 V는 남은 상한값이 현재 경계보다 낮고 계산 판본이 같을 때만 탐색을 종료합니다.",
         "분할 V의 이번 탐색은 점수가 가장 큰 후보를 선택합니다. 현재 경계는 이미 평가한 후보 중 가장 높은 점수입니다. 남은 상한값은 아직 평가하지 않은 모든 후보의 점수 이상임이 같은 판본에서 확인된 값입니다. 분할 V는 이 상한값이 현재 경계보다 낮고 계산 판본이 같을 때만 탐색을 종료합니다. 상한의 유효 근거를 확인하지 못하면 종료하지 않습니다."),
    ], {"statement-2": "분할 V는 이 상한값이 현재 경계보다 낮고 계산 판본이 같을 때만 탐색을 종료합니다."},
        "이번 합성 판본의 추가 작성 가정: 목적은 최고 점수 후보 선택이며, 현재 경계는 평가 완료 후보의 최고점이고 남은 상한은 같은 판본의 모든 미평가 후보 점수 이상인 유효 상한이다. 원래 근거의 탐색 시험은 이 조건부 비교 의미로 한정한다. 원문에 이 정의가 있었거나 실제 시스템의 상한 증명이 검증됐다고 주장하지 않는다. 점수 방향·경계·상한을 확인하지 못한 실제 입력은 종료 근거로 사용할 수 없다.",
        [("최고 점수 선택이라는 목적 방향과 평가 완료 후보의 최고점 경계를 이번 합성 판본의 가정으로 정의했다.",
          "분할 V의 이번 탐색은 점수가 가장 큰 후보를 선택합니다. 현재 경계는 이미 평가한 후보 중 가장 높은 점수입니다."),
         ("모든 미평가 후보를 덮는 동일 판본의 유효 상한이라는 조건을 추가했으며 실제 알고리즘의 증명 결과를 인증하지 않는다.",
          "남은 상한값은 아직 평가하지 않은 모든 후보의 점수 이상임이 같은 판본에서 확인된 값입니다.")],
        [("상한의 유효성 근거가 확인되지 않은 경우 종료를 허용하지 않는다.",
          "상한의 유효 근거를 확인하지 못하면 종료하지 않습니다.")],
        "상한·현재 경계·목적 방향을 새 조건으로 명시해 낮다는 비교의 의미를 고정했다. 분할 V와 동률·판본 경계는 보존하고 기존 탐색 알고리즘 전체가 올바르다는 인증으로 확대하지 않는다."),
    _spec("timezone-migration", [
        ("검사한 72행에서 지역 누락 9행을 빼면 변환 가능한 행은 63행이다.",
         "검사한 72행에서 지역 누락 9행을 빼면 지역 정보가 있는 검토 후보행은 63행이다. 이 63행이 모두 변환 가능하다고 확정한 것은 아니다."),
    ], {"statement-1": "검사한 72행에서 지역 누락 9행을 빼면 지역 정보가 있는 검토 후보행은 63행이다."},
        "이번 합성 판본은 63행을 지역 정보가 존재하는 검토 후보 수로 좁힌다. 지역 정보의 타당성, 중복 지역시각 또는 존재하지 않는 시각의 변환 가능성을 추가로 검증했다는 전제는 만들지 않는다. 해당 작은 표의 형식 조사·변환 경계 검토비와 이미 세계시인 값의 중복 변환 금지는 유지한다.",
        [], [("지역 정보 보유는 전 행의 시간 변환 가능성을 보장하지 않는다.",
              "이 63행이 모두 변환 가능하다고 확정한 것은 아니다.")],
        "72−9=63은 지역 정보 보유 수라는 확인 범위로만 남겼다. 중복 시각 검토가 끝났다는 새 가정을 넣지 않고 원래 후속 검토·동일 사건 식별 제한을 보존했다."),
    _spec("fair-order-guide", [
        ("1. 공동구매 신청표의 유효 신청을 셉니다. 첫 마감 28건과 추가 접수 17건을 합하면 신청은 45건입니다. 취소는 별도 표에 남깁니다.",
         "1. 공동구매 신청표의 접수 사건을 셉니다. 첫 마감 28건과 추가 접수 17건을 합하면 총접수 사건은 45건입니다. 취소는 별도 표에 남깁니다. 이 합계로 취소 후 남은 유효 신청 수를 확정하지 않습니다."),
    ], {"statement-1": "첫 마감 28건과 추가 접수 17건을 합하면 총접수 사건은 45건입니다."},
        "이번 합성 판본은 유효 신청 대신 첫 마감과 추가 접수 사건의 합계를 다룬다. 취소를 제외한 유효수나 주문 확정 수량은 새로 만들지 않았다. 전 직원용 방법 안내·개인별 신청 미포함·외부 전달 미승인 및 원래 안내 작성 투입의 가정은 유지한다.",
        [], [("총접수 45건은 취소 후 남은 유효 신청 수가 아니다.",
              "이 합계로 취소 후 남은 유효 신청 수를 확정하지 않습니다.")],
        "28+17=45를 접수 사건 합계로만 이름 붙이고 유효 신청이라는 미확인 결과를 제거했다. 취소·결제·주문 확정 상태와 전 직원 안내의 외부 배포 제한은 그대로 유지한다."),
    _spec("pallet-layout", [
        ("빈 팔레트의 한 층에는 상자를 가로 4개, 세로 3개 놓는 안을 비교한다.",
         "이번 배치안은 해당 상자·팔레트 조합의 기존 반복 시험에서 정한 비교 후보이며 안정성 합격을 뜻하지 않는다. 빈 팔레트의 한 층에는 상자를 가로 4개, 세로 3개 놓는 안을 비교한다."),
        ("시험 적재 후에는 모서리 돌출 여부를 네 방향에서 확인하고 사진을 남긴다.",
         "아직 수행하지 않은 다음 차수의 시험 적재 후에는 모서리 돌출 여부를 네 방향에서 확인하고 사진을 남긴다."),
    ], {},
        "이번 합성 판본의 추가 작성 가정: 원래 근거가 말한 해당 조합의 반복 적재 시험에서 본문의 4×3 비교 후보와 확인 항목을 정했다. 기존 근거의 '층별 안정 조건을 도출'은 이 후보·확인 조건 취득을 뜻하며 안정성 합격이나 운반 승인으로 사용하지 않는다. 원문에 이 연결이 명시되었다고 주장하지 않는다. 32000000원·520인시는 기존 조사에만 귀속하고 아직 수행하지 않은 후속 적재 확인이나 설비·운반 승인은 포함하지 않는다.",
        [("이 배치안이 같은 상자·팔레트 조합의 기존 반복 시험에서 정한 비교 후보라는 연결을 이번 합성 판본에 추가했다.",
          "이번 배치안은 해당 상자·팔레트 조합의 기존 반복 시험에서 정한 비교 후보이며 안정성 합격을 뜻하지 않는다."),
         ("본문의 사진·돌출 확인 지시는 아직 수행하지 않은 다음 차수의 확인으로 시간 범위를 추가 고정했다.",
          "아직 수행하지 않은 다음 차수의 시험 적재 후에는 모서리 돌출 여부를 네 방향에서 확인하고 사진을 남긴다.")],
        [("상자 계수의 일치와 실제 운반 가능 승인은 구분한다.",
          "상자 수가 맞는다고 운반 가능으로 확정하지 않는다.")],
        "기존 반복 시험의 귀속과 아직 하지 않은 후속 적재 확인을 구별했다. 이전에는 명시되지 않았던 연결은 추가 작성 가정이며 안정성 측정 결과·합격 수치·추가 비용을 창작하지 않는다."),
    _spec("vibration-map", [
        ("회전수 600에서 진폭 0.3mm, 900에서 0.7mm, 1200에서 0.4mm를 읽었다.",
         "회전속도 600회/분에서 진폭 0.3mm, 900회/분에서 0.7mm, 1200회/분에서 0.4mm를 읽었다."),
        ("900 부근의 봉우리는 다음 시험에서 좁은 간격으로 확인한다.",
         "900회/분 부근의 봉우리는 다음 시험에서 좁은 회전속도 간격으로 확인한다."),
        ("세 점만으로 최대 진동이 정확히 900에서 발생한다고 확정하지 않는다.",
         "세 점만으로 최대 진동이 정확히 900회/분에서 발생한다고 확정하지 않는다."),
    ], {"statement-1": "회전속도 600회/분에서 진폭 0.3mm, 900회/분에서 0.7mm, 1200회/분에서 0.4mm를 읽었다.",
        "statement-2": "세 점만으로 최대 진동이 정확히 900회/분에서 발생한다고 확정하지 않는다."},
        "이번 합성 판본의 추가 작성 가정: 원래 600·900·1200은 누적 회전 횟수가 아니라 회/분 단위의 회전속도다. 통상적인 단위를 추정해 원래 있었던 사실로 인증하지 않고 새 판본의 단위 선택으로 남긴다. 기존 세 지점의 진폭·고정점·같은 부하·직접 소규모 시험비와 최대점 미확정 범위는 유지한다.",
        [("600·900·1200의 회전 조건을 회/분 단위 속도로 정한 것은 이번 합성 판본의 추가 단위 가정이다.",
          "회전속도 600회/분에서 진폭 0.3mm, 900회/분에서 0.7mm, 1200회/분에서 0.4mm를 읽었다.")],
        [("세 점 관측으로 정확한 최고 진동 속도를 확정하지 않는다.",
          "세 점만으로 최대 진동이 정확히 900회/분에서 발생한다고 확정하지 않는다.")],
        "단위가 없던 회전 조건 세 곳과 후속 설명의 900을 모두 회/분으로 명시했다. 새 단위는 작성 가정으로 공개하고 진폭 값이나 실제 최대점 관측을 추가하지 않았다."),
]


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def _rows(path):
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    require(bool(lines) and all(line.strip() for line in lines), "clarification_empty_input")
    return [json.loads(line) for line in lines]


def _pinned(root, expected, members):
    root = Path(root).resolve()
    require(_sha(root / "manifest.json") == expected, "clarification_parent_manifest_changed")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    for name in members:
        require(_sha(root / name) == manifest["files"][name], "clarification_parent_member_changed")
    return {name: _rows(root / name) for name in members}


def read_inputs(parent_pack=DEFAULT_PARENT, adoption_pack=DEFAULT_ADOPTION):
    source = _pinned(parent_pack, PARENT_MANIFEST,
                     ("authoring/documents.jsonl", "answers/answers.candidate.jsonl", "answers/evidence.jsonl"))
    adoption = _pinned(adoption_pack, ADOPTION_MANIFEST,
                       ("review/decisions.jsonl", "review/revisions_required.jsonl"))
    records = source["authoring/documents.jsonl"]
    answers, details = source["answers/answers.candidate.jsonl"], source["answers/evidence.jsonl"]
    decisions, held = adoption["review/decisions.jsonl"], adoption["review/revisions_required.jsonl"]
    require(len(records) == len(answers) == len(details) == len(decisions) == 260 and len(held) == 11,
            "clarification_input_counts")
    require({r["doc_id"] for r in held} == {r["doc_id"] for r in decisions
                                          if r["conditional_reference_decision"] == "hold"},
            "clarification_hold_panel_mismatch")
    by_id = {r["input"]["doc_id"]: r for r in records}
    require(len(by_id) == 260 and len({r["doc_id"] for r in held}) == 11, "clarification_duplicate_id")
    require({by_id[r["doc_id"]]["family_id"] for r in held} == {r["family_id"] for r in REVISIONS}
            and len(REVISIONS) == 11, "clarification_revision_panel_mismatch")
    validate_reference(records, answers, details)
    return records, answers, details, held


def _edit_text(body, replacements):
    edits, cursor, target_cursor, chunks = [], 0, 0, []
    located = []
    for before, after in replacements:
        require(body.count(before) == 1 and before != after, "clarification_edit_missing_or_ambiguous")
        located.append((body.index(before), before, after))
    for start, before, after in sorted(located):
        require(start >= cursor, "clarification_edits_overlap")
        unchanged = body[cursor:start]
        chunks += [unchanged, after]
        target_start = target_cursor + len(unchanged)
        edits.append({"start": start, "end": start + len(before), "before": before, "after": after,
                      "target_start": target_start, "target_end": target_start + len(after),
                      "before_sha256": text_digest(before), "after_sha256": text_digest(after)})
        target_cursor, cursor = target_start + len(after), start + len(before)
    chunks.append(body[cursor:])
    return "".join(chunks), edits


def _bound_note(body, item):
    statement, quote = item
    require(body.count(quote) == 1, "clarification_note_quote_invalid")
    start = body.index(quote)
    return {"statement": statement, "origin": ORIGIN, "quote": quote, "start": start,
            "end": start + len(quote), "sha256": text_digest(quote)}


def _claims(original, body, replacements):
    result, changes = [], []
    require(set(replacements) <= {c["name"] for c in original}, "clarification_unknown_claim")
    for old in original:
        quote = replacements.get(old["name"], old["quote"])
        require(body.count(quote) == 1, "clarification_claim_not_unique")
        new = {**old, "claim": quote, "quote": quote, "start": body.index(quote),
               "end": body.index(quote) + len(quote), "sha256": text_digest(quote)}
        result.append(new)
        if new != old:
            changes.append({"name": old["name"], "before": old, "after": new})
    return result, changes


def compile_revisions(parent_pack=DEFAULT_PARENT, adoption_pack=DEFAULT_ADOPTION):
    old_records, old_answers, old_evidence, held = read_inputs(parent_pack, adoption_pack)
    answer_map = {r["doc_id"]: r for r in old_answers}
    detail_map = {r["doc_id"]: r for r in old_evidence}
    hold_map = {r["doc_id"]: r for r in held}
    specs = {r["family_id"]: r for r in REVISIONS}
    records, answers, evidence, lineage, review_notes = [], [], [], [], []
    for old in old_records:
        parent_id = old["input"]["doc_id"]
        old_answer, old_detail = answer_map[parent_id], detail_map[parent_id]
        if parent_id not in hold_map:
            records.append(copy.deepcopy(old))
            answers.append(copy.deepcopy(old_answer))
            evidence.append(copy.deepcopy(old_detail))
            continue
        spec = specs[old["family_id"]]
        body, edits = _edit_text(old["input"]["text"], spec["edits"])
        draft = copy.deepcopy(old)
        draft["input"]["text"] = body
        draft["input_sha256"] = value_digest(draft["input"])
        draft["claims"], claim_changes = _claims(old["claims"], body, spec["claim_quotes"])
        rationale = old_detail["rationale"] + "\n\n" + spec["rationale_append"]
        revised, answer, detail = build_case(draft, decode_context(old["input"]["context"]), rationale)
        require(answer is not None, "clarification_policy_no_fixed_answer")
        detail["parent_draft_id"] = old_detail["parent_draft_id"]
        # Recalculation is mandatory. Identity and span rebinding do not grant
        # permission to change factual policy context or force a desired label.
        require(revised["input"]["context"] == old["input"]["context"], "clarification_context_changed")
        for key in ("reference_grade", "rule_ids", "other_grade_exclusions", "policy_id", "policy_version", "policy_sha256"):
            require(answer[key] == old_answer[key], "clarification_policy_answer_changed")
        child_id = revised["input"]["doc_id"]
        require(child_id != parent_id, "clarification_child_id_not_new")
        remaining_hold = bool(spec["remaining_issues"])
        lineage.append({
            **FLAGS, "parent_doc_id": parent_id, "child_doc_id": child_id,
            "parent_input_sha256": old["input_sha256"], "child_input_sha256": revised["input_sha256"],
            "parent_body_sha256": text_digest(old["input"]["text"]), "child_body_sha256": text_digest(body),
            "parent_answer_sha256": value_digest(old_answer), "child_answer_sha256": value_digest(answer),
            "parent_evidence_sha256": value_digest(old_detail), "child_evidence_sha256": value_digest(detail),
            "family_id": old["family_id"], "scenario_id": old["scenario_id"],
            "template_family_id": old["template_family_id"], "policy_sha256": POLICY_SHA256,
            "context_sha256": value_digest(old["input"]["context"]), "edits": edits,
            "additional_assumptions": [_bound_note(body, r) for r in spec["additional_assumptions"]],
            "excluded_scope": [_bound_note(body, r) for r in spec["excluded_scope"]],
            "claim_changes": claim_changes, "rationale_change": {"before": old_detail["rationale"], "after": rationale},
            "remaining_hold": remaining_hold, "repair_kind": "synthetic_scope_clarification", "new_document_count": 0,
        })
        review_notes.append({**FLAGS, "parent_doc_id": parent_id, "child_doc_id": child_id,
                             "review_kind": "ai_authored_clarification_not_independent_approval",
                             "decision_reason": spec["decision_reason"], "remaining_issues": spec["remaining_issues"],
                             "remaining_hold": remaining_hold})
        records.append(revised)
        answers.append(answer)
        evidence.append(detail)
    require(len(records) == len(answers) == len(evidence) == 260 and len(lineage) == len(review_notes) == 11,
            "clarification_output_counts")
    validate_reference(records, answers, evidence)
    read_inputs(parent_pack, adoption_pack)
    return {"records": records, "answers": answers, "evidence": evidence,
            "lineage": lineage, "review_notes": review_notes}


def _source_hashes():
    paths = [Path(__file__), POC / "src/koipa/customer_guide_reference.py",
             POC / "src/koipa/customer_benchmark.py", POC / "src/koipa/policy_facts.py"]
    return {p.relative_to(POC).as_posix(): _sha(p) for p in paths}


def _payload(compiled):
    paths = {"records": "authoring/documents.jsonl", "answers": "answers/answers.candidate.jsonl",
             "evidence": "answers/evidence.jsonl", "lineage": "audit/lineage.jsonl",
             "review_notes": "audit/review_notes.jsonl"}
    payload = {paths[key]: "".join(json.dumps(row, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"
                                  for row in compiled[key]) for key in paths}
    summary = {
        **FLAGS, "schema_version": SCHEMA, "source_manifest_sha256": PARENT_MANIFEST,
        "adoption_manifest_sha256": ADOPTION_MANIFEST, "policy_sha256": POLICY_SHA256,
        "dataset_role": "synthetic_clarification_replacement_candidates_not_authoritative_adoption",
        "candidate_views": 260, "unchanged_original_records": 249, "changed_parent_views": 11,
        "new_independent_documents": 0, "authoritative_parent_replaced": False,
        "benchmark_decision": "hold", "adoption_allowed": False, "human_review_certified": False,
        "original_hold_dispositions_changed": False,
        "new_assumption_documents": sum(bool(r["additional_assumptions"]) for r in compiled["lineage"]),
        "author_remaining_hold": sum(r["remaining_hold"] for r in compiled["review_notes"]),
        "grade_counts": dict(Counter(r["reference_grade"] for r in compiled["answers"])),
        "numeric_model_accuracy": None, "claude_or_independent_model_used": False,
    }
    payload["summary.json"] = _json(summary)
    by_id = {r["input"]["doc_id"]: r for r in compiled["records"]}
    details = {r["doc_id"]: r for r in compiled["evidence"]}
    lines = ["# 보류 원문 11건의 별도 명료화 판본", "",
             "원문 정본·기존 처분은 불변이다. 새 판본은 대체 검토 후보이며 독립 문서 수에 더하지 않는다.",
             "아래 추가 가정은 이번 합성 판본의 작성 가정이다. 원문에서 발견한 사실이나 실제 고객 근거가 아니다.",
             "작성자의 잔여 쟁점 판단은 독립 승인·고객 GOLD가 아니며 benchmark HOLD와 모든 사용허가 false를 유지한다.", ""]
    for link, note in zip(compiled["lineage"], compiled["review_notes"], strict=True):
        record = by_id[link["child_doc_id"]]
        lines += [f"## {link['family_id']}", "", f"부모: `{link['parent_doc_id']}` → 새 판본: `{link['child_doc_id']}`",
                  f"입력 SHA: `{link['child_input_sha256']}`", "", "### 변경 구간", ""]
        for edit in link["edits"]:
            lines += [f"- 이전 [{edit['start']}:{edit['end']}]: {edit['before']}",
                      f"- 이후 [{edit['target_start']}:{edit['target_end']}]: {edit['after']}", ""]
        lines += ["### 새 판본 본문", "", record["input"]["text"], "### 추가 작성 가정", ""]
        lines += ["- " + r["statement"] for r in link["additional_assumptions"]] or ["추가 관측·단위·집계 가정 없이 표현의 범위만 명료화했다."]
        lines += ["", "### 작성 근거", "", details[link["child_doc_id"]]["rationale"], "",
                  "### 작성자 판단", "", note["decision_reason"], "", f"잔여 의미 보류: {note['remaining_hold']}", ""]
    payload["DIFFS.md"] = "\n".join(lines) + "\n"
    return payload


def _output_guard(out, parent_pack, adoption_pack):
    out = Path(out).resolve()
    require(not out.exists(), "clarification_output_exists")
    for source in (Path(parent_pack).resolve(), Path(adoption_pack).resolve()):
        require(out != source and source not in out.parents, "clarification_output_inside_source")
    require(all(not (p / "manifest.json").exists() for p in out.parents), "clarification_output_frozen_ancestor")
    return out


def _check_payload(out, payload, manifest_expected=False):
    expected = set(payload) | ({"manifest.json"} if manifest_expected else set())
    require({p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} == expected,
            "clarification_unlisted_output")
    for name, content in payload.items():
        require((out / name).read_bytes() == content.encode("utf-8"), "clarification_output_replay_mismatch")


def write_report(out, parent_pack=DEFAULT_PARENT, adoption_pack=DEFAULT_ADOPTION):
    sources = _source_hashes()
    out = _output_guard(out, parent_pack, adoption_pack)
    payload = _payload(compile_revisions(parent_pack, adoption_pack))
    require(_source_hashes() == sources, "clarification_source_drift")
    read_inputs(parent_pack, adoption_pack)
    out = _output_guard(out, parent_pack, adoption_pack)
    for name, content in payload.items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    _check_payload(out, payload)
    require(_source_hashes() == sources, "clarification_source_drift")
    read_inputs(parent_pack, adoption_pack)
    manifest = {**FLAGS, "schema_version": SCHEMA, "source_manifest_sha256": PARENT_MANIFEST,
                "adoption_manifest_sha256": ADOPTION_MANIFEST, "source_files_sha256": sources,
                "authoritative_parent_replaced": False, "adoption_allowed": False,
                "files": {name: text_digest(content) for name, content in payload.items()}}
    with (out / "manifest.json").open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(_json(manifest))
    verify_report(out, parent_pack, adoption_pack)
    return json.loads(payload["summary.json"])


def verify_report(out, parent_pack=DEFAULT_PARENT, adoption_pack=DEFAULT_ADOPTION):
    out = Path(out).resolve()
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["source_manifest_sha256"] == PARENT_MANIFEST and
            manifest["adoption_manifest_sha256"] == ADOPTION_MANIFEST, "clarification_manifest_parent_binding")
    require(manifest["source_files_sha256"] == _source_hashes(), "clarification_source_drift")
    require(all(manifest.get(k) is False for k in (*FLAGS, "authoritative_parent_replaced", "adoption_allowed")),
            "clarification_manifest_authority")
    payload = _payload(compile_revisions(parent_pack, adoption_pack))
    require(manifest["files"] == {name: text_digest(content) for name, content in payload.items()},
            "clarification_manifest_payload_binding")
    _check_payload(out, payload, True)
    return json.loads(payload["summary.json"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--verify", type=Path)
    parser.add_argument("--parent-pack", type=Path, default=DEFAULT_PARENT)
    parser.add_argument("--adoption-pack", type=Path, default=DEFAULT_ADOPTION)
    args = parser.parse_args()
    require(bool(args.out) != bool(args.verify), "clarification_choose_build_or_verify")
    result = (verify_report(args.verify, args.parent_pack, args.adoption_pack) if args.verify
              else write_report(args.out, args.parent_pack, args.adoption_pack))
    print(_json(result))


if __name__ == "__main__":
    main()
