"""P1-A8: PII 마스킹 룰 검증."""

from __future__ import annotations

from koipa.modules.m2_preprocess import mask_pii


def test_rrn_masked():
    r = mask_pii("주민번호: 901231-1234567")
    assert "[RRN]" in r.text
    assert "901231" not in r.text
    assert r.counts.get("rrn") == 1


def test_email_masked():
    r = mask_pii("연락: alice@example.com 또는 bob@test.kr")
    assert "[EMAIL]" in r.text
    assert "alice@example.com" not in r.text
    assert r.counts.get("email") == 2


def test_phone_masked():
    r = mask_pii("핸드폰 010-1234-5678 / 사무실 02-123-4567")
    assert "[PHONE]" in r.text
    assert "010-1234-5678" not in r.text
    assert r.counts.get("phone_mobile") == 1
    assert r.counts.get("phone_land") == 1


def test_credit_card_masked():
    r = mask_pii("카드 4111-1111-1111-1111 결제")
    assert "[CARD]" in r.text
    assert "4111" not in r.text


def test_business_no_masked():
    r = mask_pii("사업자등록번호: 123-45-67890")
    assert "[BIZNO]" in r.text


def test_ip_masked():
    r = mask_pii("서버 192.168.10.5에 배포")
    assert "[IP]" in r.text


def test_employee_id_masked():
    r = mask_pii("사번 12345678 사용자", mask_employee_id=True)
    assert "[EMP]" in r.text


def test_employee_id_skipped_when_disabled():
    r = mask_pii("사번 12345678 사용자", mask_employee_id=False)
    assert "[EMP]" not in r.text


def test_no_pii_unchanged():
    src = "일반 본문입니다. 영업비밀 등급은 S2."
    r = mask_pii(src)
    assert r.text == src
    assert r.total_masked == 0


def test_multiple_masks_counts():
    src = "010-1111-2222, 010-3333-4444, foo@bar.com"
    r = mask_pii(src)
    assert r.counts.get("phone_mobile") == 2
    assert r.counts.get("email") == 1
    assert r.total_masked == 3


def test_passport_masked():
    r = mask_pii("여권 M12345678 발급")
    assert "[PASSPORT]" in r.text


def test_extra_pattern():
    import re
    extra = [("project_code", re.compile(r"\bPRJ-\d{4}\b"), "[PRJCODE]")]
    r = mask_pii("프로젝트 PRJ-2026 진행", extra_patterns=extra)
    assert "[PRJCODE]" in r.text

# ── [2026-08-24] 등록번호를 개인정보로 오인하던 오탐 ──────────────────────────────
def test_registration_numbers_are_not_masked():
    """판례·공고문의 **등록번호**는 개인정보가 아니다 — 가리면 본문이 훼손된다.

    실측(배포 모델 학습셋 labeled_p1_retrain_v4_clean 합성 3,187행에서 PII 위반으로 잡힌 2건):
        「등록번호 제4100302980000호」      상표 등록번호 13자리
        「공매개시번호(0160010014)로」      공매 개시번호 10자리
    종전 패턴은 구분자를 선택(`[- ]?`)으로 두어 구분자 없는 13자리를 전부 주민등록번호로,
    `\d{3,4}` 로 10자리를 휴대전화로 잡았다. 앞뒤 lookaround 는 괄호·한글이 오면 막지 못한다.
    이 값들이 [RRN]·[PHONE] 로 지워지면 분류 신호까지 함께 사라진다.
    """
    for text in (
        "등록번호 제4100302980000호",
        "공매개시번호(0160010014)로 공매절차를 시도하였다",
    ):
        assert mask_pii(text).text == text, text


def test_space_separated_registration_number_is_still_masked_on_purpose():
    """남는 오탐 하나 — **의도한 것**이라 시험으로 못 박는다.

    「상표등록 4100302980000」처럼 공백으로 떨어진 13자리는 여전히 [RRN] 으로 가려진다.
    구분자 없는 진짜 주민번호(무하이픈 우회, 「주민 8001011234567 등록」)와 형태가 같아서
    문맥 없이는 못 가른다. 체크섬도 못 쓴다 — 우회 차단 시험이 쓰는 번호가 체크섬 무효라
    체크섬을 걸면 그 보호가 사라진다(2026-08-24 실측).

    **안전(PII 보호)과 정밀(본문 보존)이 부딪히면 안전을 택한다.** 놓치는 쪽이 위험하다.
    이 시험은 "몰라서 그런 게 아니라 정하고 그런 것"이라는 기록이다.
    """
    assert "[RRN]" in mask_pii("상표등록 4100302980000").text


def test_real_pii_still_masked_after_tightening():
    """정밀화가 **진짜 PII 를 놓치지 않는지** — 구분자 없는 우회 포함."""
    cases = {
        "주민등록번호 900101-1234567": "[RRN]",
        "외국인등록번호 900101-5234567": "[FRN]",
        "휴대폰 010-2345-6789": "[PHONE]",
        "무하이픈 01023456789": "[PHONE]",      # 우회 시도도 잡는다
        "옛번호 016-001-0014": "[PHONE]",       # 구분자 있으면 10자리도 잡는다
        "이메일 hong@koipa.re.kr": "[EMAIL]",
    }
    for text, token in cases.items():
        out = mask_pii(text).text
        assert token in out, (text, out)
