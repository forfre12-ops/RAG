"""dump_licenses.py 단위 테스트 (doc/14 §11.1 약속 이행 검증)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from dump_licenses import (  # noqa: E402
    DUAL_LICENSE_PACKAGES,
    _parse_pyproject_deps,
    check_licenses,
    classify_license,
    format_cyclonedx,
    format_json,
    format_markdown,
    format_plain,
)


# ─────────────────────────────────────────────────────────────
# classify_license
# ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lic,expected", [
    ("MIT License", "permissive"),
    ("MIT", "permissive"),
    ("Apache 2.0", "permissive"),
    ("Apache Software License", "permissive"),
    ("BSD-3-Clause", "permissive"),
    ("BSD License", "permissive"),
    ("ISC License (ISCL)", "permissive"),
    ("Python Software Foundation License", "permissive"),
    ("PostgreSQL License", "permissive"),
    ("Public Domain", "permissive"),
])
def test_classify_permissive(lic, expected):
    assert classify_license(lic) == expected


@pytest.mark.parametrize("lic", [
    "LGPL-3.0",
    "GNU Library General Public License (LGPL)",
    "Mozilla Public License 2.0 (MPL 2.0)",
    "MPL-2.0",
])
def test_classify_weak_copyleft(lic):
    assert classify_license(lic) == "weak"


@pytest.mark.parametrize("lic", [
    "AGPL-3.0",
    "GNU Affero General Public License v3",
    "GPL-3.0",
    "GPL-2.0",
    "GNU General Public License",
    "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License",
])
def test_classify_strong_copyleft(lic):
    assert classify_license(lic) == "strong"


@pytest.mark.parametrize("lic", ["", "UNKNOWN", "Custom", None])
def test_classify_unknown(lic):
    assert classify_license(lic or "") == "unknown"


# ─────────────────────────────────────────────────────────────
# check_licenses
# ─────────────────────────────────────────────────────────────


def test_check_all_permissive_passes():
    packages = [
        {"name": "fastapi", "license": "MIT", "tier": "permissive", "version": "0.115"},
        {"name": "uvicorn", "license": "BSD-3-Clause", "tier": "permissive", "version": "0.30"},
    ]
    ok, violations = check_licenses(packages)
    assert ok
    assert violations == []


def test_check_detects_strong_copyleft():
    """allowlist에 없는 신규 strong copyleft 의존성은 위반으로 잡혀야 함."""
    packages = [
        {"name": "newgplpkg", "license": "AGPL", "tier": "strong", "version": "1.0"},
        {"name": "fastapi", "license": "MIT", "tier": "permissive", "version": "0.115"},
    ]
    ok, violations = check_licenses(packages)
    assert not ok
    assert len(violations) == 1
    assert violations[0]["name"] == "newgplpkg"


def test_check_detects_unknown():
    packages = [
        {"name": "weirdpkg", "license": "", "tier": "unknown", "version": "0.1"},
    ]
    ok, violations = check_licenses(packages)
    assert not ok
    assert len(violations) == 1


def test_check_skips_empty_name():
    packages = [
        {"name": "", "license": "", "tier": "unknown", "version": ""},
    ]
    ok, violations = check_licenses(packages)
    assert ok  # 이름 없으면 카운트 안 함
    assert violations == []


def test_check_allowlist_known_risks():
    """KNOWN_RISKS_ALLOWLIST에 등록된 PyMuPDF는 기본 통과."""
    packages = [
        {"name": "PyMuPDF", "license": "AGPL", "tier": "strong", "version": "1.27"},
    ]
    # 기본 (allow_known=True): 통과
    ok, _ = check_licenses(packages)
    assert ok
    # strict (allow_known=False): 실패
    ok, violations = check_licenses(packages, allow_known=False)
    assert not ok
    assert violations[0]["name"] == "PyMuPDF"


# ─────────────────────────────────────────────────────────────
# pyproject deps 파싱
# ─────────────────────────────────────────────────────────────


def test_parse_pyproject_deps_strips_versions(tmp_path: Path):
    py = tmp_path / "pyproject.toml"
    py.write_text(
        '[project]\n'
        'name = "test"\n'
        'version = "0.1.0"\n'
        'dependencies = [\n'
        '  "fastapi>=0.115",\n'
        '  "psycopg[binary]>=3.2",\n'
        '  "celery[redis]>=5.4",\n'
        '  "transformers>=4.44,<5",\n'
        ']\n',
        encoding="utf-8",
    )
    deps = _parse_pyproject_deps(py)
    assert "fastapi" in deps
    assert "psycopg" in deps
    assert "celery" in deps
    assert "transformers" in deps
    # 버전·extras 제거 확인
    assert not any(">" in d or "[" in d or "<" in d for d in deps)


def test_parse_pyproject_deps_missing_file(tmp_path: Path):
    assert _parse_pyproject_deps(tmp_path / "nonexistent.toml") == []


# ─────────────────────────────────────────────────────────────
# 포맷터
# ─────────────────────────────────────────────────────────────


_SAMPLE_PACKAGES = [
    {
        "name": "fastapi", "version": "0.115.0",
        "license": "MIT", "url": "https://github.com/fastapi/fastapi",
        "tier": "permissive", "dual_license_note": None,
    },
    {
        "name": "PyMuPDF", "version": "1.27.1",
        "license": "Dual Licensed - GNU AFFERO GPL 3.0 or Artifex Commercial License",
        "url": "https://github.com/pymupdf/pymupdf",
        "tier": "strong",
        "dual_license_note": "AGPL-3.0 / Artifex Commercial (doc/14 §3.1)",
    },
]


def test_format_plain_contains_all_packages():
    text = format_plain(_SAMPLE_PACKAGES)
    assert "fastapi 0.115.0" in text
    assert "MIT" in text
    assert "PyMuPDF 1.27.1" in text
    assert "Artifex" in text  # dual_license_note 포함


def test_format_markdown_has_table_and_summary():
    text = format_markdown(_SAMPLE_PACKAGES)
    assert "## 등급 요약" in text
    assert "## 패키지 목록" in text
    assert "fastapi" in text
    assert "PyMuPDF" in text
    # 등급 카운트
    assert "permissive" not in text  # 한글로 표시
    assert "허용형" in text


def test_format_json_valid_structure():
    text = format_json(_SAMPLE_PACKAGES)
    data = json.loads(text)
    assert data["total"] == 2
    assert data["tier_counts"]["permissive"] == 1
    assert data["tier_counts"]["strong"] == 1
    assert len(data["packages"]) == 2


def test_format_cyclonedx_valid_sbom():
    text = format_cyclonedx(_SAMPLE_PACKAGES)
    data = json.loads(text)
    assert data["bomFormat"] == "CycloneDX"
    assert data["specVersion"] == "1.5"
    assert data["serialNumber"].startswith("urn:uuid:")
    assert len(data["components"]) == 2
    # purl 형식 확인
    fastapi_comp = next(c for c in data["components"] if c["name"] == "fastapi")
    assert fastapi_comp["purl"] == "pkg:pypi/fastapi@0.115.0"


def test_format_cyclonedx_skips_empty_name():
    packages = _SAMPLE_PACKAGES + [{"name": "", "version": "", "license": "", "url": "", "tier": "unknown", "dual_license_note": None}]
    data = json.loads(format_cyclonedx(packages))
    assert len(data["components"]) == 2  # 빈 이름 skip


# ─────────────────────────────────────────────────────────────
# 듀얼 라이선스 노트
# ─────────────────────────────────────────────────────────────


def test_pymupdf_dual_license_documented():
    """doc/14 §3.1에 명시한 PyMuPDF AGPL/Artifex 듀얼 — 자동 노트 매핑 확인."""
    assert "PyMuPDF" in DUAL_LICENSE_PACKAGES
    assert "AGPL" in DUAL_LICENSE_PACKAGES["PyMuPDF"]
    assert "Artifex" in DUAL_LICENSE_PACKAGES["PyMuPDF"]
