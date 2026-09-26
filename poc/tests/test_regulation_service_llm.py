"""문서별 관련 규정 조회의 「로컬 LLM 으로 해당 항 고르기」 옵션 — 서비스 계층 시험(DB 없음, 가짜 저장소·가짜 LLM).

잠그는 것: ① 꺼져 있으면 LLM 을 부르지도 만들지도 않는다(종전 결정형 조회 그대로) ② 로컬이 아닌 공급자면 문서를 보내지 않는다
③ 실패는 기억하지 않고 「해당 없음」은 기억한다 ④ 규정 판이 바뀌면 캐시가 갈린다 ⑤ 미리보기는 캐시를 안 쓴다.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

import numpy as np
import pytest

from koipa.regulation import llm_select
from koipa.regulation.index import ClauseRec, RegulationIndex, SentenceRec
from koipa.services.regulation_evidence_service import RegulationEvidenceService

VEC = np.asarray([1.0, 0.0, 0.0], dtype=np.float32)


def sent(seq, text, *, lead=False):
    return SentenceRec(f"s{seq}", seq, text, lead, None, VEC)


C40 = ClauseRec("c1", "r1", "규정", "v1", 1, "제40조", "시험·품질 문서", "제40조 시험·품질 문서 시험 결과 시제품", VEC,
                (sent(1, "① 미공개 시제품의 시험 결과는 기밀로 취급한다."), sent(2, "② 시험 로그는 시험 결과와 같은 등급이다.")))
C41 = ClauseRec("c2", "r1", "규정", "v1", 2, "제41조", "설계·공정 문서", "제41조 설계·공정 문서 도면 공정 조건표", VEC,
                (sent(1, "① 도면은 극비로 취급한다."), sent(2, "② 공정 조건표는 기밀로 취급한다.")))


class FakeLLM:
    name = "ollama"
    model = "fake"

    def __init__(self, answers, kind="internal"):
        self.answers = answers          # 조항 번호 → 항 번호(int) | Exception
        self.kind = kind                # 문서 종류 확인의 답 — "internal" | "public"
        self.calls = 0                  # 후보 조항에 대한 호출 수(문서 종류 확인은 세지 않는다)
        self.kind_calls = 0

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None):
        if llm_select.KIND_QUESTION in prompt:
            self.kind_calls += 1
            return SimpleNamespace(text=f'{{"kind": "{self.kind}", "reason": "x"}}', usage=SimpleNamespace(success=True, error_code=None))
        self.calls += 1
        art = re.search(r"제\d+조", prompt.split("[규정 조항]")[1]).group()
        ans = self.answers[art]
        if isinstance(ans, Exception):
            raise ans
        return SimpleNamespace(text=f'{{"item": {ans}, "reason": "x"}}', usage=SimpleNamespace(success=True, error_code=None))


def _settings(**over):
    base = dict(regulation_evidence_max_items=1, regulation_min_similarity=0.0, regulation_llm_select_enabled=True,
                llm_provider="ollama", regulation_llm_candidates=3, regulation_llm_doc_chars=1500, regulation_llm_timeout_s=5.0,
                regulation_llm_max_concurrency=2, regulation_llm_cache_ttl_s=3600,
                regulation_llm_skip_public=False)          # 문서 종류 확인은 아래 전용 시험에서 켠다 — 나머지는 후보 호출 수만 센다
    base.update(over)
    return SimpleNamespace(**base)


class Store:
    def get(self, doc_id):
        return SimpleNamespace(model="m", embedding=VEC) if doc_id != "missing" else None


def _service(llm=None, settings=None, doc_text="시험성적서 본문 시제품 시험 결과", factory_calls=None):
    def factory():
        if factory_calls is not None:
            factory_calls.append(1)
        if isinstance(llm, Exception):
            raise llm
        return llm

    svc = RegulationEvidenceService(vector_store=Store(), settings_obj=settings or _settings(),
                                    doc_text_provider=lambda d: doc_text, document_exists=lambda d: True,
                                    llm_provider_factory=factory)
    return svc, {"m": RegulationIndex([C40, C41], "m")}


def test_when_the_option_is_off_the_llm_is_never_built_or_called():
    made: list[int] = []
    llm = FakeLLM({"제40조": 1, "제41조": 1})
    svc, groups = _service(llm, _settings(regulation_llm_select_enabled=False), factory_calls=made)
    res = svc._find("d1", groups, None)
    assert made == [] and llm.calls == 0
    assert res.indexed is True and len(res.items) == 1 and res.reason is None          # 종전 결정형 조회 1위


def test_a_non_local_provider_never_receives_the_document():
    for name in ("noop", "anthropic", "openai", "google"):
        made: list[int] = []
        llm = FakeLLM({"제40조": 1, "제41조": 1})
        svc, groups = _service(llm, _settings(llm_provider=name), factory_calls=made)
        res = svc._find("d1", groups, None)
        assert res.items == [] and res.reason == "llm_not_local" and llm.calls == 0 and made == [], name


def test_the_llm_chosen_sentence_is_the_only_thing_shown():
    svc, groups = _service(FakeLLM({"제40조": 0, "제41조": 2}))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제41조"]
    assert res.items[0].sentences == ("② 공정 조건표는 기밀로 취급한다.",)
    assert res.indexed is True and res.reason is None


def test_no_applicable_item_shows_nothing_and_says_so():
    svc, groups = _service(FakeLLM({"제40조": 0, "제41조": 0}))
    res = svc._find("d1", groups, None)
    assert res.items == [] and res.reason == "not_applicable" and res.indexed is True


def test_an_unindexed_document_is_reported_before_any_llm_call():
    llm = FakeLLM({"제40조": 1, "제41조": 1})
    svc, groups = _service(llm)
    res = svc._find("missing", groups, None)
    assert res.indexed is False and res.reason == "document_not_indexed" and llm.calls == 0


def test_the_answer_is_remembered_including_none_but_not_failures():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm)
    svc._find("d1", groups, None)
    first = llm.calls
    assert first == 2
    assert svc._find("d1", groups, None).reason == "not_applicable" and llm.calls == first       # 「해당 없음」은 기억한다

    bad = FakeLLM({"제40조": RuntimeError("x"), "제41조": RuntimeError("x")})
    svc2, groups2 = _service(bad)
    assert svc2._find("d1", groups2, None).reason == "llm_unavailable"
    calls = bad.calls
    assert svc2._find("d1", groups2, None).reason == "llm_unavailable" and bad.calls > calls    # 실패는 다시 시도한다


# ⚠ 아래 호출 수 시험은 모든 후보가 「해당 없음」인 가짜로 센다 — 첫 후보가 뽑히면 아직 시작 안 한 둘째 호출을 취소하므로(의도된 동작)
#   호출 수가 부하에 따라 1 또는 2 가 되어 시험이 흔들린다(부하 큰 실행에서 실제로 흔들렸다). 「해당 없음」도 캐시에 남는다.
def test_a_changed_document_or_settings_or_invalidate_asks_again():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm)
    svc._find("d1", groups, None)
    n = llm.calls
    svc._find("d2", groups, None)                                   # 문서가 다르다
    assert llm.calls == n + 2
    n = llm.calls
    svc._find("d1", groups, None)                                   # 같은 문서 — 캐시
    assert llm.calls == n
    svc.invalidate()                                                # 규정 활성화·삭제 등 → 캐시를 버린다
    svc._find("d1", groups, None)
    assert llm.calls == n + 2


def test_a_changed_document_text_is_not_served_from_the_cache():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    text = {"v": "첫 본문"}
    svc = RegulationEvidenceService(vector_store=Store(), settings_obj=_settings(), doc_text_provider=lambda d: text["v"],
                                    document_exists=lambda d: True, llm_provider_factory=lambda: llm)
    groups = {"m": RegulationIndex([C40, C41], "m")}
    svc._find("d1", groups, None)
    n = llm.calls
    text["v"] = "다시 처리돼 바뀐 본문"
    svc._find("d1", groups, None)
    assert llm.calls == n + 2


def test_the_preview_path_does_not_use_the_cache():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm)
    svc._find("d1", groups, None, use_cache=False)
    n = llm.calls
    svc._find("d1", groups, None, use_cache=False)
    assert llm.calls == n + 2


def test_cache_can_be_turned_off():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm, _settings(regulation_llm_cache_ttl_s=0))
    svc._find("d1", groups, None)
    n = llm.calls
    svc._find("d1", groups, None)
    assert llm.calls == n + 2


def test_a_provider_that_cannot_be_built_hides_the_evidence_instead_of_failing_the_request():
    svc, groups = _service(RuntimeError("접속 설정 오류"))
    res = svc._find("d1", groups, None)
    assert res.items == [] and res.reason == "llm_unavailable" and res.indexed is True


def test_a_public_document_shows_nothing_and_says_why():
    """이미 공개된 판결문·법령·보도·공시에는 사내 규정을 붙이지 않는다 — 낱말만 겹친 항이 뜨는 것을 막는다."""
    llm = FakeLLM({"제40조": 1, "제41조": 2}, kind="public")
    svc, groups = _service(llm, _settings(regulation_llm_skip_public=True))
    res = svc._find("d1", groups, None)
    assert res.items == [] and res.reason == "public_document" and res.indexed is True and llm.kind_calls == 1


def test_an_internal_document_still_gets_its_clause_when_the_public_check_is_on():
    llm = FakeLLM({"제40조": 1, "제41조": 0}, kind="internal")
    svc, groups = _service(llm, _settings(regulation_llm_skip_public=True))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제40조"] and llm.kind_calls == 1


def test_the_public_check_can_be_turned_off_by_setting():
    llm = FakeLLM({"제40조": 1, "제41조": 0}, kind="public")
    svc, groups = _service(llm, _settings(regulation_llm_skip_public=False))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제40조"] and llm.kind_calls == 0


def test_the_number_of_candidates_asked_follows_the_setting():
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm, _settings(regulation_llm_candidates=1))
    svc._find("d1", groups, None)
    assert llm.calls == 1


def test_max_items_is_respected_when_the_llm_confirms_several():
    llm = FakeLLM({"제40조": 1, "제41조": 1})
    svc, groups = _service(llm)
    res = svc._find("d1", groups, 2)
    assert [i.article_no for i in res.items] == ["제40조", "제41조"] or [i.article_no for i in res.items] == ["제41조", "제40조"]
    assert len(res.items) == 2


@pytest.mark.parametrize("field,bad", [("regulation_llm_candidates", 0), ("regulation_llm_candidates", 11), ("regulation_llm_doc_chars", 100),
                                       ("regulation_llm_timeout_s", 0.5), ("regulation_llm_max_concurrency", 0), ("regulation_llm_cache_ttl_s", -1),
                                       ("regulation_llm_mode", "batch"), ("regulation_llm_mode", "")])
def test_settings_reject_out_of_range_values(field, bad):
    from koipa.config import Settings

    with pytest.raises(ValueError):
        Settings(**{field: bad})


# ── 고르는 방식(regulation_llm_mode) ────────────────────────────────────────────

class FakeSingleLLM:
    """한 번에 고르기(single) 방식의 답을 하는 가짜 — 문서 종류(프롬프트에 KIND_QUESTION 이 있다)와 고르기. 기호: 제40조 A1·A2 · 제41조 B1·B2."""

    name = "ollama"
    model = "fake-single"

    def __init__(self, pick, kind="internal"):
        self.pick = pick
        self.kind = kind
        self.pick_calls = 0
        self.kind_calls = 0

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.7, json_schema=None):
        ok = SimpleNamespace(success=True, error_code=None)
        if llm_select.KIND_QUESTION in prompt:
            self.kind_calls += 1
            return SimpleNamespace(text=f'{{"kind": "{self.kind}", "reason": "x"}}', usage=ok)
        self.pick_calls += 1
        return SimpleNamespace(text=self.pick, usage=ok)


def test_the_default_mode_is_per_candidate():
    from koipa.config import Settings

    assert Settings().regulation_llm_mode == "per_candidate"


def test_single_mode_asks_which_item_applies_with_one_pick_call_and_shows_the_chosen_sentence():
    llm = FakeSingleLLM('{"items": ["B2"]}')
    svc, groups = _service(llm, _settings(regulation_llm_mode="single"))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제41조"] and res.items[0].sentences == ("② 공정 조건표는 기밀로 취급한다.",)
    assert llm.pick_calls == 1 and llm.kind_calls == 0                       # 이 시험의 기본 설정은 문서 종류 확인을 끈다


def test_single_mode_checks_the_document_kind_first_when_the_public_check_is_on():
    llm = FakeSingleLLM('{"items": ["A1"]}', kind="public")
    svc, groups = _service(llm, _settings(regulation_llm_mode="single", regulation_llm_skip_public=True))
    res = svc._find("d1", groups, None)
    assert res.items == [] and res.reason == "public_document" and llm.kind_calls == 1 and llm.pick_calls == 0


def test_the_mode_is_part_of_the_cache_key():
    llm = FakeSingleLLM('{"items": ["B2"]}')
    cfg = _settings(regulation_llm_mode="single")
    svc, groups = _service(llm, cfg)
    svc._find("d1", groups, None)
    svc._find("d1", groups, None)
    assert llm.pick_calls == 1                                                # 같은 방식 — 캐시
    cfg.regulation_llm_mode = "per_candidate"
    svc._find("d1", groups, None)
    assert llm.pick_calls > 1                                                 # 방식이 바뀌면 캐시를 쓰지 않고 다시 묻는다


def test_an_unknown_mode_falls_back_to_per_candidate():
    llm = FakeLLM({"제40조": 0, "제41조": 2})
    svc, groups = _service(llm, _settings(regulation_llm_mode="nonsense"))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제41조"] and llm.calls == 2


# ── 독립 리뷰(2026-09-26)가 짚은 것 — 서버 주소 · 호출 한도 · 부분 실패 · 캐시 키 · 설정 전달 · 미리보기 예산 ────────────────

class AddressedLLM(FakeLLM):
    """운영 어댑터처럼 서버 주소(base_url)를 드러내는 가짜 — 이름은 로컬(vllm)이어도 주소가 사외면 문서를 보내면 안 된다."""

    def __init__(self, answers, base_url, name="vllm", **kw):
        super().__init__(answers, **kw)
        self.base_url = base_url
        self.name = name


def test_an_outside_address_never_receives_the_document_even_when_the_provider_name_is_local():
    for url in ("https://api.example.com/v1", "http://8.8.8.8:8001/v1", "http://[2001:4860:4860::8888]/v1", ""):
        llm = AddressedLLM({"제40조": 1, "제41조": 1}, url)
        svc, groups = _service(llm, _settings(llm_provider="vllm"))
        res = svc._find("d1", groups, None)
        assert res.items == [] and res.reason == "llm_not_local" and llm.calls == 0 and llm.kind_calls == 0, url


def test_an_inside_address_is_used():
    for url in ("http://localhost:8001/v1", "http://127.0.0.1:8001/v1", "http://10.1.2.3:8001/v1", "http://192.168.0.7:8000/v1"):
        llm = AddressedLLM({"제40조": 1, "제41조": 0}, url)
        svc, groups = _service(llm, _settings(llm_provider="vllm"))
        assert [i.article_no for i in svc._find("d1", groups, None).items] == ["제40조"], url


def test_the_startup_warning_names_an_outside_llm_address_and_a_plain_storage(caplog):
    import logging

    from koipa.api.app import _warn_regulation_prerequisites

    def cfg(**over):
        base = dict(regulation_reference_enabled=True, regulation_llm_select_enabled=True, embedding_provider="hf", llm_provider="vllm",
                    local_llm_base_url="http://localhost:8001/v1", vllm_base_url="http://localhost:8001/v1", storage_encryption_enabled=True)
        base.update(over)
        return SimpleNamespace(**base)

    with caplog.at_level(logging.WARNING, logger="koipa.api.app"):
        _warn_regulation_prerequisites(cfg())
        assert caplog.records == []                                                           # 사내 주소 + 암호화 = 경고 없음
        _warn_regulation_prerequisites(cfg(local_llm_base_url="https://api.example.com/v1"))
        assert any("사내가 아니거나" in r.getMessage() and "api.example.com" in r.getMessage() for r in caplog.records)
        caplog.clear()
        _warn_regulation_prerequisites(cfg(llm_provider="ollama", local_llm_base_url="https://api.example.com/v1"))   # ollama 는 코드가 localhost 로 고정한다
        assert caplog.records == []
        _warn_regulation_prerequisites(cfg(storage_encryption_enabled=False))
        assert any("평문" in r.getMessage() for r in caplog.records)


def test_a_partial_failure_is_shown_but_not_remembered():
    """1위 후보 호출이 일시 오류로 빠진 채 2위가 「가장 직접적인 항」으로 1시간 굳으면 안 된다(독립 리뷰 R2·R3)."""
    llm = FakeLLM({"제40조": RuntimeError("일시 오류"), "제41조": 2})
    svc, groups = _service(llm, _settings(regulation_llm_candidates=2))
    res = svc._find("d1", groups, None)
    assert [i.article_no for i in res.items] == ["제41조"] and res.reason is None
    n = llm.calls
    assert n == 2
    llm.answers["제40조"] = 1                                                                  # 서버가 살아났다
    res2 = svc._find("d1", groups, None)
    assert llm.calls > n and [i.article_no for i in res2.items] == ["제40조"]                  # 기억하지 않았으므로 다시 물어 1위가 보인다


def test_a_change_beyond_the_prompt_window_is_not_served_from_the_cache():
    """프롬프트에는 앞 doc_chars 글자만 들어가지만 조회(낱말 채널)는 본문 6,000자를 본다 — 뒷부분만 바뀌어도 옛 결과를 주면 안 된다."""
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    text = {"v": "가" * 400 + "시험 결과 A"}
    svc = RegulationEvidenceService(vector_store=Store(), settings_obj=_settings(regulation_llm_doc_chars=300), doc_text_provider=lambda d: text["v"],
                                    document_exists=lambda d: True, llm_provider_factory=lambda: llm)
    groups = {"m": RegulationIndex([C40, C41], "m")}
    svc._find("d1", groups, None)
    n = llm.calls
    text["v"] = "가" * 400 + "시험 결과 B"
    svc._find("d1", groups, None)
    assert llm.calls == n + 2


def _ask_again_after(mutate, *, max_items_after=None):
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    cfg = _settings()
    svc, groups = _service(llm, cfg)
    svc._find("d1", groups, None)
    n = llm.calls
    svc._find("d1", groups, None)
    assert llm.calls == n, "같은 조건이면 캐시여야 한다"
    mutate(svc, cfg, llm)
    svc._find("d1", groups, max_items_after)
    return llm.calls > n


@pytest.mark.parametrize("name,mutate,after", [
    ("규정 판 지문(다른 프로세스가 규정을 바꿈)", lambda svc, cfg, llm: setattr(svc, "_signature", (99, "다른 판")), None),
    ("공급자 이름", lambda svc, cfg, llm: setattr(cfg, "llm_provider", "vllm"), None),
    ("모델", lambda svc, cfg, llm: setattr(llm, "model", "다른 모델"), None),
    ("후보 수", lambda svc, cfg, llm: setattr(cfg, "regulation_llm_candidates", 2), None),
    ("문서 글자 수", lambda svc, cfg, llm: setattr(cfg, "regulation_llm_doc_chars", 1400), None),
    ("공개 자료 확인", lambda svc, cfg, llm: setattr(cfg, "regulation_llm_skip_public", True), None),
    ("최소 유사도 문턱", lambda svc, cfg, llm: setattr(cfg, "regulation_min_similarity", 0.001), None),
    ("보이는 조항 수", lambda svc, cfg, llm: None, 2),
])
def test_every_ingredient_of_the_cache_key_separates_the_entries(name, mutate, after):
    assert _ask_again_after(mutate, max_items_after=after), name


def test_the_cache_expires_after_the_ttl_and_is_bounded(monkeypatch):
    from koipa.services import regulation_evidence_service as mod

    now = {"t": 0.0}
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc = RegulationEvidenceService(vector_store=Store(), settings_obj=_settings(regulation_llm_cache_ttl_s=100), doc_text_provider=lambda d: "시험 결과",
                                    document_exists=lambda d: True, llm_provider_factory=lambda: llm, clock=lambda: now["t"])
    groups = {"m": RegulationIndex([C40, C41], "m")}
    svc._find("d1", groups, None)
    n = llm.calls
    now["t"] = 50.0
    svc._find("d1", groups, None)
    assert llm.calls == n                                                                      # 아직 유효
    now["t"] = 150.0
    svc._find("d1", groups, None)
    assert llm.calls == n + 2                                                                  # TTL 이 지났다 — 다시 묻는다
    monkeypatch.setattr(mod, "LLM_CACHE_MAX_ENTRIES", 2)
    now["t"] = 151.0
    for d in ("a", "b", "c"):
        svc._find(d, groups, None)
    n = llm.calls
    svc._find("c", groups, None)
    assert llm.calls == n                                                                      # 가장 최근 것은 남아 있다
    svc._find("a", groups, None)
    assert llm.calls > n                                                                       # 가장 오래된 것은 밀려났다


def test_the_settings_reach_the_llm_call(monkeypatch):
    """서비스가 설정값을 llm_select 로 그대로 넘기는가 — 상수로 바뀌어도 시험이 통과하던 곳(독립 리뷰 R3)."""
    from concurrent.futures import ThreadPoolExecutor

    seen: dict[str, object] = {}
    real = llm_select.select_applicable

    def spy(provider, doc_text, candidates, **kw):
        seen.update(kw)
        return real(provider, doc_text, candidates, **kw)

    monkeypatch.setattr(llm_select, "select_applicable", spy)
    pools: list[int] = []
    monkeypatch.setattr(llm_select, "shared_executor", lambda n: (pools.append(n), ThreadPoolExecutor(max_workers=2))[1])
    llm = FakeLLM({"제40조": 0, "제41조": 0})
    svc, groups = _service(llm, _settings(regulation_llm_doc_chars=777, regulation_llm_timeout_s=12.5, regulation_llm_max_concurrency=3,
                                          regulation_llm_skip_public=True, regulation_llm_mode="per_candidate"))
    svc._find("d1", groups, 2)
    assert seen["doc_chars"] == 777 and seen["timeout_s"] == 12.5 and seen["skip_public"] is True and seen["limit"] == 2 and seen["mode"] == "per_candidate"
    assert pools == [3]


def test_the_prompt_window_follows_the_doc_chars_setting():
    prompts: list[str] = []

    class Recording(FakeLLM):
        def generate(self, prompt, **kw):
            prompts.append(prompt)
            return super().generate(prompt, **kw)

    svc, groups = _service(Recording({"제40조": 0, "제41조": 0}), _settings(regulation_llm_doc_chars=500), doc_text="가" * 3000)
    svc._find("d1", groups, None)
    doc_parts = [p.split("[문서 앞부분]\n")[1].split("\n\n[규정 조항]")[0] for p in prompts if "[규정 조항]" in p]
    assert doc_parts and all(d == "가" * 500 for d in doc_parts)


def test_an_empty_document_is_not_sent_to_the_llm():
    llm = FakeLLM({"제40조": 1, "제41조": 1})
    svc, groups = _service(llm, doc_text="   \n ")
    res = svc._find("d1", groups, None)
    assert res.items == [] and res.reason == "not_applicable" and llm.calls == 0 and llm.kind_calls == 0


def test_the_provider_call_limits_are_set_once_from_the_deadline_setting():
    """호출 한 건이 SDK 기본(시간 초과 600초·재시도)으로 몇 시간씩 스레드를 붙잡지 않게 — 문서 마감 = 호출 한도(독립 리뷰 R2)."""
    limits: list[float] = []

    class Limited(FakeLLM):
        def limit_calls(self, *, timeout_s, max_retries=0):
            limits.append(timeout_s)

    svc, groups = _service(Limited({"제40조": 0, "제41조": 0}), _settings(regulation_llm_timeout_s=42.0))
    svc._find("d1", groups, None)
    svc._find("d2", groups, None)
    assert limits == [42.0]


def test_the_preview_stops_asking_the_llm_once_the_total_budget_is_spent():
    """미리보기는 문서를 순차로 판정한다 — 문서마다 마감이 따로라 LLM 이 느리면 요청이 (문서 수 × 마감)까지 걸리던 것을 전체 예산(마감의 2배)으로 묶는다."""
    import time as _time

    class Slow(FakeLLM):
        def generate(self, prompt, **kw):
            _time.sleep(0.25)
            return super().generate(prompt, **kw)

    svc, groups = _service(Slow({"제40조": 0, "제41조": 0}), _settings(regulation_llm_candidates=1, regulation_llm_timeout_s=0.3))
    t0 = _time.monotonic()
    results = svc._find_many([f"d{i}" for i in range(6)], groups)
    assert _time.monotonic() - t0 < 6 * 0.25                                                   # 예산이 없었다면 6 × 0.25 초
    assert results[0].reason == "not_applicable" and results[0].indexed is True
    assert results[-1].reason == "llm_unavailable" and results[-1].indexed is None and results[-1].items == []


def test_the_raw_file_key_is_the_same_for_storing_and_reading_long_file_names():
    """행에는 파일명을 500자로 잘라 적는다 — 저장 키도 같아야 읽기·삭제가 같은 키를 찾는다(독립 리뷰 R1)."""
    from koipa.services.regulation_service import _raw_key

    long_name = "가" * 600 + ".md"
    assert _raw_key("abc123", long_name) == _raw_key("abc123", long_name[:500]) and len(_raw_key("abc123", long_name)) == len("abc123/") + 500
    assert _raw_key("abc123", "규정.md") == "abc123/규정.md"


def test_llm_option_defaults_and_bounds_are_locked():
    """기본값과 경계를 잠근다 — 상수를 바꿔도 통과하던 곳(독립 리뷰 R3). 문서(.env.example·INSTALL·설계서)가 말하는 값과 같아야 한다."""
    from koipa.config import Settings

    s = Settings()
    assert (s.regulation_llm_select_enabled, s.regulation_llm_candidates, s.regulation_llm_doc_chars, s.regulation_llm_timeout_s,
            s.regulation_llm_max_concurrency, s.regulation_llm_cache_ttl_s, s.regulation_llm_skip_public, s.regulation_llm_mode) == (
        False, 5, 1500, 60.0, 6, 3600, True, "per_candidate")
    for field, lo, hi in (("regulation_llm_candidates", 1, 10), ("regulation_llm_doc_chars", 300, 6000), ("regulation_llm_timeout_s", 1, 600),
                          ("regulation_llm_max_concurrency", 1, 32), ("regulation_llm_cache_ttl_s", 0, 86400)):
        Settings(**{field: lo})
        Settings(**{field: hi})                                                                # 경계값은 받는다
    for field, bad in (("regulation_llm_candidates", 11), ("regulation_llm_doc_chars", 299), ("regulation_llm_doc_chars", 6001),
                       ("regulation_llm_timeout_s", 0.99), ("regulation_llm_timeout_s", 601), ("regulation_llm_max_concurrency", 33)):
        with pytest.raises(ValueError):
            Settings(**{field: bad})
