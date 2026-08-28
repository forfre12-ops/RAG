"""콘솔이 부르는 주소 ↔ 실제 API 라우트 ↔ e2e 본보기 응답, 세 가지를 맞물려 잠근다.

왜 필요한가.
  e2e 하니스(tests/e2e_console)는 화면이 눌리는지를 본다. 그런데 그 하니스가 쓰는 응답이
  실제 서버와 어긋나면, **화면 시험은 전부 통과하는데 실서버에서는 안 되는** 상태가 된다.
  종전에도 "문서 ↔ 문서" 점검만으로는 유령 계약을 못 잡았고(오류코드 계약), 콘솔이 부르는
  경로의 접두사를 잘못 읽어 404 를 오진한 적이 있다.

그래서 세 축을 한 번에 본다.
  A. 콘솔이 부르는 METHOD·경로가 FastAPI 라우트 표에 **실재하는가** (오타·삭제·접두사 오류)
  B. 그 경로가 e2e 본보기에 **있는가** (하니스가 모르는 경로를 콘솔이 부르고 있지 않은가)
  C. 본보기 응답이 그 엔드포인트의 **response_model 로 검증되는가** (필드명 드리프트)

PG·모델 없이 돈다 — 라우트 표와 스키마만 읽는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import BaseModel

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "src" / "koipa" / "api" / "static"
_FIXTURES_PATH = _ROOT / "tests" / "e2e_console" / "lib" / "fixtures.json"
_PREFIX = "/api/v1"

# 콘솔 소스 — 인라인 스크립트가 든 HTML 과 외부 스크립트 전부.
_CONSOLE_SOURCES = [
    "admin.html",
    "index.html",
    "golden_jobs.js",
    "deploy_badge.js",
    "app.js",
]

# 서버가 파이썬으로 그려 내려 주는 화면(후보 관리·검수 서명)은 static 파일이 아니다.
# 종전에는 이 셋이 스캔 밖이어서, 그 화면이 없는 경로를 부르거나 본보기가 빠져도
# A·B 축이 잡지 못했다. 뜨는 스크립트가 쓰는 렌더러를 그대로 불러 같이 본다.
_RENDERED_SCREENS = ["render_manage_html", "render_signoff_html_sample"]

# 본보기 화면의 주소에는 실제 잡 id 가 박혀 있다(/golden/jobs/ffffffff-…/signoff).
# 라우트 표는 그 자리를 경로 변수로 들고 있으므로 UUID 꼴 조각은 변수로 본다.
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# 화면 이동용 주소(브라우저가 직접 여는 HTML)는 fetch 계약이 아니라 링크다 — 별도 시험이 본다.
_HTML_SUFFIX = re.compile(r"\.html$")


# ── 라우트 표 ────────────────────────────────────────────────────────────────
def _norm(path: str) -> str:
    """경로 변수 이름을 지워 비교 가능한 형태로. /a/{job_id}/b → /a/{}/b"""
    return re.sub(r"\{[^}]*\}", "{}", path.rstrip("/") or "/")


@pytest.fixture(scope="module")
def routes() -> dict[tuple[str, str], object]:
    """(METHOD, 정규화 경로) → response_model(없으면 None). FastAPI 앱에서 직접 읽는다."""
    from koipa.api.app import app

    table: dict[tuple[str, str], object] = {}
    for included in app.routes:
        if type(included).__name__ != "_IncludedRouter":
            continue
        ctx = getattr(included, "include_context", None)
        prefix = getattr(ctx, "prefix", "") or ""
        for r in getattr(included, "original_router").routes:
            path = _norm(prefix + getattr(r, "path", ""))
            for m in getattr(r, "methods", None) or []:
                table[(m, path)] = getattr(r, "response_model", None)
    assert len(table) > 40, f"라우트를 제대로 못 읽었다({len(table)}건)"
    return table


# ── 콘솔이 부르는 주소 뽑기 ───────────────────────────────────────────────────
_API_CALL = re.compile(
    r"""api\(\s*['"](GET|POST|PUT|PATCH|DELETE)['"]\s*,\s*(?:'([^']*)'|`([^`]*)`|"([^"]*)")""",
    re.X,
)
# 직접 fetch — 첫 인자에서 주소를 뽑고, 뒤따르는 옵션에서 method 를 읽는다(없으면 GET).
_FETCH = re.compile(
    # 옵션은 **넉넉히** 붙잡는다. 비탐욕(.{0,200}?)으로 두면 `{` 한 글자만 잡혀
    # method 를 못 읽고 전부 GET 으로 오인한다(실제로 그렇게 오탐이 났다).
    r"""fetch\(\s*(?:`([^`]*)`|'([^']*)'|"([^"]*)")\s*(?:,\s*(\{.{0,240}))?""",
    re.S,
)
# apiUrl 은 따옴표 문자열과 템플릿 리터럴 둘 다 받는다 — 후자를 못 읽으면
# `/classify/jobs/${id}` 같은 호출이 스캔에서 통째로 빠진다.
_APIURL = re.compile(r"""apiUrl\(\s*(?:["']([^"']+)["']|`([^`]+)`)""")


def _clean(raw: str) -> str | None:
    """`${c.base}/golden/jobs/${encodeURIComponent(id)}?x=1` → `/golden/jobs/{}`"""
    p = raw.strip()
    p = p.split("?")[0].split("#")[0]
    p = p.replace("${c.base}", "").replace("${cfg().base}", "")
    p = re.sub(r"\$\{[^}]*\}", "{}", p)
    p = _UUID.sub("{}", p)
    if p.startswith(_PREFIX):
        p = p[len(_PREFIX):]
    if not p.startswith("/"):
        return None                      # 상대 자원(./demo_docs/...) 등은 API 가 아니다
    if p.endswith("+"):                  # `'/admin/keywords?'+qs` 형태의 잔재
        p = p[:-1]
    return _norm(p)


# ── 서버 렌더 화면의 호출 뽑기 ────────────────────────────────────────────────
# 이 화면들은 주소를 상수와 이어 붙여 만든다(`req(api+'/'+encodeURIComponent(id)+'/decision')`).
# 정규식 하나로는 못 읽으므로, 상수를 먼저 모으고 인자식을 괄호 균형을 지키며 떼어 낸 뒤
# 항 단위로 풀어 준다. 풀리지 않는 항이 하나라도 있으면 그 호출은 버린다(오탐 금지).
_CONST = re.compile(r"""(?:const|var|let)\s+([A-Za-z_$][\w$]*)\s*=\s*["']([^"']*)["']""")
_STRING_TERM = re.compile(r"""^["'](.*)["']$""", re.S)
# 주소를 만들어 돌려주는 무인자 도우미 — 이름을 상수 이름으로 바꿔 읽는다.
_URL_HELPERS = {"qurl()": "api"}


def _balanced(src: str, open_idx: int) -> tuple[str, str]:
    """`(` 위치에서 시작해 첫 인자식과 나머지 인자들을 통째로 돌려준다."""
    depth, quote, first_end = 0, "", -1
    i = open_idx
    while i < len(src):
        ch = src[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
        elif ch in "\"'`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                head = src[open_idx + 1: first_end if first_end > 0 else i]
                tail = src[first_end + 1: i] if first_end > 0 else ""
                return head, tail
        elif ch == "," and depth == 1 and first_end < 0:
            first_end = i
        i += 1
    return "", ""


def _strip_encode(expr: str) -> str:
    """encodeURIComponent(무엇이든) → '{}' (경로 변수 한 조각)."""
    while True:
        i = expr.find("encodeURIComponent(")
        if i < 0:
            return expr
        inner, _ = _balanced(expr, i + len("encodeURIComponent") )
        end = i + len("encodeURIComponent(") + len(inner) + 1
        expr = expr[:i] + "'{}'" + expr[end:]


def _resolve(expr: str, consts: dict[str, str]) -> str | None:
    """`api+'/'+encodeURIComponent(id)+'/decision'` → `/api/v1/golden/candidates/{}/decision`"""
    expr = _strip_encode(expr).strip()
    for helper, name in _URL_HELPERS.items():
        expr = expr.replace(helper, name)
    out = []
    for term in expr.split("+"):
        term = term.strip()
        m = _STRING_TERM.match(term)
        if m:
            out.append(m.group(1))
        elif term in consts:
            out.append(consts[term])
        else:
            return None                  # 런타임 값 — 이 호출은 읽을 수 없다
    return "".join(out)


def _rendered_calls() -> set[tuple[str, str]]:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_dump_console_html", _ROOT / "scripts" / "dump_console_html.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    found: set[tuple[str, str]] = set()
    for fn_name in _RENDERED_SCREENS:
        src = getattr(mod, fn_name)()
        consts = {k: v for k, v in _CONST.findall(src)}
        for m in re.finditer(r"\b(?:fetch|req)\(", src):
            head, tail = _balanced(src, m.end() - 1)
            raw = _resolve(head, consts)
            if not raw:
                continue
            p = _clean(raw)
            if not p or _HTML_SUFFIX.search(p):
                continue
            mm = re.search(r"method\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", tail[:240])
            found.add(((mm.group(1) if mm else "GET"), p))
    return found


def _console_calls() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    for name in _CONSOLE_SOURCES:
        src = (_STATIC / name).read_text(encoding="utf-8")
        for m in _API_CALL.finditer(src):
            method = m.group(1)
            raw = m.group(2) or m.group(3) or m.group(4) or ""
            p = _clean(raw)
            if p:
                found.add((method, p))
        for m in _FETCH.finditer(src):
            raw = m.group(1) or m.group(2) or m.group(3) or ""
            opts = m.group(4) or ""
            if not raw or "${" not in raw and not raw.startswith(("/", "http")) and "base" not in raw:
                continue
            p = _clean(raw)
            if not p or _HTML_SUFFIX.search(p):
                continue
            mm = re.search(r"method\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", opts)
            found.add(((mm.group(1) if mm else "GET"), p))
        for m in _APIURL.finditer(src):
            p = _clean(m.group(1) or m.group(2) or "")
            if p:
                # app.js 의 apiUrl 은 호출부에서 method 를 준다 — 주변에서 찾는다.
                head = src[max(0, m.start() - 40): m.start()]
                tail = src[m.end(): m.end() + 300]
                if "postSSE(" in head:
                    found.add(("POST", p))        # SSE 헬퍼는 언제나 POST 다(sse.js)
                    continue
                mm = re.search(r"method\s*:\s*['\"](GET|POST|PUT|PATCH|DELETE)['\"]", tail)
                found.add(((mm.group(1) if mm else "GET"), p))
    # 순수 화면 이동(HTML)은 계약 대상이 아니다.
    return {(m, p) for m, p in found if not _HTML_SUFFIX.search(p) and p != "/"}


@pytest.fixture(scope="module")
def console_calls() -> set[tuple[str, str]]:
    calls = _console_calls() | _rendered_calls()
    assert len(calls) >= 25, f"콘솔 호출을 제대로 못 뽑았다({len(calls)}건): {sorted(calls)}"
    return calls


def test_rendered_screens_are_scanned():
    """서버 렌더 화면에서 실제로 호출을 뽑았는지부터 본다 — 0건이면 A·B 축이 조용히 빈다."""
    calls = _rendered_calls()
    # 후보 관리 7개(세션·목록·상세·결정·출처·원장·업로드) + 서명 3개(세션·점검·제출).
    assert len(calls) >= 9, f"서버 렌더 화면에서 뽑은 호출이 너무 적다({len(calls)}건): {sorted(calls)}"
    assert ("POST", "/golden/candidates/{}/decision") in calls, sorted(calls)
    assert ("POST", "/golden/jobs/{}/signoff") in calls, sorted(calls)


@pytest.fixture(scope="module")
def fixtures() -> dict[str, object]:
    data = json.loads(_FIXTURES_PATH.read_text(encoding="utf-8"))
    data.pop("_note", None)
    return data


# ── A. 콘솔이 부르는 주소가 실재하는가 ────────────────────────────────────────
def test_every_console_endpoint_exists_on_the_server(routes, console_calls):
    """콘솔에 적힌 주소 중 서버에 없는 것 = 그 버튼은 실서버에서 404 다."""
    missing = sorted(f"{m} {_PREFIX}{p}" for m, p in console_calls if (m, _PREFIX + p) not in routes)
    assert not missing, (
        "콘솔이 부르는데 서버에 없는 경로:\n  " + "\n  ".join(missing)
        + "\n(경로를 고치거나, 없어진 기능이면 화면에서 함께 걷어낼 것)"
    )


# ── B. e2e 본보기가 그 주소를 덮는가 ──────────────────────────────────────────
def test_e2e_fixtures_cover_every_console_endpoint(console_calls, fixtures):
    """본보기에 없는 경로는 하니스가 404 로 답한다 — 시나리오가 실제를 못 본다."""
    covered = {(k.split(" ", 1)[0], _norm(k.split(" ", 1)[1])) for k in fixtures}
    uncovered = sorted(f"{m} {p}" for m, p in console_calls if (m, p) not in covered)
    assert not uncovered, (
        "콘솔이 부르는데 e2e 본보기에 없는 경로:\n  " + "\n  ".join(uncovered)
        + f"\n({_FIXTURES_PATH.relative_to(_ROOT)} 에 응답 본보기를 추가할 것)"
    )


def test_no_stale_fixtures(routes, fixtures):
    """반대 방향 — 서버에 없는 경로의 본보기를 들고 있으면 그것도 드리프트다."""
    stale = sorted(
        key for key in fixtures
        if (key.split(" ", 1)[0], _norm(_PREFIX + key.split(" ", 1)[1])) not in routes
    )
    assert not stale, "서버에 없는 경로의 본보기:\n  " + "\n  ".join(stale)


# ── C. 본보기 응답이 실제 스키마로 검증되는가 ─────────────────────────────────
def _fixture_items(fixtures, routes):
    for key, body in fixtures.items():
        method, path = key.split(" ", 1)
        model = routes.get((method, _norm(_PREFIX + path)))
        yield key, body, model


def test_fixtures_validate_against_response_models(fixtures, routes):
    """필드명이 실제 응답 모델과 어긋나면 여기서 깨진다(evidence 의 type/excerpt 같은 것)."""
    problems: list[str] = []
    checked = 0
    for key, body, model in _fixture_items(fixtures, routes):
        if not (isinstance(model, type) and issubclass(model, BaseModel)):
            continue
        checked += 1
        try:
            model.model_validate(body)
        except Exception as exc:  # noqa: BLE001 — 어느 본보기가 왜 틀렸는지 모아서 보고한다
            problems.append(f"{key} ({model.__name__}): {str(exc)[:400]}")
    assert checked >= 15, f"검증한 본보기가 너무 적다({checked}건) — 모델 연결이 끊겼는지 확인"
    assert not problems, "본보기가 실제 응답 모델과 맞지 않는다:\n  " + "\n  ".join(problems)


def test_fixtures_without_response_model_are_listed(fixtures, routes):
    """response_model 이 없는 엔드포인트는 자동 검증이 안 된다 — 사실을 드러내 둔다.

    (여기서 실패시키지 않는다. 다만 목록이 늘어나면 그만큼 손으로 맞춰야 할 곳이 늘어난 것이다.)
    """
    unbound = sorted(
        key for key, _body, model in _fixture_items(fixtures, routes)
        if not (isinstance(model, type) and issubclass(model, BaseModel))
    )
    # 자동 검증이 안 되는 것은 지금 이만큼이다. 늘어나면 이 시험이 알려 준다.
    # (2026-08-23: 후보 관리 화면 시나리오를 붙이며 /golden/candidates 계열 5개가 늘었다.
    #  전부 dict 반환이라 본보기를 손으로 맞춰야 한다 — 실제로 그때 provenance 응답의
    #  필수 필드를 빠뜨렸고 이 파일의 다른 시험이 그것을 잡았다.)
    assert len(unbound) <= 11, (
        "response_model 없이 dict 를 돌려주는 엔드포인트가 늘었다 — 본보기 드리프트를"
        " 자동으로 못 잡는다:\n  " + "\n  ".join(unbound)
    )


# ── D. 본보기의 **값**이 서버가 실제로 내는 값인가 ────────────────────────────
# 왜(2026-08-24). 위 C 는 필드 **이름**만 본다. 타입이 str 이면 글자 내용은 아무거나
# 통과한다. 실제로 `/golden/jobs` 본보기가 `kind:"register"` 였는데 서버는
# `golden_register` 를 낸다. 화면은 그 값으로 분기하므로(golden_jobs.js) 하니스에서는
# 등록 잡이 "후보 생성" 으로 잘못 그려졌고, 시나리오는 통과했다. 값이 곧 계약인 자리를
# 여기서 못 박는다 — 열거형처럼 쓰이는 문자열과 서버가 정한 기본값.


def test_job_kind_values_match_what_the_server_emits(fixtures):
    """잡 종류 문자열 — 화면이 이 값으로 「문서 묶음 등록 / 후보 생성」을 가른다."""
    from koipa.api.golden import golden_job_list  # noqa: F401 — 라우트 로딩 확인용

    allowed = {"golden_build", "golden_register"}
    jobs = fixtures["GET /golden/jobs"]["jobs"]
    bad = [j.get("kind") for j in jobs if j.get("kind") not in allowed]
    assert not bad, (
        f"서버가 내지 않는 kind 값이 본보기에 있다: {bad} — 허용: {sorted(allowed)}"
        " (golden.py 의 필터·golden_build_service 의 payload 와 같은 글자여야 한다)"
    )


def test_job_list_ordering_default_matches_the_schema(fixtures):
    """`ordering` 은 서버가 고정으로 내려주는 값이다 — 본보기가 다른 값이면 화면의
    '재정렬했다' 안내 분기가 시험에서 한 번도 안 켜진다."""
    from koipa.schemas.golden import GoldenJobListResponse

    server_default = GoldenJobListResponse(jobs=[]).ordering
    assert fixtures["GET /golden/jobs"]["ordering"] == server_default


def test_registerable_build_paths_are_actually_listable(fixtures):
    """목록 본보기의 경로가 실서버 목록 규칙을 통과하는가.

    종전 본보기는 `datasets/proxy_gold/build_*.jsonl` 이었다 — 실서버 목록이 절대 낼 수
    없는 경로다(허용 폴더 밖). 그 경로로 등록 흐름 전체를 시험하고 있었다.
    """
    from koipa.services.golden_build_service import _is_review_source

    bad = [b["path"] for b in fixtures["GET /golden/builds"]["builds"]
           if not _is_review_source(b["path"])]
    assert not bad, (
        f"실서버 목록이 낼 수 없는 경로가 본보기에 있다: {bad}"
        " (golden_build_service._REVIEW_SOURCE_DIRS 아래여야 한다)"
    )


# ── 화면 이동 링크 ───────────────────────────────────────────────────────────
def test_console_html_links_point_at_real_routes(routes):
    """공용 메뉴가 가리키는 서버 렌더 화면(.html)이 실재하는 라우트인가."""
    missing = []
    for name in ("admin.html", "index.html"):
        src = (_STATIC / name).read_text(encoding="utf-8")
        for href in re.findall(r'class="cnav-link"[^>]*href="([^"]+)"', src):
            target = href.split("#")[0]
            if not target.startswith(_PREFIX):
                continue                                  # /console/* 는 정적 마운트
            if ("GET", _norm(target)) not in routes:
                missing.append(f"{name}: {href}")
    assert not missing, "메뉴가 가리키는 서버 화면이 라우트 표에 없다:\n  " + "\n  ".join(missing)
