"""배포 정본(poc/deploy_manifest.toml) 로더.

번들 빌더(build_offline_bundle.py)·검사기(check_deploy_manifest.py)·시험이 같은 정본을 읽는다.
정본에 없는 파일은 번들·이미지에 싣지 않는다. 이 모듈은 읽기만 한다.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

POC_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = POC_ROOT / "deploy_manifest.toml"

_cache: dict[Path, dict] = {}


def load(path: Path | None = None) -> dict:
    p = Path(path) if path else MANIFEST_PATH
    if p not in _cache:
        with p.open("rb") as f:
            _cache[p] = tomllib.load(f)
    return _cache[p]


def _files(section: str, key: str = "files", path: Path | None = None) -> list[str]:
    node = load(path)
    for part in section.split("."):
        node = node[part]
    return list(node[key])


def bundle_root_scripts(path: Path | None = None) -> list[str]:
    """번들 루트로 복사하는 실행 스크립트(poc/scripts 에서)."""
    return _files("bundle.root", "scripts", path)


def bundle_docs(path: Path | None = None) -> list[str]:
    return _files("bundle.docs", "files", path)


def bundle_licenses(path: Path | None = None) -> list[str]:
    return _files("bundle.licenses", "files", path)


def bundle_acceptance_files(path: Path | None = None) -> list[str]:
    """acceptance/ 바로 아래 파일(러너·기대 라벨). 표본 문서는 docs/ 아래 전부."""
    return _files("bundle.acceptance", "files", path)


def bundle_images(path: Path | None = None) -> list[str]:
    """docker save 로 tar 를 만드는 이미지의 tar 이름(확장자 제외). 같은 이미지를 쓰는 서비스는 한 번만."""
    return _files("bundle.images", "save", path)


def observability_images(path: Path | None = None) -> list[str]:
    return _files("bundle.images", "observability", path)


def container_scripts(path: Path | None = None) -> list[str]:
    """도커 이미지(api·worker)에 싣는 scripts/ 파일. 이 목록이 Dockerfile COPY 의 정본이다."""
    return _files("container.scripts", "allow", path)


def container_scripts_not_in_image(path: Path | None = None) -> dict[str, str]:
    """문서·코드가 언급하지만 이미지에는 넣지 않는 스크립트 → 이유."""
    return dict(load(path)["container"]["scripts"]["not_in_image"])


def container_excluded_paths(path: Path | None = None) -> list[str]:
    """이미지 빌드 컨텍스트에서 빼는 경로(.dockerignore 로 실현)."""
    return list(load(path)["container"]["exclude_paths"]["paths"])
