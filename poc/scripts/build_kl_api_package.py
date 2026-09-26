#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""KL 에 드리는 API 연동 자료를 규약서에서 만든다 — 기계판독 규격(yaml)과 사람용 안내서(html).

■ 무엇을 만드는가

  doc/03_openapi_koipa_kl.yaml (전체 규약서, 정본) 에서 `x-audience: kl` 인 작업만 골라
    · koipa_kl_openapi.yaml     KL 이 호출하는 5개 인터페이스(IF-01·02·03·05·06)만 담은 OpenAPI
    · KL_API_연동_안내서.html    호출 순서·예시·오류 처리·제한값. 필드 표는 규약서에서 그대로 뽑는다
  를 출력 폴더에 쓴다. 예시 응답은 손으로 적지 않고 `samples/*.json` 을 읽는다 — 그 파일들은
  실제 엔진에 요청을 보내 받은 응답이다(다시 만들 때도 같은 방식으로 받아 교체한다).

■ 왜 손으로 쓰지 않는가

  전체 규약서는 한 번 코드와 어긋난 채 KL 에 나간 적이 있다(작업 조회 응답의 result↔results 등,
  tests/test_kl_openapi_schema_matches_code.py 머리말 참고). 그 시험이 규약서↔코드를 묶고,
  이 스크립트가 규약서↔안내서를 묶는다 — 규약서가 바뀌면 test_kl_api_package.py 가 산출물이
  낡았다고 알린다.

■ 사용

    python scripts/build_kl_api_package.py                 # 출력 폴더에 쓴다
    python scripts/build_kl_api_package.py --check         # 쓰지 않고 산출물이 최신인지만 본다(어긋나면 종료코드 1)

산출물은 문서라 소스 이미지·번들에는 넣지 않는다(폴더는 doc/result/ 아래).
CSS 와 머리띠(로고 포함)는 이미 KL 에 나간 회신서(doc/result/KL_회신_2026-08-28/)에서 그대로 가져온다.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import re
import sys
from pathlib import Path

import yaml

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]                                   # 저장소 루트
SPEC = ROOT / "doc" / "03_openapi_koipa_kl.yaml"
OUT_DIR = ROOT / "doc" / "result" / "KL_API_연동안내_2026-09-25"
STYLE_SOURCE = ROOT / "doc" / "result" / "KL_회신_2026-08-28" / "KL_API_통신방안_검토회신.html"

YAML_NAME = "koipa_kl_openapi.yaml"
GUIDE_NAME = "KL_API_연동_안내서.html"
DATE = "2026-09-25"

# 회신서(IF-01~IF-06)의 번호. IF-04 는 엔진이 KL 로 보내는 통보라 규약서 경로가 없다.
KL_INTERFACES: dict[tuple[str, str], str] = {
    ("get", "/healthz"): "IF-01",
    ("post", "/documents"): "IF-02",
    ("post", "/classify/async"): "IF-03",
    ("get", "/classify/jobs/{job_id}"): "IF-05",
    ("get", "/classify/{doc_id}"): "IF-06",
}
HTTP_METHODS = ("get", "post", "put", "delete", "patch")
GRADE_NAMES = {"TS": "특급기밀", "S1": "1급 비밀", "S2": "2급 대외비", "S3": "3급 공개"}

SAMPLES = {
    "upload": "01_documents_response.json",
    "async": "02_classify_async_response.json",
    "job": "03_job_done.json",
    "doc": "04_classify_by_doc_response.json",
}

KL_INFO_DESCRIPTION = """한국지식재산보호원 AI 영업비밀관리시스템 — KL 웹시스템이 호출하는 5개 인터페이스만 담은 규격이다.
전체 규약서에서 자동으로 만든 것이라 직접 고치지 않는다.

  IF-01  GET  /healthz                    준비 상태 확인
  IF-02  POST /documents                  문서 등록(multipart) -> doc_id
  IF-03  POST /classify/async             비동기 분류 요청(callback_url 은 선택)
  IF-04  POST {callback_url}              엔진 -> KL 완료 통보(선택, POST /classify/async 의 callback_url 참고)
  IF-05  GET  /classify/jobs/{job_id}     작업 상태·결과 조회
  IF-06  GET  /classify/{doc_id}          문서 단위 최근 결과 조회

호출 순서와 예시는 함께 드리는 「AI 분류 엔진 연동 안내서」에 있다.
인증은 서버 간 호출 기준 X-API-Key 헤더다(/healthz 는 인증 없음).
오류는 HTTP 상태 코드로 구분한다. 본문은 대부분 {"detail": "사유"} 이고(422 는 항목별 배열),
심볼릭 code 필드는 413·429·500 에만 있다.
"""


# ─────────────────────────────────────────────────────────────────────────────
# 규약서 → KL 전용 OpenAPI
# ─────────────────────────────────────────────────────────────────────────────


def load_spec(path: Path = SPEC) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def kl_operations(spec: dict) -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for path, item in spec["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS and op.get("x-audience") == "kl"
    )


def _refs(node, found: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/"):
                _, _, section, name = value.split("/", 3)
                found.add((section, name))
            else:
                _refs(value, found)
    elif isinstance(node, list):
        for value in node:
            _refs(value, found)


def _reachable(spec: dict, roots: dict) -> set[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    todo: set[tuple[str, str]] = set()
    _refs(roots, todo)
    while todo:
        section, name = todo.pop()
        if (section, name) in seen:
            continue
        seen.add((section, name))
        _refs(spec["components"][section][name], todo)
    return seen


def subset(spec: dict) -> dict:
    """`x-audience: kl` 작업만 남기고, 그 작업이 쓰는 컴포넌트만 남긴다."""
    found = kl_operations(spec)
    if set(found) != set(KL_INTERFACES):
        raise SystemExit(
            "규약서의 KL 작업이 IF 번호 표(KL_INTERFACES)와 다르다 — "
            f"규약서에만 {sorted(set(found) - set(KL_INTERFACES))} · 표에만 {sorted(set(KL_INTERFACES) - set(found))}"
        )

    paths: dict = {}
    for path, item in spec["paths"].items():
        kept = {m: copy.deepcopy(op) for m, op in item.items() if (m, path) in KL_INTERFACES}
        if kept:
            for method, op in kept.items():
                op["x-interface"] = KL_INTERFACES[(method, path)]
            paths[path] = kept

    used = _reachable(spec, paths)
    components: dict = {}
    for section, entries in spec["components"].items():
        if section == "securitySchemes":
            components[section] = copy.deepcopy(entries)
            continue
        picked = {name: copy.deepcopy(body) for name, body in entries.items() if (section, name) in used}
        if picked:
            components[section] = picked

    tags_used = {t for item in paths.values() for op in item.values() for t in op.get("tags", [])}
    out: dict = {
        "openapi": spec["openapi"],
        "info": {
            "title": "Koipa AI Engine API — KL 연동 인터페이스",
            "description": KL_INFO_DESCRIPTION,
            "version": spec["info"]["version"],
            "contact": spec["info"].get("contact", {}),
        },
        "servers": copy.deepcopy(spec["servers"]),
        "security": copy.deepcopy(spec["security"]),
        "tags": [t for t in spec.get("tags", []) if t["name"] in tags_used],
        "paths": paths,
        "components": components,
    }
    return out


class _BlockDumper(yaml.SafeDumper):
    """여러 줄 문자열을 `|` 블록으로 쓴다(따옴표 안에 \\n 을 끼워 넣으면 사람이 못 읽는다)."""


def _present_str(dumper: yaml.SafeDumper, data: str):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


_BlockDumper.add_representer(str, _present_str)


def render_yaml(data: dict) -> str:
    header = (
        "# 자동 생성 파일 — 전체 규약서(doc/03_openapi_koipa_kl.yaml)에서 KL 이 호출하는 작업만 추렸다.\n"
        "# 직접 고치지 않는다. 만드는 곳: poc/scripts/build_kl_api_package.py\n"
    )
    body = yaml.dump(data, Dumper=_BlockDumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=110)
    return header + body


# ─────────────────────────────────────────────────────────────────────────────
# 스키마 → 필드 표
# ─────────────────────────────────────────────────────────────────────────────


def _resolve(spec: dict, node: dict) -> dict:
    for _ in range(20):
        if not (isinstance(node, dict) and "$ref" in node):
            return node
        node = spec["components"]["schemas"][node["$ref"].rsplit("/", 1)[1]]
    raise ValueError("$ref 가 20단계를 넘게 이어진다")


def flat(spec: dict, node: dict) -> tuple[dict, set[str]]:
    node = _resolve(spec, node)
    props: dict = {}
    required: set[str] = set()
    for part in node.get("allOf", []):
        p, r = flat(spec, part)
        props.update(p)
        required |= r
    props.update(node.get("properties") or {})
    required |= set(node.get("required") or [])
    return props, required


def _named(node: dict) -> str | None:
    """스키마 참조 이름. `allOf: [$ref]` 한 개짜리(nullable 참조를 3.0 에서 쓰는 모양)도 참조로 본다."""
    if "$ref" in node:
        return node["$ref"].rsplit("/", 1)[1]
    parts = node.get("allOf")
    if parts and len(parts) == 1 and "$ref" in parts[0]:
        return parts[0]["$ref"].rsplit("/", 1)[1]
    return None


def _describe(spec: dict, node: dict) -> str:
    """필드 설명 — 속성 자신의 설명을 먼저, 없으면 참조한 스키마의 설명."""
    text = node.get("description")
    if not text:
        name = _named(node)
        if name:
            text = spec["components"]["schemas"][name].get("description")
    return _clean(text)


def _type_label(spec: dict, node: dict) -> str:
    name = _named(node)
    if name:
        target = spec["components"]["schemas"][name]
        base = ("string (" + " | ".join(str(v) for v in target["enum"]) + ")") if target.get("enum") else f"객체 ({name})"
        return base + (" · null 가능" if node.get("nullable") else "")
    if "oneOf" in node:
        return " 또는 ".join(_type_label(spec, n) for n in node["oneOf"])
    kind = node.get("type", "object")
    if kind == "array":
        item = node.get("items", {})
        item_name = _named(item)
        return (f"{item_name} 객체 배열" if item_name else _type_label(spec, item) + " 배열")
    if node.get("format") == "binary":
        return "파일"
    if node.get("enum"):
        return "string (" + " | ".join(str(v) for v in node["enum"]) + ")"
    label = {"integer": "정수", "number": "숫자", "boolean": "true/false", "string": "문자열", "object": "객체"}.get(kind, kind)
    if node.get("nullable"):
        label += " · null 가능"
    return label


def _clean(text) -> str:
    return " ".join(str(text or "").split())


def _inline(text: str) -> str:
    """설명 문자열을 HTML 로. 규약서가 쓰는 **굵게** 와 `코드` 만 옮긴다."""
    out = html.escape(text)
    out = re.sub(r"[*][*](.+?)[*][*]", lambda m: "<b>" + m.group(1) + "</b>", out)
    return re.sub(r"[`]([^`]+)[`]", lambda m: "<code>" + m.group(1) + "</code>", out)


def field_table(spec: dict, schema: dict, *, overrides: dict[str, str] | None = None, request: bool = False) -> str:
    """스키마의 필드를 표로. request=True 면 '· null 가능' 을 뺀다(요청에서는 안 보내는 것과 같다)."""
    props, required = flat(spec, schema)
    overrides = overrides or {}
    head = ["필드", "형식", "필수", "설명"]
    rows = []
    for name, node in props.items():
        desc = overrides.get(name) or _describe(spec, node)
        kind = _type_label(spec, node)
        if request:
            kind = kind.replace(" · null 가능", "")
        cells = [f"<code>{html.escape(name)}</code>", html.escape(kind),
                 "필수" if name in required else "선택", _inline(desc)]
        rows.append(cells)
    return _table(head, rows, center={2}, widths=[22, 20, 7, 51])


def _table(head: list[str], rows: list[list[str]], *, center: set[int] | None = None, widths: list[int] | None = None) -> str:
    center = center or set()
    out = ['<div class="tw"><table>', "<thead><tr>"]
    for i, h in enumerate(head):
        width = f' style="width:{widths[i]}%"' if widths else ""
        out.append(f"<th{width}>{h}</th>")
    out.append("</tr></thead><tbody>")
    for row in rows:
        out.append("<tr>" + "".join(f'<td class="c">{c}</td>' if i in center else f"<td>{c}</td>" for i, c in enumerate(row)) + "</tr>")
    out.append("</tbody></table></div>")
    return "".join(out)


def _pre(text: str) -> str:
    return f"<pre>{html.escape(text)}</pre>"


def _json_pre(data) -> str:
    return _pre(json.dumps(data, ensure_ascii=False, indent=2))


def _details(summary: str, inner: str) -> str:
    return f"<details><summary>{html.escape(summary)}</summary>{inner}</details>"


# ─────────────────────────────────────────────────────────────────────────────
# 안내서
# ─────────────────────────────────────────────────────────────────────────────


def _shell() -> tuple[str, str]:
    """회신서에서 CSS 와 머리띠를 가져온다. 머리띠 문구만 바꾼다."""
    if not STYLE_SOURCE.exists():
        raise SystemExit(f"양식 원본이 없다: {STYLE_SOURCE}")
    text = STYLE_SOURCE.read_text(encoding="utf-8")
    style = re.search(r"<style[^>]*>(.*?)</style>", text, re.S)
    nav = re.search(r'<nav class="nav">.*?</nav>', text, re.S)
    if not style or not nav:
        raise SystemExit("회신서에서 style/nav 를 못 찾았다 — 양식 원본이 바뀌었는지 확인할 것")
    nav_html = re.sub(r'(<span class="brand-sub">).*?(</span>)', r"\1AI 분류 엔진 연동 안내서\2", nav.group(0), flags=re.S)
    nav_html = re.sub(r'(<span class="nav-badge">).*?(</span>)', r"\1연동 안내\2", nav_html, flags=re.S)
    # 한글 윈도우의 기본 고정폭 글꼴(굴림체)은 역슬래시(\)를 원화 기호(₩)로 그려 curl 줄이음이 ₩ 로 보인다
    # (헤드리스 브라우저 캡처로 확인). 글자는 그대로 \ 라 복사는 되지만 읽는 사람이 헷갈리므로 글꼴 순서를 정한다.
    extra = (
        "details{margin:8px 0 8px 18px}summary{cursor:pointer;font-size:12.5px;color:var(--dim)}"
        "details pre{margin-left:0}"
        'pre,code,.flow{font-family:Consolas,"Cascadia Mono","D2Coding",Menlo,"Courier New",monospace}'
    )
    return style.group(1) + extra, nav_html


def _sample(samples: dict[str, object], key: str):
    if key not in samples:
        raise SystemExit(f"예시 응답이 없다: samples/{SAMPLES[key]}")
    return samples[key]


def render_guide(spec: dict, samples: dict[str, object]) -> str:
    schemas = spec["components"]["schemas"]
    upload = _sample(samples, "upload")
    accepted = _sample(samples, "async")
    job = _sample(samples, "job")
    by_doc = _sample(samples, "doc")
    doc_id = upload["doc_id"]
    job_id = accepted["job_id"]
    if set(schemas["Grade"]["enum"]) != set(GRADE_NAMES):
        raise SystemExit("등급 코드가 규약서와 안내서의 이름 표(GRADE_NAMES)에서 다르다")

    upload_form = spec["paths"]["/documents"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]
    servers = spec["servers"][0]["url"]

    def h2(text: str) -> str:
        return f"<h2>{html.escape(text)}</h2>"

    def h3(text: str) -> str:
        return f"<h3>{html.escape(text)}</h3>"

    def p(text: str) -> str:
        return f"<p>{text}</p>"

    def note(text: str) -> str:
        return f'<div class="note">{text}</div>'

    def ul(items: list[str]) -> str:
        return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"

    b: list[str] = []

    # ── 머리 ────────────────────────────────────────────────────────────
    b.append('<div class="top"><div class="eyebrow">연계 규약 · 호출 안내</div>'
             "<h1>AI 분류 엔진 연동 안내서</h1>"
             f'<div class="meta">KL 웹시스템 → 분류 엔진 호출 규격과 예시 · {DATE} · 엔진 모델 {html.escape(str(job["results"][0]["model_version"]))} 기준</div></div>')
    b.append('<table class="docinfo"><tbody>'
             "<tr><th>대상</th><td>KL 웹시스템 개발자</td></tr>"
             "<tr><th>범위</th><td>KL 이 호출하는 5개 인터페이스(IF-01 · 02 · 03 · 05 · 06)와, 엔진이 KL 로 보내는 선택 통보(IF-04)</td></tr>"
             "<tr><th>근거</th><td>엔진에 직접 요청을 보내 확인한 결과와 소스 코드. 이 안내서의 예시 응답은 모두 실행 결과입니다</td></tr>"
             f"<tr><th>함께 드리는 파일</th><td><code>{YAML_NAME}</code> (OpenAPI 3.0.3, 위 인터페이스만 수록) · <code>samples/</code> (예시 응답 JSON)</td></tr>"
             "</tbody></table>")
    b.append(note("문서를 <b>등록</b>해 <code>doc_id</code> 를 받고(①), 그 <code>doc_id</code> 로 <b>분류를 요청</b>한 뒤(②), "
                  "<b>작업 조회</b>로 결과를 받습니다(③). 인증은 <code>X-API-Key</code> 헤더 하나입니다."))

    # ── 1. 호출 순서 ────────────────────────────────────────────────────
    b.append(h2("1. 호출 순서"))
    b.append(_table(
        ["순서", "KL → 엔진", "엔진 응답", "다음 동작"],
        [
            ["①", "<code>POST /api/v1/documents</code><br>파일 + actor (IF-02)", "201 · <code>doc_id</code>", "<code>doc_id</code> 를 저장합니다"],
            ["②", "<code>POST /api/v1/classify/async</code><br><code>{\"doc_id\": …}</code> (IF-03)", "202 · <code>job_id</code>", "작업 조회를 시작합니다(또는 <code>callback_url</code> 지정)"],
            ["③", "<code>GET /api/v1/classify/jobs/{job_id}</code> (IF-05)", "200 · <code>status</code>", "<code>done</code> 이면 <code>results[0]</code> 에 결과가 있습니다. <code>queued</code> 면 잠시 뒤 다시 조회합니다"],
            ["④", "<code>GET /api/v1/classify/{doc_id}</code> (IF-06)", "200 · 저장된 결과", "이후 언제든 등급과 사람이 확정한 등급을 조회합니다"],
        ],
        center={0}, widths=[7, 33, 20, 40],
    ))
    b.append(p("①과 ②를 한 번에 하려면 등록 요청에 <code>enqueue_classification=true</code> 를 넣습니다. "
               "응답의 <code>classification_job_id</code> 로 ③을 바로 진행합니다."))
    b.append(_table(
        ["IF", "호출", "용도", "비고"],
        [
            ["IF-01", "<code>GET /api/v1/healthz</code>", "준비 상태 확인", "인증 없음"],
            ["IF-02", "<code>POST /api/v1/documents</code>", "문서 등록(multipart) → <code>doc_id</code>", "분류의 전제입니다"],
            ["IF-03", "<code>POST /api/v1/classify/async</code>", "비동기 분류 요청", "분당 60건 한도"],
            ["IF-04", "<code>POST {callback_url}</code>", "엔진 → KL 완료·실패 통보", "<code>callback_url</code> 을 보낸 경우에만 발생합니다(7장)"],
            ["IF-05", "<code>GET /api/v1/classify/jobs/{job_id}</code>", "작업 상태·결과 조회", "결과 전체(근거·경고 포함)는 여기와 IF-04 통보에 있습니다"],
            ["IF-06", "<code>GET /api/v1/classify/{doc_id}</code>", "문서 단위 최근 결과 조회", "저장된 요약과 사람이 확정한 등급을 돌려줍니다"],
        ],
        widths=[8, 34, 30, 28],
    ))

    # ── 2. 접속과 인증 ──────────────────────────────────────────────────
    b.append(h2("2. 접속과 인증"))
    b.append(ul([
        f"주소 형식은 <code>http://&lt;엔진 주소&gt;:8000/api/v1</code> 입니다(온프레미스 Docker 내부망 기준 <code>{html.escape(servers)}</code>). 실제 주소와 키 값은 배포 시 확정됩니다.",
        "서버 간 호출은 <code>X-API-Key: &lt;키&gt;</code> 헤더로 인증합니다. 헤더가 없거나 키가 다르면 401 입니다. 예: <code>{\"detail\":\"missing authorization\"}</code>",
        "요청은 JSON(<code>Content-Type: application/json</code>) 또는 등록만 multipart/form-data 입니다. 응답은 UTF-8 JSON, 시각은 ISO 8601 UTC 입니다.",
        "준비 확인 <code>GET /api/v1/healthz</code> 는 인증이 없고, 200 과 함께 <code>status</code> · <code>model_version</code> · <code>uptime_sec</code> 를 돌려줍니다. 그 밖의 필드는 운영 진단용이라 바뀔 수 있습니다.",
    ]))
    b.append(_pre('export BASE="http://<엔진 주소>:8000/api/v1"\nexport KOIPA_API_KEY="<발급받은 키>"\n\ncurl "$BASE/healthz"'))

    # ── 3. 호출 상세 ────────────────────────────────────────────────────
    b.append(h2("3. 호출 상세와 예시"))
    b.append(h3("3-1. 문서 등록 — IF-02"))
    b.append(_pre(
        'curl -X POST "$BASE/documents" \\\n'
        '  -H "X-API-Key: $KOIPA_API_KEY" \\\n'
        '  -F \'actor={"user_id":"kl-portal","role":"kl_backend"}\' \\\n'
        "  -F 'source_type=internal' \\\n"
        "  -F 'security_marking=confidential' \\\n"
        "  -F 'access_scope=department' \\\n"
        "  -F 'file=@cost_report.txt;type=text/plain'"))
    b.append(p("<code>actor</code> 는 JSON 문자열이며 <code>role</code> 은 <code>admin · reviewer · system · kl_backend</code> 중 하나입니다. "
               "KL 자체 문서번호는 엔진에 저장하지 않습니다 — 응답의 <code>doc_id</code> 와 KL 문서번호의 대응은 KL 쪽에서 보관하십시오."))
    b.append(field_table(spec, upload_form, request=True, overrides={
        "actor": "호출자 정보 JSON 문자열: {\"user_id\": \"...\", \"role\": \"...\"}. 감사 기록에 남습니다.",
        "file": "등록할 원본 파일. 지원 형식은 아래 '등록에서 알아 둘 점'을 봅니다.",
    }))
    b.append(p("응답(201) 예시:"))
    b.append(_json_pre(upload))
    b.append(field_table(spec, {"$ref": "#/components/schemas/DocumentUploadResponse"}))
    b.append(h3("등록에서 알아 둘 점"))
    b.append(ul([
        "<b>같은 내용의 파일을 다시 등록</b>하면 새 문서를 만들지 않고 <b>기존 <code>doc_id</code></b> 를 돌려줍니다(파일 내용의 SHA-256 기준). "
        "이때 함께 보낸 메타데이터는 갱신되지 않습니다. 바꿀 값은 분류 요청의 <code>metadata</code> 로 보냅니다(5장).",
        "<b>지원하지 않는 형식이거나 본문이 추출되지 않아도 201</b> 로 등록됩니다. 이때 <code>char_count</code> 가 0 이고 <code>warnings</code> 에 사유가 담깁니다"
        "(예: <code>[\"extract: unsupported: exe\", \"no text extracted\"]</code>). 분류를 요청하기 전에 <code>char_count</code> 를 확인하십시오.",
        "지원 형식: <code>txt · md · log · csv</code> / <code>hwp · hwpx</code> / <code>docx · doc</code> / <code>xlsx · xlsm · xls</code> / <code>pptx · pptm</code> / <code>pdf</code>. "
        "이미지(<code>jpg · png</code> 등)와 스캔 PDF 는 OCR 을 하지 않으므로 본문 없이 등록됩니다(<code>char_count</code> 0).",
        "<code>requires_review</code> 가 <code>true</code> 이면 추출 품질이 낮아 분류 전에 검수가 필요하다는 표시이며, 자동분류를 그대로 통과하지 않습니다.",
    ]))

    b.append(h3("3-2. 분류 요청 — IF-03"))
    b.append(_pre(
        'curl -X POST "$BASE/classify/async" \\\n'
        '  -H "X-API-Key: $KOIPA_API_KEY" -H "Content-Type: application/json" \\\n'
        f"  -d '{{\"doc_id\":\"{doc_id}\"}}'"))
    b.append(p("<code>content</code> 는 보내지 않는 것이 표준입니다. 생략하면 등록할 때 저장한 본문을 사용합니다."))
    b.append(field_table(spec, {"$ref": "#/components/schemas/ClassifyAsyncRequest"}, request=True, overrides={
        "doc_id": "등록(IF-02)이 돌려준 doc_id(UUID). KL 자체 문서번호가 아닙니다.",
        "metadata": "선택. 5장의 세 필드(source_type · security_marking · access_scope)를 담는 객체입니다. 요청에 있는 값이 저장된 값보다 우선합니다.",
    }))
    b.append(p("응답(202) 예시:"))
    b.append(_json_pre(accepted))
    b.append(field_table(spec, {"$ref": "#/components/schemas/ClassifyAsyncResponse"}))
    b.append(ul([
        "같은 <code>doc_id</code> 로 다시 요청하면 <b>새 작업</b>이 만들어집니다. IF-06 은 가장 최근 결과를 돌려줍니다.",
        "등록하지 않은 <code>doc_id</code> 를 보내면 오류가 아니라 <b>최고 등급(TS) · <code>needs_review</code></b> 결과가 오고, 사유는 <code>warnings</code> 에 담깁니다(본문을 확인할 수 없어 안전 쪽으로 처리). "
        "이 결과는 검수 목록과 판정 이력에 저장되지 않습니다. 반드시 등록한 뒤 분류를 요청하십시오.",
    ]))

    b.append(h3("3-3. 결과 조회 — IF-05"))
    b.append(_pre(f'curl "$BASE/classify/jobs/{job_id}" -H "X-API-Key: $KOIPA_API_KEY"'))
    b.append(p("<code>status</code> 가 <code>done</code> 이 될 때까지 조회합니다. 시험 환경에서 1건은 접수부터 완료까지 약 7초가 걸렸고(1회 측정), 배포 환경과 문서 길이에 따라 다릅니다. "
               "1초 간격 조회를 권장하며, 작업 상태는 기본 24시간 보관하므로 그 안에 <b>결과를 KL 쪽에 저장</b>하십시오."))
    b.append(_table(
        ["status", "뜻", "조치"],
        [
            ["<code>queued</code>", "큐에 접수되어 처리 전", "잠시 뒤 다시 조회합니다"],
            ["<code>done</code>", "성공. <code>results[0]</code> 에 결과가 있습니다(단건 요청은 길이 1)", "결과를 저장합니다"],
            ["<code>failed</code>", "실패. <code>error</code> 에 사유가 있습니다", "사유를 확인하고 요청을 다시 보냅니다"],
            ["<code>partial</code>", "다건 작업에서 일부만 성공. <code>failed_doc_ids</code> · <code>errors</code> 참조", "단건 연동에서는 나오지 않습니다"],
        ],
        widths=[16, 56, 28],
    ))
    b.append(field_table(spec, {"$ref": "#/components/schemas/JobStatus"}))
    b.append(p("완료 응답(200) 예시 — 전체는 아래에서 펼쳐 볼 수 있습니다:"))
    b.append(_details("전체 응답 보기 (03_job_done.json)", _json_pre(job)))

    b.append(h3("3-4. 문서 단위 조회 — IF-06"))
    b.append(_pre(f'curl "$BASE/classify/{doc_id}" -H "X-API-Key: $KOIPA_API_KEY"'))
    b.append(p("<code>doc_id</code> 는 등록이 돌려준 UUID 여야 합니다(아니면 422). 분류 이력이 없으면 404 입니다. "
               "IF-06 은 <b>저장된 값으로 응답을 만들기 때문에</b> 등급 · 확률 · 모델 버전 · 상태와 사람이 확정한 등급(<code>confirmed_*</code>)만 담습니다. "
               "근거(<code>evidence</code>) · 경고(<code>warnings</code>) · 룰/모델 판정이 필요하면 IF-05 의 작업 결과를 사용합니다."))
    b.append(field_table(spec, {"$ref": "#/components/schemas/StoredClassificationResponse"}, overrides={
        "label": "예측 등급 코드. 사람이 확정한 등급은 confirmed_label 입니다.",
    }))
    b.append(_details("응답 예시 보기 (04_classify_by_doc_response.json)", _json_pre(by_doc)))

    # ── 4. 결과 읽는 법 ─────────────────────────────────────────────────
    b.append(h2("4. 결과 읽는 법"))
    b.append(p("IF-05 의 <code>results[]</code> 한 건(<code>ClassifyJobResult</code>)의 필드입니다. IF-06 은 이 중 등급 · 확률 · 모델 버전 · 상태만 담고 확정 등급을 더합니다(3-4)."))
    b.append(field_table(spec, {"$ref": "#/components/schemas/ClassifyJobResult"}, overrides={
        "label": "예측 등급 코드. 사람이 확정한 등급은 confirmed_label 입니다.",
        "automation_assessment": "내부 검증용 관측치입니다. 연동에 쓰지 않으며 필드는 예고 없이 바뀔 수 있습니다.",
    }))
    b.append(h3("등급 코드"))
    b.append(_table(["label", "이름"], [[f"<code>{c}</code>", GRADE_NAMES[c]] for c in schemas["Grade"]["enum"]], center={0}, widths=[20, 80]))
    b.append(h3("status — 검수가 필요한가"))
    b.append(_table(
        ["status", "뜻"],
        [
            ["<code>staging</code>", "자동 확정 후보입니다(검수 라우팅 조건에 걸리지 않았습니다)"],
            ["<code>needs_review</code>", "사람이 확인해야 합니다. 오류가 아니며 <code>label</code> 은 예측값으로 채워져 있습니다. 엔진의 검수 큐에 올라갑니다"],
            ["<code>needs_second_review</code>", "2차 검수 대상입니다"],
        ],
        widths=[28, 72],
    ))
    b.append(ul([
        "<code>label</code> 은 <b>예측 등급</b>입니다. 사람이 검수로 등급을 확정해도 바뀌지 않고, 확정 등급은 <code>confirmed_label</code> · <code>confirmed_by</code> · <code>confirmed_at</code> 으로 따로 옵니다(확정 전에는 null). "
        "모델이 무엇이라 했는지와 사람이 무엇으로 정했는지를 둘 다 남기는 설계입니다.",
        "<code>warnings</code> 는 사람이 읽는 사유 문자열입니다. 형식이 바뀔 수 있으므로 화면 표시와 로그에 쓰고, 문자열로 분기하지 않습니다.",
        "<code>grade_candidates</code> 는 비밀관리성(M)을 판단할 접근범위·보안표시를 받지 못해 등급이 하나로 정해지지 않을 때 남는 후보입니다. 비어 있으면 <code>label</code> 이 유일한 답입니다. "
        "<code>label</code> 을 대체하지 않으며, 접근범위·보안표시를 보내면(5장) 줄어듭니다.",
        "<code>confidence</code> 는 선택한 등급(<code>label</code>)의 확률(0~1)이고 <code>scores</code> 는 등급별 확률입니다.",
    ]))

    # ── 5. 메타데이터 ───────────────────────────────────────────────────
    b.append(h2("5. 메타데이터 세 필드 (ICD 협의안 v0.9 기준)"))
    b.append(p("본문에서 관측되지 않는 정보(출처·보안표시·접근범위)는 KL 이 알려 주시면 등급에 반영됩니다. "
               "등록 폼 필드로 보내면 저장되고, 분류 요청의 <code>metadata</code> 객체로도 보낼 수 있습니다. 둘 다 있으면 <b>요청 값이 우선</b>이고, 없는 필드만 저장된 값으로 채웁니다."))
    b.append(_table(
        ["필드", "값", "효과"],
        [
            ["<code>source_type</code>", "<code>public · registered_patent · academic · internal · external_confidential</code>", "공개 출처면 분류 시 S3 로 상한(cap)이 걸립니다"],
            ["<code>security_marking</code>", "<code>top_secret · secret · confidential</code> (등록 폼은 <code>none</code> 도 가능)", "등급의 하한(floor)입니다. <code>top_secret</code>→TS · <code>secret</code>→S1 · <code>confidential</code>→S2 로 올리기만 하고 내리지는 않습니다. 소문자 정확 일치이며 한글 표기는 무시됩니다"],
            ["<code>access_scope</code>", "<code>approved_only · designated · department · all_employees</code>", "비밀관리성(M) 신호입니다. 보안표시가 없을 때 관리수준을 정하는 주 입력입니다"],
        ],
        widths=[20, 42, 38],
    ))
    b.append(_pre('# 분류 요청에서 덮어쓰기(예: 이미 등록한 문서의 접근범위를 바꿀 때)\n'
                  f'curl -X POST "$BASE/classify/async" -H "X-API-Key: $KOIPA_API_KEY" -H "Content-Type: application/json" \\\n'
                  f"  -d '{{\"doc_id\":\"{doc_id}\",\"metadata\":{{\"access_scope\":\"approved_only\"}}}}'"))

    # ── 6. 오류 ─────────────────────────────────────────────────────────
    b.append(h2("6. 오류와 재시도"))
    b.append(p("오류는 <b>HTTP 상태 코드로 구분</b>합니다. 본문은 대부분 <code>{\"detail\": \"사유\"}</code> 이며(422 는 항목별 배열), "
               "<code>code</code> 필드는 413(JSON 본문 한도) · 429 · 500 에만 있습니다. <code>code</code> 문자열이 아니라 상태 코드로 분기하십시오."))
    b.append(_table(
        ["상태", "본문 예", "언제", "처리"],
        [
            ["401", "<code>{\"detail\":\"missing authorization\"}</code>", "키가 없거나 틀림", "키를 확인합니다. 재시도해도 같습니다"],
            ["404", "<code>{\"detail\":\"job not found\"}</code><br><code>{\"detail\":\"no classification for doc_id\"}</code>", "없는 job_id(보관 24시간 경과 포함) / 분류 이력 없는 doc_id", "IF-05 는 IF-06 으로, IF-06 은 분류 요청부터 다시 진행합니다"],
            ["413", "<code>{\"detail\":\"file too large: …\"}</code><br><code>{\"detail\":\"request body too large\",\"code\":\"KOIPA_BODY_TOO_LARGE\"}</code>", "파일 20MB 초과 / JSON 본문 25MB 초과(기본값)", "크기를 줄여 다시 보냅니다"],
            ["422", "<code>{\"detail\":[{\"type\":\"missing\",\"loc\":[\"body\",\"doc_id\"],\"msg\":\"Field required\",…}]}</code><br><code>{\"detail\":\"empty file\"}</code><br><code>{\"detail\":\"doc_id must be a UUID\"}</code>", "필수 필드 누락 · 형식 오류 · 빈 파일", "<code>loc</code> 가 가리키는 필드를 고쳐 다시 보냅니다"],
            ["429", "<code>{\"code\":\"KOIPA_RATE_LIMIT\",\"message\":\"rate limit exceeded: 60 per 1 minute\",\"retry_after_sec\":60}</code>", "분류 요청이 분당 60건 초과", "<code>Retry-After</code> 헤더(초) 뒤에 다시 보냅니다"],
            ["500", "<code>{\"code\":\"KOIPA_INTERNAL\",\"message\":\"internal server error\",\"request_id\":\"…\",\"retryable\":true}</code>", "엔진 내부 오류", "<code>retryable</code> 이 true 이므로 다시 보낼 수 있습니다. 반복되면 <code>request_id</code> 와 함께 알려 주십시오"],
            ["503", "<code>{\"detail\":\"service temporarily unavailable\"}</code>", "IF-06 조회 중 DB 일시 장애", "잠시 뒤 다시 조회합니다"],
        ],
        widths=[6, 40, 24, 30],
    ))
    b.append(p("등록(IF-02)은 같은 파일이면 같은 <code>doc_id</code> 를 돌려주므로 재시도해도 안전합니다."))

    # ── 7. 콜백 ─────────────────────────────────────────────────────────
    b.append(h2("7. 완료 통보(IF-04) — 선택"))
    b.append(p("분류 요청(IF-03)에 <code>callback_url</code> 을 넣으면, 작업이 끝났을 때 엔진이 그 주소로 JSON 을 <code>POST</code> 합니다. 넣지 않으면 통보는 없고 IF-05 로 조회합니다."))
    b.append(_pre('완료   {"job_id": "…", "status": "done",   "results": [ { …ClassifyJobResult… } ]}\n'
                  '실패   {"job_id": "…", "status": "failed", "error": "…"}'))
    b.append(ul([
        "발송은 <b>60초 주기</b>로 처리되므로 완료 후 최대 약 1분 뒤에 도착할 수 있습니다. 즉시성이 필요하면 IF-05 조회를 사용하십시오.",
        "수신 서버가 2xx 를 돌려주면 성공입니다. 그 밖의 응답이면 <b>최대 5회</b>까지 다시 보내고, 그래도 실패하면 엔진의 실패 보관함에 남으며 더는 자동으로 보내지 않습니다.",
        "허용 주소는 <code>http</code> · <code>https</code> 입니다. loopback(<code>localhost</code> · 127.x) · link-local(169.254.x) · 클라우드 메타데이터 · 예약·멀티캐스트 주소는 거부되며, "
        "이때에도 분류 요청은 202 로 접수되고 통보만 가지 않습니다. 사설망 주소(10.x · 172.16~31.x · 192.168.x)는 기본 설정에서 허용됩니다.",
        "엔진은 통보에 <b>서명이나 인증 헤더를 붙이지 않습니다</b>. 수신 주소는 내부망 접근 제한이나 URL 에 넣은 1회용 토큰 등으로 보호하시기 바랍니다.",
        "통보가 유실될 수 있으므로 IF-05 조회를 함께 두는 것을 권장합니다.",
    ]))

    # ── 8. 제한값 ───────────────────────────────────────────────────────
    b.append(h2("8. 제한값과 보존 기간"))
    b.append(p("아래 값은 현행 기본 설정입니다. 배포 설정에 따라 달라질 수 있습니다."))
    b.append(_table(
        ["항목", "값", "넘거나 지나면"],
        [
            ["업로드 파일 크기", "20MB", "413"],
            ["JSON 요청 본문", "25MB", "413 (<code>KOIPA_BODY_TOO_LARGE</code>)"],
            ["<code>content</code> 길이", "1,048,576자", "422"],
            ["분류 요청(IF-03) 호출 한도", "분당 60건", "429 + <code>Retry-After</code>"],
            ["작업 상태 보관(IF-05)", "24시간", "404 — 이후에는 IF-06 으로 최근 결과를 조회합니다"],
        ],
        widths=[36, 24, 40],
    ))
    b.append(p("문서 등록과 조회에는 호출 한도를 두지 않았습니다(현행 설정)."))

    # ── 9. 달라진 점 ────────────────────────────────────────────────────
    b.append(h2("9. 이전 명세서 초안과 달라진 점"))
    b.append(p("이전 명세서 초안(<code>1.0.0-draft</code>)과 실제 동작이 달랐던 항목입니다. 초안으로 클라이언트를 만드셨다면 확인해 주십시오. 이 안내서와 <code>koipa_kl_openapi.yaml</code> 이 실제 동작 기준입니다."))
    b.append(_table(
        ["#", "항목", "이전 명세서", "실제 동작"],
        [
            ["1", "작업 조회(IF-05) 응답", "<code>result</code>(객체) · <code>progress</code>", "<code>results</code>(배열, 단건은 길이 1). <code>progress</code> 는 없습니다"],
            ["2", "분류 접수(IF-03) 응답", "<code>job_id</code> · <code>status_url</code> · <code>estimated_sec</code>", "<code>estimated_sec</code> 없음. <code>status</code> 가 있고 <code>status_url</code> 은 호스트 없는 경로입니다"],
            ["3", "분류 요청의 <code>content</code>", "필수", "선택. 생략하면 등록 때 저장한 본문을 씁니다"],
            ["4", "등록 후 분류 호출", "<code>POST /classify?doc_id=…</code>", "본문 <code>{\"doc_id\": …}</code> 로 보냅니다(쿼리 파라미터는 받지 않음)"],
            ["5", "등록 폼 필드", "<code>doc_type</code> · <code>external_ref</code>", "<code>doc_type</code> · <code>external_ref</code> 는 받지 않습니다(보내도 무시). <code>source_type</code> · <code>security_marking</code> · <code>access_scope</code> · <code>enqueue_classification</code> 추가"],
            ["6", "분류 결과 필드", "15개", "IF-05 결과 한 건은 18개 — <code>rule_evaluation_factors</code> · <code>grade_candidates</code> · <code>grade_candidates_reason</code> · <code>automation_assessment</code> 추가. 사람이 확정한 등급 <code>confirmed_label</code> · <code>confirmed_by</code> · <code>confirmed_at</code> 은 IF-06 에서만 옵니다"],
            ["7", "오류 본문", "<code>{code, message, …}</code>", "대부분 <code>{\"detail\": …}</code>. <code>code</code> 는 413·429·500 에만 있고 값은 <code>KOIPA_BODY_TOO_LARGE</code> · <code>KOIPA_RATE_LIMIT</code> · <code>KOIPA_INTERNAL</code> 셋뿐입니다"],
            ["8", "문서 조회(IF-06)", "분류 결과 전체", "저장된 요약 10개 항목만 담습니다(3-4장 표)"],
        ],
        center={0}, widths=[4, 20, 30, 46],
    ))

    b.append(f'<div class="foot">AI 분류 엔진 연동 안내서 — 한국지식재산보호원 AI 영업비밀 등급분류 시스템 · {DATE} · 규격 파일 {YAML_NAME}</div>')

    style, nav = _shell()
    return (
        '<!DOCTYPE html>\n<html lang="ko">\n<head>\n<meta charset="UTF-8" />\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0" />\n'
        "<title>AI 분류 엔진 연동 안내서</title>\n"
        f"<style>{style}</style>\n</head>\n<body>\n{nav}\n"
        '<div class="wrap">\n' + "\n".join(b) + "\n</div>\n</body>\n</html>\n"
    )


# ─────────────────────────────────────────────────────────────────────────────


def load_samples(samples_dir: Path) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, name in SAMPLES.items():
        path = samples_dir / name
        if not path.exists():
            raise SystemExit(f"예시 응답 파일이 없다: {path}")
        out[key] = json.loads(path.read_text(encoding="utf-8"))
    return out


def build(samples_dir: Path, spec_path: Path = SPEC) -> dict[str, str]:
    """{파일 이름: 내용} — 디스크에는 쓰지 않는다."""
    spec = load_spec(spec_path)
    return {
        YAML_NAME: render_yaml(subset(spec)),
        GUIDE_NAME: render_guide(spec, load_samples(samples_dir)),
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=OUT_DIR, help="출력 폴더(samples/ 가 이미 있어야 한다)")
    ap.add_argument("--check", action="store_true", help="쓰지 않고 산출물이 최신인지만 본다")
    args = ap.parse_args()

    files = build(args.out / "samples")
    stale = []
    for name, content in files.items():
        target = args.out / name
        current = target.read_text(encoding="utf-8").replace("\r\n", "\n") if target.exists() else None
        if current != content:
            stale.append(name)
        if not args.check:
            target.write_text(content, encoding="utf-8", newline="\n")
    if args.check:
        if stale:
            print(f"낡았거나 없다: {stale} — python scripts/build_kl_api_package.py 로 다시 만든다")
            raise SystemExit(1)
        print("최신이다")
        return
    print(f"썼다: {args.out}")
    for name, content in files.items():
        print(f"  {name}  {len(content.encode('utf-8')):,} bytes")


if __name__ == "__main__":
    main()
