"""KL 이 호출하는 작업(x-audience: kl)의 요청·응답 필드가 실제 코드와 같은지 잠근다.

왜(2026-09-25). 경로·메서드 대조(test_openapi_contract_matches_routes)는 통과하는데 스키마 안쪽은
어긋나 있었다. KL 연동용 5개 작업을 규약서와 app.openapi() 로 대조하니 이런 것이 나왔다.
  · 작업 조회 응답: 규약서는 result·progress, 코드는 results(배열) — 규약서대로 클라이언트를 만들면
    결과를 영영 못 읽는다
  · 비동기 접수 응답: 규약서에만 estimated_sec, 코드에만 status
  · 등록 폼: 코드에는 source_type·security_marking·access_scope·enqueue_classification 이 있는데
    규약서에 없어, 메타데이터를 등록 때 보내야 한다는 사실이 명세로는 안 보였다
  · 분류 결과: 코드 22필드 중 7개(rule_evaluation_factors·grade_candidates·confirmed_label 등)가 없었다
  · 오류 본문: 규약서는 {code, message} 를 약속했는데 실제는 대부분 {"detail": ...} 이고, 예시로 든
    코드 8개 중 6개(KOIPA_AUTH·KOIPA_NOT_FOUND 등)는 소스에 한 번도 나오지 않았다
문서↔문서 점검으로는 안 잡히고, 경로 대조로도 안 잡힌다 — 필드 이름 집합과 필수 목록을 직접 비교한다.

이 시험이 실패하면: 코드가 바뀌었으면 규약서(doc/03_openapi_koipa_kl.yaml)를 코드에 맞춰 고친다.
규약서가 옳고 코드가 틀렸다면 코드를 고친다. 시험을 느슨하게 하는 것으로 통과시키지 않는다.
비교 대상은 필드 이름과 필수 여부다(타입·설명은 보지 않는다).
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "doc" / "03_openapi_koipa_kl.yaml"
SRC = ROOT / "poc" / "src" / "koipa"
PREFIX = "/api/v1"
HTTP_METHODS = ("get", "post", "put", "delete", "patch")


@pytest.fixture(scope="module")
def doc() -> dict:
    if not SPEC.exists():
        pytest.skip(f"규약서를 찾을 수 없다: {SPEC}")
    return yaml.safe_load(SPEC.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def code() -> dict:
    from koipa.api.app import app

    return app.openapi()


def _resolve(spec: dict, node: dict) -> dict:
    for _ in range(20):
        if not (isinstance(node, dict) and "$ref" in node):
            return node
        ref = node["$ref"]
        assert ref.startswith("#/components/schemas/"), f"스키마 참조가 아니다: {ref}"
        node = spec["components"]["schemas"][ref.rsplit("/", 1)[1]]
    raise AssertionError("$ref 가 20단계를 넘게 이어진다")


def _flat(spec: dict, node: dict) -> tuple[dict, set[str]]:
    """$ref 를 풀고 allOf 를 합쳐 (속성 이름 → 스키마, 필수 집합) 로 만든다."""
    node = _resolve(spec, node)
    props: dict = {}
    required: set[str] = set()
    for part in node.get("allOf", []):
        p, r = _flat(spec, part)
        props.update(p)
        required |= r
    props.update(node.get("properties") or {})
    required |= set(node.get("required") or [])
    return props, required


def _kl_operations(doc: dict) -> list[tuple[str, str]]:
    return sorted(
        (method, path)
        for path, item in doc["paths"].items()
        for method, op in item.items()
        if method in HTTP_METHODS and op.get("x-audience") == "kl"
    )


def _operation(spec: dict, method: str, path: str, prefixed: bool) -> dict:
    item = spec["paths"][(PREFIX + path) if prefixed else path]
    return item[method]


def _request_schema(spec: dict, op: dict) -> dict | None:
    content = (op.get("requestBody") or {}).get("content") or {}
    if not content:
        return None
    return content[next(iter(content))]["schema"]


def _success_schema(spec: dict, op: dict) -> dict | None:
    for status, response in (op.get("responses") or {}).items():
        if str(status).startswith("2"):
            content = (response or {}).get("content") or {}
            if content:
                return content[next(iter(content))].get("schema")
    return None


def _diff(doc_spec: dict, code_spec: dict, method: str, path: str) -> list[str]:
    """한 작업의 요청·성공 응답을 비교해 어긋난 곳을 문장으로 돌려준다."""
    problems: list[str] = []
    d = _operation(doc_spec, method, path, prefixed=False)
    c = _operation(code_spec, method, path, prefixed=True)

    for label, pick in (("요청", _request_schema), ("응답", _success_schema)):
        ds, cs = pick(doc_spec, d), pick(code_spec, c)
        if cs is None and ds is None:
            continue
        if cs is None or ds is None:
            problems.append(f"{method.upper()} {path} {label}: 한쪽에만 본문이 있다(규약서={ds is not None}, 코드={cs is not None})")
            continue
        dp, dr = _flat(doc_spec, ds)
        cp, cr = _flat(code_spec, cs)
        if not cp:
            continue        # 코드가 타입 없는 dict 를 돌려주는 응답(/healthz) — 실제 응답 시험이 따로 본다
        if set(dp) != set(cp):
            problems.append(
                f"{method.upper()} {path} {label} 필드: 규약서에만 {sorted(set(dp) - set(cp))} · 코드에만 {sorted(set(cp) - set(dp))}"
            )
        if dr != cr:
            problems.append(f"{method.upper()} {path} {label} 필수: 규약서에만 {sorted(dr - cr)} · 코드에만 {sorted(cr - dr)}")
    return problems


def test_the_kl_operations_are_the_five_agreed_interfaces(doc):
    """IF-01·02·03·05·06 — 회신서가 KL 이 호출한다고 밝힌 다섯 개."""
    assert _kl_operations(doc) == sorted([
        ("get", "/healthz"),
        ("post", "/documents"),
        ("post", "/classify/async"),
        ("get", "/classify/jobs/{job_id}"),
        ("get", "/classify/{doc_id}"),
    ])


def test_every_kl_operation_matches_the_code(doc, code):
    problems: list[str] = []
    for method, path in _kl_operations(doc):
        problems += _diff(doc, code, method, path)
    assert not problems, "\n".join(problems)


def test_the_spec_is_a_valid_openapi_document(doc):
    """OpenAPI 3.0.3 문서로 유효한가. 이름 비교로는 안 보이는 문법 실수를 잡는다.

    2026-09-25 에 세 곳이 무효였다 — 3.1 식 `type: [string, "null"]`(3.0 은 `nullable: true`)과,
    flow 매핑 `{ description: A, B }` 안의 쉼표(둘째 조각이 새 키가 된다). CI 의 openapi-lint 가
    같은 검사를 하지만 워크플로가 main push 때만 돌아 로컬에서는 몰랐다.
    """
    validator = pytest.importorskip("openapi_spec_validator")
    errors = list(validator.OpenAPIV30SpecValidator(doc).iter_errors())
    assert not errors, [("/".join(str(x) for x in e.path)) for e in errors[:5]]


def test_the_grade_codes_match_the_code(doc, code):
    assert doc["components"]["schemas"]["Grade"]["enum"] == code["components"]["schemas"]["Grade"]["enum"]


def test_the_job_result_items_are_the_classification_result(doc):
    """코드는 results 를 타입 없는 배열로 내므로 규약서가 결과 모양을 약속하는 유일한 곳이다."""
    results = doc["components"]["schemas"]["JobStatus"]["properties"]["results"]
    assert results["items"] == {"$ref": "#/components/schemas/ClassifyResponse"}


def test_the_documented_healthz_fields_exist_in_the_real_response(doc):
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    r = TestClient(app).get(f"{PREFIX}/healthz")
    assert r.status_code == 200, r.text
    props, required = _flat(doc, _operation(doc, "get", "/healthz", prefixed=False)["responses"]["200"]["content"]["application/json"]["schema"])
    assert set(props) <= set(r.json()), f"규약서에 있는데 실제 응답에 없는 필드: {sorted(set(props) - set(r.json()))}"
    assert required <= set(r.json())


def test_every_documented_error_code_exists_in_the_source(doc):
    """규약서가 예로 든 KOIPA_* 코드가 소스에 실제로 있는지 — 없으면 클라이언트가 안 오는 코드를 기다린다."""
    import re

    text = SPEC.read_text(encoding="utf-8")
    documented = set(re.findall(r"KOIPA_[A-Z_]+", text))
    assert documented, "규약서에 오류 코드 언급이 없다 — 이 시험이 아무것도 안 보고 있다"
    source = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in SRC.rglob("*.py"))
    phantom = sorted(c for c in documented if c not in source)
    assert not phantom, f"규약서에만 있는 오류 코드(소스에 없음): {phantom}"


def test_the_check_would_catch_a_renamed_field(doc, code):
    """비교가 실제로 잡는지 — 규약서 사본에서 결과 필드를 옛 이름으로 되돌려 본다(파일은 안 건드린다)."""
    broken = copy.deepcopy(doc)
    job = broken["components"]["schemas"]["JobStatus"]["properties"]
    job["result"] = job.pop("results")
    found = _diff(broken, code, "get", "/classify/jobs/{job_id}")
    assert any("results" in p and "result" in p for p in found), found


def test_the_check_would_catch_a_missing_form_field(doc, code):
    broken = copy.deepcopy(doc)
    form = _operation(broken, "post", "/documents", prefixed=False)["requestBody"]["content"]["multipart/form-data"]["schema"]
    del form["properties"]["access_scope"]
    found = _diff(broken, code, "post", "/documents")
    assert any("access_scope" in p for p in found), found
