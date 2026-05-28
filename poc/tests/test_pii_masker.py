"""P1-A8: PII 마스킹 룰 검증."""

from __future__ import annotations

from lloydk.modules.m2_preprocess import mask_pii


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
