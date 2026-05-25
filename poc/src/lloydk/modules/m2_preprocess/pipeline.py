from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class Chunk:
    index: int
    text: str
    start_char: int
    end_char: int


class Extractor:
    def supports(self, suffix: str) -> bool: ...
    def extract(self, path: Path) -> str: ...


class PdfExtractor(Extractor):
    def supports(self, suffix): return suffix.lower() == ".pdf"

    def extract(self, path: Path) -> str:
        import fitz  # PyMuPDF
        doc = fitz.open(path)
        return "\n".join(p.get_text("text") for p in doc)


class DocxExtractor(Extractor):
    def supports(self, suffix): return suffix.lower() == ".docx"

    def extract(self, path: Path) -> str:
        from docx import Document
        d = Document(str(path))
        return "\n".join(p.text for p in d.paragraphs)


class TxtExtractor(Extractor):
    def supports(self, suffix): return suffix.lower() in {".txt", ".md"}

    def extract(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8", errors="ignore")


_EXTRACTORS: list[Extractor] = [PdfExtractor(), DocxExtractor(), TxtExtractor()]


def extract_file(path: str | Path) -> str:
    p = Path(path)
    for ex in _EXTRACTORS:
        if ex.supports(p.suffix):
            return ex.extract(p)
    raise ValueError(f"unsupported format: {p.suffix}")


_WHITESPACE = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")
_BOILERPLATE_PATTERNS = [
    re.compile(r"페이지\s*\d+\s*/\s*\d+"),
    re.compile(r"^- ?\d+ ?- ?$", re.MULTILINE),
]


def normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WHITESPACE.sub(" ", text)
    for pat in _BOILERPLATE_PATTERNS:
        text = pat.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text)
    return text.strip()


def chunk_text(text: str, max_len: int = 512, overlap: int = 64) -> list[Chunk]:
    chunks: list[Chunk] = []
    if not text:
        return chunks
    i = 0
    idx = 0
    n = len(text)
    while i < n:
        end = min(i + max_len, n)
        chunks.append(Chunk(index=idx, text=text[i:end], start_char=i, end_char=end))
        if end == n:
            break
        i = end - overlap
        idx += 1
    return chunks


class PreprocessPipeline:
    def __init__(self, max_len: int = 512, overlap: int = 64):
        self.max_len = max_len
        self.overlap = overlap

    def run_text(self, text: str) -> str:
        return normalize(text)

    def run_file(self, path: str | Path) -> str:
        return normalize(extract_file(path))

    def chunk(self, text: str) -> list[Chunk]:
        return chunk_text(text, self.max_len, self.overlap)
