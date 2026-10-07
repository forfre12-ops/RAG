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
# 주의: '외톨이 숫자 줄(\d{1,3} 단독)' 삭제는 의도적으로 제거했다.
#   기존엔 1~3자리 숫자만 있는 모든 줄을 통째로 지웠는데, 영업비밀 도메인에서
#   단독 줄 금액·수량·연도·항목번호(예: "12억", "350", "2024")가 영구 소실되는
#   데이터 손실 버그였다. 페이지번호는 아래 _strip_page_number_lines()가
#   문서 가장자리·앞뒤 빈줄·단조증가라는 보수적 휴리스틱으로만 제거한다.
_BOILERPLATE_PATTERNS = [
    re.compile(r"^-\s*\d+\s*-$", re.M),          # 페이지 번호 (- 3 -) — 명백한 마커
    re.compile(r"^Page\s+\d+\s+of\s+\d+$", re.M | re.I),
    re.compile(r"<EOP>|<EOL>|\x00+"),
]

# 보수적 페이지번호 줄 — '- 3 -' 형태 외에 순수 숫자 단독줄도 페이지번호일 수 있으나,
# 데이터 손실 방지를 위해 (1) 문서 첫/끝 줄이거나 앞뒤가 빈줄로 둘러싸이고
# (2) 문서 전체에서 단조증가하는 경우에만 페이지번호로 간주해 제거한다.
_BARE_NUMBER_LINE = re.compile(r"^\s*(\d{1,4})\s*$")


def _strip_page_number_lines(text: str) -> str:
    """순수 숫자 단독줄 중 '페이지번호로 강하게 추정되는' 줄만 보수적으로 제거.

    제거 조건(모두 충족):
      - 줄이 1~4자리 숫자만 포함.
      - 줄이 문서 가장자리(첫/마지막 줄)이거나 앞·뒤가 모두 빈 줄.
      - 그렇게 추린 후보 숫자들이 문서 순서대로 단조증가(=페이지 흐름).
    이 중 하나라도 어긋나면 본문 수치로 보고 보존한다(미탐 0순위는 아니나
    비밀 수치 소실 방지가 데이터 정합성에 직결).
    """
    lines = text.split("\n")
    n = len(lines)
    # 1차: 가장자리/빈줄 경계 + 순수숫자 후보 인덱스 수집
    candidates: list[tuple[int, int]] = []  # (line_index, value)
    for i, line in enumerate(lines):
        m = _BARE_NUMBER_LINE.match(line)
        if not m:
            continue
        prev_blank = (i == 0) or (lines[i - 1].strip() == "")
        next_blank = (i == n - 1) or (lines[i + 1].strip() == "")
        if prev_blank and next_blank:
            candidates.append((i, int(m.group(1))))
    if len(candidates) < 2:
        # 1개뿐이면 페이지번호인지 본문 수치인지 구분 불가 → 보존(데이터 손실 방지).
        return text
    # 2차: 후보 값이 단조증가(중복/감소 없음)일 때만 페이지번호 흐름으로 인정.
    values = [v for _, v in candidates]
    monotonic = all(values[k] < values[k + 1] for k in range(len(values) - 1))
    if not monotonic:
        return text
    drop = {i for i, _ in candidates}
    return "\n".join(line for i, line in enumerate(lines) if i not in drop)

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
        # 순수 숫자 단독줄은 보수적 휴리스틱으로만 페이지번호 제거(데이터 손실 방지).
        text = _strip_page_number_lines(text)
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
