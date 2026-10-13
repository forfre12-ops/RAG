"""규정 분할기·조항 종류 태깅 시험 — 순수 함수라 DB·임베더가 필요 없다.

고정 입력 `tests/fixtures/regulation/sample_org_regulation.md` = 시연용 사내 규정(가상 ○○전자, 53조+부칙).
시험 설계는 실측에서 나온 함정마다 하나씩 잠근다(splitter 머리말 참고).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from koipa.regulation.splitter import (
    MAX_CLAUSE_CHARS,
    MODE_ARTICLE,
    MODE_NUMBERED,
    MODE_PARAGRAPH,
    WARN_PARAGRAPH_MODE,
    _article_sequence_ok,
    _is_wrapped_continuation,
    _parse_article_head,
    _reflow_wrapped_lines,
    _wrap_width,
    normalize_text,
    split_regulation,
    split_sentences,
)
from koipa.regulation.tagger import (
    KIND_GENERAL,
    KIND_GRADE_DEF,
    KIND_HANDLING,
    KIND_PROCEDURE,
    default_display,
    tag_clause,
)

FIXTURE = Path(__file__).parent / "fixtures" / "regulation" / "sample_org_regulation.md"


@pytest.fixture(scope="module")
def sample():
    return split_regulation(FIXTURE.read_text(encoding="utf-8"))


def _by_no(result, article_no):
    return [c for c in result.clauses if c.article_no == article_no]


# ── 조항 분할 ─────────────────────────────────────────────────────────────

def test_sample_is_article_mode_and_finds_every_article(sample):
    assert sample.mode == MODE_ARTICLE
    assert not sample.warnings
    numbered = {c.article_no for c in sample.clauses if not c.article_no.startswith("부칙")}
    assert numbered == {f"제{n}조" for n in range(1, 54)}      # 굵은 글씨 머리글도 53개 전부 읽는다


def test_addendum_articles_do_not_collide_with_body_articles(sample):
    nos = [c.article_no for c in sample.clauses]
    assert len(nos) == len(set(nos))                          # 부칙 제N조 가 본문 제N조 와 겹치지 않는다
    assert any(n.startswith("부칙 제") for n in nos)


def test_chapter_titles_are_kept(sample):
    c = _by_no(sample, "제34조")[0]
    assert "외부 제공" in c.chapter and c.title == "외부 제공의 승인"


def test_reference_inside_a_sentence_is_not_a_heading():
    text = "\n".join([f"제{i}조(항목{i})\n이 조는 본문이다. 이 조는 본문이다. 이 조는 본문이다." for i in range(1, 6)])
    text += "\n제3조에 따른 절차를 거친다. 이 문장은 제3조 본문의 뒷줄이다."
    r = split_regulation(text)
    assert r.mode == MODE_ARTICLE and len(r.clauses) == 5
    assert "제3조에 따른 절차" in r.clauses[2].text or "제3조에 따른 절차" in r.clauses[4].text


def test_plain_and_spaced_headings():
    body = "본문 문장이다. " * 6
    text = "\n".join([f"제 {i} 조 (제목{i})\n{body}" for i in range(1, 6)] + ["제6조 목적\n" + body])
    r = split_regulation(text)
    assert r.mode == MODE_ARTICLE
    assert [c.article_no for c in r.clauses] == [f"제{i}조" for i in range(1, 7)]
    assert r.clauses[5].title == "목적"


def test_body_on_the_same_line_as_heading_is_kept():
    body = "이 조는 본문이 머리글과 같은 줄에 있다. 두 번째 문장도 있다."
    text = "\n".join([f"제{i}조(제목{i}) {body}" for i in range(1, 6)])
    r = split_regulation(text)
    assert len(r.clauses) == 5
    assert any("머리글과 같은 줄" in s.text for s in r.clauses[0].sentences)


def test_control_characters_are_removed():
    assert normalize_text("가\x07나\x0b다\r\n라") == "가나다\n라"
    assert "**" not in normalize_text("**제1조(목적)**")


def test_repeated_page_headers_are_dropped():
    body = "본문 문장이다. 본문 문장이다. 본문 문장이다. 본문 문장이다."
    parts = []
    for i in range(1, 8):
        parts += ["2025년 사내 문서보안 규정", f"제{i}조(항목{i})", body]
    r = split_regulation("\n".join(parts))
    assert r.mode == MODE_ARTICLE
    assert all("사내 문서보안 규정" not in c.text for c in r.clauses)


def test_long_clause_is_split_at_line_boundaries_and_keeps_the_heading_once():
    lines = [f"{i}. " + "가" * 80 + " 항목이다." for i in range(30)]
    text = "제1조(긴 조)\n" + "\n".join(lines)
    text += "\n" + "\n".join(f"제{i}조(다른 조)\n본문 문장이다. 본문 문장이다. 본문 문장이다." for i in range(2, 7))
    r = split_regulation(text)
    parts = _by_no(r, "제1조")
    assert len(parts) >= 2
    assert all(len(c.text) <= MAX_CLAUSE_CHARS + 100 for c in parts)
    assert parts[0].text.startswith("제1조(긴 조)") and not parts[1].text.startswith("제1조(긴 조)")


def test_empty_and_short_inputs_give_no_clauses():
    assert split_regulation("").clauses == []
    assert split_regulation("짧다").clauses == []


def test_split_is_deterministic():
    raw = FIXTURE.read_text(encoding="utf-8")
    a, b = split_regulation(raw), split_regulation(raw)
    assert [(c.article_no, c.text) for c in a.clauses] == [(c.article_no, c.text) for c in b.clauses]


# ── 문장 분할 ─────────────────────────────────────────────────────────────

def test_lead_in_sentences_are_marked(sample):
    c = _by_no(sample, "제12조")[0]
    lead = [s for s in c.sentences if s.is_lead]
    assert lead and "다음 각 호" in lead[0].text
    assert any(not s.is_lead for s in c.sentences)


def test_grade_list_items_share_a_group_regardless_of_label_names(sample):
    c = _by_no(sample, "제34조")[0]
    grouped = [s for s in c.sentences if s.list_group is not None]
    assert len(grouped) == 4 and len({s.list_group for s in grouped}) == 1
    # 라벨 이름을 고정하지 않는다 — 다른 등급 이름이어도 묶인다
    other = split_sentences(["1. 특급: 승인 후 열람한다.", "2. 1급: 부서장이 승인한다.", "3. 2급: 팀장이 승인한다.",
                             "4. 3급: 제한이 없다."])
    assert [s.list_group for s in other] == [1, 1, 1, 1]


def test_document_kind_lists_without_colon_are_not_grade_lists(sample):
    c = _by_no(sample, "제12조")[0]
    assert all(s.list_group is None for s in c.sentences)     # "1. 차세대 제품의 …" 는 등급별 목록이 아니다


def test_two_item_run_is_not_a_list():
    out = split_sentences(["1. 갑: 첫 번째 규칙이다.", "2. 을: 두 번째 규칙이다.", "세 번째는 일반 문장이다 여기는 길다."])
    assert all(s.list_group is None for s in out)


def test_short_fragments_are_not_sentences():
    assert split_sentences(["예.", "가나다", "이 문장은 열두 글자를 넘는 문장이다."])[0].text.startswith("이 문장은")


# ── 인용을 머리글로 읽지 않는다 (공공 「기록물관리 지침」 시험에서 조항 66개가 전부 엉뚱한 조 번호로 나왔다) ─────────────

@pytest.mark.parametrize("line", [
    "제9조 제1항 제1호",                                                  # 표 칸·인용 — 제목이 또 다른 조항을 가리킨다
    "제18조(문서의 접수·처리) 참조",                                       # 참조 줄
    "제30조(기록물의 보존처리), 제31조(보존기록물의 점검) 제32조(보존기록물의 반출제한)",   # 나열
    "제3조 및 제4조에 따른다",                                              # 문장 안의 참조(옛 시험과 같은 종류)
])
def test_citation_shaped_lines_are_not_headings(line):
    assert _parse_article_head(line) is None


@pytest.mark.parametrize("line,no,title", [
    ("제1조(목적) 이 규정은 문서 보안에 관한 사항을 정한다.", "제1조", "목적"),
    ("제12조 목적", "제12조", "목적"),
    ("**제7조(정의)**", "제7조", "정의"),
    ("제5조", "제5조", ""),
])
def test_real_headings_still_read(line, no, title):
    head = _parse_article_head(line)
    assert head is not None and head[0] == no and head[1] == title


def test_a_numbered_document_full_of_citations_is_not_read_as_articles():
    """번호 머리글 문서에 인용 줄이 5개 이상 섞여 있어도 article 모드로 잘못 잡지 않는다."""
    body = "본문 문장이다. " * 6
    parts = [f"{i}. 제목{i}\n{body}" for i in range(1, 9)]
    junk = ["제9조 제1항 제1호", "제18조(문서의 접수·처리) 참조", "제7조", "제4조", "제11조", "제49조"]
    text = "\n".join(p + "\n" + (junk[i] if i < len(junk) else "") for i, p in enumerate(parts))
    r = split_regulation(text)
    assert r.mode == MODE_NUMBERED, r.mode
    assert all(not c.article_no.startswith("제") for c in r.clauses)


@pytest.mark.parametrize("nums,addendum_from,ok", [
    ([1, 2, 3, 4, 5, 6], None, True),
    ([1, 2, 4, 5, 7, 8, 9], None, True),                 # 빠진 번호(개정으로 삭제)는 흔하다
    ([1, 2, 3, 4, 5, 1, 2], 5, True),                    # 부칙에서 처음부터 다시 센다
    ([18, 9, 30, 49, 7, 4, 11, 49], None, False),        # 지침에서 실제로 읽힌 머리글 번호 — 조 순서가 아니다
    ([3, 3, 3, 3, 3, 3], None, False),                   # 같은 번호가 되풀이된다 — 인용이 반복되는 모양
])
def test_article_numbers_must_follow_article_order(nums, addendum_from, ok):
    lines: list[str] = []
    for i, n in enumerate(nums):
        if addendum_from is not None and i == addendum_from:
            lines.append("부칙")
        lines.append(f"제{n}조(제목)")
        lines.append("본문 문장이다. 본문 문장이다.")
    assert _article_sequence_ok(lines) is ok


def test_page_furniture_with_changing_page_numbers_is_dropped_but_short_numbered_titles_stay():
    """쪽 번호가 붙은 머리글("18 │ 2025년 기록물관리 지침 │")은 쪽마다 줄이 달라 번호 머리글로 읽혔다(지침 시험에서 85개)."""
    body = "본문 문장이다. " * 6
    pages = [f"{i}. 제목{i}\n{body}\n{10 + i} │ 2025년 기록물관리 지침 │" for i in range(1, 8)]
    r = split_regulation("\n".join(pages))
    assert r.mode == MODE_NUMBERED and len(r.clauses) == 7
    assert not any("2025년" in c.title or "2025년" in c.text for c in r.clauses)
    # 숫자만 다른 짧은 번호 제목("1. 개요" · "2. 개요" …)은 5번을 넘게 되풀이돼도 지우지 않는다
    r2 = split_regulation("\n".join(f"{i}. 개요\n{body}" for i in range(1, 8)))
    assert r2.mode == MODE_NUMBERED and len(r2.clauses) == 7


def test_low_coverage_adds_a_warning_and_normal_documents_do_not(sample):
    assert not sample.warnings                                   # 시연 규정은 경고가 없다
    body = "본문 문장이다. " * 6
    junk = "\n".join(f"쪽 머리에 붙은 설명 줄 번호 {i} 는 조항이 아니다 그저 길다 길다 길다 길다 길다." for i in range(400))
    r = split_regulation(junk + "\n" + "\n".join(f"제{i}조(제목{i})\n{body}" for i in range(1, 7)))
    assert r.mode == MODE_ARTICLE
    assert any("%만 조항으로 나뉘었습니다" in w for w in r.warnings), r.warnings


# ── 줄바꿈으로 나뉜 글(PDF) — 문장이 줄 끝에서 끊기지 않는다 ──────────────────────────────────────────
# 시연 규정을 PDF 로 만들어 제품 추출기를 거쳐 본 실측(2026-09-26): 다시 잇기 전에는 문장의 23% 만 원본과 같았고 마침표로 끝나는
# 문장이 36% 였다 — 검수 화면에 문장 조각이 「규정 원문 문장」으로 나온다. 여기서는 PDF 추출이 내는 모양을 글자로 흉내 내 잠근다.

def _wrap_lines(text: str, width: int, by: str) -> list[str]:
    """문단 하나를 폭(글자 수)에 맞춰 줄로 꺾는다. by='char' 는 어절 한가운데도 꺾고, 'word' 는 공백에서만 꺾는다."""
    if by == "char":
        return [text[i:i + width] for i in range(0, len(text), width)] or [""]
    lines, cur = [], ""
    for w in text.split(" "):
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = w if not cur else f"{cur} {w}"
    return lines + [cur] if cur else lines


def _as_pdf_text(md: str, width: int, by: str, blank_per_line: bool, page_every: int = 45) -> str:
    """마크다운 규정을 PDF 추출 글자 모양으로: 줄 꺾기 + 문단 구분 방식 + 쪽마다 머리글·쪽 번호."""
    out: list[str] = []
    page = 1
    for para in [ln.strip() for ln in md.split("\n") if ln.strip()]:
        para = para.replace("**", "").lstrip("# ").strip()
        for wrapped in _wrap_lines(para, width, by):
            out.append(wrapped)
            if blank_per_line:
                out.append("")                                    # 줄마다 따로 상자가 된 PDF — 빈 줄이 모든 줄 뒤에 온다
            if len(out) % page_every == 0:
                out += ["○○전자 문서보안 규정", "", f"- {page} -", ""]      # 쪽 머리글과 쪽 번호
                page += 1
        if not blank_per_line:
            out.append("")                                        # 문단 하나가 한 상자 — 상자 사이에만 빈 줄
    return "\n".join(out)


def _shown_sentences(result) -> list[str]:
    return [s.text for c in result.clauses if default_display(tag_clause(c.title, c.chapter))
            for s in c.sentences if not s.is_lead]


def _squash(s: str) -> str:
    return "".join(s.split())


@pytest.mark.parametrize("width,by,blank_per_line", [
    (30, "char", True), (30, "char", False), (42, "char", True), (42, "char", False),
    (30, "word", True), (30, "word", False), (48, "word", True), (48, "word", False),
])
def test_wrapped_pdf_text_gives_the_same_sentences_as_the_original(width, by, blank_per_line):
    md = FIXTURE.read_text(encoding="utf-8")
    original = split_regulation(md)
    wrapped = split_regulation(_as_pdf_text(md, width, by, blank_per_line))
    assert wrapped.mode == MODE_ARTICLE and not wrapped.warnings
    assert [c.article_no for c in wrapped.clauses] == [c.article_no for c in original.clauses]
    a, b = _shown_sentences(wrapped), _shown_sentences(original)
    assert len(a) == len(b) == 171
    assert [_squash(x) for x in a] == [_squash(x) for x in b]           # 글자가 그대로다(이음새 공백 하나는 다를 수 있다)
    assert sum(x.rstrip().endswith((".", "?", "!")) for x in a) == sum(x.rstrip().endswith((".", "?", "!")) for x in b)


def test_paragraph_per_line_text_is_left_alone():
    """워드·마크다운처럼 문단이 한 줄인 글은 줄 길이가 제각각이라 폭이 안 잡히고, 줄 하나도 이어 붙이지 않는다."""
    lines = split_lines = FIXTURE.read_text(encoding="utf-8").split("\n")
    assert _wrap_width(lines) == 0
    assert _reflow_wrapped_lines(lines) is lines                          # 같은 객체 — 아무것도 안 했다
    assert split_lines is lines


def test_wrap_width_is_found_only_when_lines_pile_up_at_one_width():
    wrapped = [("가" * 40) if i % 3 else ("나" * 17) for i in range(60)]                # 긴 줄은 모두 40자
    assert _wrap_width(wrapped) == 40
    ragged = ["가" * n for n in range(20, 80)]                                        # 길이가 제각각
    assert _wrap_width(ragged) == 0
    assert _wrap_width(["가" * 40] * 10) == 0                                         # 줄이 너무 적어 폭을 재지 않는다
    assert _wrap_width(["가" * 12] * 60) == 0                                         # 폭이 좁은 글(짧은 줄이 원래 모양)


@pytest.mark.parametrize("cur,nxt,joined", [
    ("가" * 40, "이어지는 줄이다", True),                                 # 폭까지 찬 줄 + 이어짐
    ("가" * 40 + ".", "이어지는 줄이다", False),                          # 마침표로 끝난 줄
    ("가" * 40 + ":", "이어지는 줄이다", False),                          # 콜론으로 끝난 줄
    ("가" * 20, "이어지는 줄이다", False),                                # 폭까지 안 찬 줄
    ("가" * 40, "① 새 항목이다", False),                                 # 원문자 항목
    ("가" * 40, "1. 새 항목이다", False),                                # 번호 항목
    ("가" * 40, "가. 새 항목이다", False),                               # 가나다 항목
    ("가" * 40, "- 새 항목이다", False),                                 # 글머리
    ("가" * 40, "※ 참고 사항이다", False),
    ("가" * 40, "제12조(극비 문서의 취급)", False),                       # 조 머리글
    ("가" * 40, "제3조에 따라 이어지는 줄이다", True),                    # 인용으로 시작하는 줄은 머리글이 아니다
    ("가" * 40, "제2장 등급의 관리", False),                             # 장 제목
    ("가" * 40, "3천만 원 이상인 경우이다", True),                        # 숫자로 시작해도 항목 표식이 아니면 이음
    ("가" * 40, "다. 극비 문서마다 승인자를 명시한다.", True),               # 앞 문장의 끝 글자("…한"+"다.")가 밀려 내려온 것
    ("나. " + "가" * 40, "다. 극비 문서마다 승인자를 명시한다.", False),   # 둘째 항목 다음의 "다." 는 셋째 항목이다
    ("가" * 30 + " | " + "나" * 10, "이어지는 줄이다", False),           # 표 행
    ("제12조(극비 문서의 취급)", "본문 줄이다", False),                    # 본문 없는 머리글에 본문을 붙이지 않는다
])
def test_which_lines_are_joined(cur, nxt, joined):
    assert _is_wrapped_continuation(cur, nxt, 40) is joined


def test_sentence_tail_pushed_to_the_next_line_is_joined_without_a_space():
    lines = ([f"제{i}조(제목{i})" for i in range(1)]
             + ["가" * 39 + "한", "다. 다음 문장이다."] + ["나" * 39 + "한", "다."] * 15)
    out = _reflow_wrapped_lines(lines)
    assert ("가" * 39 + "한다. 다음 문장이다.") in out                       # "한 다." 가 아니라 "한다."


def test_page_number_only_lines_are_dropped_even_when_nothing_is_wrapped():
    body = "본문 문장이다. " * 6
    text = "\n".join([f"제{i}조(제목{i})\n{body}\n- {i} -" for i in range(1, 7)])
    r = split_regulation(text)
    assert r.mode == MODE_ARTICLE and len(r.clauses) == 6
    assert all("- " + str(i) + " -" not in c.text for i, c in enumerate(r.clauses, 1))


# ── 다른 모드 ─────────────────────────────────────────────────────────────

def test_numbered_mode():
    body = "본문 문장이다. " * 6
    text = "\n".join(f"{i}. 제목{i}\n{body}" for i in range(1, 7))
    r = split_regulation(text)
    assert r.mode == MODE_NUMBERED and len(r.clauses) == 6 and r.clauses[0].article_no == "1"


def test_paragraph_mode_warns():
    text = "\n\n".join(["이 문서는 머리글이 없는 규정이다. 문단으로만 나뉜다. 문단으로만 나뉜다. 문단으로만 나뉜다."] * 4)
    r = split_regulation(text)
    assert r.mode == MODE_PARAGRAPH and r.clauses and r.warnings == [WARN_PARAGRAPH_MODE]
    assert r.clauses[0].article_no.startswith("문단 ")


# ── 태깅 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("title,chapter,kind", [
    ("목적", "제1장 총칙", KIND_GENERAL),
    ("용어의 정의", "", KIND_GENERAL),
    ("정보보호위원회", "", KIND_GENERAL),
    ("등급의 구분", "", KIND_GRADE_DEF),
    ("극비 등급", "", KIND_GRADE_DEF),
    ("일반 등급", "", KIND_GRADE_DEF),
    ("등급 결정 절차", "", KIND_PROCEDURE),
    ("잠정 등급", "", KIND_PROCEDURE),
    ("등급 하향", "", KIND_PROCEDURE),
    ("재분류와 등급 상향", "", KIND_PROCEDURE),
    ("등급 판단의 세부 기준", "", KIND_PROCEDURE),
    ("경과 조치", "부칙", KIND_GENERAL),
    ("종전 규정의 폐지", "부 칙", KIND_GENERAL),
    ("극비 문서의 종류", "", KIND_HANDLING),
    ("열람 범위", "", KIND_HANDLING),
    ("외부 제공의 승인", "", KIND_HANDLING),
    ("시험·품질 문서", "", KIND_HANDLING),
    ("", "", KIND_HANDLING),
])
def test_tag_rules(title, chapter, kind):
    assert tag_clause(title, chapter) == kind


def test_only_handling_is_displayed_by_default():
    assert default_display(KIND_HANDLING)
    assert not any(default_display(k) for k in (KIND_GENERAL, KIND_PROCEDURE, KIND_GRADE_DEF, "other"))


def test_sample_tagging_matches_the_hand_picked_exclusion(sample):
    """시험에서 손으로 뺀 21개(제1~11·13·15·17·19~25조) = 규칙이 뺀 집합. (규칙은 이 규정을 보고 만들었다 — 다른 규정으로 재검증 필요)"""
    excluded = set()
    for c in sample.clauses:
        if not c.article_no.startswith("제"):
            continue
        if not default_display(tag_clause(c.title, c.chapter)):
            excluded.add(int(c.article_no[1:-1]))
    assert excluded == set(range(1, 12)) | {13, 15, 17} | set(range(19, 26))


# ── 표 위주 규정(추출기가 표 칸을 " | " 로 이어 붙인다) ─────────────────────────────────

def test_table_cell_separators_do_not_leak_into_displayed_sentences():
    """워드 표로 쓴 규정은 `제1조(목적) | 본문` 꼴로 추출된다 — 검수자에게 보이는 문장 앞에 `| ` 가 붙으면 안 된다."""
    rows = [(f"제{i}조(제목{i})", f"이 조는 표의 오른쪽 칸에 적은 본문이다. 이 조는 {i}번째 조의 본문이다.") for i in range(1, 7)]
    text = "\n".join(f"{a} | {b}" for a, b in rows)
    r = split_regulation(text)
    assert r.mode == MODE_ARTICLE and len(r.clauses) == 6
    sents = [s.text for c in r.clauses for s in c.sentences]
    assert sents and all(not s.startswith("|") and not s.endswith("|") for s in sents), sents
    assert sents[0].startswith("이 조는 표의 오른쪽 칸에 적은 본문이다")     # 글자는 그대로다
