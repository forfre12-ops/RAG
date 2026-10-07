"""문서 청크 분할 — LangChain 호환 RecursiveTextSplitter 자체 구현.

LangChain이 무거워서 무의존 자체 구현. interface는 호환.

- split (v1): 문자 기반 recursive splitter. 하위호환용.
- split_v2 (A4): 헤딩 보존 + 문장 경계 hybrid. 운영 권장.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_SEPARATORS = ["\n\n", "\n", "。", ". ", " ", ""]

# A4: 헤딩 패턴 — 한국어/영문 공공문서 일반 형식
# - "제1조", "제 1 절"
# - "Ⅰ.", "Ⅱ.", "1.", "1.1", "1.1.1"
# - "[1]", "(1)"
# - markdown "#"
#
# 숫자 헤딩 과민성 수정(2026-06): 기존 ``\d+(\.\d+){0,3}\s*[.．、]?\s+\S`` 는
#   '2024.', '1. 5억원', '3 명' 같은 일반 본문줄을 헤딩으로 오인해 과분절을 유발했다.
#   (임베딩/reranking 품질 저하). 제약을 강화:
#     - 번호가 '단일 숫자'면 뒤에 구분자([.．、]) 필수 ('3 명'처럼 구분자 없는 단일숫자 배제),
#       '다단계 번호'(1.1, 1.1.1)는 자체 점이 섹션 의미라 트레일링 구분자 선택.
#     - 제목 텍스트 길이 하한 2자 이상 (한두 글자 단위명 배제).
#     - 제목 첫 글자는 숫자/통화기호가 아니어야 함 ('1. 5억원'·'1. 12,000원' 같은
#       금액 나열을 헤딩에서 제외).
#     - 단독 4자리 연도('2024.')는 _NUMERIC_HEADING_EXCLUDE로 별도 차단.
_HEADING_PATTERNS = [
    re.compile(r"^\s*제\s*\d+\s*[조절장항호]", re.MULTILINE),
    re.compile(r"^\s*[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\s*[.．、]", re.MULTILINE),
    # 다단계 번호(1.1, 1.1.1 …): 트레일링 구분자 선택, 제목(숫자·통화 아님 2자+) 필수.
    re.compile(
        r"^\s*\d{1,2}(\.\d{1,2}){1,3}\s*[.．、]?\s+(?![\d₩$￦])\S{2,}",
        re.MULTILINE,
    ),
    # 단일 번호(1, 2 …): 트레일링 구분자([.．、]) 필수 + 제목(숫자·통화 아님 2자+).
    re.compile(
        r"^\s*\d{1,2}\s*[.．、]\s+(?![\d₩$￦])\S{2,}",
        re.MULTILINE,
    ),
    re.compile(r"^\s*[\[\(]\d+[\]\)]", re.MULTILINE),
    re.compile(r"^\s*#{1,6}\s+\S", re.MULTILINE),
]

# 위 숫자 헤딩 패턴에 걸려도 헤딩이 아닌 줄(연도 단독·날짜 등) 추가 배제.
# 예: '2024.', '2024. 1.', '2024. 1. 5.' (날짜) — 섹션 번호가 아니라 일자 표기.
_NUMERIC_HEADING_EXCLUDE = re.compile(
    r"^\s*(?:19|20)\d{2}\s*[.．、](?:\s*\d{1,2}\s*[.．、]?)*\s*$"
)

# 문장 경계 — 한국어 종결 + 영문 . ? !
_SENTENCE_BOUNDARY = re.compile(r"(?<=[.!?。？！])\s+|(?<=[다요죠음임함])\.\s+")


@dataclass
class Chunk:
    index: int
    text: str
    char_count: int
    overlap_prev: int = 0
    overlap_next: int = 0
    heading_path: list[str] = field(default_factory=list)  # A4: 소속 헤딩 경로


def split(text: str, *, size: int = 512, overlap: int = 64) -> list[Chunk]:
    """문자 기반 분할. size/overlap 모두 글자 단위. (v1, 하위호환)"""
    if not text:
        return []
    if len(text) <= size:
        return [Chunk(index=0, text=text, char_count=len(text))]

    parts = _recursive_split(text, size, _SEPARATORS)
    chunks: list[Chunk] = []
    for i, part in enumerate(parts):
        prev_tail = parts[i - 1][-overlap:] if i > 0 and overlap > 0 else ""
        merged = prev_tail + part
        chunks.append(
            Chunk(
                index=i,
                text=merged,
                char_count=len(merged),
                overlap_prev=len(prev_tail),
                overlap_next=overlap if i < len(parts) - 1 else 0,
            )
        )
    return chunks


def split_v2(
    text: str,
    *,
    size: int = 512,
    overlap: int = 64,
    respect_heading: bool = True,
    respect_sentence: bool = True,
) -> list[Chunk]:
    """A4: 헤딩 보존 + 문장 경계 hybrid 분할.

    전략:
    1. respect_heading=True면 헤딩 라인에서 강제 분할(섹션 단위 경계).
    2. 각 섹션을 size 한도로 다시 잘라야 할 때, respect_sentence=True면
       문장 경계에서 자르되 size를 초과하지 않도록 함.
    3. overlap은 직전 청크 tail에서 가져오되, 문장 경계에 맞춰 잘림.
    4. 각 청크에는 소속 heading_path 첨부(검색 결과 reranking·display 활용).
    """
    if not text:
        return []

    sections = _split_by_heading(text) if respect_heading else [("", text)]
    # 헤딩 없는 단일 섹션이고 전체가 size 한도 안이면 v1과 동일 결과
    if len(sections) == 1 and len(text) <= size:
        h, body = sections[0]
        return [Chunk(
            index=0, text=text, char_count=len(text),
            heading_path=[h] if h else [],
        )]

    raw_chunks: list[tuple[list[str], str]] = []  # (heading_path, body)

    for heading, body in sections:
        path = [heading] if heading else []
        if len(body) <= size:
            raw_chunks.append((path, body))
            continue
        # 큰 섹션 → 문장 경계로 패킹
        parts = _pack_by_sentence(body, size) if respect_sentence else _recursive_split(body, size, _SEPARATORS)
        for p in parts:
            raw_chunks.append((path, p))

    chunks: list[Chunk] = []
    for i, (path, part) in enumerate(raw_chunks):
        if i > 0 and overlap > 0:
            prev_body = raw_chunks[i - 1][1]
            prev_tail = _tail_at_sentence(prev_body, overlap)
        else:
            prev_tail = ""
        merged = (prev_tail + part) if prev_tail else part
        chunks.append(
            Chunk(
                index=i,
                text=merged,
                char_count=len(merged),
                overlap_prev=len(prev_tail),
                overlap_next=overlap if i < len(raw_chunks) - 1 else 0,
                heading_path=list(path),
            )
        )
    return chunks


def _split_by_heading(text: str) -> list[tuple[str, str]]:
    """헤딩 라인을 기준으로 (heading, body) 쌍 리스트로 분할.
    헤딩이 없는 선두 부분은 ("", body)로 반환."""
    # 모든 패턴의 매치 위치 수집
    matches: list[tuple[int, str]] = []
    for pat in _HEADING_PATTERNS:
        for m in pat.finditer(text):
            # \s*가 줄바꿈을 포함하므로 m.start()는 빈 줄을 가리킬 수 있음.
            # 실제 헤딩 라인의 시작 위치를 다시 산출.
            ms = m.start()
            # ms 이후 첫 비공백 문자 위치
            i = ms
            while i < len(text) and text[i] in (" ", "\t", "\n", "\r"):
                i += 1
            line_start = text.rfind("\n", 0, i) + 1  # 0이면 문서 시작
            line_end = text.find("\n", i)
            if line_end == -1:
                line_end = len(text)
            heading_line = text[line_start:line_end].strip()
            # 연도·날짜 단독줄('2024.', '2024. 1. 5.')은 섹션 헤딩이 아니므로 제외.
            if heading_line and not _NUMERIC_HEADING_EXCLUDE.match(heading_line):
                matches.append((line_start, heading_line))

    if not matches:
        return [("", text)]

    matches.sort(key=lambda x: x[0])
    # 중복 제거(같은 위치)
    dedup: list[tuple[int, str]] = []
    seen: set[int] = set()
    for pos, h in matches:
        if pos in seen:
            continue
        seen.add(pos)
        dedup.append((pos, h))

    sections: list[tuple[str, str]] = []
    # 첫 헤딩 앞부분(전문 등)
    if dedup[0][0] > 0:
        prefix = text[:dedup[0][0]].strip()
        if prefix:
            sections.append(("", prefix))

    for i, (pos, heading) in enumerate(dedup):
        end = dedup[i + 1][0] if i + 1 < len(dedup) else len(text)
        body = text[pos:end].strip()
        sections.append((heading, body))

    return sections


def _pack_by_sentence(text: str, size: int) -> list[str]:
    """문장 경계로 잘라가며 size 한도 안에 packing."""
    sentences = _SENTENCE_BOUNDARY.split(text)
    out: list[str] = []
    buf = ""
    for s in sentences:
        if not s:
            continue
        candidate = (buf + " " + s) if buf else s
        if len(candidate) <= size:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            if len(s) > size:
                # 한 문장이 너무 크면 fallback 강제 분할
                out.extend(_recursive_split(s, size, _SEPARATORS))
                buf = ""
            else:
                buf = s
    if buf:
        out.append(buf)
    return out


def _tail_at_sentence(text: str, max_len: int) -> str:
    """text의 끝쪽에서 max_len 안쪽 가장 가까운 문장 경계까지 잘라 반환."""
    if len(text) <= max_len:
        return text
    tail = text[-max_len:]
    m = _SENTENCE_BOUNDARY.search(tail)
    if m:
        return tail[m.end():]
    return tail


def _recursive_split(text: str, size: int, seps: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    sep = seps[0]
    rest = seps[1:]
    if sep == "":
        return [text[i : i + size] for i in range(0, len(text), size)]
    parts = text.split(sep)
    out: list[str] = []
    buf = ""
    for p in parts:
        candidate = (buf + sep + p) if buf else p
        if len(candidate) <= size:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            if len(p) > size:
                out.extend(_recursive_split(p, size, rest))
                buf = ""
            else:
                buf = p
    if buf:
        out.append(buf)
    return out
