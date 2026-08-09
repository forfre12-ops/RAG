"""M2 Preprocess — 추출/정규화/청크/파이프라인."""

from __future__ import annotations

from pathlib import Path

from lloydk.modules.m2_preprocess import (
    PreprocessPipeline,
    extract,
    normalize,
    quality_score,
    split,
)


def test_extract_txt(tmp_path: Path):
    p = tmp_path / "sample.txt"
    p.write_text("안녕하세요. 본문입니다.", encoding="utf-8")
    r = extract(p)
    assert r.method == "plain"
    assert "본문입니다" in r.text
    assert r.error is None


def test_extract_plain_utf16_warns_and_lowers_quality(tmp_path: Path):
    p = tmp_path / "utf16.txt"
    p.write_bytes("본문".encode("utf-16"))
    r = extract(p)
    assert r.method == "plain"
    assert "본문" in r.text
    assert r.quality < 1.0
    assert any(w.startswith("plain_decoded_as_") for w in r.warnings)


def test_extract_unsupported_returns_error(tmp_path: Path):
    p = tmp_path / "x.unknown"
    p.write_text("data", encoding="utf-8")
    r = extract(p)
    assert r.text == ""
    assert r.error is not None


def test_normalize_strips_boilerplate():
    text = "본문 첫 줄\n\n- 3 -\nPage 1 of 5\n본문 둘째 줄"
    out = normalize(text)
    assert "Page 1 of 5" not in out
    assert "- 3 -" not in out
    assert "본문 첫 줄" in out
    assert "본문 둘째 줄" in out


def test_normalize_collapses_whitespace():
    text = "ab    cd\n\n\n\nef"
    out = normalize(text)
    assert "    " not in out
    assert "\n\n\n" not in out


def test_quality_score_short_text_is_low():
    raw = "abc"
    norm = "abc"
    assert quality_score(raw, norm) <= 0.3


def test_quality_score_korean_is_high():
    raw = "본 문서는 한국어로 작성된 충분히 긴 텍스트로 정규화 품질을 평가한다." * 3
    norm = raw
    assert quality_score(raw, norm) >= 0.7


def test_split_short_text_returns_single_chunk():
    chunks = split("짧은 텍스트", size=512, overlap=64)
    assert len(chunks) == 1
    assert chunks[0].text == "짧은 텍스트"


def test_split_long_text_produces_chunks_with_overlap():
    text = "가나다라마바사 " * 200  # ~1600자
    chunks = split(text, size=200, overlap=40)
    assert len(chunks) >= 5
    # 첫 청크 제외하고 overlap_prev > 0
    assert all(c.overlap_prev > 0 for c in chunks[1:])


def test_hundred_page_text_contract_preserves_final_page_through_chunking():
    """100페이지 전자문서는 끝 페이지까지 분류 입력 청크에 남아야 한다."""
    text = "\n\n".join(
        f"[PAGE {page:03d}]\n" + ("본문 내용 " * 200)
        for page in range(1, 101)
    )
    chunks = split(text, size=512, overlap=64)

    all_chunk_text = "\n".join(chunk.text for chunk in chunks)
    assert all(f"[PAGE {page:03d}]" in all_chunk_text for page in range(1, 101))
    assert len(chunks) > 100


def test_pdfminer_terminal_form_feed_does_not_create_extra_page():
    from lloydk.modules.m2_preprocess.extractor import _pdf_text_page_count

    assert _pdf_text_page_count("page 1\fpage 2\f") == 2
    assert _pdf_text_page_count("page 1\fpage 2\f\n") == 2
    assert _pdf_text_page_count("page 1") == 1


def test_pipeline_run_text_normalizes_for_classify_service():
    pipe = PreprocessPipeline()
    out = pipe.run_text("foo   bar\n\n\n\nbaz")
    assert isinstance(out, str)
    assert "   " not in out


def test_pipeline_run_text_full_returns_result():
    pipe = PreprocessPipeline(chunk_size=300, chunk_overlap=30)
    result = pipe.run_text_full("한국어 본문입니다. " * 100)
    assert result.text
    assert len(result.chunks) >= 1
    assert result.quality > 0.5
    assert result.metadata["chunk_count"] == len(result.chunks)
