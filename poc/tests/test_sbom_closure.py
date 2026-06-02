"""dump_licenses 의존성 폐포(closure) 알고리즘 단위 테스트 (lite, env 독립).

배경(2026-06-02): SBOM이 오염된 전역 venv 전체를 덤프해 프로젝트와 무관한 패키지가
섞였다. closure scope는 lloydk-ai 선언 의존 + 선택 extras의 transitive 폐포만 산출한다.
본 테스트는 합성 의존 그래프(monkeypatch)로 알고리즘을 env 독립적으로 고정한다:
extras는 root에만 적용되고, 전이 의존의 optional extra는 자동 포함되지 않는다(pip 시맨틱).
"""

from __future__ import annotations

import importlib.metadata as im
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "dump_licenses",
    Path(__file__).resolve().parents[1] / "scripts" / "dump_licenses.py",
)
_dl = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_dl)


# 합성 의존 그래프 (canon 이름 기준).
_FAKE_REQUIRES = {
    "lloydk-ai": ["fastapi>=1", "torch>=2 ; extra == 'full'", "pytest ; extra == 'dev'"],
    "fastapi": ["starlette<1", "playwright ; extra == 'test'"],  # 전이 extra → 제외돼야
    "starlette": [],
    "torch": ["filelock"],
    "filelock": [],
}


@pytest.fixture
def fake_requires(monkeypatch):
    def _req(name):
        return _FAKE_REQUIRES.get(_dl._canon(name))
    monkeypatch.setattr(im, "requires", _req)


def test_closure_includes_base_and_selected_extra(fake_requires):
    c = _dl._dependency_closure("lloydk-ai", extras=("full",))
    assert "fastapi" in c and "starlette" in c   # base + transitive
    assert "torch" in c and "filelock" in c       # full extra + 그 전이


def test_closure_excludes_unselected_extra(fake_requires):
    c = _dl._dependency_closure("lloydk-ai", extras=("full",))
    assert "pytest" not in c  # dev extra 미선택 → 제외


def test_closure_excludes_transitive_optional_extra(fake_requires):
    # playwright는 fastapi의 'test' extra → 전이 optional은 자동 포함 안 함(오염 방지 핵심).
    c = _dl._dependency_closure("lloydk-ai", extras=("full", "test"))
    assert "playwright" not in c


def test_closure_excludes_self(fake_requires):
    c = _dl._dependency_closure("lloydk-ai", extras=())
    assert "lloydk-ai" not in c


def test_classify_license_strong_copyleft():
    assert _dl.classify_license("GNU Affero General Public License v3") == "strong"
    assert _dl.classify_license("MIT License") == "permissive"
    assert _dl.classify_license("LGPL-3.0") == "weak"
