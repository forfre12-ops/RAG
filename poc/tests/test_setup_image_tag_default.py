"""setup.sh — manifest.yaml 이 없을 때 IMAGE_TAG_DEFAULT 폴백이 실제로 도달하는가.

배경(2026-09-27, Rocky8 실치설치 리허설). `_copy_infra()` 로 번들 모양 폴더를 만들면(실빌드가
아니라 스크립트·문서·모델을 직접 모은 것이라) manifest.yaml 이 없다. 이 상태로 setup.sh 를
돌리면 트레이스도 에러 메시지도 없이 조용히 멈췄다. 원인: `sed -n ... "$BUNDLE/manifest.yaml"
2>/dev/null | head -1` 에서 파일이 없어 sed 가 rc=2 로 실패하고, `set -euo pipefail` 하의
pipefail 이 그 실패를 파이프 전체 종료코드로 올려, 바로 다음 줄의 ":-1.0.0-rc1" 폴백이 실행되기도
전에 스크립트 자체가 죽었다(폴백이 있는데 도달을 못 한다).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash 가 없다")

_TEXT = SETUP.read_text(encoding="utf-8")


def _extract_image_tag_default_block() -> str:
    start = _TEXT.index('IMAGE_TAG_DEFAULT="$(sed -n')
    second_line_start = _TEXT.index('IMAGE_TAG_DEFAULT="${IMAGE_TAG_DEFAULT:-1.0.0-rc1}"', start)
    end = _TEXT.index("\n", second_line_start)
    return _TEXT[start:end]


def _run(block: str, bundle: Path) -> subprocess.CompletedProcess:
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'BUNDLE="{bundle.as_posix()}"\n'
        f"{block}\n"
        'echo "IMAGE_TAG_DEFAULT=$IMAGE_TAG_DEFAULT"\n'
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True)


def test_image_tag_default_falls_back_when_manifest_missing(tmp_path: Path):
    block = _extract_image_tag_default_block()
    assert not (tmp_path / "manifest.yaml").exists()
    result = _run(block, tmp_path)
    assert result.returncode == 0, f"manifest.yaml 이 없다고 스크립트가 죽으면 안 된다: {result.stderr}"
    assert "IMAGE_TAG_DEFAULT=1.0.0-rc1" in result.stdout


def test_image_tag_default_reads_manifest_version_when_present(tmp_path: Path):
    (tmp_path / "manifest.yaml").write_text("version: v-abc123\nbuild_date: today\n", encoding="utf-8")
    block = _extract_image_tag_default_block()
    result = _run(block, tmp_path)
    assert result.returncode == 0, result.stderr
    assert "IMAGE_TAG_DEFAULT=v-abc123" in result.stdout
