"""[번들-안전게이트] 하드닝 운영에서 안전 게이트 강제 — C23.

검증 항목:
1. evaluate_safety_gates 순수 함수: required+off → must_raise; not-required+off → 보고만.
2. onprem-local·full-train 프로파일이 require_safety_gates=True를 켜고, lite-* 는 안 켠다.
3. assert_production_credentials(full): require_safety_gates=True인데 게이트 off → RuntimeError.
4. require_safety_gates=False면 게이트 off여도 (warn만, raise 안 함) — lite-cloud 비파괴.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import lloydk.config as config_mod
from lloydk.config import (
    _PROFILE_DEFAULTS,
    Settings,
    evaluate_safety_gates,
)


def _settings(**over) -> Settings:
    base = dict(
        agreement_gate_enabled=False,
        metadata_floor_enabled=False,
        storage_encryption_enabled=False,
        require_safety_gates=False,
    )
    base.update(over)
    return Settings(_env_file=None, **base)


def test_evaluate_all_on_no_raise() -> None:
    sg = evaluate_safety_gates(
        _settings(
            agreement_gate_enabled=True,
            metadata_floor_enabled=True,
            storage_encryption_enabled=True,
            require_safety_gates=True,
        )
    )
    assert sg["off"] == []
    assert sg["required"] is True
    assert sg["must_raise"] is False


def test_evaluate_required_with_off_must_raise() -> None:
    sg = evaluate_safety_gates(
        _settings(
            agreement_gate_enabled=False,  # 하나만 off
            metadata_floor_enabled=True,
            storage_encryption_enabled=True,
            require_safety_gates=True,
        )
    )
    assert sg["off"] == ["AGREEMENT_GATE_ENABLED"]
    assert sg["must_raise"] is True


def test_evaluate_not_required_off_is_report_only() -> None:
    sg = evaluate_safety_gates(_settings())  # 전부 off, require False
    assert set(sg["off"]) == {
        "AGREEMENT_GATE_ENABLED",
        "METADATA_FLOOR_ENABLED",
        "STORAGE_ENCRYPTION_ENABLED",
    }
    assert sg["required"] is False
    assert sg["must_raise"] is False  # require 아니면 raise 안 함(가시화만)


def test_source_prior_in_enforced_safety_gates() -> None:
    """[P1] 비공지성 source-prior 게이트가 강제집합에 포함되어 하드닝서 .env로 못 끄게."""
    from lloydk.config import _SAFETY_GATES

    envs = {env for _, env in _SAFETY_GATES}
    assert "SOURCE_PRIOR_ENABLED" in envs
    # 하드닝(require=True)에서 source_prior만 끄면 startup 차단(다른 게이트는 켬).
    sg = evaluate_safety_gates(_settings(
        agreement_gate_enabled=True,
        metadata_floor_enabled=True,
        storage_encryption_enabled=True,
        source_prior_enabled=False,   # 비공지성 게이트만 off
        require_safety_gates=True,
    ))
    assert sg["off"] == ["SOURCE_PRIOR_ENABLED"]
    assert sg["must_raise"] is True


def test_hardened_profiles_source_prior_on_by_default() -> None:
    """하드닝 프로파일 기본값에 source_prior가 켜져 있어 강제집합 추가가 오탐 안 냄."""
    from lloydk.config import Settings, apply_profile_defaults

    for profile in ("onprem-local", "full-train"):
        s = Settings(deploy_profile=profile, _env_file=None)
        apply_profile_defaults(s)
        assert s.source_prior_enabled is True, profile
        assert evaluate_safety_gates(s)["off"] == []  # 게이트 누락 0


def test_hardened_profiles_require_safety_gates() -> None:
    for profile in ("onprem-local", "full-train"):
        assert _PROFILE_DEFAULTS[profile].get("require_safety_gates") is True, profile
    # lite-* 는 require_safety_gates를 켜지 않는다(클라우드 개발/데모 tier).
    for profile in ("lite-noapi", "lite-cloud"):
        assert "require_safety_gates" not in _PROFILE_DEFAULTS[profile], profile


def test_assert_blocks_hardened_with_gate_off() -> None:
    """poc_mode=full + require_safety_gates=True + 게이트 off → RuntimeError (fail-clear)."""
    config_mod._SECRETS_FILLED = True  # SM 보충 skip
    snap = {
        k: getattr(config_mod.settings, k)
        for k in (
            "poc_mode",
            "api_key",
            "minio_secret_key",
            "audit_chain_secret",
            "agreement_gate_enabled",
            "metadata_floor_enabled",
            "storage_encryption_enabled",
            "require_safety_gates",
        )
    }
    config_mod.settings.poc_mode = "full"
    config_mod.settings.api_key = "x"
    config_mod.settings.minio_secret_key = "y"
    config_mod.settings.audit_chain_secret = "z"  # 자격증명 누락 체크 통과
    config_mod.settings.require_safety_gates = True
    config_mod.settings.agreement_gate_enabled = False  # 의도적으로 off → 차단 대상
    config_mod.settings.metadata_floor_enabled = True
    config_mod.settings.storage_encryption_enabled = True
    try:
        with patch.dict(
            os.environ,
            {"TESTING": "", "PYTEST_CURRENT_TEST": "", "LLOYDK_AUDIT_CHAIN_SECRET": "z"},
        ):
            try:
                config_mod.assert_production_credentials()
            except RuntimeError as exc:
                assert "AGREEMENT_GATE_ENABLED" in str(exc)
                assert "require_safety_gates" in str(exc)
            else:
                raise AssertionError("하드닝 모드 + 게이트 off → RuntimeError 기대")
    finally:
        for k, v in snap.items():
            setattr(config_mod.settings, k, v)
        config_mod._SECRETS_FILLED = False


def test_ci_safety_gate_script_passes() -> None:
    """P0#5: CI 안전게이트 스크립트가 green 인지 — CI job 이 실행하는 그 스크립트를 그대로 구동.

    스크립트가 rot(하드닝 프로파일 게이트 누락/강제 무력화)하면 여기서도 exit!=0 으로 잡힌다.
    _env_file=None 으로 hermetic 하므로 로컬 .env 유무와 무관.
    """
    script = Path(__file__).resolve().parents[1] / "scripts" / "ci_check_safety_gates.py"
    assert script.exists(), script
    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert proc.returncode == 0, f"CI 안전게이트 스크립트 실패:\n{proc.stdout}\n{proc.stderr}"


def test_assert_allows_non_hardened_with_gate_off() -> None:
    """require_safety_gates=False면 게이트 off여도 안전게이트 사유로는 raise 안 함(lite-cloud 비파괴)."""
    config_mod._SECRETS_FILLED = True
    snap = {
        k: getattr(config_mod.settings, k)
        for k in (
            "poc_mode",
            "api_key",
            "minio_secret_key",
            "audit_chain_secret",
            "cors_allow_origins",
            "api_key_trust_actor_role_header",
            "classifier_model_dir",
            "agreement_gate_enabled",
            "metadata_floor_enabled",
            "storage_encryption_enabled",
            "require_safety_gates",
        )
    }
    config_mod.settings.poc_mode = "full"
    config_mod.settings.api_key = "x"
    config_mod.settings.minio_secret_key = "y"
    config_mod.settings.audit_chain_secret = "z"
    config_mod.settings.cors_allow_origins = ["https://app.example.com"]
    config_mod.settings.api_key_trust_actor_role_header = False
    config_mod.settings.classifier_model_dir = ""  # rule-fallback 의도(모델 경로 체크 통과)
    config_mod.settings.require_safety_gates = False
    config_mod.settings.agreement_gate_enabled = False  # off지만 require False라 raise 안 함
    config_mod.settings.metadata_floor_enabled = False
    config_mod.settings.storage_encryption_enabled = False
    try:
        with patch.dict(
            os.environ,
            {
                "TESTING": "",
                "PYTEST_CURRENT_TEST": "",
                "RATE_LIMIT_DISABLED": "",
                "AUDIT_DISABLED": "",
                "LLOYDK_AUDIT_CHAIN_SECRET": "z",
            },
        ):
            # 안전게이트 사유로 raise 하면 안 됨. (다른 운영 체크는 위에서 모두 통과하도록 구성)
            config_mod.assert_production_credentials()
    finally:
        for k, v in snap.items():
            setattr(config_mod.settings, k, v)
        config_mod._SECRETS_FILLED = False
