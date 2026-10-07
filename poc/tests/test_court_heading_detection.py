"""공개 판결문 검출기가 **띄어 쓴 표제**를 놓치지 않는가.

왜 이 시험이 있는가(2026-09-05). `build_p1_v5_clean.py` 에는 "공개 판결문은 비공지성
결여로 정의상 S3" 라는 규칙이 있고, 검출은 소송 어휘 마커 3종 이상으로 한다. 그런데
마커가 붙여 쓴 부분 문자열("원고"·"주문")이라 판결문이 실제로 쓰는 표기를 못 잡았다 —
판결문은 당사자 표제를 【원 고】·【주 문】·【신 청 인】처럼 **글자 사이를 띄어** 쓴다.

실측(배포본 학습셋 datasets/labeled_p1_v5_clean): train 47 · val 8 · test 8 미검출,
그중 TS/S1 이 39건이었다. 공개 판결문을 최고등급으로 가르치고 있었다는 뜻이다.
2026-07-26 주석이 "1868건 중 1821건" 이라 적어 둔 잔여 47 이 정확히 이것이다.

이 시험이 막는 것은 그 회귀다. 고칠 때 흔히 하는 실수(마커에 공백 허용을 그냥 붙이기)도
함께 막는다 — '발주 문서'가 '주 문'에 걸리면 안 된다.
"""
from __future__ import annotations

import json
from pathlib import Path

from scripts.build_p1_v5_clean import (
    _court_heading_hits,
    _court_marker_hits,
    is_public_ruling,
)

_DATASET = Path(__file__).resolve().parents[1] / "datasets" / "labeled_p1_v5_clean"

# 실제 판결문 머리 — 배포본 학습셋 #1933(두산중공업 경업금지가처분)에서 가져왔다.
# 붙여 쓴 마커로는 '주문' 하나만 맞아 문턱 3에 미달했다.
_REAL_INJUNCTION = (
    "경업금지가처분(두산중공업 경업금지가처분 사건)\n\n"
    "【신 청 인】 두산중공업 주식회사 (소송대리인 변호사 이태섭외 2인) "
    "【피신청인】 【주 문】 1. 신청인이 피신청인들을 위한 보증으로 십억원을 공탁하거나 "
    "같은 금액을 보험금액으로 하는 지급보증위탁계약 체결문서를 제출하는 것을 조건으로, "
    "피신청인들은 별지 목록 기재 경업금지 기간 만료일까지 취업하여서는 아니 된다."
)


def test_spaced_headings_are_detected():
    """띄어 쓴 표제가 붙여 쓴 것과 같이 잡힌다."""
    assert _court_heading_hits("【원 고】") == 1
    assert _court_heading_hits("【원고】") == 1
    # 같은 표제가 여러 번 나와도 종류 수는 1 — 반복으로 문턱을 넘기지 못한다.
    assert _court_heading_hits("【주 문】 ... 【주문】 ... 【주 문】") == 1
    # 서로 다른 표제는 각각 센다.
    assert _court_heading_hits("【원 고】【피 고】【주 문】") == 3


def test_real_injunction_ruling_is_detected():
    """실제로 놓쳤던 문서가 이제 잡힌다(회귀 트립와이어)."""
    assert _court_marker_hits(_REAL_INJUNCTION) >= 3
    assert is_public_ruling({"text": _REAL_INJUNCTION, "source": "synthetic"})


def test_business_document_is_not_a_ruling():
    """'발주 문서'·'주문 수량'·'원고지' 는 판결문이 아니다.

    마커에 공백 허용을 그냥 붙였다면 '주 문' 에 걸렸을 문장들이다. 표제를 【 】 로
    한정하는 이유가 이것이라 시험으로 고정한다.
    """
    for text in (
        "발주 문서를 검토한 결과 주문 수량이 변경되었다. 원고지 작성 요령을 참고한다.",
        "구매 발주 문서 · 주 문서 양식 · 원고 마감 · 피고용인 명부",
        "이번 분기 주 문의 사항은 납기 지연이며, 원 고객사와 재협의가 필요하다.",
    ):
        assert _court_marker_hits(text) < 3, text
        assert not is_public_ruling({"text": text, "source": "synthetic"})


def test_customer_real_is_never_auto_downgraded():
    """고객 실문서는 표제가 있어도 자동 강등하지 않는다(기존 안전 규칙 보존)."""
    assert not is_public_ruling(
        {"text": _REAL_INJUNCTION, "document_origin": "customer_real"}
    )


def test_deployed_dataset_still_carries_undetected_rulings():
    """배포본 학습셋 파일에는 아직 미검출 판례가 남아 있다 — 재빌드가 필요하다는 기록.

    검출기를 고쳐도 **이미 만들어진 파일은 바뀌지 않는다.** 재빌드는 학습셋을 바꾸고
    모델을 바꾸므로 배포 결정이라 여기서 하지 않는다. 그 상태를 시험으로 남겨,
    재빌드가 이뤄지면 이 시험이 실패하며 "이제 정리됐다"고 알려주게 한다.

    데이터 파일이 없는 환경(배포 번들·CI 최소 체크아웃)에서는 건너뛴다.
    """
    train = _DATASET / "train.jsonl"
    if not train.exists():
        import pytest

        pytest.skip("배포본 학습셋 파일 없음 — 데이터 포함 체크아웃에서만 확인한다")

    rows = [json.loads(x) for x in train.read_text(encoding="utf-8").splitlines() if x.strip()]
    missed = [r for r in rows if is_public_ruling(r) and not r.get("is_court")]
    high = [r for r in missed if r.get("label") in ("TS", "S1")]

    # 실측 시점(2026-09-05)의 값. 재빌드하면 0 이 되어 이 시험이 실패한다 — 그때
    # 기대값을 0 으로 바꾸고 위 서술을 지운다.
    assert len(missed) == 46, "미검출 판례 수가 변했다: %d" % len(missed)
    assert len(high) == 27, "미검출 고등급 판례 수가 변했다: %d" % len(high)
