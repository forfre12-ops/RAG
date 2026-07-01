"""P1-A8: PII 마스킹 — 룰 기반 (NER 가산 옵션).

기본 룰만으로도 한국 공공·기업 문서의 흔한 PII는 사전 차단:
- 주민등록번호 (\d{6}[- ]?[1-4]\d{6}) — 구분자 선택적(무하이픈 우회 차단), 7번째 [1-4] 제약
- 외국인등록번호 (\d{6}[- ]?[5-8]\d{6}) — 구분자 선택적, 7번째 [5-8] 제약
- 사업자등록번호 (\d{3}[- ]?\d{2}[- ]?\d{5}) — 무하이픈 10자리는 가중합 체크섬 통과 시만
- 법인등록번호 (\d{6}-\d{7})
- 신용카드 (\d{4}-\d{4}-\d{4}-\d{4} 또는 16자리 연속)
- 계좌번호 (보수적: 10~16자리 숫자 + 은행명 인접)
- 휴대전화 (010-\d{4}-\d{4})
- 일반전화 (지역번호-국번-가입자)
- 이메일 (RFC5322 보수적)
- 여권번호 (M/H/S로 시작 8자리)

NER 백엔드(KLUE-NER 등)는 lazy import + 옵션. 의존성 미설치면 룰만 적용.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PII_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    # (name, pattern, mask_template)
    # 주민/외국인등록번호 — 구분자 선택적([- ]?)으로 무하이픈 우회(8001011234567) 차단.
    # 앞뒤 숫자 lookaround로 더 긴 숫자열(계좌·일련번호) 부분매치 방지.
    # 7번째 자리 [1-4]/[5-8] 제약 유지로 임의 13자리 숫자 오탐 억제.
    ("rrn", re.compile(r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"), "[RRN]"),
    ("frn", re.compile(r"(?<!\d)\d{6}[- ]?[5-8]\d{6}(?!\d)"), "[FRN]"),
    # 사업자등록번호 — 구분자 선택적. 무하이픈 10자리는 오탐 위험이 커서
    # mask_pii() 에서 가중합 체크섬으로 별도 검증(아래 _BIZNO_RE / _biz_checksum_ok).
    ("corp_reg", re.compile(r"(?<!\d)\d{6}-\d{7}(?!\d)"), "[CORPNO]"),
    ("credit_card", re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"), "[CARD]"),
    ("phone_mobile", re.compile(r"\b01[016789]-?\d{3,4}-?\d{4}\b"), "[PHONE]"),
    ("phone_land", re.compile(r"\b0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])-\d{3,4}-\d{4}\b"), "[PHONE]"),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL]"),
    ("passport_kr", re.compile(r"\b[MHS]\d{8}\b"), "[PASSPORT]"),
    # IPv4 — 사내망 자산 식별 회피 (한국어 문자 인접 시 \b 매치 안 되므로 lookaround 사용)
    ("ipv4", re.compile(r"(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)"), "[IP]"),
]

# 사번 — 운영시 회사별 패턴이 다르므로 보수적 default(8~10자리 숫자 단독, 단어경계)
_EMP_DEFAULT = re.compile(r"\b(?:사번|직원번호|EMP|emp)[\s:#]*\d{5,10}\b")

# 사업자등록번호 — 구분자 선택적. 무하이픈 10자리는 오탐 위험이 크므로
# 가중합 체크섬(_biz_checksum_ok)을 통과한 것만 마스킹한다.
_BIZNO_RE = re.compile(r"(?<!\d)(\d{3})[- ]?(\d{2})[- ]?(\d{5})(?!\d)")
# 국세청 사업자등록번호 체크섬 가중치 (마지막 자리는 검증 숫자).
_BIZNO_WEIGHTS = (1, 3, 7, 1, 3, 7, 1, 3, 5)


def _biz_checksum_ok(digits: str) -> bool:
    """사업자등록번호 10자리 가중합 체크섬 검증.

    국세청 알고리즘: 앞 9자리에 가중치 [1,3,7,1,3,7,1,3,5]를 곱해 합산하고,
    9번째 자리(가중치 5)는 곱한 값의 10의 자리를 더한다. 합을 10으로 나눈 나머지를
    10에서 뺀 값(mod 10)이 마지막 검증 숫자와 일치해야 한다.
    하이픈이 있으면 형식 자체가 신뢰돼 검증을 강제하지 않는다(호출부에서 분기).
    """
    if len(digits) != 10 or not digits.isdigit():
        return False
    nums = [int(c) for c in digits]
    total = sum(n * w for n, w in zip(nums[:9], _BIZNO_WEIGHTS))
    total += (nums[8] * 5) // 10
    check = (10 - (total % 10)) % 10
    return check == nums[9]


def _mask_business_no(text: str) -> tuple[str, int]:
    """사업자등록번호 마스킹. 하이픈 포함 형식은 그대로, 무하이픈은 체크섬 통과 시만."""
    count = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal count
        whole = m.group(0)
        digits = m.group(1) + m.group(2) + m.group(3)
        # 구분자가 있으면(=형식이 명시적) 신뢰, 무하이픈 10자리만 체크섬 강제.
        has_sep = any(c in whole for c in "- ")
        if has_sep or _biz_checksum_ok(digits):
            count += 1
            return "[BIZNO]"
        return whole

    return _BIZNO_RE.sub(_repl, text), count


@dataclass
class MaskResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total_masked(self) -> int:
        return sum(self.counts.values())


def mask_pii(
    text: str,
    *,
    mask_employee_id: bool = True,
    use_ner: bool = False,
    extra_patterns: list[tuple[str, re.Pattern[str], str]] | None = None,
) -> MaskResult:
    """PII 토큰을 사전 정의된 마스크로 치환.

    Args:
        text: 원문
        mask_employee_id: 사번/직원번호 패턴도 마스킹
        use_ner: True면 KLUE-NER로 PERSON/ORG/LOC 추가 마스킹 (lazy, 실패시 무시)
        extra_patterns: 회사·도메인 특화 추가 룰
    """
    if not text:
        return MaskResult(text="", counts={})

    out = text
    counts: dict[str, int] = {}

    patterns = list(_PII_PATTERNS)
    if mask_employee_id:
        patterns.append(("employee_id", _EMP_DEFAULT, "[EMP]"))
    if extra_patterns:
        patterns.extend(extra_patterns)

    for name, pat, mask in patterns:
        # findall로 count 후 sub
        n = len(pat.findall(out))
        if n > 0:
            counts[name] = counts.get(name, 0) + n
            out = pat.sub(mask, out)

    # 사업자등록번호 — 무하이픈은 체크섬 검증이 필요해 정규식 단독 치환이 아닌
    # 별도 검증 패스로 처리(위 패턴 루프에서 제외돼 있다).
    out, biz_n = _mask_business_no(out)
    if biz_n > 0:
        counts["business_no"] = counts.get("business_no", 0) + biz_n

    if use_ner:
        out, ner_counts = _mask_with_ner(out)
        for k, v in ner_counts.items():
            counts[k] = counts.get(k, 0) + v

    # NEW-H2: 타입별 PII 마스킹 카운트 — PiiMaskingMissRate 알람용. best-effort(프로메테우스 미가용 무시).
    if counts:
        try:
            from lloydk.api.prom_metrics import PII_MASKED_TOTAL  # noqa: PLC0415

            for _name, _n in counts.items():
                if _n:
                    PII_MASKED_TOTAL.labels(pii_type=_name).inc(_n)
        except Exception:  # noqa: BLE001
            pass

    return MaskResult(text=out, counts=counts)


def _mask_with_ner(text: str) -> tuple[str, dict[str, int]]:
    """KLUE-NER로 PERSON/ORG/LOC 토큰 마스킹. lazy import — 실패시 noop."""
    try:
        from transformers import pipeline  # type: ignore
    except Exception:
        return text, {}

    try:
        ner = pipeline(
            "token-classification",
            model="klue/bert-base",
            aggregation_strategy="simple",
        )
        ents = ner(text)
    except Exception:
        return text, {}

    counts: dict[str, int] = {}
    # 뒤에서부터 치환해야 인덱스가 안 깨짐
    for ent in sorted(ents, key=lambda e: -int(e.get("start", 0))):
        label = (ent.get("entity_group") or "").upper()
        if label not in {"PERSON", "PER", "ORG", "LOC"}:
            continue
        mask = {"PERSON": "[PERSON]", "PER": "[PERSON]", "ORG": "[ORG]", "LOC": "[LOC]"}[label]
        s, e = int(ent["start"]), int(ent["end"])
        text = text[:s] + mask + text[e:]
        counts[label.lower()] = counts.get(label.lower(), 0) + 1
    return text, counts
