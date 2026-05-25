"""문서 청크 분할 — LangChain 호환 RecursiveTextSplitter 자체 구현.

LangChain이 무거워서 무의존 자체 구현. interface는 호환.
"""

from __future__ import annotations

from dataclasses import dataclass

_SEPARATORS = ["\n\n", "\n", "。", ". ", " ", ""]


@dataclass
class Chunk:
    index: int
    text: str
    char_count: int
    overlap_prev: int = 0
    overlap_next: int = 0


def split(text: str, *, size: int = 512, overlap: int = 64) -> list[Chunk]:
    """문자 기반 분할. size/overlap 모두 글자 단위."""
    if not text:
        return []
    if len(text) <= size:
        return [Chunk(index=0, text=text, char_count=len(text))]

    parts = _recursive_split(text, size, _SEPARATORS)
    # overlap 적용
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


def _recursive_split(text: str, size: int, seps: list[str]) -> list[str]:
    if len(text) <= size:
        return [text]
    sep = seps[0]
    rest = seps[1:]
    if sep == "":
        # 마지막 fallback: 문자 단위 강제 분할
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
