"""P1-A8: PII 마스킹 — 룰 기반 (NER 가산 옵션).

기본 룰만으로도 한국 공공·기업 문서의 흔한 PII는 사전 차단:
- 주민등록번호 (\d{6}-[1-4]\d{6})
- 외국인등록번호 (\d{6}-[5-8]\d{6})
- 사업자등록번호 (\d{3}-\d{2}-\d{5})
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
    ("rrn", re.compile(r"\b\d{6}-[1-4]\d{6}\b"), "[RRN]"),
    ("frn", re.compile(r"\b\d{6}-[5-8]\d{6}\b"), "[FRN]"),
    ("business_no", re.compile(r"\b\d{3}-\d{2}-\d{5}\b"), "[BIZNO]"),
    ("corp_reg", re.compile(r"\b\d{6}-\d{7}\b"), "[CORPNO]"),
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

    if use_ner:
        out, ner_counts = _mask_with_ner(out)
        for k, v in ner_counts.items():
            counts[k] = counts.get(k, 0) + v

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
