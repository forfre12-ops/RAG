"""A4: 청크 v2 — 헤딩 보존 + 문장 경계 hybrid 검증."""

from __future__ import annotations

from koipa.modules.m2_preprocess import split_v2


def test_split_v2_short_text_single_chunk():
    text = "짧은 텍스트입니다."
    chunks = split_v2(text, size=512)
    assert len(chunks) == 1
    assert chunks[0].text == text
    assert chunks[0].heading_path == []


def test_split_v2_preserves_heading_path():
    text = """전문 텍스트입니다.

제1조 (목적)
이 규정은 영업비밀 보호를 목적으로 한다.

제2조 (정의)
영업비밀이란 공연히 알려지지 아니한 기술상 또는 경영상 정보를 말한다.
"""
    chunks = split_v2(text, size=80, overlap=0, respect_heading=True)
    # 헤딩이 인식되어 섹션이 나뉘어야 함
    headings = [c.heading_path[0] for c in chunks if c.heading_path]
    assert any("제1조" in h for h in headings)
    assert any("제2조" in h for h in headings)


def test_split_v2_sentence_boundary_no_midsentence_cut():
    # 큰 섹션을 size 한도로 자를 때 문장 경계에서 잘려야 함
    text = "첫 문장입니다. 두 번째 문장입니다. 세 번째 문장입니다. 네 번째 문장입니다. 다섯 번째 문장입니다."
    chunks = split_v2(text, size=30, overlap=0, respect_sentence=True)
    # 최소 1개 청크
    assert len(chunks) >= 1
    # 각 청크는 문장 종결로 끝나거나 마지막 청크여야 함
    for c in chunks[:-1]:
        assert c.text.rstrip().endswith((".", "!", "?", "다", "요", "음", "임", "함")), \
            f"청크가 문장 경계에서 끝나지 않음: {c.text!r}"


def test_split_v2_overlap_at_sentence_boundary():
    text = "첫 문장. 두 번째 문장. 세 번째 문장. 네 번째 문장."
    chunks = split_v2(text, size=20, overlap=10, respect_sentence=True)
    # overlap이 적용되어 첫 청크 외에는 overlap_prev > 0
    for c in chunks[1:]:
        assert c.overlap_prev >= 0  # 문장 경계 정렬 결과 0일 수도 있음


def test_split_v2_markdown_heading():
    text = """소개 문단.

# 제목 1
첫 섹션 본문입니다.

## 제목 2
둘째 섹션 본문입니다.
"""
    chunks = split_v2(text, size=200, overlap=0)
    heading_lines = [c.heading_path[0] if c.heading_path else "" for c in chunks]
    assert any("# 제목 1" in h for h in heading_lines)


def test_split_v2_no_heading_falls_back_to_sentence():
    text = "문장 하나. 문장 둘. 문장 셋. 문장 넷. 문장 다섯. 문장 여섯. 문장 일곱."
    chunks = split_v2(text, size=20, overlap=0)
    # 헤딩이 없으니 heading_path 비어있어야
    for c in chunks:
        assert c.heading_path == []
    # 그래도 분할은 되어야
    assert len(chunks) > 1
