"""후보 조항에서 「이 문서에 직접 적용되는 항」을 로컬 LLM 으로 **고르게** 한다 — 화면에는 규정 원문만 나간다.

왜 있나(2026-09-26). 조회 점수(밀집·낱말·교차 인코더)로는 「이 규정이 이 문서에 해당하는가」를 못 가른다 — 같은 71건에서 도움과
그 밖을 가르는 AUROC 가 0.42~0.71 이었다(설계서 §3.4). 그래서 조회가 준 후보 조항 상위 K 개를 로컬 LLM 에게 보이고, 조항의 항 가운데
문서에 직접 적용되는 것을 **번호로 고르게** 한다.

⛔ LLM 이 만든 글은 화면에 나가지 않는다 — 고른 항의 규정 원문만 나간다(원문 그대로 원칙 P3). 아무 항도 고르지 않으면 아무것도 안 보인다.
⛔ 등급을 판정하지 않는다. 프롬프트도 등급을 묻지 않는다.
⛔ 판정이 서지 않으면(LLM 오류·시간 초과·해석 불가) 보이지 않는다 — 확인하지 못한 규정을 「해당」으로 내보내지 않는다.
⛔ **로컬 LLM 만 쓴다.** 문서 본문(고객사 기밀)이 프롬프트에 들어가므로 원격 공급자(anthropic·openai·google)나 목업(noop)이면 호출하지 않는다.
   공급자 이름만이 아니라 서버 **주소**도 이 서버·사내망 안일 때만 보낸다(`endpoint_is_local`).
"""

from __future__ import annotations

import inspect
import ipaddress
import json
import logging
import re
import socket
import threading
import time
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from koipa.adapters.llm.base import accepts_json_schema
from koipa.regulation.index import ClauseRec, EvidenceItem, SentenceRec, evidence_item

logger = logging.getLogger(__name__)

REASON_NOT_APPLICABLE = "not_applicable"       # 모든 후보에서 해당 항이 없다고 답했다 — 정상 응답
REASON_PUBLIC_DOCUMENT = "public_document"     # 이미 공개된 외부 자료(판결문·법령·보도·공시 등)라 사내 규정을 붙이지 않았다 — 정상 응답
REASON_LLM_UNAVAILABLE = "llm_unavailable"     # LLM 오류·시간 초과·해석 불가로 판정이 서지 않았다
REASON_LLM_NOT_LOCAL = "llm_not_local"         # 로컬이 아닌 공급자이거나 LLM 서버 주소가 사내가 아니라 문서를 보내지 않았다

# 문서 본문이 프롬프트에 들어가므로 온프렘 안에서 도는 공급자만 허용한다(허용목록 — 모르는 이름은 막는다).
LOCAL_PROVIDERS = frozenset({"ollama", "vllm", "local_openai", "lm_studio"})

SYSTEM = ("당신은 사내 문서보안 규정 담당자입니다. 규정 조항의 어느 항이 특정 문서에 직접 적용되는지만 가려냅니다. "
          "문서의 등급을 판정하지 않습니다.")

# 이 문구는 71건 시험에서 다듬은 것이다(설계서 §3.6) — 처음 판(「낱말만 겹치면 0」)은 정밀도 58% 였고, 「이 문서 자체가 그 항이 정한 종류의 사내 문서」·
# 「외부 공개 자료·논평은 0」·「확실하지 않으면 0」을 더한 이 판은 80% 대였다. 고칠 때는 같은 시트로 다시 재고, 해당하지 않는 규정이 뜬 문서 수를 본다.
QUESTION = (
    "질문: 위 규정 조항의 항 가운데 이 문서에 직접 적용되는 것이 있습니까? 있으면 가장 직접적인 항 하나의 번호를, 없으면 0 을 답하십시오.\n"
    "- 직접 적용: 이 문서 자체가 그 항이 정한 종류의 사내 문서이거나(시험성적서·설계도면·계약서·평가서 등), 이 문서가 다루는 사내 정보가 "
    "그 항이 정한 대상에 해당한다. 검수자가 \"이 문서는 이 항으로 다룬다\"고 말할 수 있다.\n"
    "- 0: 낱말·주제만 겹친다. 문서의 종류·내용이 어느 항의 대상도 아니다. 문서 내용과 상관없이 걸리는 일반 절차·권한·점검·승인 항이다. "
    "판결문·법령·보도·논문·공시처럼 이미 외부에 공개된 자료이거나 어떤 주제를 설명·논평할 뿐인 문서도 0 이다.\n"
    "- 확실하지 않으면 0 을 답하십시오.\n"
    "JSON 으로만 답하십시오: {\"item\": 항 번호 또는 0, \"reason\": \"한 문장\"}")

JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"item": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["item", "reason"],
}

# 문서가 사내 문서인지 이미 공개된 외부 자료인지 — 규정은 회사가 만든 비공개 사내 문서를 다룬다(설계서 D-06). 공개 판결문에 「특허 출원 전
# 발명 내용은 공개하지 않는다」 같은 항이 낱말만 겹쳐 뜨는 것을 막는다. 같은 120건에서 119건을 맞혔다(공개 자료 38건 중 37, 사내 문서 82건 중 82).
# system 문구는 방식마다 다르다. 한 번에 고르기(single)는 종류 확인도 고르기와 **같은 SYSTEM** 으로 부른다 — 두 호출이 같은 문서 앞부분(system + `[문서 앞부분]` + 문서)으로
# 시작해 서버가 그 읽기를 재사용한다. CPU 만(GPU 층 0)에서 문구가 다르면 문서당 106~112초, 같으면 74~75초였다(−31%, 답 같음 — 시험 자료 time_cpu_prefix.py).
# 후보마다 묻기(per_candidate)는 종전 **KIND_SYSTEM** 을 그대로 쓴다 — 같은 SYSTEM 으로 바꾸자 공개 판결문 1건에 「특허 출원 전의 발명 내용은 공개하지 않는다」 항이
# 새로 떴다(공개 자료 38건 중 0 → 1, 재실행에서도 같은 문서). 얻는 것도 작았다(GPU 문서당 2.9 → 2.6~2.7초). 설계서 §3.6.
KIND_SYSTEM = "당신은 문서 분류 담당자입니다. 문서가 사내 문서인지 이미 공개된 외부 자료인지만 가립니다."
KIND_QUESTION = (
    "질문: 이 문서는 어느 쪽입니까?\n"
    "- internal: 회사가 내부에서 작성·보유하는 사내 문서(회의록·보고서·시험성적서·계약 검토·평가서·메모 등). 비공개 정보를 담을 수 있다.\n"
    "- public: 판결문·법령·보도자료·논문·공시·증권사·연구기관의 시장 보고서처럼 이미 외부에 공개된 자료이거나, 외부 공개 자료를 그대로 옮긴 문서.\n"
    "JSON 으로만 답하십시오: {\"kind\": \"internal\" 또는 \"public\", \"reason\": \"한 문장\"}")
KIND_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"kind": {"type": "string", "enum": ["internal", "public"]}, "reason": {"type": "string"}},
    "required": ["kind", "reason"],
}

_ITEM_RE = re.compile(r'"item"\s*:\s*(-?\d+)')
_KIND_RE = re.compile(r'"kind"\s*:\s*"(internal|public)"')
_OBJ_RE = re.compile(r"\{.*?\}", re.S)


def is_local_provider(name: str | None) -> bool:
    return (name or "").strip().lower() in LOCAL_PROVIDERS


# 공급자 **이름**이 로컬이어도 vllm·local_openai 는 `LOCAL_LLM_BASE_URL` 을 그대로 쓴다 — 주소가 사외면 이름 검사만으로는 문서 본문이 밖으로 나간다
# (독립 리뷰 R1·R3, 2026-09-26). 그래서 서버 **주소**도 이 서버 또는 사내망 안인지 확인한다(ollama·lm_studio 는 코드가 localhost 로 고정한다).
_SHARED_ADDRESS_SPACE = ipaddress.ip_network("100.64.0.0/10")      # 사업자 공유 주소 — 사내 오버레이·VPN 이 쓴다(공인 주소가 아니다)
_HOST_CHECK_TTL_S = 300.0
_host_checks: dict[str, tuple[float, bool]] = {}
_host_checks_lock = threading.Lock()


def _address_is_private(text: str) -> bool:
    """IP 주소가 공인 인터넷 밖(루프백·사설·링크 로컬·공유 주소 공간)인가. IP 표기가 아니면 ValueError."""
    ip = ipaddress.ip_address(text.split("%", 1)[0])              # fe80::1%eth0 의 범위 표시는 뗀다
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped                                         # ::ffff:8.8.8.8 이 사설로 잘못 읽히지 않게 안쪽 IPv4 로 본다
    return bool(ip.is_loopback or ip.is_private or ip.is_link_local or (ip.version == 4 and ip in _SHARED_ADDRESS_SPACE))


def endpoint_is_local(base_url: str | None) -> bool:
    """LLM 서버 주소가 이 서버 또는 사내망 안인가 — 아니거나 확인하지 못하면 False(문서 본문을 보내지 않는다).

    · localhost·루프백·사설·링크 로컬 IP 는 그대로 통과한다.
    · 호스트 이름은 이름을 풀어 나온 주소가 **전부** 위 범위일 때만 통과한다(도커 서비스 이름·사내 DNS 이름 포함). 풀리지 않으면 막는다.
    · 결과는 호스트별로 5분 기억한다(요청마다 이름 풀이를 하지 않는다).
    """
    try:
        host = (urlsplit(str(base_url or "")).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return _address_is_private(host)
    except ValueError:
        pass                                                        # IP 가 아니라 이름이다
    now = time.monotonic()
    with _host_checks_lock:
        hit = _host_checks.get(host)
    if hit is not None and now - hit[0] < _HOST_CHECK_TTL_S:
        return hit[1]
    try:
        addrs = {info[4][0] for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)}
        ok = bool(addrs) and all(_address_is_private(a) for a in addrs)
    except (OSError, ValueError):
        ok = False                                                  # 이름을 못 풀면 사외일 수 있다 — 막는다
    with _host_checks_lock:
        _host_checks[host] = (now, ok)
    return ok


@dataclass
class Selection:
    items: list[EvidenceItem] = field(default_factory=list)
    reason: str | None = None            # items 가 비었을 때만
    calls: int = 0
    failures: int = 0


def selectable(clause: ClauseRec) -> list[SentenceRec]:
    """고를 수 있는 항 = 서두("다음 각 호…")가 아닌 문장, 조항 안의 순서대로."""
    return sorted((s for s in clause.sentences if not s.is_lead), key=lambda s: s.seq)


def build_prompt(doc_text: str, clause: ClauseRec, sents: Sequence[SentenceRec], doc_chars: int) -> str:
    doc = " ".join(doc_text.split())[: max(1, doc_chars)]
    body = "\n".join(f"[{n}] {s.text}" for n, s in enumerate(sents, 1))
    return f"[문서 앞부분]\n{doc}\n\n[규정 조항]\n{clause.article_no}({clause.title})\n{body}\n\n{QUESTION}"


def parse_choice(text: str, n: int) -> int | None:
    """답에서 항 번호를 읽는다. 0 = 해당 항 없음, 1..n = 고른 항, None = 읽을 수 없음(범위 밖 포함)."""
    value: Any = None
    for cand in (text, *(m.group(0) for m in _OBJ_RE.finditer(text or ""))):
        try:
            obj = json.loads(cand)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict) and "item" in obj:
            value = obj["item"]
            break
    if value is None:
        m = _ITEM_RE.search(text or "")
        value = int(m.group(1)) if m else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= n else None


def build_kind_prompt(doc_text: str, doc_chars: int) -> str:
    return f"[문서 앞부분]\n{' '.join(doc_text.split())[: max(1, doc_chars)]}\n\n{KIND_QUESTION}"


def parse_kind(text: str) -> str | None:
    """답에서 문서 종류를 읽는다. 'internal' | 'public' | None(읽을 수 없음)."""
    for cand in (text, *(m.group(0) for m in _OBJ_RE.finditer(text or ""))):
        try:
            obj = json.loads(cand)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("kind") in ("internal", "public"):
            return str(obj["kind"])
    m = _KIND_RE.search(text or "")
    return m.group(1) if m else None


# 스키마 출력이면 답의 첫 키(item·kind)에 값이 나온다 — `{"item": 3,` 는 토큰 대여섯 개다. 뒤이은 reason 문장은 쓰지 않는데 생성 시간의 대부분이었다
# (출력 약 50토큰이 호출당 0.7초). 온도 0 에서 값은 뒤의 문장과 무관하게 정해지므로 값이 나온 뒤 끊는다 — 같은 40호출에서 전체 출력과 값이 모두 같았다
# (호출당 0.8초 → 0.35초). 스키마를 못 쓰는 서버는 키 순서를 보장하지 못하므로 종전대로 200 이다.
_VALUE_ONLY_TOKENS = 16


def _accepts_kwarg(provider: Any, name: str) -> bool:
    """provider.generate 가 이 이름의 인자를 받는가 — 시험용 가짜·다른 어댑터를 깨지 않으려고 시그니처로 탐지한다(accepts_json_schema 와 같은 방식)."""
    generate = getattr(provider, "generate", None)
    if generate is None:
        return False
    try:
        return name in inspect.signature(generate).parameters
    except (TypeError, ValueError):
        return False


def _generate(provider: Any, system: str, prompt: str, schema: dict[str, Any], use_schema: bool, max_tokens: int | None = None) -> str:
    kwargs: dict[str, Any] = {"system": system, "temperature": 0.0,
                              "max_tokens": max_tokens if max_tokens is not None else (_VALUE_ONLY_TOKENS if use_schema else 200)}
    if use_schema:
        kwargs["json_schema"] = schema
    if getattr(provider, "name", "") == "ollama" and len(system) + len(prompt) > OLLAMA_PROMPT_LIMIT_CHARS:
        # 컨텍스트를 넘으면 서버가 앞부분(문서)을 잘라 넣고 문서 없이 고른다 — 확인하지 못한 규정을 「해당」으로 내보내지 않으므로 부르지 않는다
        raise RuntimeError("LLM 호출 실패: PromptTooLong")
    if _accepts_kwarg(provider, "no_reasoning"):
        kwargs["no_reasoning"] = True       # Ollama 의 Qwen3 가 생각에 토큰을 다 쓰지 않게 — 이 기능이 요청할 때만 켠다(다른 호출자는 종전 그대로)
    resp = provider.generate(prompt, **kwargs)
    usage = getattr(resp, "usage", None)
    if usage is not None and getattr(usage, "success", True) is False:
        raise RuntimeError(f"LLM 호출 실패: {getattr(usage, 'error_code', None)}")
    return str(getattr(resp, "text", "") or "")


def _why(exc: BaseException) -> str:
    """로그에 남길 실패 원인 — 우리 호출 실패는 오류 코드까지(APIConnectionError 등), 그 밖은 예외 종류만(문서 본문이 섞일 수 있는 메시지는 안 남긴다)."""
    if isinstance(exc, RuntimeError) and str(exc).startswith("LLM 호출 실패:"):
        return str(exc)
    return type(exc).__name__


def _ask(provider: Any, prompt: str, use_schema: bool) -> str:
    return _generate(provider, SYSTEM, prompt, JSON_SCHEMA, use_schema)


def _ask_kind(provider: Any, prompt: str, use_schema: bool, system: str = KIND_SYSTEM) -> str:
    return _generate(provider, system, prompt, KIND_SCHEMA, use_schema)


# ── 한 번에 고르기(single) ─────────────────────────────────────────────────────
# 후보 조항마다 따로 묻는 방식(per_candidate, 기본)은 문서를 후보 수만큼 다시 읽는다 — 호출 6번. 문서를 한 번만 보이고 후보의 항에 **기호**(A1·A2·B1…)를 붙여
# 한꺼번에 보이면 고르는 호출은 1번이다(문서 종류 확인은 종전 그대로 앞서 1번).
# 같은 71건·판정자 3명 다수결로 견준 결과(설계서 §3.6): 해당 규정이 뜬 문서 14 → 30, 정밀도 78% → 70%, 해당 안 되는 규정이 뜬 문서 4 → 13, 문서당 2.9초 → 1.1초(GPU).
# 더 많이 찾는 대신 틀린 것도 더 뜨므로 「해당되는 규정만」을 앞세우는 기본은 per_candidate 로 두고, single 은 설정으로 고르는 선택지다.
# ⚠ 기호에 숫자를 이어 붙이지 않은 까닭: 처음 판은 항에 1부터 이어지는 번호를 붙였더니 모델이 조항 번호(제34조·제40조…)와 항 번호를 섞어 답했다
#   (120건 중 45건은 범위 밖 번호라 답을 읽을 수 없었고, 범위 안이어도 조항 번호를 고른 것이 섞였다). 알파벳+숫자 기호는 조항 번호와 겹칠 수 없고, 답의 기호를 스키마의 enum 으로 묶어 엉뚱한 값이 나올 수 없다.
# ⚠ 「후보마다 따로 답하되 한 번에 묻기」(batch)도 재 보았으나 정밀도 64%·업무 문서 4/11 에 규정이 떠 single 에 밀려 넣지 않았다.
MODE_SINGLE = "single"
MODE_PER_CANDIDATE = "per_candidate"
MODES = (MODE_SINGLE, MODE_PER_CANDIDATE)
MAX_PROMPT_ITEMS = 80            # 프롬프트에 넣는 항의 상한 — 조회 순위가 앞선 후보부터 채운다
MAX_PROMPT_CLAUSES = 26          # 후보 기호가 A~Z
# Ollama 는 모델을 4,096토큰 컨텍스트로 적재한다(0.34.2 실측: /api/ps context_length — 모델 자체는 40,960 까지 된다). 프롬프트가 넘치면 서버가 **앞부분**(system·
# [문서 앞부분])을 잘라 넣어 문서를 못 본 채 고르게 된다(독립 리뷰 R2). 한국어는 토큰당 약 1.4자(측정: 프롬프트 3,335자 = 2,256~2,420토큰)라 4,096토큰 ≈ 5,700자다.
# 한 번에 고르기는 후보의 항을 한꺼번에 싣는 방식이라 실제 규정(조항 최대 1,200자 × 후보 5개)에서 넘칠 수 있다 — 항 목록에 글자 예산을 둔다.
# 시연 규정 71건은 후보 블록이 최대 1,665자라 예산(기본 문서 1,500자일 때 2,600자) 안이다 — 잰 수치는 그대로다.
_SINGLE_PROMPT_CHARS = 5200      # 4,096토큰 ≈ 5,700자에서 출력·여유를 뺀 값
_SINGLE_FRAME_CHARS = 1100       # 질문·판단 기준·JSON 지시·머리글 등 항 목록이 아닌 틀
OLLAMA_PROMPT_LIMIT_CHARS = 5600  # 이보다 긴 프롬프트는 Ollama 기본 컨텍스트를 넘는다 — 부르지 않는다(안 보임)
_SINGLE_TOKENS = 64              # 답이 `{"items": ["B2", "A1"]}` 정도라 짧다(설명 문장이 없다)
_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"

_DIRECT_TEXT = (
    "- 직접 적용: 이 문서 자체가 그 항이 정한 종류의 사내 문서이거나(시험성적서·설계도면·계약서·평가서 등), 이 문서가 다루는 사내 정보가 "
    "그 항이 정한 대상에 해당한다. 검수자가 \"이 문서는 이 항으로 다룬다\"고 말할 수 있다.\n"
    "- 적용 아님: 낱말·주제만 겹친다. 문서의 종류·내용이 어느 항의 대상도 아니다. 문서 내용과 상관없이 걸리는 일반 절차·권한·점검·승인 항이다. "
    "판결문·법령·보도·논문·공시처럼 이미 외부에 공개된 자료이거나 어떤 주제를 설명·논평할 뿐인 문서에는 어느 항도 적용하지 않는다.\n"
    "- 확실하지 않으면 적용하지 않는다(빈 목록).\n")


def single_schema(labels: Sequence[str], limit: int) -> dict[str, Any]:
    """답의 기호를 보인 항의 기호로만 묶고(enum) 개수를 `limit` 이하로 묶는다."""
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {"type": "string", "enum": list(labels)}, "maxItems": max(1, limit), "uniqueItems": True}},
        "required": ["items"],
    }


def build_single_prompt(doc_text: str, blocks: Sequence[tuple[ClauseRec, Sequence[SentenceRec]]], doc_chars: int,
                        limit: int) -> tuple[str, dict[str, tuple[ClauseRec, SentenceRec]]]:
    """문서 앞부분 + 후보 조항의 항(조항은 A·B·C…, 항은 A1·A2·B1… 기호) + 질문. `blocks` = [(조항, 고를 수 있는 항들), ...] — 프롬프트와 기호→(조항, 항) 표를 낸다."""
    doc = " ".join(doc_text.split())[: max(1, doc_chars)]
    lines: list[str] = []
    labels: dict[str, tuple[ClauseRec, SentenceRec]] = {}
    for ci, (clause, sents) in enumerate(blocks[:MAX_PROMPT_CLAUSES]):
        letter = _LETTERS[ci]
        lines.append(f"{letter}. {clause.article_no}({clause.title})")
        for si, s in enumerate(sents, 1):
            lab = f"{letter}{si}"
            labels[lab] = (clause, s)
            lines.append(f"  {lab} {s.text}")
    question = (f"질문: 위 규정 후보의 항 가운데 이 문서에 직접 적용되는 것이 있습니까? "
                f"있으면 가장 직접적인 항의 기호(예: {next(iter(labels), 'A1')})를 직접적인 순서로 최대 {max(1, limit)}개, 없으면 빈 목록을 답하십시오.\n")
    return (f"[문서 앞부분]\n{doc}\n\n[규정 후보]\n" + "\n".join(lines) + "\n\n" + question + _DIRECT_TEXT
            + 'JSON 으로만 답하십시오: {"items": ["항 기호", ...]}'), labels


_ITEMS_RE = re.compile(r'"items"\s*:\s*\[([^\]]*)')
_LABEL_RE = re.compile(r"[A-Z]\d+")


def _labels_from_text(text: str, ok: set[str]) -> list[str] | None:
    """JSON 으로 못 읽는 답(잘린 답 등)에서 기호를 읽는다. 목록이 닫히지 않았으면 마지막 기호가 잘렸을 수 있다 —
    더 긴 기호의 앞부분이면(A1 ↔ A12) 읽지 않고, 기호가 하나도 없이 끊긴 `{"items": [` 는 「해당 없음」이 아니라 읽을 수 없는 답으로 본다."""
    m = _ITEMS_RE.search(text)
    if m is None:
        return None
    region = m.group(1)
    labels = _LABEL_RE.findall(region)
    if text[m.end(1): m.end(1) + 1] == "]":                       # 목록이 닫혔다 — 그대로 읽는다(빈 목록은 해당 없음)
        return labels
    if not labels:
        return None
    last = labels[-1]
    complete = region[region.rfind(last) + len(last):].startswith('"')
    if not complete and any(v != last and v.startswith(last) for v in ok):
        return None
    return labels


def parse_single(text: str, valid: Sequence[str] | set[str]) -> list[str] | None:
    """답에서 고른 항 기호들을 읽는다(중복은 한 번). 읽을 수 없으면 None — 목록이 없거나, 보이지 않은 기호가 하나라도 있으면 읽을 수 없는 것으로 본다
    (확인 못 한 규정을 「해당」으로 내보내지 않는다)."""
    ok = set(valid)
    raw: Any = None
    for cand in (text, *(m.group(0) for m in _OBJ_RE.finditer(text or ""))):
        try:
            obj = json.loads(cand)
        except (TypeError, ValueError):
            continue
        if isinstance(obj, dict) and "items" in obj:
            raw = obj["items"]
            break
    else:
        raw = _labels_from_text(text or "", ok)
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for v in raw:
        if not isinstance(v, str) or v not in ok:
            return None
        if v not in out:
            out.append(v)
    return out


_executor_lock = threading.Lock()
_executors: dict[int, ThreadPoolExecutor] = {}


def shared_executor(max_workers: int) -> ThreadPoolExecutor:
    """프로세스 안에서 LLM 호출 **동시 수**를 묶는다(대기열은 묶지 않는다 — 마감을 넘긴 호출은 호출한 쪽이 취소한다). 프로세스마다 따로라 워커가 N개면 N배가 나간다."""
    n = max(1, int(max_workers))
    with _executor_lock:
        ex = _executors.get(n)
        if ex is None:
            ex = _executors[n] = ThreadPoolExecutor(max_workers=n, thread_name_prefix="reg-llm")
        return ex


def select_applicable(
    provider: Any,
    doc_text: str,
    candidates: Sequence[ClauseRec],
    *,
    limit: int = 1,
    doc_chars: int = 1500,
    timeout_s: float = 60.0,
    executor: ThreadPoolExecutor | None = None,
    skip_public: bool = True,
    mode: str = MODE_PER_CANDIDATE,
) -> Selection:
    """후보 조항 가운데 문서에 직접 적용되는 항을 LLM 으로 고른다. `mode` = single(문서를 한 번만 보이고 호출 1번) | per_candidate(후보마다 따로 + 문서 종류 1번)."""
    if mode == MODE_SINGLE:
        return _select_single(provider, doc_text, candidates, limit=limit, doc_chars=doc_chars, timeout_s=timeout_s, executor=executor,
                              skip_public=skip_public)
    return _select_per_candidate(provider, doc_text, candidates, limit=limit, doc_chars=doc_chars, timeout_s=timeout_s, executor=executor,
                                 skip_public=skip_public)


def _item_chars_budget(doc_chars: int) -> int:
    """한 번에 고르기 프롬프트에서 항 목록이 쓸 수 있는 글자 수 — 문서 앞부분(doc_chars)과 질문 틀을 뺀 나머지(최소 400자)."""
    return max(400, _SINGLE_PROMPT_CHARS - _SINGLE_FRAME_CHARS - max(1, int(doc_chars)))


def _prompt_blocks(candidates: Sequence[ClauseRec], budget: int | None = None) -> list[tuple[ClauseRec, list[SentenceRec]]]:
    """프롬프트에 넣을 (조항, 고를 수 있는 항들) — 조회 순위가 앞선 후보부터 항 상한(MAX_PROMPT_ITEMS)·조항 상한(MAX_PROMPT_CLAUSES)·
    글자 예산(`budget`, 항 목록 전체의 글자 수)까지. 예산을 넘는 항·후보는 뒤에서부터 빠진다(첫 조항의 첫 항은 예산을 넘어도 싣는다)."""
    blocks: list[tuple[ClauseRec, list[SentenceRec]]] = []
    total = 0
    used = 0
    for clause in candidates:
        if len(blocks) >= MAX_PROMPT_CLAUSES or total >= MAX_PROMPT_ITEMS:
            break
        sents = selectable(clause)[: MAX_PROMPT_ITEMS - total]          # 첫 후보가 상한보다 길어도 앞부분은 보인다
        cut = False
        if budget is not None and sents:
            fit: list[SentenceRec] = []
            cost = len(clause.article_no) + len(clause.title) + 6       # 「A. 제N조(제목)」 줄
            for s in sents:
                line = len(s.text) + 7                                  # 「  A1 …」 줄
                if (blocks or fit) and used + cost + line > budget:
                    break
                cost += line
                fit.append(s)
            cut = len(fit) < len(sents)
            sents = fit
            used += cost if fit else 0
        if sents:
            blocks.append((clause, sents))
            total += len(sents)
        if cut:
            break                                                       # 예산을 다 썼다 — 순위가 낮은 후보는 싣지 않는다
    return blocks


def _kind_gate(provider: Any, ex: ThreadPoolExecutor, doc_text: str, doc_chars: int, use_schema: bool, deadline: float, sel: Selection) -> bool:
    """문서 종류를 확인한다(고르기와 같은 SYSTEM 문구로 — 서버가 문서 앞부분 읽기를 재사용한다). 사내 문서(internal)이면 True.
    공개 자료이면 public_document, 종류를 못 읽으면 llm_unavailable 을 `sel` 에 적고 False."""
    sel.calls += 1
    fut = ex.submit(_ask_kind, provider, build_kind_prompt(doc_text, doc_chars), use_schema, SYSTEM)
    try:
        kind = parse_kind(fut.result(timeout=max(0.0, deadline - time.monotonic())))
    except Exception as exc:  # noqa: BLE001
        fut.cancel()                                                # 마감을 넘겼으면 아직 시작 안 한 호출은 버린다
        logger.warning("규정 LLM 문서 종류 판정 실패: %s", _why(exc))
        kind = None
    if kind == "internal":
        return True
    if kind is None:
        sel.failures += 1
        sel.reason = REASON_LLM_UNAVAILABLE
    else:
        sel.reason = REASON_PUBLIC_DOCUMENT
    return False


def _select_single(
    provider: Any,
    doc_text: str,
    candidates: Sequence[ClauseRec],
    *,
    limit: int,
    doc_chars: int,
    timeout_s: float,
    executor: ThreadPoolExecutor | None,
    skip_public: bool,
) -> Selection:
    """문서 종류를 먼저 확인하고(`skip_public`), 사내 문서이면 문서 앞부분과 후보 조항의 항 전체를 **한 번에** 보여 직접 적용되는 항의 기호
    (직접적인 순서, 최대 `limit`개)를 받는다. 공개 자료이면 고르는 호출을 하지 않는다. 호출은 최대 2번이다.

    · 항이 하나도 없으면(후보가 비었거나 고를 수 있는 항이 없음) → not_applicable (호출 없음)
    · 공개 자료(public) → public_document · 종류를 못 읽거나 답을 못 읽으면(오류·시간 초과·보이지 않은 기호) → llm_unavailable(안 보인다)
    """
    sel = Selection()
    blocks = _prompt_blocks(candidates, _item_chars_budget(doc_chars))
    if not blocks:
        sel.reason = REASON_NOT_APPLICABLE
        return sel
    ex = executor or shared_executor(6)
    use_schema = accepts_json_schema(provider)
    deadline = time.monotonic() + max(0.1, float(timeout_s))
    if skip_public and not _kind_gate(provider, ex, doc_text, doc_chars, use_schema, deadline, sel):
        return sel
    prompt, labels = build_single_prompt(doc_text, blocks, doc_chars, limit)
    sel.calls += 1
    fut = ex.submit(_generate, provider, SYSTEM, prompt, single_schema(list(labels), limit), use_schema, _SINGLE_TOKENS if use_schema else 200)
    try:
        picks = parse_single(fut.result(timeout=max(0.0, deadline - time.monotonic())), set(labels))
    except Exception as exc:  # noqa: BLE001 — 시간 초과·연결 오류·호출 실패 모두 「판정 못 함」
        fut.cancel()
        logger.warning("규정 LLM 판정 실패(한 번에 고르기): %s", _why(exc))
        picks = None
    if picks is None:
        sel.failures += 1
        sel.reason = REASON_LLM_UNAVAILABLE
        return sel
    for lab in picks:
        item = evidence_item(*labels[lab])
        if item not in sel.items:                                   # 같은 등급별 목록의 줄들은 한 항목으로 합쳐진다 — 합친 뒤에 limit 을 센다
            sel.items.append(item)
        if len(sel.items) >= max(1, limit):
            break
    if not sel.items:
        sel.reason = REASON_NOT_APPLICABLE
    return sel


def _select_per_candidate(
    provider: Any,
    doc_text: str,
    candidates: Sequence[ClauseRec],
    *,
    limit: int = 1,
    doc_chars: int = 1500,
    timeout_s: float = 60.0,
    executor: ThreadPoolExecutor | None = None,
    skip_public: bool = True,
) -> Selection:
    """후보를 조회 순위 순으로 보며 LLM 이 항을 고른 것을 `limit` 개까지 모은다. 후보마다 한 번씩 **동시에** 묻는다.

    · 어느 후보에서도 항을 못 골랐고 실패가 없으면 → not_applicable(정상 응답)
    · 어느 후보에서도 못 골랐고 실패가 하나라도 있으면 → llm_unavailable(판정이 안 섰다 — 보이지 않는다)
    · 일부가 실패해도 다른 후보에서 골랐으면 그것은 보인다(고른 것은 LLM 이 확인한 원문이다)
    · `skip_public` 이면 문서가 이미 공개된 외부 자료(판결문·법령·보도·공시)인지도 **같이** 묻는다 — 공개 자료이면 고른 것과 상관없이
      아무것도 안 보이고(public_document), 종류를 확인하지 못하면 안 보인다(llm_unavailable)
    """
    sel = Selection()
    if not candidates:
        sel.reason = REASON_NOT_APPLICABLE
        return sel
    ex = executor or shared_executor(6)
    use_schema = accepts_json_schema(provider)
    kind_fut: Future[str] | None = ex.submit(_ask_kind, provider, build_kind_prompt(doc_text, doc_chars), use_schema) if skip_public else None
    jobs: list[tuple[ClauseRec, list[SentenceRec], Future[str]]] = []
    for clause in candidates:
        sents = selectable(clause)
        jobs.append((clause, sents, ex.submit(_ask, provider, build_prompt(doc_text, clause, sents, doc_chars), use_schema)))
    sel.calls = len(jobs) + (1 if kind_fut is not None else 0)

    deadline = time.monotonic() + max(0.1, float(timeout_s))
    try:
        if kind_fut is not None:
            try:
                kind = parse_kind(kind_fut.result(timeout=max(0.0, deadline - time.monotonic())))
            except Exception as exc:  # noqa: BLE001
                logger.warning("규정 LLM 문서 종류 판정 실패: %s", _why(exc))
                kind = None
            if kind != "internal":
                for _, _, fut in jobs:
                    fut.cancel()
                if kind is None:
                    sel.failures += 1
                    sel.reason = REASON_LLM_UNAVAILABLE
                else:
                    sel.reason = REASON_PUBLIC_DOCUMENT
                return sel
        for clause, sents, fut in jobs:
            if len(sel.items) >= max(1, limit):
                fut.cancel()                         # 아직 시작 안 한 호출은 버린다
                continue
            try:
                choice = parse_choice(fut.result(timeout=max(0.0, deadline - time.monotonic())), len(sents))
            except Exception as exc:  # noqa: BLE001 — 시간 초과·연결 오류·호출 실패 모두 「판정 못 함」
                logger.warning("규정 LLM 판정 실패(%s): %s", clause.article_no, _why(exc))
                choice = None
            if choice is None:
                sel.failures += 1
                continue
            if choice >= 1:
                sel.items.append(evidence_item(clause, sents[choice - 1]))
        if not sel.items:
            sel.reason = REASON_LLM_UNAVAILABLE if sel.failures else REASON_NOT_APPLICABLE
        return sel
    finally:
        # 마감을 넘기거나 일찍 끝나도(공개 자료·limit 도달·시간 초과) 아직 시작 안 한 호출은 버린다 — 안 버리면 실행기 대기열에 남아
        # 뒤 요청을 막는다(독립 리뷰 R2·R3: 요청 5건이 헛호출 15건을 남겼다). 이미 도는 호출은 어댑터의 호출 한도가 끊는다.
        for f in (kind_fut, *(j[2] for j in jobs)):
            if f is not None:
                f.cancel()
