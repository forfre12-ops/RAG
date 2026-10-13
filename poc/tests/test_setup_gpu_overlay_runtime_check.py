"""setup.sh — GPU 오버레이는 nvidia-smi 만 보고 적용하면 안 된다.

배경(2026-09-28, Rocky8.10 실번들 e2e 리허설). `nvidia-smi` 는 드라이버(호스트) 수준의 GPU
유무만 확인한다 — 컨테이너 런타임에 `nvidia` 런타임이 등록돼 있는지(=nvidia-container-toolkit
설치 여부)는 완전히 별개다. 실측: WSL2 Rocky 8.10 에 nvidia-container-toolkit 을 설치하지 않은
채로 `nvidia-smi -L` 이 성공하는 호스트(GeForce RTX 5070 Ti, 드라이버 정상)에서 setup.sh 가
`docker-compose.gpu.yml` 오버레이를 적용했고, `docker compose up` 이 곧바로
"could not select device driver \"nvidia\" with capabilities: [[gpu]]" 로 마이그레이션
단계째 죽었다 — 코드 자체의 주석은 "장치만 예약되고 학습은 CPU 휠로 돈다(느림, 실패는 아님)"
라고 말하지만 실제로는 하드 크래시였다. 고침: 오버레이를 고르기 전에 `docker info` 의
`.Runtimes` 에 `nvidia` 키가 등록돼 있는지 먼저 확인하고, 없으면 오버레이를 아예 고르지 않고
CPU 로 기동한다(경고 메시지만 남긴다).
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


def _extract_gpu_overlay_block() -> str:
    start = _TEXT.index('_gpu_overlay=""')
    end = _TEXT.index("\n  _run_deploy", start)
    return _TEXT[start:end]


def _run(block: str, bundle: Path, *, nvidia_runtime_registered: bool, gpu_image_present: bool,
          overlay_files: list[str]) -> subprocess.CompletedProcess:
    for name in overlay_files:
        p = bundle / "infra-config" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("# dummy overlay\n", encoding="utf-8")
    runtimes_json = '{"runc":{}}' if not nvidia_runtime_registered else '{"nvidia":{},"runc":{}}'
    script = (
        "#!/usr/bin/env bash\n"
        "set -uo pipefail\n"
        "inf() { printf 'INF:%s\\n' \"$*\"; }\n"
        "NODE=jjw\n"
        "CRT=docker\n"
        f'BUNDLE="{bundle.as_posix()}"\n'
        "nvidia-smi() { [ \"$1\" = \"-L\" ] && return 0 || return 0; }\n"
        "docker() {\n"
        "  case \"$1\" in\n"
        "    info) printf '%s' '" + runtimes_json + "' ;;\n"
        f"    image) {'return 0' if gpu_image_present else 'return 1'} ;;\n"
        f"    images) {'echo koipa-gpu:cu130' if gpu_image_present else 'return 0'} ;;\n"
        "    *) return 1 ;;\n"
        "  esac\n"
        "}\n"
        f"{block}\n"
        'echo "GPU_OVERLAY=$_gpu_overlay"\n'
    )
    return subprocess.run([BASH, "-c", script], capture_output=True, encoding="utf-8")


def test_overlay_not_applied_when_nvidia_runtime_missing(tmp_path: Path):
    """드라이버는 있는데(nvidia-smi) 컨테이너 런타임에 nvidia 가 없으면 오버레이를 고르지 않는다."""
    block = _extract_gpu_overlay_block()
    result = _run(block, tmp_path, nvidia_runtime_registered=False, gpu_image_present=False,
                  overlay_files=["docker-compose.gpu.yml"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("GPU_OVERLAY="), result.stdout
    assert "nvidia 런타임이 등록돼 있지 않다" in result.stdout


def test_overlay_applied_when_nvidia_runtime_registered(tmp_path: Path):
    """런타임이 등록돼 있으면(기존 동작) gpu.yml 오버레이를 그대로 고른다."""
    block = _extract_gpu_overlay_block()
    result = _run(block, tmp_path, nvidia_runtime_registered=True, gpu_image_present=False,
                  overlay_files=["docker-compose.gpu.yml"])
    assert result.returncode == 0, result.stderr
    assert "docker-compose.gpu.yml" in result.stdout
    assert "GPU 감지" in result.stdout


def test_gpu_train_overlay_preferred_when_cuda_image_present(tmp_path: Path):
    """런타임 등록 + CUDA 이미지 존재 시 gpu-train.yml 을 우선한다(기존 동작 유지 확인)."""
    block = _extract_gpu_overlay_block()
    result = _run(block, tmp_path, nvidia_runtime_registered=True, gpu_image_present=True,
                  overlay_files=["docker-compose.gpu.yml", "docker-compose.gpu-train.yml"])
    assert result.returncode == 0, result.stderr
    assert "docker-compose.gpu-train.yml" in result.stdout
