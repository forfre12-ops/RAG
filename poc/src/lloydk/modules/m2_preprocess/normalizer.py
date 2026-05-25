"""한국어 텍스트 정규화 + 노이즈/불용어 제거.

전략:
- 공백·문장부호 정규화
- 페이지 번호/머리말/꼬리말 패턴 제거
- 한국어 특수: 자모 분리(NFKC), 전각→반각
- 가벼운 불용어 제거 (등급분류에 무의미한 행정 보일러)
"""

from __future__ import annotations

import re
import unicodedata

# 한국 공문에 흔한 보일러플레이트 패턴
_BOILERPLATE_PATTERNS = [
    re.compile(r"^-\s*\d+\s*-$", re.M),          # 페이지 번호 (- 3 -)
    re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.M | re.I),
    re.compile(r"^\s*\d{1,3}\s*$", re.M),         # 외톨이 페이지번호 줄
    re.compile(r"<EOP>|<EOL>|\x00+"),
]

_STOPWORDS = {
    "끝", "이상", "참조", "별첨", "별지", "주관", "수신", "수신자", "수신처",
}


def normalize(text: str, *, strip_boilerplate: bool = True, remove_stopwords: bool = False) -> str:
    if not text:
        return ""
    # 1) Unicode 정규화
    text = unicodedata.normalize("NFKC", text)
    # 2) 줄바꿈 통일
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # 3) 보일러
    if strip_boilerplate:
        for pat in _BOILERPLATE_PATTERNS:
            text = pat.sub("", text)
    # 4) 공백 정규화 (탭/연속 공백 → 1칸, 빈줄 ≤2)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    # 5) 불용어
    if remove_stopwords:
        for sw in _STOPWORDS:
            text = re.sub(rf"\b{re.escape(sw)}\b", "", text)
    return text.strip()


def quality_score(raw: str, normalized: str) -> float:
    """추출 품질 추정. 0.0~1.0."""
    if not raw:
        return 0.0
    ratio = len(normalized) / max(len(raw), 1)
    # 텍스트가 너무 짧으면 추출 실패 가능성
    if len(normalized) < 50:
        return 0.2
    # 한글 비율
    korean = sum(1 for c in normalized if "가" <= c <= "힯")
    kr_ratio = korean / max(len(normalized), 1)
    return min(1.0, 0.5 * ratio + 0.5 * (1.0 if kr_ratio > 0.1 else kr_ratio * 10))
