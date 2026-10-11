"""Regression guards for the fast UI/API deployment path.

These are intentionally structural: they prevent a future Dockerfile edit from
putting ``src/`` or release identity back before the expensive locked install,
and prevent the 211 switch script from quietly becoming a build host again.
"""

from __future__ import annotations

from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_locked_installer_can_separate_dependencies_from_editable_project() -> None:
    script = _read("scripts/docker_install_locked.sh")
    assert "--no-project-install" in script
    assert "if [ \"${install_project}\" -eq 1 ]; then" in script
    assert "python -m pip install -e . --no-deps" in script


@pytest.mark.parametrize(
    "dockerfile",
    [
        "Dockerfile.api",
        "Dockerfile.api.prod",
        "Dockerfile.worker",
        "Dockerfile.api.customer",
        "Dockerfile.worker.customer",
    ],
)
def test_runtime_images_cache_locked_dependencies_before_source_and_release_identity(dockerfile: str) -> None:
    text = _read(dockerfile)
    dependency_install = text.index("docker_install_locked.sh --no-project-install")
    source_copy = text.index("COPY src ./src")
    editable_install = text.index("RUN python -m pip install -e . --no-deps")
    identity = text.index("ARG KOIPA_BUILD_SHA=unknown")

    assert dependency_install < source_copy < editable_install
    assert dependency_install < identity < editable_install


def test_prebuilt_switcher_never_builds_on_the_target_host() -> None:
    script = _read("scripts/deploy_prebuilt_images.sh")
    assert "docker build" not in script.lower()
    assert "docker load -i" in script
    assert "docker pull" in script
    assert "up -d --no-deps" in script
    assert "KOIPA_BUILD_SHA" in script
    assert ".previous" in script
    assert "python3 -" in script


def test_release_builder_is_exact_sha_and_component_scoped() -> None:
    script = _read("scripts/build_release_images.sh")
    # Windows Git-Bash runs can inject the Git executable explicitly, but both
    # staged and unstaged source changes must still block a release build.
    assert "diff --quiet" in script
    assert "--services" in script
    assert "docker save -o" in script
    assert "out.parent / archive" in script
    assert "docker push" in script
    assert "git push" not in script
