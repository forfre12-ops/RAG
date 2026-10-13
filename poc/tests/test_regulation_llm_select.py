"""로컬 LLM 으로 「이 문서에 직접 적용되는 항」 고르기 시험 — 가짜 LLM 으로 도는 순수 시험(DB·모델 없음).

왜 있나. 조회 점수로는 해당 여부를 못 가르므로(설계서 §3.4) 후보 조항을 로컬 LLM 이 훑어 항을 **번호로 고른다**. 이 시험은
① 답을 읽는 규칙 ② 조회 순위를 지키는가 ③ 실패·시간 초과·해석 불가를 「해당」으로 내보내지 않는가 ④ 문서 본문을 로컬이 아닌 공급자로
보내지 않는가 ⑤ 화면에 나가는 글이 규정 원문뿐인가를 잠근다.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import numpy as np
import pytest

from koipa.regulation import llm_select
from koipa.regulation.index import ClauseRec, SentenceRec

VEC = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)


def sent(seq, text, *, lead=False, group=None):
    return SentenceRec(f"s{seq}", seq, text, lead, group, VEC)


def clause(seq, no, title, sentences):
    return ClauseRec(f"c{seq}", "r1", "규정", "v1", seq, no, title, f"{no} {title}", VEC, tuple(sentences))


C40 = clause(1, "제40조", "시험·품질 문서", [
    sent(1, "① 미공개 시제품의 시험 결과는 기밀로 취급한다."),
    sent(2, "② 시험 장비의 원시 데이터와 로그는 해당 시험 결과와 같은 등급으로 취급한다."),
])
C41 = clause(2, "제41조", "설계·공정 문서", [
    sent(1, "다음 각 호의 문서는 설계 문서로 본다.", lead=True),
    sent(2, "① 도면과 회로도는 극비로 취급한다."),
    sent(3, "② 공정 조건표와 공정 레시피는 핵심 공정은 극비로 취급한다."),
])
C34 = clause(3, "제34조", "외부 제공의 승인", [
    sent(1, "① 외부 제공은 등급별 승인 절차를 거친다."),
    sent(2, "1. 극비: 외부 제공 금지.", group=7),
    sent(3, "2. 기밀: 비밀유지계약을 체결한다.", group=7),
    sent(4, "3. 대외비: 부서장의 승인을 받는다.", group=7),
])


class FakeProvider:
    """조항 번호마다 정한 답을 돌려준다 — int(고른 항 번호) · Exception(오류) · str(원문 그대로) · (초, 답)(지연)."""

    name = "ollama"
    model = "fake-model"

    def __init__(self, answers, kind="internal"):
        self.answers = answers
        self.kind = kind                    # 문서 종류 판정의 답 — "internal" | "public" | Exception | str(원문 그대로)
        self.kind_calls = 0
        self.prompts: list[str] = []
        self.schemas: list[object] = []
        self.max_tokens: list[int] = []
        self.systems: list[tuple[str, str | None]] = []       # (종류 확인이면 "kind" · 고르기면 "pick", 받은 system 문구)

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None):
        self.max_tokens.append(max_tokens)
        self.systems.append(("kind" if llm_select.KIND_QUESTION in prompt else "pick", system))
        if llm_select.KIND_QUESTION in prompt:
            self.kind_calls += 1
            if isinstance(self.kind, Exception):
                raise self.kind
            text = self.kind if self.kind not in ("internal", "public") else f'{{"kind": "{self.kind}", "reason": "x"}}'
            return SimpleNamespace(text=text, usage=SimpleNamespace(success=True, error_code=None))
        self.prompts.append(prompt)
        self.schemas.append(json_schema)
        art = re.search(r"제\d+조", prompt.split("[규정 조항]")[1]).group()
        ans = self.answers[art]
        if isinstance(ans, tuple):
            time.sleep(ans[0])
            ans = ans[1]
        if isinstance(ans, Exception):
            raise ans
        if isinstance(ans, str):
            return SimpleNamespace(text=ans, usage=SimpleNamespace(success=True, error_code=None))
        if ans is False:
            return SimpleNamespace(text="", usage=SimpleNamespace(success=False, error_code="APIConnectionError"))
        return SimpleNamespace(text=f'{{"item": {ans}, "reason": "시험"}}', usage=SimpleNamespace(success=True, error_code=None))


class NoSchemaProvider:
    """json_schema 인자를 받지 않는 공급자 — 스키마를 억지로 넘기지 않는다."""

    name = "ollama"
    model = "m"

    def __init__(self):
        self.kwargs = []

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7):
        self.kwargs.append((system, max_tokens, temperature))
        return SimpleNamespace(text='{"item": 1, "reason": "x"}', usage=SimpleNamespace(success=True, error_code=None))


@pytest.fixture()
def ex():
    pool = ThreadPoolExecutor(max_workers=4)
    yield pool
    pool.shutdown(wait=True, cancel_futures=True)


# ── 답 읽기 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,n,want", [
    ('{"item": 2, "reason": "x"}', 3, 2),
    ('{"item": 0, "reason": "해당 없음"}', 3, 0),
    ('```json\n{"item": 1, "reason": "x"}\n```', 3, 1),
    ('설명입니다. {"item": 3, "reason": "x"} 끝', 3, 3),
    ('{"item": 2, "reason": "잘린 답', 3, 2),                  # 닫히지 않은 JSON — 번호만 읽는다
    ('{"item": 3, "reas', 3, 3),                               # 번호 뒤에서 생성을 끊은 답(_VALUE_ONLY_TOKENS)
    ('{"item": 0', 3, 0),
    ('{"item": 12, "re', 12, 12),
    ('{"ite', 3, None),                                        # 번호 앞에서 끊기면 읽을 수 없다 — 실패로 센다
    ('{"item": 4, "reason": "x"}', 3, None),                   # 범위 밖
    ('{"item": -1, "reason": "x"}', 3, None),
    ('{"item": true, "reason": "x"}', 3, None),                # 불리언은 번호가 아니다
    ('{"item": "2", "reason": "x"}', 3, None),                 # 문자열도 아니다
    ('{"reason": "x"}', 3, None),
    ("해당 조항이 없습니다", 3, None),
    ("", 3, None),
])
def test_parse_choice(text, n, want):
    assert llm_select.parse_choice(text, n) == want


def test_only_local_providers_are_allowed():
    for name in ("ollama", "OLLAMA", " vllm ", "local_openai", "lm_studio"):
        assert llm_select.is_local_provider(name)
    for name in ("anthropic", "openai", "google", "gemini", "noop", "", None, "unknown"):
        assert not llm_select.is_local_provider(name)


# ── 프롬프트 ─────────────────────────────────────────────────────────────────

def test_prompt_numbers_selectable_sentences_and_skips_lead_in_lines():
    sents = llm_select.selectable(C41)
    assert [s.seq for s in sents] == [2, 3]                       # 서두 문장은 고를 수 없다
    p = llm_select.build_prompt("시험성적서 본문  입니다.\n둘째 줄", C41, sents, 1500)
    assert "제41조(설계·공정 문서)" in p
    assert "[1] ① 도면과 회로도는 극비로 취급한다." in p and "[2] ② 공정 조건표와 공정 레시피는" in p
    assert "다음 각 호의 문서는" not in p
    assert "시험성적서 본문 입니다. 둘째 줄" in p                  # 공백 정리
    assert "등급을 판정" in llm_select.SYSTEM and "등급" not in llm_select.QUESTION.split("JSON")[0].replace("등급별", "")


def test_prompt_cuts_the_document_at_the_configured_length():
    p = llm_select.build_prompt("가" * 5000, C40, llm_select.selectable(C40), 300)
    assert "가" * 300 in p and "가" * 301 not in p


# ── 고르기 ───────────────────────────────────────────────────────────────────

def test_the_first_accepted_candidate_in_rank_order_wins_even_when_a_later_one_answers_sooner(ex):
    prov = FakeProvider({"제40조": (0.3, 1), "제41조": 2, "제34조": 0})
    sel = llm_select.select_applicable(prov, "문서", [C40, C41, C34], executor=ex)
    assert [i.article_no for i in sel.items] == ["제40조"]        # 제41조가 먼저 끝나도 순위가 앞선 제40조가 이긴다
    assert sel.items[0].sentences == ("① 미공개 시제품의 시험 결과는 기밀로 취급한다.",)
    assert sel.reason is None and sel.calls == 4 and sel.failures == 0          # 후보 3 + 문서 종류 확인 1


def test_the_chosen_sentence_is_shown_verbatim_and_lead_in_lines_are_never_chosen(ex):
    sel = llm_select.select_applicable(FakeProvider({"제41조": 2}), "문서", [C41], executor=ex)
    assert sel.items[0].sentences == ("② 공정 조건표와 공정 레시피는 핵심 공정은 극비로 취급한다.",)
    assert sel.items[0].is_grade_list is False


def test_a_grade_list_line_shows_the_whole_list(ex):
    sel = llm_select.select_applicable(FakeProvider({"제34조": 3}), "문서", [C34], executor=ex)
    it = sel.items[0]
    assert it.is_grade_list and len(it.sentences) == 3 and it.sentences[0].startswith("1. 극비")


def test_when_every_candidate_answers_none_nothing_is_shown_and_it_is_not_an_error(ex):
    sel = llm_select.select_applicable(FakeProvider({"제40조": 0, "제41조": 0}), "문서", [C40, C41], executor=ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_NOT_APPLICABLE and sel.failures == 0


def test_no_candidates_is_simply_not_applicable(ex):
    sel = llm_select.select_applicable(FakeProvider({}), "문서", [], executor=ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_NOT_APPLICABLE and sel.calls == 0


@pytest.mark.parametrize("bad", [
    RuntimeError("연결 끊김"),                 # 호출이 예외를 던진다
    False,                                    # 공급자가 success=False 응답을 돌려준다(어댑터가 예외를 삼킨 경우)
    "해당 없습니다",                            # 읽을 수 없는 답
    '{"item": 9, "reason": "x"}',             # 범위 밖 번호
])
def test_a_failed_judgement_is_never_shown_as_applicable(ex, bad):
    """판정이 서지 않으면 안 보인다 — 나머지가 모두 「없음」이어도 llm_unavailable 이지 not_applicable 이 아니다."""
    sel = llm_select.select_applicable(FakeProvider({"제40조": bad, "제41조": 0}), "문서", [C40, C41], executor=ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and sel.failures == 1


def test_a_failure_on_one_candidate_does_not_hide_another_that_the_llm_confirmed(ex):
    sel = llm_select.select_applicable(FakeProvider({"제40조": RuntimeError("x"), "제41조": 2}), "문서", [C40, C41], executor=ex)
    assert [i.article_no for i in sel.items] == ["제41조"] and sel.failures == 1 and sel.reason is None


def test_a_slow_llm_times_out_and_nothing_is_shown(ex):
    t0 = time.monotonic()
    sel = llm_select.select_applicable(FakeProvider({"제40조": (1.5, 1)}), "문서", [C40], timeout_s=0.2, executor=ex)
    assert time.monotonic() - t0 < 1.0
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE


def test_limit_collects_that_many_confirmed_items_in_rank_order(ex):
    sel = llm_select.select_applicable(FakeProvider({"제40조": 2, "제41조": 0, "제34조": 1}), "문서", [C40, C41, C34], limit=2, executor=ex)
    assert [i.article_no for i in sel.items] == ["제40조", "제34조"]
    one = llm_select.select_applicable(FakeProvider({"제40조": 2, "제41조": 0, "제34조": 1}), "문서", [C40, C41, C34], limit=1, executor=ex)
    assert [i.article_no for i in one.items] == ["제40조"]


def test_the_schema_is_passed_only_to_providers_that_accept_it(ex):
    p = FakeProvider({"제40조": 1})
    llm_select.select_applicable(p, "문서", [C40], executor=ex)
    assert p.schemas == [llm_select.JSON_SCHEMA]
    q = NoSchemaProvider()
    sel = llm_select.select_applicable(q, "문서", [C40], executor=ex, skip_public=False)
    assert sel.items and q.kwargs[0][0] == llm_select.SYSTEM and q.kwargs[0][2] == 0.0      # 온도 0 — 같은 입력은 같은 답


def test_generation_stops_right_after_the_value_only_when_the_schema_fixes_the_key_order(ex):
    """스키마 출력은 값(item·kind)이 첫 키라 값 뒤의 reason 문장은 만들 필요가 없다 — 호출 시간의 대부분이 그 문장이었다."""
    p = FakeProvider({"제40조": 1, "제41조": 0})
    llm_select.select_applicable(p, "문서", [C40, C41], executor=ex)
    assert p.max_tokens and set(p.max_tokens) == {llm_select._VALUE_ONLY_TOKENS}         # 후보 2 + 문서 종류 확인 1 모두
    assert 8 <= llm_select._VALUE_ONLY_TOKENS <= 32                                       # `{"item": 12,` 가 들어가고 문장까지 가지는 않는다
    q = NoSchemaProvider()
    llm_select.select_applicable(q, "문서", [C40], executor=ex, skip_public=False)
    assert q.kwargs[0][1] == 200                                                          # 키 순서를 보장 못 하는 서버는 종전대로


# ── 이미 공개된 외부 자료(판결문·법령·보도·공시)에는 사내 규정을 붙이지 않는다 ───────────────────

@pytest.mark.parametrize("text,want", [
    ('{"kind": "public", "reason": "판결문"}', "public"),
    ('{"kind": "internal", "reason": "회의록"}', "internal"),
    ('```json\n{"kind": "public", "reason": "x"}\n```', "public"),
    ('{"kind": "public", "reason": "잘린 답', "public"),
    ('{"kind": "internal", "rea', "internal"),                 # 값 뒤에서 생성을 끊은 답
    ('{"kind": "confidential", "reason": "x"}', None),
    ("사내 문서입니다", None),
    ("", None),
])
def test_parse_kind(text, want):
    assert llm_select.parse_kind(text) == want


def test_a_public_document_shows_nothing_even_when_a_clause_is_chosen(ex):
    prov = FakeProvider({"제40조": 1, "제41조": 2}, kind="public")
    sel = llm_select.select_applicable(prov, "판결문 본문", [C40, C41], executor=ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_PUBLIC_DOCUMENT and prov.kind_calls == 1


def test_when_the_document_kind_cannot_be_confirmed_nothing_is_shown(ex):
    for bad in (RuntimeError("x"), "모르겠습니다"):
        sel = llm_select.select_applicable(FakeProvider({"제40조": 1}, kind=bad), "문서", [C40], executor=ex)
        assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and sel.failures == 1


def test_the_kind_check_can_be_turned_off_and_then_costs_no_call(ex):
    prov = FakeProvider({"제40조": 1}, kind="public")
    sel = llm_select.select_applicable(prov, "판결문 본문", [C40], executor=ex, skip_public=False)
    assert [i.article_no for i in sel.items] == ["제40조"] and prov.kind_calls == 0 and sel.calls == 1


def test_the_kind_prompt_asks_only_internal_or_public_and_never_about_grades():
    p = llm_select.build_kind_prompt("가" * 3000, 300)
    assert "가" * 300 in p and "가" * 301 not in p
    assert "internal" in p and "public" in p and "등급" not in p


def test_per_candidate_keeps_its_own_kind_system_prompt(ex):
    """후보마다 묻기는 종전 KIND_SYSTEM 으로 종류를 묻는다 — 고르기와 같은 문구로 바꾸면 공개 판결문 1건에 규정이 새로 떴다(설계서 §3.6)."""
    prov = FakeProvider({"제40조": 0})
    llm_select.select_applicable(prov, "문서", [C40], executor=ex)
    assert sorted(prov.systems) == [("kind", llm_select.KIND_SYSTEM), ("pick", llm_select.SYSTEM)]
    assert llm_select.KIND_SYSTEM != llm_select.SYSTEM


def test_the_answer_text_never_reaches_the_screen(ex):
    """화면 항목은 규정 원문 문장뿐이다 — LLM 이 낸 「이유」 글자는 어디에도 실리지 않는다.

    (종전 단정은 dataclass repr 가 작은따옴표로 찍히는 탓에 깨질 수 없었다 — 독립 리뷰 R3. 이제 눈에 띄는 표지 글자를 이유에 실어 **모든 필드**에서 찾는다.)
    """
    import dataclasses

    marker = "LLM-이유-표지-7f3a91"
    provider = FakeProvider({"제40조": f'{{"item": 1, "reason": "{marker}"}}'}, kind=f'{{"kind": "internal", "reason": "{marker}"}}')
    sel = llm_select.select_applicable(provider, "문서", [C40], executor=ex)
    assert len(sel.items) == 1 and sel.calls == 2
    item = sel.items[0]
    assert marker not in repr(dataclasses.asdict(item)) and marker not in repr(sel)
    # 화면에 나가는 모든 글자는 규정 데이터(조항·문장·규정명)에서만 온다
    assert (item.rgltn_nm, item.ver_lbl_nm, item.article_no, item.title) == (C40.rgltn_nm, C40.ver_lbl_nm, C40.article_no, C40.title)
    assert item.sentences == (C40.sentences[0].text,)


# ── 한 번에 묻기(single) — 종류를 먼저 확인하고, 문서를 한 번만 보이고 후보의 항 전체에서 기호로 고른다 ───────────

class FakeSingleProvider:
    """문서 종류 질문(프롬프트에 KIND_QUESTION 이 있다)과 고르기 질문에 정한 답을 돌려준다.
    `pick` = 고르기 답(str 원문 · Exception · False[success=False 응답]) · `kind` = 종류 답("internal"|"public"|Exception|str 원문) · `delay` = 고르기 답을 늦춘다."""

    name = "ollama"
    model = "fake-single"

    def __init__(self, pick, kind="internal", delay=0.0):
        self.pick = pick
        self.kind = kind
        self.delay = delay
        self.kind_prompts: list[str] = []
        self.pick_prompts: list[str] = []
        self.pick_schemas: list[object] = []
        self.pick_max_tokens: list[int] = []
        self.systems: list[tuple[str, str | None]] = []

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None):
        ok = SimpleNamespace(success=True, error_code=None)
        self.systems.append(("kind" if llm_select.KIND_QUESTION in prompt else "pick", system))
        if llm_select.KIND_QUESTION in prompt:
            self.kind_prompts.append(prompt)
            if isinstance(self.kind, Exception):
                raise self.kind
            text = f'{{"kind": "{self.kind}", "reason": "x"}}' if self.kind in ("internal", "public") else self.kind
            return SimpleNamespace(text=text, usage=ok)
        self.pick_prompts.append(prompt)
        self.pick_schemas.append(json_schema)
        self.pick_max_tokens.append(max_tokens)
        if self.delay:
            time.sleep(self.delay)
        if isinstance(self.pick, Exception):
            raise self.pick
        if self.pick is False:
            return SimpleNamespace(text="", usage=SimpleNamespace(success=False, error_code="APIConnectionError"))
        return SimpleNamespace(text=self.pick, usage=ok)


class FakeSingleNoSchema(FakeSingleProvider):
    """json_schema 인자를 받지 않는 공급자 — 스키마를 억지로 넘기지 않는다."""

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7):
        return super().generate(prompt, system=system, max_tokens=max_tokens, temperature=temperature)


def ask_single(provider, cands, ex, **kw):
    return llm_select.select_applicable(provider, "문서 본문", cands, executor=ex, mode=llm_select.MODE_SINGLE, **kw)


@pytest.mark.parametrize("text,want", [
    ('{"items": ["B2", "A1"]}', ["B2", "A1"]),
    ('{"items": []}', []),
    ('{"items": ["A1", "A1"]}', ["A1"]),                                  # 중복은 한 번
    ('```json\n{"items": ["B1"]}\n```', ["B1"]),
    ('설명입니다. {"items": ["A2"]} 끝', ["A2"]),
    ('{"items": ["A2", "B1', ["A2", "B1"]),                               # 닫히지 않은 JSON — 기호만 읽는다
    ('{"items": ["C1"]}', None),                                          # 보이지 않은 기호
    ('{"items": [1]}', None),                                             # 기호가 아니라 숫자
    ('{"items": "A1"}', None),                                            # 목록이 아니다
    ('{"kind": "internal"}', None),                                       # 목록이 없다
    ("해당 조항이 없습니다", None),
    ("", None),
])
def test_parse_single(text, want):
    assert llm_select.parse_single(text, {"A1", "A2", "B1", "B2"}) == want


def test_single_schema_pins_the_labels_and_the_count():
    s = llm_select.single_schema(["A1", "B1"], 2)
    it = s["properties"]["items"]
    assert it["items"]["enum"] == ["A1", "B1"] and it["maxItems"] == 2 and it["uniqueItems"] is True and s["required"] == ["items"]
    assert llm_select.single_schema([], 0)["properties"]["items"]["maxItems"] == 1


def test_single_prompt_labels_items_with_letters_so_they_cannot_be_mixed_up_with_article_numbers():
    blocks = [(C40, llm_select.selectable(C40)), (C41, llm_select.selectable(C41))]
    prompt, labels = llm_select.build_single_prompt("시험성적서  본문\n둘째 줄", blocks, 1500, 1)
    assert list(labels) == ["A1", "A2", "B1", "B2"] and labels["B1"][1].text.startswith("① 도면") and labels["A2"][0] is C40
    assert "A. 제40조(시험·품질 문서)" in prompt and "  A1 ① 미공개" in prompt and "B. 제41조(설계·공정 문서)" in prompt and "  B2 ② 공정 조건표" in prompt
    assert "다음 각 호의 문서는" not in prompt                            # 서두 문장은 고를 수 없다
    assert "시험성적서 본문 둘째 줄" in prompt and prompt.count("[문서 앞부분]") == 1           # 문서는 한 번만 보인다
    assert "최대 1개" in prompt and "예: A1" in prompt and re.search(r"\[\d+\]", prompt) is None   # 숫자만 붙은 번호는 없다
    cut, _ = llm_select.build_single_prompt("가" * 5000, blocks, 300, 1)
    assert "가" * 300 in cut and "가" * 301 not in cut
    assert "최대 3개" in llm_select.build_single_prompt("본문", blocks, 1500, 3)[0]


def test_single_checks_the_kind_first_then_picks_from_any_candidate_with_one_call(ex):
    p = FakeSingleProvider('{"items": ["B2"]}')                            # 기호: 제40조 A1·A2 · 제41조 B1·B2 · 제34조 C1~C4
    sel = ask_single(p, [C40, C41, C34], ex)
    assert [i.article_no for i in sel.items] == ["제41조"] and sel.items[0].sentences == ("② 공정 조건표와 공정 레시피는 핵심 공정은 극비로 취급한다.",)
    assert sel.calls == 2 and len(p.kind_prompts) == 1 and len(p.pick_prompts) == 1 and sel.reason is None and sel.failures == 0
    assert p.pick_schemas[0]["properties"]["items"]["items"]["enum"] == ["A1", "A2", "B1", "B2", "C1", "C2", "C3", "C4"]
    assert p.pick_max_tokens == [llm_select._SINGLE_TOKENS]


def test_single_asks_the_kind_with_the_same_system_prompt_as_the_pick(ex):
    """같은 system 문구여야 두 호출이 같은 문서 앞부분으로 시작해 서버가 읽기를 재사용한다 — CPU 에서 문서당 106~112초 → 74~75초(설계서 §3.6)."""
    p = FakeSingleProvider('{"items": ["A1"]}')
    ask_single(p, [C40], ex)
    assert p.systems == [("kind", llm_select.SYSTEM), ("pick", llm_select.SYSTEM)]


def test_kind_and_single_pick_prompts_start_with_the_same_document_block():
    """서버가 앞부분 읽기를 재사용하려면 system 문구뿐 아니라 프롬프트도 같은 문서 블록으로 시작해야 한다 — 질문을 문서 앞으로 옮기면 재사용이 끊긴다."""
    kind = llm_select.build_kind_prompt("시험성적서  본문", 1500)
    pick, _ = llm_select.build_single_prompt("시험성적서  본문", [(C40, llm_select.selectable(C40))], 1500, 1)
    head = "[문서 앞부분]\n시험성적서 본문\n\n"
    assert kind.startswith(head) and pick.startswith(head)


def test_single_grade_list_line_shows_the_whole_list_and_duplicates_collapse(ex):
    sel = ask_single(FakeSingleProvider('{"items": ["C2", "C3", "A1"]}'), [C40, C41, C34], ex, limit=3)
    assert [i.article_no for i in sel.items] == ["제34조", "제40조"]           # C2·C3 은 같은 등급별 목록 — 한 항목으로 합쳐진다, 직접적인 순서를 지킨다
    assert sel.items[0].is_grade_list and len(sel.items[0].sentences) == 3
    one = ask_single(FakeSingleProvider('{"items": ["C2", "A1"]}'), [C40, C41, C34], ex, limit=1)
    assert [i.article_no for i in one.items] == ["제34조"]                    # limit 를 넘는 기호는 버린다


def test_single_empty_list_is_not_applicable_and_not_an_error(ex):
    sel = ask_single(FakeSingleProvider('{"items": []}'), [C40, C41], ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_NOT_APPLICABLE and sel.failures == 0 and sel.calls == 2


def test_single_public_document_is_not_asked_which_clause_applies(ex):
    p = FakeSingleProvider('{"items": ["A1"]}', kind="public")
    sel = ask_single(p, [C40], ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_PUBLIC_DOCUMENT and sel.calls == 1 and p.pick_prompts == []


def test_single_without_the_public_check_only_picks(ex):
    p = FakeSingleProvider('{"items": ["A2"]}', kind="public")
    sel = ask_single(p, [C40], ex, skip_public=False)
    assert [i.article_no for i in sel.items] == ["제40조"] and sel.calls == 1 and p.kind_prompts == []


@pytest.mark.parametrize("bad", [
    RuntimeError("연결 끊김"),
    False,                                          # success=False 응답
    "해당 없습니다",
    '{"items": ["Z9"]}',                            # 보이지 않은 기호
    '{"items": [1]}',                               # 기호가 아니라 숫자
])
def test_single_failed_pick_is_never_shown_as_applicable(ex, bad):
    sel = ask_single(FakeSingleProvider(bad), [C40, C41], ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and sel.failures == 1 and sel.calls == 2


@pytest.mark.parametrize("bad_kind", [RuntimeError("x"), "모르겠습니다"])
def test_single_unknown_document_kind_shows_nothing_and_does_not_pick(ex, bad_kind):
    p = FakeSingleProvider('{"items": ["A1"]}', kind=bad_kind)
    sel = ask_single(p, [C40], ex)
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and sel.failures == 1 and p.pick_prompts == []


def test_single_slow_llm_times_out_and_nothing_is_shown(ex):
    t0 = time.monotonic()
    sel = ask_single(FakeSingleProvider('{"items": ["A1"]}', delay=1.5), [C40], ex, timeout_s=0.2)
    assert time.monotonic() - t0 < 1.0
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE


def test_single_no_candidates_or_no_selectable_items_makes_no_call(ex):
    only_lead = clause(9, "제99조", "서두만", [sent(1, "다음 각 호와 같다.", lead=True)])
    for cands in ([], [only_lead]):
        p = FakeSingleProvider('{"items": ["A1"]}')
        sel = ask_single(p, cands, ex)
        assert sel.items == [] and sel.reason == llm_select.REASON_NOT_APPLICABLE and sel.calls == 0 and p.kind_prompts == [] and p.pick_prompts == []


def test_single_prompt_is_bounded_in_items_and_clauses(ex):
    big = clause(5, "제50조", "긴 조항", [sent(i, f"항 {i} 본문") for i in range(1, 121)])
    p = FakeSingleProvider('{"items": ["A80"]}')
    sel = ask_single(p, [big, C40], ex)
    prompt = p.pick_prompts[0]
    assert f"A{llm_select.MAX_PROMPT_ITEMS} 항 {llm_select.MAX_PROMPT_ITEMS} 본문" in prompt and f"A{llm_select.MAX_PROMPT_ITEMS + 1} " not in prompt
    assert "제40조" not in prompt and [i.article_no for i in sel.items] == ["제50조"]    # 순위가 앞선 후보가 상한을 다 쓰면 뒤 후보는 안 들어간다
    assert ask_single(FakeSingleProvider('{"items": ["A81"]}'), [big], ex).reason == llm_select.REASON_LLM_UNAVAILABLE
    many = [clause(i, f"제{i}조", "t", [sent(1, f"항 {i}")]) for i in range(1, 31)]
    q = FakeSingleProvider('{"items": ["Z1"]}')
    got = ask_single(q, many, ex)
    assert "Z. 제26조" in q.pick_prompts[0] and "제27조" not in q.pick_prompts[0] and [i.article_no for i in got.items] == ["제26조"]


def test_single_passes_no_schema_and_the_long_limit_to_providers_that_do_not_accept_one(ex):
    q = FakeSingleNoSchema('{"items": ["A1"]}')
    sel = ask_single(q, [C40], ex)
    assert sel.items and q.pick_schemas == [None] and q.pick_max_tokens == [200]


# ── 독립 리뷰(2026-09-26)가 짚은 것 — 서버 주소 · 마감 뒤 대기열 · 추론 끄기 · limit · 잘린 답 · 실패 원인 ────────────────────

@pytest.mark.parametrize("url,want", [
    ("http://localhost:11434/v1", True),
    ("http://127.0.0.1:8001/v1", True),
    ("http://[::1]:8001/v1", True),
    ("http://10.20.30.40:8001/v1", True),
    ("http://172.16.5.4/v1", True),
    ("http://192.168.0.9:8000/v1", True),
    ("http://169.254.10.1/v1", True),                       # 링크 로컬
    ("http://100.64.1.2:8001/v1", True),                    # 사업자 공유 주소 공간(사내 오버레이·VPN)
    ("http://[fd00::5]:8001/v1", True),
    ("http://[::ffff:10.0.0.5]/v1", True),                  # IPv4 매핑 사설
    ("https://8.8.8.8/v1", False),
    ("http://93.184.216.34:8001/v1", False),
    ("http://[2001:4860:4860::8888]/v1", False),
    ("http://[::ffff:8.8.8.8]/v1", False),                  # IPv4 매핑 공인이 사설로 잘못 읽히면 안 된다
    ("", False),
    (None, False),
    ("not a url", False),
    ("http:///v1", False),
])
def test_endpoint_is_local_by_address(url, want):
    assert llm_select.endpoint_is_local(url) is want


def _fake_getaddrinfo(mapping, seen=None):
    import socket

    def gai(host, port, *a, **k):
        if seen is not None:
            seen.append(host)
        if host not in mapping:
            raise socket.gaierror("no such host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (addr, 0)) for addr in mapping[host]]
    return gai


def test_a_hostname_is_local_only_when_every_resolved_address_is_private(monkeypatch):
    monkeypatch.setattr(llm_select, "_host_checks", {})
    monkeypatch.setattr(llm_select.socket, "getaddrinfo", _fake_getaddrinfo(
        {"vllm": ["172.20.0.7"], "gpu.corp.example": ["10.1.1.2"], "mixed.corp.example": ["10.1.1.1", "8.8.8.8"], "public.example": ["93.184.216.34"]}))
    assert llm_select.endpoint_is_local("http://vllm:8001/v1") is True                        # 도커 서비스 이름
    assert llm_select.endpoint_is_local("http://gpu.corp.example:8001/v1") is True            # 사내 DNS 이름
    assert llm_select.endpoint_is_local("http://mixed.corp.example/v1") is False              # 하나라도 공인이면 막는다
    assert llm_select.endpoint_is_local("http://public.example/v1") is False
    assert llm_select.endpoint_is_local("http://nowhere.invalid/v1") is False                 # 풀리지 않으면 막는다


def test_a_resolved_hostname_is_remembered_so_requests_do_not_resolve_it_again(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(llm_select, "_host_checks", {})
    monkeypatch.setattr(llm_select.socket, "getaddrinfo", _fake_getaddrinfo({"vllm": ["172.20.0.7"]}, seen))
    for _ in range(5):
        assert llm_select.endpoint_is_local("http://vllm:8001/v1") is True
    assert seen == ["vllm"]


def test_per_candidate_drops_calls_that_have_not_started_when_the_deadline_passes():
    """마감을 넘긴 뒤에도 실행기 대기열의 호출이 돌면 뒤 요청을 막는다 — 독립 리뷰가 재현한 것: 요청 5건이 헛호출 15건을 남겼다."""
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        prov = FakeProvider({"제40조": (0.4, 0), "제41조": (0.4, 0), "제34조": (0.4, 0)})
        t0 = time.monotonic()
        sel = llm_select.select_applicable(prov, "문서", [C40, C41, C34], executor=pool, timeout_s=0.15, skip_public=False)
        assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and time.monotonic() - t0 < 0.35
        time.sleep(1.4)                                     # 취소하지 않았다면 대기열의 두 호출이 이 사이에 돈다
        assert len(prov.prompts) == 1                       # 처음 시작한 한 건뿐
    finally:
        pool.shutdown(wait=True, cancel_futures=True)


class ReasoningAwareProvider(FakeProvider):
    """추론 끄기(no_reasoning)를 받는 어댑터처럼 서명에 인자가 있는 가짜."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.no_reasoning_flags: list[bool] = []

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None, no_reasoning=False):
        self.no_reasoning_flags.append(no_reasoning)
        return super().generate(prompt, system=system, max_tokens=max_tokens, temperature=temperature, json_schema=json_schema)


class ReasoningAwareSingle(FakeSingleProvider):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.no_reasoning_flags: list[bool] = []

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None, no_reasoning=False):
        self.no_reasoning_flags.append(no_reasoning)
        return super().generate(prompt, system=system, max_tokens=max_tokens, temperature=temperature, json_schema=json_schema)


def test_the_reasoning_off_request_is_made_only_to_providers_that_accept_it(ex):
    """Ollama 의 Qwen3 가 생각에 토큰을 다 쓰지 않게 요청하는 것은 이 기능뿐이다 — 인자를 받지 않는 공급자(다른 어댑터·가짜)에는 넘기지 않는다."""
    p = ReasoningAwareProvider({"제40조": 1})
    llm_select.select_applicable(p, "문서", [C40], executor=ex)
    assert p.no_reasoning_flags and all(p.no_reasoning_flags)                                   # 종류 확인 호출도 포함
    s = ReasoningAwareSingle('{"items": ["A1"]}')
    ask_single(s, [C40], ex)
    assert len(s.no_reasoning_flags) == 2 and all(s.no_reasoning_flags)
    llm_select.select_applicable(FakeProvider({"제40조": 1}), "문서", [C40], executor=ex)       # 인자가 없는 가짜 — 넘기면 TypeError 라 이 줄이 통과해야 한다


def test_single_folds_a_grade_list_before_counting_the_limit(ex):
    """limit 를 넘는 기호를 자르기 전에 같은 등급별 목록의 줄(C2·C3)을 한 항목으로 합친다 — 안 그러면 limit=2 에서 다른 조항이 밀려난다(독립 리뷰 R3)."""
    sel = ask_single(FakeSingleProvider('{"items": ["C2", "C3", "A1"]}'), [C40, C41, C34], ex, limit=2)
    assert [i.article_no for i in sel.items] == ["제34조", "제40조"]


def test_parse_single_does_not_read_a_label_that_may_have_been_cut_off():
    valid = {"A1", "A12", "B1"}
    assert llm_select.parse_single('{"items": ["A1', valid) is None              # A12 의 앞부분일 수 있다
    assert llm_select.parse_single('{"items": ["A1"', valid) == ["A1"]            # 닫는 따옴표까지 왔다
    assert llm_select.parse_single('{"items": ["B1', valid) == ["B1"]             # B10 같은 더 긴 기호가 없다
    assert llm_select.parse_single('{"items": [', valid) is None                  # 빈 목록(해당 없음)이 아니라 잘린 답
    assert llm_select.parse_single('{"items": []', valid) == []                   # 닫힌 빈 목록은 해당 없음
    assert llm_select.parse_single('{"items": ["A1", "B1"]}', valid) == ["A1", "B1"]


def test_the_failure_reason_keeps_the_error_code_but_never_free_text(caplog):
    import logging

    assert llm_select._why(RuntimeError("LLM 호출 실패: APIConnectionError")) == "LLM 호출 실패: APIConnectionError"
    assert llm_select._why(RuntimeError("문서 본문이 섞였을 수 있는 임의의 메시지")) == "RuntimeError"
    assert llm_select._why(TimeoutError("x")) == "TimeoutError"
    with caplog.at_level(logging.WARNING, logger="koipa.regulation.llm_select"):
        pool = ThreadPoolExecutor(max_workers=2)
        try:
            llm_select.select_applicable(FakeProvider({"제40조": False}), "문서", [C40], executor=pool, skip_public=False)
        finally:
            pool.shutdown(wait=True)
    assert any("APIConnectionError" in r.getMessage() for r in caplog.records)         # 원인을 알 수 있어야 운영자가 서버 문제를 찾는다


def test_the_single_prompt_never_asks_about_the_grade():
    """「등급을 묻지 않는다」 잠금이 후보마다 묻기·종류 확인에만 있었다 — 한 번에 고르기 프롬프트도 같다(독립 리뷰 R3: 등급을 묻도록 바꿔도 시험이 통과했다)."""
    blocks = [(C40, llm_select.selectable(C40)), (C41, llm_select.selectable(C41))]
    prompt, _ = llm_select.build_single_prompt("시험성적서 본문", blocks, 1500, 2)
    question = prompt[prompt.index("질문:"):]
    assert "등급" not in question and "grade" not in question.lower()


def test_the_single_prompt_item_list_stays_inside_the_character_budget():
    """실제 규정은 조항이 길다(최대 1,200자 × 후보 5개) — 항 목록이 Ollama 기본 컨텍스트(4,096토큰)를 넘겨 서버가 문서를 잘라 내지 않게 글자 예산을 둔다(독립 리뷰 R2)."""
    big = [clause(i, f"제{i}조", "긴 조항", [sent(n, "가" * 110) for n in range(1, 11)]) for i in range(1, 6)]      # 조항 5개 × 항 10개 × 110자
    blocks = llm_select._prompt_blocks(big, llm_select._item_chars_budget(1500))
    prompt, _ = llm_select.build_single_prompt("문서" * 700, blocks, 1500, 1)
    assert 0 < len(blocks) < 5 and len(prompt) < llm_select.OLLAMA_PROMPT_LIMIT_CHARS
    assert blocks[0][0] is big[0] and len(blocks[0][1]) == 10               # 순위가 앞선 후보는 온전히 남는다
    assert len(llm_select._prompt_blocks(big)) == 5                         # 예산을 안 주면(종전) 5개가 다 실린다
    huge = [clause(1, "제1조", "t", [sent(1, "가" * 5000)])]
    assert llm_select._prompt_blocks(huge, 400)[0][1][0].text == "가" * 5000  # 첫 조항의 첫 항은 예산을 넘어도 싣는다(아무것도 못 고르는 것보다 낫다)
    assert llm_select._item_chars_budget(1500) == 2600 and llm_select._item_chars_budget(6000) == 400


def test_the_measured_demo_candidates_fit_the_budget_so_the_measured_numbers_stand():
    """시연 규정 71건의 후보 블록은 최대 1,665자였다 — 예산(문서 1,500자일 때 2,600자) 안이라 잰 수치(정밀도·뜬 문서 수)에 영향이 없다."""
    demo = [clause(i, f"제{i}조", "시연 조항", [sent(n, "가" * 60) for n in range(1, 6)]) for i in range(1, 6)]       # 5개 × 5항 × 60자 ≈ 1,700자
    assert len(llm_select._prompt_blocks(demo, llm_select._item_chars_budget(1500))) == 5


def test_a_prompt_longer_than_the_ollama_default_context_is_not_sent(ex):
    p = FakeProvider({"제40조": 1})                                          # 이름 = ollama
    sel = llm_select.select_applicable(p, "가" * 7000, [C40], executor=ex, doc_chars=6000, skip_public=False)
    assert sel.items == [] and sel.reason == llm_select.REASON_LLM_UNAVAILABLE and p.prompts == []         # 문서를 잘라 넣은 채 고르게 두지 않는다
    v = FakeProvider({"제40조": 1})
    v.name = "vllm"
    sel2 = llm_select.select_applicable(v, "가" * 7000, [C40], executor=ex, doc_chars=6000, skip_public=False)
    assert [i.article_no for i in sel2.items] == ["제40조"]                   # 컨텍스트가 큰 서버(vLLM 등)는 막지 않는다


def test_single_mode_applies_the_budget_so_a_long_regulation_still_gets_an_answer(ex):
    """예산이 배선돼 있는가 — 안 그러면 긴 조항 5개가 한 프롬프트에 다 실려 Ollama 컨텍스트를 넘고 부르지도 못한다(안 보임)."""
    big = [clause(i, f"제{i}조", "긴 조항", [sent(n, "가" * 110) for n in range(1, 11)]) for i in range(1, 6)]
    p = FakeSingleProvider('{"items": ["A1"]}')
    sel = llm_select.select_applicable(p, "가" * 1500, big, executor=ex, mode=llm_select.MODE_SINGLE, doc_chars=1500, skip_public=False)
    assert [i.article_no for i in sel.items] == ["제1조"] and sel.reason is None
    assert len(p.pick_prompts) == 1 and len(p.pick_prompts[0]) < llm_select.OLLAMA_PROMPT_LIMIT_CHARS
