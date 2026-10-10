"""Target-host preflight must fail closed outside the actual Linux host.

The script is shipped at the bundle root and intentionally never starts
compose containers or tries an external connection.
"""
from __future__ import annotations

from pathlib import Path


POC = Path(__file__).resolve().parents[1]
SCRIPT = POC / "scripts" / "preflight_target_host.sh"


def test_target_preflight_is_a_shipped_read_only_bundle_script() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    manifest = (POC / "deploy_manifest.toml").read_text(encoding="utf-8")
    assert '"preflight_target_host.sh"' in manifest
    assert 'docker compose' not in text
    assert 'docker run' not in text
    assert 'curl ' not in text
    assert 'NOT_READY' in text
    assert 'NOT_APPLICABLE' in text


def test_target_preflight_does_not_treat_wsl_as_the_customer_host() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'is_wsl' in text
    assert 'WSL is not the Rocky/customer target host' in text
    assert 'exit 2' in text


def test_target_preflight_covers_declared_host_risks() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for required in (
        'NEED_DISK_GB:-80',
        'PG_PORT',
        'REDIS_PORT',
        'nvidia-smi',
        'NVIDIA Container Toolkit',
        'HF_HUB_OFFLINE',
        'TRANSFORMERS_OFFLINE',
        'image-internal HWP/HWPX/PDF/XLS extractors',
    ):
        assert required in text
