"""규정 원문 → 조항·문장 분할 (순수 함수, 외부 의존 없음).

■ 세 가지 모드 — 자동으로 고른다
    article    `제N조(제목)` 머리글이 5개 이상. 조 하나가 조항 하나.
    numbered   `1. 제목` / `1.1 제목` 머리글이 5개 이상.
    paragraph  위 둘 다 아님. 줄 묶음 단위(정확도가 낮다 → 경고를 돌려준다).

■ 이 모듈이 하지 않는 것
  파일 읽기·추출(FUN-022 추출기 몫) · 임베딩 · DB. 넣어 준 글자만 나눈다.

■ 시험에서 나온 함정 (tests/test_regulation_splitter.py 가 지킨다)
  · 굵은 글씨 머리글 `**제1조(목적)**` 를 못 읽으면 조각이 0개가 된다(처음 실측에서 실제로 그랬다).
  · 부칙의 `제1조` 가 본문 `제1조` 와 같은 번호라 id 가 겹친다 → 부칙은 "부칙 제N조" 로 구분한다.
  · 문장 안의 참조("제34조에 따른 …")를 머리글로 읽으면 안 된다.
  · **줄 첫머리의 인용도 머리글이 아니다**(공공 「기록물관리 지침」 시험, 2026-09-25): 표 칸·참조 줄
    ("제9조 제1항 제1호", "제18조(문서의 접수·처리) 참조", "제30조(…), 제31조(…)")이 머리글 8개로 읽혀 조항 66개가
    전부 엉뚱한 조 번호로 나왔다. 그래서 ① 인용 모양은 머리글에서 뺀다 ② **머리글 번호가 조 순서(오름차순)로 이어지는지**
    확인한 뒤에만 article 모드로 본다 ③ 어느 모드든 조항으로 나뉜 글자 비율이 낮으면 경고를 돌려준다.
  · **PDF 에서 뽑은 글자는 보이는 줄 단위라 문장이 줄바꿈에서 끊긴다**(2026-09-26, 시연 규정을 PDF 로 만들어 제품 추출기를
    거쳐 본 실측): 문장 238개 중 원본과 글자가 같은 것은 23%, 마침표로 끝나는 것은 36% — 검수 화면에 문장 조각이 원문 문장으로 나온다.
    그래서 줄이 한 폭에 몰려 있는 글(`_wrap_width`)이면 **줄바꿈으로 나뉜 줄을 다시 잇는다**(`_reflow_wrapped_lines`).
    마침표로 끝난 줄·번호/글머리로 시작하는 줄·표 행·조 머리글은 잇지 않고, 줄이 한 폭에 안 몰린 글(문단 단위 워드·마크다운)은 손대지 않는다.
  · 서두 문장("다음 각 호의 …")은 내용이 없다 — 선택 후보에서 빼려고 `is_lead` 로 표시한다.
  · 등급별 목록("1. 극비: … 2. 기밀: …")은 한 줄만 뽑으면 그 등급으로 끄는 표시가 된다 →
    같은 묶음(`list_group`)으로 표시해 두었다가 선택기가 목록 전체를 보인다. **라벨 이름은 고정하지 않는다**
    (회원사마다 등급 이름이 다르다).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

MAX_CLAUSE_CHARS = 1200
MIN_CLAUSE_CHARS = 40
MIN_SENTENCE_CHARS = 12
ARTICLE_MODE_MIN = 5
NUMBERED_MODE_MIN = 5
LIST_GROUP_MIN = 3          # 이 개수 이상 연속한 "번호. 라벨: 내용" 줄이 한 목록이다
REPEATED_LINE_MIN = 5       # 같은 짧은 줄이 이만큼 반복되면 머리글·꼬리말로 본다
REPEATED_LINE_MAX_CHARS = 60
PAGE_FURNITURE_MIN_CHARS = 8   # 숫자를 뺀 나머지가 이보다 짧으면 번호 제목("3. 목적")일 수 있어 남긴다

MODE_ARTICLE = "article"
MODE_NUMBERED = "numbered"
MODE_PARAGRAPH = "paragraph"

WARN_PARAGRAPH_MODE = "조 단위 구분이 없어 정확도가 낮을 수 있습니다."
COVERAGE_WARN_BELOW = 0.5   # 조항으로 나뉜 글자가 전체의 이 비율 미만이면 경고한다
SEQUENCE_MAX_BREAK_RATIO = 0.2   # 조 번호가 오름차순에서 벗어난 쌍의 허용 비율(부칙에서 처음부터 다시 세는 것은 뺀다)
# 줄바꿈으로 나뉜 줄 다시 잇기 — 줄 길이가 한 폭에 몰려 있는 글(PDF·줄 나눔이 박힌 텍스트)에만 쓴다
WRAP_MIN_LINES = 30         # 이보다 줄이 적으면 폭을 재지 않는다
WRAP_TOP_FRACTION = 0.2     # 가장 긴 줄 20% 의 길이를 본다
WRAP_CLUSTER_SPREAD = 12    # 그 줄들의 길이 차가 이 안이면 한 폭에 몰린 것(긴 어절 하나가 줄을 밀어내는 폭)
WRAP_MIN_WIDTH = 25         # 이보다 좁으면(짧은 줄이 원래 모양인 글) 손대지 않는다
WRAP_WALL_BAND = 6          # 폭 바로 아래 이 만큼(글자)의 구간이 '벽' — 줄바꿈으로 나뉜 글은 여기에 줄이 몰린다
WRAP_WALL_MIN_LINES = 8     # 벽에 줄이 이만큼은 있어야 한다
WRAP_WALL_RATIO = 1.8       # 벽의 줄 밀도가 그 아래 구간(10글자)의 밀도의 이 배 이상이어야 한다 — 길이가 고르게 퍼진 글을 거른다
WRAP_JOIN_SLACK_MAX = 16    # 어절이 길어 줄이 폭보다 이만큼까지 덜 찰 수 있다
_BREAK = "\x00"             # 쪽 머리글·꼬리말·쪽 번호를 지운 자리 표식 — 문장이 쪽 경계에서 이어지는 것을 잇게 해 준다
WRAP_BLANK_NOISE = 0.7      # 줄 뒤에 빈 줄이 오는 비율이 이 이상이면 빈 줄은 문단 구분이 아니라 추출 흔적이다

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BOLD = re.compile(r"\*\*|__")
_NUM_STRIP = re.compile(r"\d+")
_ART_HEAD = re.compile(r"^\s*[*#>\s]*(제\s*\d+\s*조(?:\s*의\s*\d+)?)")
_CHAPTER = re.compile(r"^\s*[*#>\s]*((?:제\s*\d+\s*(?:장|편|절)|부\s*칙)\b[^\n]*|부\s*칙)\s*$")
# 줄 첫머리의 인용: 조 번호 뒤에 다른 조·항·호를 가리키는 말이 오면 머리글이 아니다("제9조 제1항 제1호")
_CITATION_TITLE = re.compile(r"^제\s*\d+\s*(?:조|항|호|목|장|절)")
# 조 번호(괄호 제목) 뒤가 나열·참조로 이어지면 머리글이 아니다("제30조(…), 제31조(…)" · "제18조(…) 참조")
_CITATION_TAIL = re.compile(r"^(?:[,，、·ㆍ]|(?:참조|참고)(?![가-힣])|및\s|또는\s)")
_NUM_HEAD = re.compile(r"^\s*[*#>\s]*(\d+(?:\.\d+){0,3})[.)]?\s+(\S[^\n]{0,58})$")
# 서두 문장 판정은 **공백을 뺀 글자**로 본다 — 글자 단위로 꺾인 PDF 를 이으면 "다 음 기준" 처럼 어절 한가운데 공백이 끼일 수 있다
_LEAD = re.compile(r"다음(각호|기준|과같|중)|다음에따른다|아래와같")
# 라벨은 짧은 글자·숫자("극비", "1급")이고 글자가 하나는 있어야 한다(날짜·숫자만인 "1. 2024: …" 는 제외)
_LIST_ITEM = re.compile(r"^\d+\.\s*((?=[^:：]*[가-힣A-Za-z])[가-힣A-Za-z0-9 ]{1,10}?)\s*[:：]\s*\S")
_SENT_SPLIT = re.compile(r"(?<=다\.)\s+")
# 쪽 번호만 있는 줄("- 12 -", "Page 3", "3 / 16", "12쪽") — 쪽마다 값이 달라 반복 줄 세기로는 안 잡힌다
_PAGE_NO = re.compile(r"^\s*(?:[-–—]\s*\d{1,4}\s*[-–—]|(?:page|p\.?)\s*\d{1,4}(?:\s*/\s*\d{1,4})?|\d{1,4}\s*/\s*\d{1,4}|\d{1,4}\s*쪽)\s*$", re.I)
# 새 항목의 시작(번호·원문자·가나다·글머리·표 행) — 이 줄은 앞 줄의 이어짐이 아니다.
# 조 머리글은 여기 넣지 않는다: "제3조에 따라 …" 로 시작하는 줄은 앞 문장의 이어짐일 수 있어 `_parse_article_head` 가 가른다.
_ITEM_START = re.compile(
    r"^(?:\d{1,3}[.)]\s|\d{1,2}\.\d{1,2}[.\s]|\(\d{1,3}\)|\([가-힣ㄱ-ㅎA-Za-z]\)|[가-힣][.)]\s"
    "|[①-⑳㉑-㉟㊱-㊿㉠-㉻㈀-㈞]"
    # 강한 글머리는 뒤에 공백이 없어도 표식이다("ㅇ생산현황"). 약한 글머리는 뒤에 공백이 있어야 한다 — 가운뎃점은
    # 어절 안에도 쓰여("견적서·발주서") 글자 단위로 꺾인 줄이 "·협력사" 로 시작할 수 있다.
    "|[ㅇ○●◎■□▶▷◆◇※☞☛▣]"
    "|[▪▫◦•·*\\-–—]\\s"
    r"|\|)"
)
_NO_SPACE_BEFORE = re.compile(r"^[.,;:!?)\]}」』]")   # 줄 첫머리의 닫는 부호·마침표는 앞 글자에 붙는다
_TERMINAL_END = re.compile(r"[.。?!;:]\s*$")
_SENTENCE_TAIL = re.compile(r"^다[.,)](?:\s|$)")     # 앞 줄 문장의 끝 글자만 다음 줄로 밀려 내려온 모양


@dataclass
class Sentence:
    seq: int
    text: str
    is_lead: bool = False
    list_group: int | None = None


def compose_embed_text(article_no: str, title: str, chapter: str, text: str) -> str:
    """임베딩·낱말 색인에 넣는 글자 — 머리글 정보를 붙여 조각을 나눈 뒤에도 조항을 알아본다.

    색인(서비스)과 조회(캐시)가 **같은 글자**를 써야 하므로 이 함수 하나가 정본이다.
    """
    head = f"{article_no}({title})" if title else article_no
    return f"[{chapter} {head}] {text}" if chapter else f"[{head}] {text}"


@dataclass
class Clause:
    seq: int
    article_no: str
    title: str
    chapter: str
    text: str
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def embed_text(self) -> str:
        return compose_embed_text(self.article_no, self.title, self.chapter, self.text)


@dataclass
class SplitResult:
    mode: str
    clauses: list[Clause]
    warnings: list[str] = field(default_factory=list)


def normalize_text(raw: str) -> str:
    """제어 문자(PDF 추출의 \\x07 등)·굵은 글씨 표식을 걷고 줄바꿈을 맞춘다. 글자 내용은 바꾸지 않는다."""
    t = raw.replace("\r\n", "\n").replace("\r", "\n")
    t = _CONTROL.sub("", t)
    t = _BOLD.sub("", t)
    return "\n".join(ln.rstrip() for ln in t.split("\n"))


def _parse_article_head(line: str) -> tuple[str, str, str] | None:
    """(조 번호, 제목, 같은 줄에 이어진 본문) 또는 None. 문장 안의 참조는 머리글이 아니다."""
    m = _ART_HEAD.match(line)
    if not m:
        return None
    token = re.sub(r"\s+", "", m.group(1))
    after = line[m.end(1):]
    if after == "":
        return token, "", ""
    first = after[0]
    if first in "(（":
        close = re.search(r"[)）]", after)
        if not close:
            return None
        tail = after[close.end():].strip()
        if _CITATION_TAIL.match(tail):
            return None
        return token, after[1:close.start()].strip(), tail
    if first.isspace():
        rest = after.strip()
        if not rest:
            return token, "", ""
        if rest[0] in "(（":
            close = re.search(r"[)）]", rest)
            if not close:
                return None
            tail = rest[close.end():].strip()
            if _CITATION_TAIL.match(tail):
                return None
            return token, rest[1:close.start()].strip(), tail
        # 괄호 없는 제목("제1조 목적"): 짧고 문장이 아니고 다른 조항을 가리키는 말이 아닐 때만 머리글
        if (len(rest) <= 30 and not rest.endswith(("다", "다.", ".", "。"))
                and not _CITATION_TITLE.match(rest)):
            return token, rest, ""
    return None


def _clean_heading(text: str) -> str:
    return re.sub(r"^[#>*\s]+", "", text).strip()


def _drop_repeated_lines(lines: list[str]) -> list[str]:
    """쪽 머리글·꼬리말처럼 같은 짧은 줄이 여러 번 반복되면 버린다.

    조 머리글·장 제목은 남긴다. **문장으로 끝나는 줄("…다.")도 남긴다** — 머리글·꼬리말은 보통 마침표로 끝나지 않고,
    같은 본문 문장이 여러 조에 되풀이되는 규정에서 본문이 지워지면 안 된다.

    지운 줄은 **`_BREAK` 표식으로 남긴다** — 쪽 경계에서 문장이 이어질 때 그 표식이 있어야 앞뒤 줄을 이을 수 있다.
    표식은 `_reflow_wrapped_lines` 가 걷어 내므로 이 함수의 결과를 그대로 다른 곳에 넘기지 않는다.
    """
    counts = Counter(ln.strip() for ln in lines if ln.strip())
    drop = {t for t, n in counts.items()
            if n >= REPEATED_LINE_MIN and len(t) <= REPEATED_LINE_MAX_CHARS
            and not t.endswith(("다.", "다", ".", "。"))
            and not _parse_article_head(t) and not _CHAPTER.match(t)}
    # 쪽 번호가 붙은 머리글·꼬리말("18 │ 2025년 기록물관리 지침 │", "- 12 -", "Page 3")은 쪽마다 줄이 달라 위 세기로 안 잡힌다.
    # 숫자를 뺀 뒤 같은 줄이 여러 번 나오고 **숫자 아닌 부분이 충분히 길면**(짧은 번호 제목 "3. 목적" 을 지우지 않으려고) 버린다.
    counts_nd = Counter(_NUM_STRIP.sub("", ln.strip()) for ln in lines if ln.strip())
    drop_nd = {k for k, n in counts_nd.items()
               if n >= REPEATED_LINE_MIN and PAGE_FURNITURE_MIN_CHARS <= len(k) <= REPEATED_LINE_MAX_CHARS
               and not k.endswith(("다.", "다", ".", "。"))}
    def keep(ln: str) -> bool:
        t = ln.strip()
        if not t or _parse_article_head(t) or _CHAPTER.match(t):
            return True                                 # 빈 줄·조 머리글·장 제목은 언제나 남긴다
        if _PAGE_NO.match(t):
            return False                                # 쪽 번호만 있는 줄
        return t not in drop and _NUM_STRIP.sub("", t) not in drop_nd

    return [ln if keep(ln) else _BREAK for ln in lines]


def _wrap_width(lines: list[str]) -> int:
    """줄이 **한 폭에 몰려 있으면** 그 폭(글자 수), 아니면 0. 줄바꿈으로 나뉜 글인지 가르는 유일한 근거다.

    PDF 추출·줄 나눔이 박힌 텍스트는 문단 안의 줄이 거의 같은 길이(한 폭)까지 찬 뒤 넘어간다 — 그래서 가장 긴 줄 20% 의 길이가
    서로 몇 글자 안에 몰려 있다. 문단 하나가 한 줄인 글(워드·한글·마크다운)은 긴 줄들의 길이가 제각각이라 0 이 나온다.
    조금이라도 애매하면 0 이다 — 잘못 이어 붙이는 것이 안 잇는 것보다 나쁘다.
    """
    lens = sorted((len(t) for t in (ln.strip() for ln in lines) if t and t != _BREAK and not _PAGE_NO.match(t)), reverse=True)
    if len(lens) < WRAP_MIN_LINES:
        return 0
    top = lens[: max(8, int(len(lens) * WRAP_TOP_FRACTION))]
    width = top[len(top) // 10]                          # 표 행·제목처럼 유난히 긴 줄 몇 개는 건너뛴다
    if width < WRAP_MIN_WIDTH or width - top[-1] > WRAP_CLUSTER_SPREAD:
        return 0
    # 벽: 폭 바로 아래(WRAP_WALL_BAND 글자)에 줄이 몰리고, 그 아래 10글자 구간보다 훨씬 빽빽해야 한다. 긴 줄들이 좁은 범위에
    # 있어도 그 범위에 줄이 고르게 퍼져 있으면(짧은 항목 목록) 넘쳐서 꺾인 글이 아니다.
    wall = sum(1 for n in lens if width - WRAP_WALL_BAND <= n <= width)
    below = sum(1 for n in lens if width - WRAP_WALL_BAND - 10 <= n < width - WRAP_WALL_BAND)
    if wall < WRAP_WALL_MIN_LINES or wall / (WRAP_WALL_BAND + 1) < WRAP_WALL_RATIO * below / 10:
        return 0
    return width


def _blank_is_noise(lines: list[str]) -> bool:
    """거의 모든 줄 뒤에 빈 줄이 오면(줄마다 따로 상자로 뽑힌 PDF) 빈 줄은 문단 구분이 아니라 추출 흔적이다."""
    nonblank = followed = 0
    for i, ln in enumerate(lines):
        if not ln.strip() or ln == _BREAK:
            continue
        nonblank += 1
        if i + 1 < len(lines) and not lines[i + 1].strip():
            followed += 1
    return nonblank > 0 and followed / nonblank >= WRAP_BLANK_NOISE


def _is_wrapped_continuation(cur: str, nxt: str, width: int) -> bool:
    """`nxt` 가 `cur` 의 줄바꿈 뒤 이어짐인가 — 아래를 **모두** 만족할 때만 잇는다."""
    if _TERMINAL_END.search(cur):
        return False                                     # 마침표·물음표·콜론으로 끝난 줄은 문장·문단의 끝이다
    if len(cur) < width - min(WRAP_JOIN_SLACK_MAX, max(WRAP_CLUSTER_SPREAD, width // 2)):
        return False                                     # 폭까지 차지 않은 줄은 넘쳐서 꺾인 줄이 아니다(긴 어절이 줄을 밀어낸 만큼은 허용)
    # "다. …" 로 시작하는 줄: 앞 문장의 끝 글자("…삭제한" + "다.")가 다음 줄로 밀린 것이 흔하다. 가나다 목록의 셋째 항목 표식과
    # 모양이 같으므로, 앞 줄이 둘째 항목("나. …")일 때만 새 항목으로 본다.
    sentence_tail = _SENTENCE_TAIL.match(nxt) is not None and re.match(r"^나[.)]\s", cur) is None
    if not sentence_tail and (_ITEM_START.match(nxt) or _CHAPTER.match(nxt) or _parse_article_head(nxt)):
        return False                                     # 새 항목·장 제목·조 머리글
    if " | " in cur or " | " in nxt:
        return False                                     # 표 행
    if _CHAPTER.match(cur):
        return False
    head = _parse_article_head(cur)
    if head is not None and not head[2]:
        return False                                     # 본문 없는 조 머리글에 본문을 붙이지 않는다
    return True


def _reflow_wrapped_lines(lines: list[str]) -> list[str]:
    """줄바꿈으로 나뉜 줄을 다시 이어 한 문단으로 만들고 `_BREAK` 표식을 걷는다. 줄이 한 폭에 안 몰렸고 표식도 없으면 같은 객체를 돌려준다.

    이음새는 공백 하나다. 한글이 글자 단위로 꺾인 PDF 에서는 어절 한가운데가 이어질 때 공백이 하나 끼어들 수 있다
    (원문 글자는 그대로이고 띄어쓰기만 다르다) — 문장 경계를 바로잡는 것이 목적이라 그 정도는 받아들인다.
    쪽 경계(`_BREAK` 가 낀 자리)는 빈 줄이 있어도 건너뛰어 이을 수 있다 — 문장이 쪽을 넘어 이어지는 일은 흔하다.
    """
    width = _wrap_width(lines)
    if not width:
        return lines if _BREAK not in lines else [ln for ln in lines if ln != _BREAK]
    cross_blank = _blank_is_noise(lines)
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        if lines[i] == _BREAK:
            i += 1
            continue
        cur = lines[i].strip()
        if not cur:
            out.append(lines[i])
            i += 1
            continue
        j = i + 1
        while True:
            k, saw_break = j, False
            while k < n and (lines[k] == _BREAK or not lines[k].strip()):
                saw_break = saw_break or lines[k] == _BREAK
                k += 1
            if k >= n:
                break
            if k > j and not (cross_blank or saw_break):
                break                                    # 빈 줄이 문단 구분인 글에서, 쪽 경계도 아닌 빈 줄은 넘지 않는다
            nxt = lines[k].strip()
            if not _is_wrapped_continuation(cur, nxt, width):
                break
            # 문장 끝 글자("다.")나 닫는 부호가 줄 첫머리로 밀려 내려온 것은 앞 글자에 붙는다(공백 없이 잇는다)
            glue = _SENTENCE_TAIL.match(nxt) or _NO_SPACE_BEFORE.match(nxt)
            cur = f"{cur}{'' if glue else ' '}{nxt}"
            j = k + 1
        out.append(cur)
        i = j
    return out


def split_sentences(body_lines: list[str]) -> list[Sentence]:
    """줄 단위 → 한 줄 안에서 `다.` 뒤 공백으로 분할 → 짧은 것 제외 → 서두·목록 표시."""
    sentences: list[Sentence] = []
    for ln in body_lines:
        ln = ln.strip()
        if not ln or set(ln) <= set("|-: "):
            continue
        for s in _SENT_SPLIT.split(ln):
            # 표 칸 구분자("| ")가 줄 양끝에 남은 것은 추출 흔적이라 걷는다(글자 내용은 그대로) — 표 위주 규정에서 나온다
            s = s.strip().strip("|").strip()
            if len(s) >= MIN_SENTENCE_CHARS:
                sentences.append(Sentence(seq=len(sentences), text=s, is_lead=bool(_LEAD.search("".join(s.split())))))
    _mark_list_groups(sentences)
    return sentences


def _mark_list_groups(sentences: list[Sentence]) -> None:
    """"번호. 라벨: 내용" 줄이 3개 이상 연속하면 한 목록으로 묶는다(라벨 이름은 보지 않는다)."""
    gid = 0
    run: list[Sentence] = []

    def close() -> None:
        nonlocal gid, run
        if len(run) >= LIST_GROUP_MIN:
            gid += 1
            for s in run:
                s.list_group = gid
        run = []

    for s in sentences:
        if _LIST_ITEM.match(s.text):
            run.append(s)
        else:
            close()
    close()


def _split_long(lines: list[str]) -> list[list[str]]:
    """MAX 를 넘는 조항은 줄 경계에서 나눈다(줄 하나가 MAX 보다 길면 그대로 둔다)."""
    parts: list[list[str]] = []
    cur: list[str] = []
    size = 0
    for ln in lines:
        if cur and size + len(ln) + 1 > MAX_CLAUSE_CHARS:
            parts.append(cur)
            cur, size = [], 0
        cur.append(ln)
        size += len(ln) + 1
    if cur:
        parts.append(cur)
    return parts


def _emit(out: list[Clause], article_no: str, title: str, chapter: str, header_line: str,
          body_lines: list[str]) -> None:
    lines = [header_line] + body_lines if header_line else body_lines
    lines = [ln for ln in lines if ln.strip()]
    for part_idx, part in enumerate(_split_long(lines)):
        text = "\n".join(part).strip()
        if len(text) < MIN_CLAUSE_CHARS:
            continue
        # 머리글 줄은 첫 조각에만 들어 있다 — 문장 분할은 머리글 줄을 뺀 본문으로 한다
        body = part[1:] if (part_idx == 0 and header_line and part and part[0] == header_line) else part
        clause = Clause(seq=len(out), article_no=article_no, title=title, chapter=chapter, text=text)
        clause.sentences = split_sentences(body)
        out.append(clause)


def _is_addendum(chapter: str) -> bool:
    return bool(re.match(r"^부\s*칙", chapter))


def _split_article(lines: list[str]) -> list[Clause]:
    out: list[Clause] = []
    chapter = ""
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            _emit(out, cur["no"], cur["title"], cur["chapter"], cur["header"], cur["body"])
        cur = None

    for line in lines:
        head = _parse_article_head(line)
        if head:
            flush()
            token, title, rest = head
            no = f"부칙 {token}" if _is_addendum(chapter) else token
            header = f"{no}({title})" if title else no
            cur = {"no": no, "title": title, "chapter": chapter, "header": header, "body": [rest] if rest else []}
            continue
        ch = _CHAPTER.match(line)
        if ch and not line.strip().startswith(("|",)):
            flush()
            chapter = _clean_heading(ch.group(1))
            continue
        if cur is not None and line.strip():
            cur["body"].append(line.strip())
    flush()
    return out


def _split_numbered(lines: list[str]) -> list[Clause]:
    out: list[Clause] = []
    chapter = ""
    cur: dict | None = None

    def flush() -> None:
        nonlocal cur
        if cur:
            _emit(out, cur["no"], cur["title"], cur["chapter"], cur["header"], cur["body"])
        cur = None

    for line in lines:
        m = _NUM_HEAD.match(line)
        if m and ":" not in m.group(2) and "：" not in m.group(2) and not m.group(2).rstrip().endswith(("다.", "다")):
            flush()
            token, title = m.group(1), _clean_heading(m.group(2))
            if "." not in token:
                chapter = title
            cur = {"no": token, "title": title, "chapter": chapter if "." in token else "",
                   "header": f"{token} {title}", "body": []}
            continue
        if cur is not None and line.strip():
            cur["body"].append(line.strip())
    flush()
    return out


def _split_paragraph(lines: list[str]) -> list[Clause]:
    """머리글도 번호도 없을 때 — 빈 줄·글머리(ㅇ)로 나눈 줄 묶음. 짧은 조각은 뒤와 합친다."""
    blocks: list[dict] = []
    cur: list[str] = []
    title = ""

    def flush() -> None:
        nonlocal cur
        if cur:
            blocks.append({"title": title, "lines": cur})
        cur = []

    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            flush()
            continue
        is_title = (len(s) <= 40 and not s.endswith(("다.", "다", ".", ":", "：", ",")) and i + 1 < len(lines)
                    and len(lines[i + 1].strip()) > len(s))
        if is_title:
            flush()
            title = s
            continue
        if s.startswith(("ㅇ", "■", "●")) and cur:
            flush()
        cur.append(s)
    flush()

    merged: list[dict] = []
    for b in blocks:
        if merged and sum(len(x) for x in merged[-1]["lines"]) < 120 and merged[-1]["title"] == b["title"]:
            merged[-1]["lines"].extend(b["lines"])
        else:
            merged.append({"title": b["title"], "lines": list(b["lines"])})
    out: list[Clause] = []
    for n, b in enumerate(merged, 1):
        _emit(out, f"문단 {n}", b["title"], "", "", b["lines"])
    return out


def _article_sequence_ok(lines: list[str]) -> bool:
    """머리글로 읽힌 줄들의 조 번호가 **조 순서로 이어지는가**. 인용이 머리글로 잘못 읽히면 번호가 뒤죽박죽이다.

    본문 조 번호는 오름차순이다. 부칙(`부칙` 장 제목 뒤)에서는 처음부터 다시 세므로 그 되돌림은 위반으로 세지 않는다.
    위반 쌍이 전체 쌍의 `SEQUENCE_MAX_BREAK_RATIO` 를 넘으면 article 모드로 보지 않는다.
    """
    nums: list[tuple[int, bool]] = []      # (조 번호의 앞 숫자, 부칙 안인가)
    in_addendum = False
    for ln in lines:
        head = _parse_article_head(ln)
        if head:
            m = re.match(r"제(\d+)조", head[0])
            if m:
                nums.append((int(m.group(1)), in_addendum))
            continue
        ch = _CHAPTER.match(ln)
        if ch and not ln.strip().startswith("|"):
            in_addendum = _is_addendum(_clean_heading(ch.group(1)))
    pairs = breaks = 0
    for (a, a_add), (b, b_add) in zip(nums, nums[1:]):
        pairs += 1
        if b < a and not (b_add and not a_add):        # 본문 → 부칙으로 넘어가며 다시 세는 것은 위반이 아니다
            breaks += 1
        elif b == a:
            breaks += 1                                 # 같은 조 번호가 이어서 또 나온다 — 인용이 반복되는 모양
    return pairs == 0 or breaks <= max(1, int(pairs * SEQUENCE_MAX_BREAK_RATIO))


def _with_coverage_warning(result: SplitResult, lines: list[str]) -> SplitResult:
    """조항으로 나뉜 글자가 전체의 절반에 못 미치면 알린다 — 잘못 나뉜 것이 조용히 통과하지 않게."""
    total = sum(len(ln.strip()) for ln in lines if ln.strip())
    covered = sum(len(c.text) for c in result.clauses)
    if total > 0 and result.clauses and covered / total < COVERAGE_WARN_BELOW:
        pct = int(covered * 100 / total)
        result.warnings.append(f"규정 글자의 {pct}%만 조항으로 나뉘었습니다. 조항 표가 규정 원문과 맞는지 확인해 주십시오.")
    return result


def split_regulation(raw: str) -> SplitResult:
    """규정 원문 글자 → 모드·조항·문장. 조항이 하나도 없으면 빈 목록(호출자가 422 로 거절한다)."""
    text = normalize_text(raw)
    lines = _reflow_wrapped_lines(_drop_repeated_lines(text.split("\n")))
    n_art = sum(1 for ln in lines if _parse_article_head(ln))
    if n_art >= ARTICLE_MODE_MIN and _article_sequence_ok(lines):
        return _with_coverage_warning(SplitResult(MODE_ARTICLE, _reseq(_split_article(lines))), lines)
    n_num = 0
    for ln in lines:
        m = _NUM_HEAD.match(ln)
        if m and ":" not in m.group(2) and "：" not in m.group(2) and not m.group(2).rstrip().endswith(("다.", "다")):
            n_num += 1
    if n_num >= NUMBERED_MODE_MIN:
        return _with_coverage_warning(SplitResult(MODE_NUMBERED, _reseq(_split_numbered(lines))), lines)
    clauses = _reseq(_split_paragraph(lines))
    return _with_coverage_warning(
        SplitResult(MODE_PARAGRAPH, clauses, [WARN_PARAGRAPH_MODE] if clauses else []), lines)


def _reseq(clauses: list[Clause]) -> list[Clause]:
    for i, c in enumerate(clauses):
        c.seq = i
    return clauses
