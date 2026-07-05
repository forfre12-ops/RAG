"""[P0#①-a] seed_active_model_version 는 deploy gate·locked-eval·감사를 모두 우회하는
데모 전용 CLI(activate_model_version 직접 호출)다. 하드닝/운영 프로파일에서는 이 우회가
거부되어야 한다 — 운영 활성은 POST /admin/model/activate(게이트·감사)로만.

PG 불요: 가드는 session_scope 이전에 판정한다(거부 시 DB 미접근).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import seed_active_model_version as seed  # noqa: E402
from lloydk import config as cfg  # noqa: E402


@pytest.mark.parametrize("profile", ["onprem-local", "full-train"])
def test_seed_refused_in_hardened_profile(monkeypatch, profile):
    monkeypatch.setattr(cfg.settings, "deploy_profile", profile)
    monkeypatch.setattr(cfg.settings, "require_safety_gates", True, raising=False)
    # 거부 → exit 2, DB 미접근(가드가 session_scope 이전에 반환).
    assert seed.main() == 2


def test_seed_refused_when_require_safety_gates(monkeypatch):
    # 프로파일명이 아니어도 require_safety_gates=True(하드닝 신호)면 거부.
    monkeypatch.setattr(cfg.settings, "deploy_profile", "")
    monkeypatch.setattr(cfg.settings, "require_safety_gates", True, raising=False)
    assert seed.main() == 2
