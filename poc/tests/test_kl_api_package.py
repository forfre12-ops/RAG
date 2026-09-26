"""KL 에 드리는 API 연동 자료(doc/result/KL_API_연동안내_*)가 규약서와 어긋나지 않게 잠근다.

만드는 곳은 scripts/build_kl_api_package.py 다. 이 시험이 지키는 것:
  · KL 전용 규격은 x-audience: kl 작업 5개만 담고, 내부 운영면 경로·스키마가 새지 않는다
  · 참조(`$ref`)가 전부 풀리고 OpenAPI 3.0.3 로 유효하다
  · 커밋된 산출물이 지금 규약서로 다시 만든 것과 같다 — 규약서를 고치고 다시 안 만들면 여기서 걸린다
  · 안내서에 스크립트와 외부 자원이 없다(오프라인·인쇄·첨부 열람에서 그대로 보여야 한다)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_kl_api_package as pkg  # noqa: E402

PACKAGE = pkg.OUT_DIR


@pytest.fixture(scope="module")
def spec() -> dict:
    if not pkg.SPEC.exists():
        pytest.skip(f"규약서를 찾을 수 없다: {pkg.SPEC}")
    return pkg.load_spec()


@pytest.fixture(scope="module")
def kl(spec) -> dict:
    return pkg.subset(spec)


def _all_refs(node, out: list[str]) -> list[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref":
                out.append(value)
            else:
                _all_refs(value, out)
    elif isinstance(node, list):
        for value in node:
            _all_refs(value, out)
    return out


def test_the_kl_spec_has_only_the_five_kl_operations(kl):
    got = sorted((m, p) for p, item in kl["paths"].items() for m in item)
    assert got == sorted(pkg.KL_INTERFACES)
    for path, item in kl["paths"].items():
        for method, op in item.items():
            assert op["x-audience"] == "kl"
            assert op["x-interface"] == pkg.KL_INTERFACES[(method, path)]


def test_no_internal_schema_leaks_into_the_kl_spec(spec, kl):
    internal_only = {"ReviewQueueItem", "ReviewQueueResponse", "TrainJobSummary", "SyntheticDoc", "MetricsReport", "GradeDefinition"}
    present = internal_only & set(kl["components"]["schemas"])
    assert not present, f"내부 운영면 스키마가 KL 규격에 들어갔다: {sorted(present)}"
    assert set(kl["components"]["schemas"]) < set(spec["components"]["schemas"])


def test_every_reference_in_the_kl_spec_resolves(kl):
    for ref in _all_refs(kl, []):
        _, _, section, name = ref.split("/", 3)
        assert name in kl["components"][section], f"풀리지 않는 참조: {ref}"


def test_the_kl_spec_is_valid_openapi(kl):
    validator = pytest.importorskip("openapi_spec_validator")
    errors = list(validator.OpenAPIV30SpecValidator(kl).iter_errors())
    assert not errors, [("/".join(str(x) for x in e.path)) for e in errors[:5]]


def test_the_rendered_yaml_reads_back_to_the_same_document(kl):
    assert yaml.safe_load(pkg.render_yaml(kl)) == kl


def test_the_committed_package_is_current(tmp_path):
    """규약서나 예시 응답이 바뀌었는데 산출물을 다시 안 만들었으면 실패한다."""
    if not (PACKAGE / "samples").exists():
        pytest.skip(f"산출물 폴더가 없다: {PACKAGE}")
    for name, fresh in pkg.build(PACKAGE / "samples").items():
        committed = (PACKAGE / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        assert committed == fresh, f"{name} 이 낡았다 — python scripts/build_kl_api_package.py 로 다시 만든다"


def test_the_guide_needs_no_script_and_no_outside_resource():
    if not (PACKAGE / pkg.GUIDE_NAME).exists():
        pytest.skip("안내서가 없다")
    text = (PACKAGE / pkg.GUIDE_NAME).read_text(encoding="utf-8")
    assert "<script" not in text
    assert not re.search(r'(?:src|href)="https?://', text), "외부 자원을 부른다"


def test_every_documented_field_appears_in_the_guide(spec):
    """필드 표는 규약서에서 뽑으므로, 규약서에 필드가 늘면 안내서에도 늘어야 한다."""
    if not (PACKAGE / pkg.GUIDE_NAME).exists():
        pytest.skip("안내서가 없다")
    text = (PACKAGE / pkg.GUIDE_NAME).read_text(encoding="utf-8")
    for schema in ("ClassifyJobResult", "StoredClassificationResponse", "JobStatus", "DocumentUploadResponse", "ClassifyAsyncResponse", "ClassifyAsyncRequest"):
        props, _ = pkg.flat(spec, {"$ref": f"#/components/schemas/{schema}"})
        missing = [p for p in props if f"<code>{p}</code>" not in text]
        assert not missing, f"{schema} 의 필드가 안내서에 없다: {missing}"
