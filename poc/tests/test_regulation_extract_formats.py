"""규정 파일 형식별 추출 → 분할 — 실제 추출기(FUN-022)를 거친다. DB·임베더는 필요 없다.

왜 있나. 서비스 시험(test_regulation_service_db.py)은 마크다운·텍스트만 올린다. 회원사 규정은 대부분 워드·PDF·한글 파일이라
추출기가 내놓는 글자(줄바꿈·표 칸 구분·굵은 글씨)가 분할기 입력이 된다. 여기서는 **같은 규정을 워드 파일로 만들어**
마크다운과 같은 조항 목록이 나오는지, 표로 쓴 규정이 표 구분자를 문장에 흘리지 않는지 본다.

⚠ HWP·HWPX·PDF 는 시험용 파일을 만들 수단이 없어 여기서 재지 않았다(설계서 U-09). 이 시험이 초록이어도 그 형식은 보증하지 않는다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

docx = pytest.importorskip("docx")

from koipa.modules.m2_preprocess.extractor import extract  # noqa: E402
from koipa.regulation.splitter import MODE_ARTICLE, split_regulation  # noqa: E402
from koipa.regulation.tagger import default_display, tag_clause  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "regulation" / "sample_org_regulation.md"


def _shown(result):
    return [c for c in result.clauses if default_display(tag_clause(c.title, c.chapter))]


def _md_to_docx(md: str, path: Path) -> None:
    """마크다운 규정을 사람이 워드로 쓴 모양으로 옮긴다 — 장 제목은 제목 스타일, 조 제목은 굵은 문단, 본문은 일반 문단."""
    d = docx.Document()
    for ln in md.split("\n"):
        t = re.sub(r"\*\*|__", "", ln.strip())
        if not t:
            continue
        if re.match(r"^#+\s", ln):
            d.add_heading(re.sub(r"^#+\s*", "", t), level=1)
        elif re.match(r"^제\s*\d+\s*조", t) and len(t) < 60:
            d.add_paragraph().add_run(t).bold = True
        else:
            d.add_paragraph(t)
    d.save(str(path))


def test_a_word_file_gives_the_same_clauses_as_the_markdown_original(tmp_path):
    md = FIXTURE.read_text(encoding="utf-8")
    p = tmp_path / "regulation.docx"
    _md_to_docx(md, p)

    ext = extract(p)
    assert ext.error is None and len(ext.text) > 5000

    from_word, from_md = split_regulation(ext.text), split_regulation(md)
    assert from_word.mode == MODE_ARTICLE and not from_word.warnings
    assert [c.article_no for c in from_word.clauses] == [c.article_no for c in from_md.clauses]
    assert [c.title for c in from_word.clauses] == [c.title for c in from_md.clauses]
    assert len(_shown(from_word)) == len(_shown(from_md)) == 32
    assert (sum(len(c.sentences) for c in from_word.clauses)
            == sum(len(c.sentences) for c in from_md.clauses))


def test_a_regulation_written_as_a_word_table_is_split_into_articles_without_table_separators(tmp_path):
    """표 위주 규정 — 추출기가 표 한 행을 `제1조(목적) | 본문` 으로 이어 붙인다."""
    d = docx.Document()
    table = d.add_table(rows=6, cols=2)
    for i in range(6):
        table.cell(i, 0).text = f"제{i + 1}조(제목{i + 1})"
        table.cell(i, 1).text = f"이 조는 표의 오른쪽 칸에 적은 본문이다. 이 조는 {i + 1}번째 조의 본문이다."
    p = tmp_path / "table.docx"
    d.save(str(p))

    ext = extract(p)
    assert ext.error is None
    r = split_regulation(ext.text)
    assert r.mode == MODE_ARTICLE and [c.article_no for c in r.clauses] == [f"제{i}조" for i in range(1, 7)]
    sents = [s.text for c in r.clauses for s in c.sentences]
    assert sents and all(not s.startswith("|") and not s.endswith("|") for s in sents), sents
    assert sents[0].startswith("이 조는 표의 오른쪽 칸에 적은 본문이다")


# ── PDF: 줄바꿈에서 문장이 끊기지 않는가 ───────────────────────────────────────────────

def _korean_font() -> str | None:
    """PDF 를 만들 한글 글꼴 — 없으면 이 시험은 건너뛴다(글꼴 없이는 한글 텍스트 층을 만들 수 없다)."""
    import glob

    candidates = [r"C:\Windows\Fonts\malgun.ttf", "/System/Library/Fonts/AppleSDGothicNeo.ttc"]
    for pat in ("/usr/share/fonts/**/Nanum*.ttf", "/usr/share/fonts/**/NotoSansCJK*.ttc", "/usr/share/fonts/**/*Gothic*.ttf",
                "/usr/share/fonts/**/*Malgun*.ttf"):
        candidates += sorted(glob.glob(pat, recursive=True))
    return next((c for c in candidates if Path(c).exists()), None)


@pytest.mark.parametrize("by,width", [("char", 300.0), ("word", 420.0)])
def test_a_wrapped_pdf_gives_the_same_sentences_as_the_markdown_original(tmp_path, by, width):
    """시연 규정을 실제 PDF(줄 나눔·쪽 머리글·쪽 번호 포함)로 만들어 제품 추출기(pdfminer)를 거친다.

    다시 잇기 전에는 문장의 23% 만 원본과 같았다 — 검수자에게 보일 「규정 원문 문장」이 줄 끝에서 끊긴 조각이었다.
    """
    fitz = pytest.importorskip("fitz")
    font_path = _korean_font()
    if font_path is None:
        pytest.skip("한글 글꼴이 없어 PDF 텍스트 층을 만들 수 없다")
    font = fitz.Font(fontfile=font_path)

    def wrap_para(text: str, size: float) -> list[str]:
        lines, cur = [], ""
        units = list(text) if by == "char" else text.split(" ")
        for u in units:
            trial = cur + u if by == "char" else (u if not cur else f"{cur} {u}")
            if cur and font.text_length(trial, fontsize=size) > width:
                lines.append(cur)
                cur = u
            else:
                cur = trial
        return lines + [cur] if cur else lines

    md = FIXTURE.read_text(encoding="utf-8")
    doc = fitz.open()
    height, top, bottom, left, fs = 842.0, 70.0, 70.0, 50.0, 10.5
    page, y = None, 0.0

    def new_page():
        nonlocal page, y
        page = doc.new_page(width=595, height=height)
        page.insert_font(fontname="kr", fontfile=font_path)
        y = top
        page.insert_text((left, 40), "○○전자 문서보안 규정", fontname="kr", fontsize=8.5)          # 쪽 머리글
        page.insert_text((left, height - 35), f"- {len(doc)} -", fontname="kr", fontsize=8.5)     # 쪽 번호

    new_page()
    for raw in [ln for ln in md.split("\n") if ln.strip()]:
        text = raw.replace("**", "").lstrip("# ").strip()
        size = fs + (1.5 if raw.lstrip().startswith(("#", "**")) else 0)
        lines = wrap_para(text, size)
        if y + len(lines) * size * 1.2 + 6 > height - bottom:
            new_page()
        for ln in lines:
            page.insert_text((left, y), ln, fontname="kr", fontsize=size)
            y += size * 1.2
        y += 6
    pdf = tmp_path / "regulation.pdf"
    doc.save(str(pdf))

    ext = extract(pdf)
    assert ext.error is None and len(doc) >= 8
    assert max(len(ln) for ln in ext.text.split("\n")) < 80          # 줄이 실제로 꺾여 있다(문단 한 줄이 아니다)

    from_pdf, from_md = split_regulation(ext.text), split_regulation(md)
    assert from_pdf.mode == MODE_ARTICLE and not from_pdf.warnings
    assert [c.article_no for c in from_pdf.clauses] == [c.article_no for c in from_md.clauses]

    def shown(r):
        return ["".join(s.text.split()) for c in _shown(r) for s in c.sentences if not s.is_lead]

    assert shown(from_pdf) == shown(from_md) and len(shown(from_md)) == 171
