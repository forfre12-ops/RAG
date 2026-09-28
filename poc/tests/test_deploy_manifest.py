"""배포 정본(deploy_manifest.toml)이 번들 빌더·Dockerfile 과 어긋나지 않는지.

번들과 이미지에 실리는 파일의 목록은 정본 한 곳에 있다. 코드가 그 목록을 따로 들고 있으면
선언과 실제가 어긋난다 — README.md 는 선언만 있고 만드는 코드가 없었고, 같은 이미지의 beat.tar 가
796MB 를 두 번 실었고, `COPY scripts` 가 567개(쓰이는 것은 27개)를 이미지에 실었다.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
_SCRIPTS = _POC / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import check_deploy_manifest as cdm  # noqa: E402
import deploy_manifest as dm  # noqa: E402
from build_offline_bundle import (  # noqa: E402
    ComponentEntry,
    _copy_infra,
    _hf_keep_main_revision,
    _obs_tar_name,
    _prune_unreferenced_blobs,
    build_manifest,
    expected_files,
    image_tar_plan,
)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return dm.load()


@pytest.fixture(scope="module")
def dry_manifest():
    """실제 airgap compose·config 로 만든 dry-run 매니페스트(외부 호출 없음)."""
    return build_manifest(
        version="t", target_env="test", dry_run=True,
        compose_path=_POC / "docker-compose.airgap.yml",
        config_path=_POC / "src" / "koipa" / "config.py",
    )


# ── 정본 ↔ 리포 ─────────────────────────────────────────────
def test_manifest_matches_repo(manifest):
    """원본 실재 · 이미지 scripts 허용 목록 == 런타임 근거 폐포 · Dockerfile COPY 블록/.dockerignore 동기."""
    errs = cdm.check(manifest)
    assert not errs, "\n".join(errs)


def test_derived_container_scripts_never_reference_host_or_build_only_scripts(manifest):
    """이미지에 싣는 스크립트가 빌드 호스트 전용·번들 루트(호스트) 스크립트를 부르면 그 명령은 이미지 안에서 깨진다."""
    forbidden = set(dm.bundle_root_scripts()) | set(dm.container_scripts_not_in_image())
    allow = dm.container_scripts()
    for name in allow:
        text = cdm._exec_text(_POC / "scripts" / name)
        for f in forbidden:
            assert f not in text, f"이미지에 싣는 {name} 이 이미지에 없는 {f} 를 부른다"


def test_dockerfiles_do_not_copy_whole_scripts_dir():
    for df in cdm._dockerfiles():
        text = df.read_text(encoding="utf-8")
        assert "COPY scripts ./scripts" not in text, f"{df.name}: scripts 를 폴더째 싣는다"
        assert cdm.BLOCK_BEGIN in text and cdm.BLOCK_END in text


# ── 정본 ↔ 빌더 ─────────────────────────────────────────────
def test_builder_image_tars_equal_manifest(manifest, dry_manifest):
    """빌더가 만드는 tar 이름은 정본의 bundle.images.save 와 같다(beat 는 worker 와 같은 이미지라 없다)."""
    assert list(image_tar_plan(dry_manifest.components)) == dm.bundle_images()
    assert "beat" in dry_manifest.components          # 서비스로는 남아 있다
    assert "beat" not in image_tar_plan(dry_manifest.components)


def test_builder_observability_images_equal_manifest(dry_manifest):
    got = [_obs_tar_name(i) for i in dry_manifest.observability_images]
    assert got == dm.observability_images()


def test_expected_files_omit_retired_items(dry_manifest):
    files = expected_files(dry_manifest.components, dry_manifest.models, dry_manifest.observability_images)
    joined = "\n".join(files)
    for retired in ("beat.tar", "python-deps", "db-migrations", "redis_exporter"):
        assert retired not in joined, retired
    # 정본의 루트 스크립트·문서가 전부 선언돼 있다
    for s in dm.bundle_root_scripts():
        assert s in files
    for d in dm.bundle_docs():
        assert f"docs/{d}" in files


def test_expected_files_do_not_declare_unshipped_models():
    """LLM·대체 임베더는 번들에 싣지 않는다 — 선언만 있고 실물이 없던 항목(models/Qwen-Qwen3-14B/)을 만들지 않는다."""
    from build_offline_bundle import ModelEntry

    comps = {"postgres": ComponentEntry("postgres:16", "16")}
    models = [
        ModelEntry("Qwen/Qwen3-14B", None, None, "Apache-2.0", "llm"),
        ModelEntry("BAAI/bge-m3", None, None, "MIT", "embedding_fallback"),
        ModelEntry("nlpai-lab/KURE-v1", None, None, "MIT", "embedding"),
    ]
    files = expected_files(comps, models)
    assert not any("Qwen" in f or "bge-m3" in f for f in files)
    assert "models/hf/hub/models--nlpai-lab--KURE-v1/" in files


def test_image_tar_plan_merges_services_sharing_an_image():
    comps = {
        "api": ComponentEntry("koipa-api:1", "1"),
        "worker": ComponentEntry("koipa-worker:1", "1"),
        "beat": ComponentEntry("koipa-worker:1", "1"),
    }
    assert image_tar_plan(comps) == {"api": "koipa-api:1", "worker": "koipa-worker:1"}


def test_copy_infra_ships_only_declared_files(tmp_path, manifest):
    """빌더가 실제로 복사·생성한 파일은 전부 정본이 허용한 것이다(선언 외 파일 0)."""
    _copy_infra(tmp_path, version="t")
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files, "복사된 파일이 없다"
    allow, _required = cdm.bundle_allow_patterns(manifest)
    import fnmatch

    extras = [f for f in files if not any(fnmatch.fnmatchcase(f, pat) for pat in allow)]
    assert not extras, f"정본에 없는 파일이 번들에 복사된다: {extras[:10]}"
    # 폴더째 복사하던 것들이 정본 목록만 실렸는지
    assert not (tmp_path / "db-migrations").exists()
    assert not (tmp_path / "python-deps").exists()
    assert sorted(p.name for p in (tmp_path / "licenses").iterdir()) == sorted(dm.bundle_licenses())
    acc = tmp_path / "acceptance"
    if acc.is_dir():
        assert not (acc / "README.md").exists() and not (acc / "real_fixtures.json").exists()


# ── 산출물 검사기 ────────────────────────────────────────────
def test_check_bundle_flags_undeclared_and_missing(tmp_path, manifest):
    for rel in ("setup.sh", "python-deps/wheels/x.whl", "docker-images/beat.tar", "licenses/sbom.json"):
        f = tmp_path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    errs = "\n".join(cdm.check_bundle(manifest, tmp_path))
    assert "python-deps/wheels/x.whl" in errs
    assert "docker-images/beat.tar" in errs
    assert "licenses/sbom.json" in errs
    assert "정본이 요구하는 파일이 번들에 없다" in errs      # 필수 파일이 빠졌다
    assert "정본에 없는 파일이 번들에 있다: setup.sh" not in errs   # 선언된 파일은 통과


def test_check_bundle_flags_extra_hf_revision(tmp_path, manifest):
    base = tmp_path / "models" / "hf" / "hub" / "models--nlpai-lab--KURE-v1"
    (base / "refs").mkdir(parents=True)
    (base / "refs" / "main").write_text("aaaa", encoding="utf-8")
    for rev in ("aaaa", "bbbb"):
        (base / "snapshots" / rev).mkdir(parents=True)
        (base / "snapshots" / rev / "config.json").write_text("{}", encoding="utf-8")
    errs = "\n".join(cdm.check_bundle(manifest, tmp_path))
    assert "HF 캐시 nlpai-lab/KURE-v1" in errs


# ── HF 캐시: refs/main 리비전만 ──────────────────────────────
def _fake_hf_cache(root: Path, main: str, others: list[str]) -> Path:
    cache = root / "models--org--m"
    (cache / "refs").mkdir(parents=True)
    (cache / "refs" / "main").write_text(main, encoding="utf-8")
    for rev in [main, *others]:
        (cache / "snapshots" / rev).mkdir(parents=True)
        (cache / "snapshots" / rev / "model.safetensors").write_bytes(b"weights")
        (cache / ".no_exist" / rev).mkdir(parents=True)
        (cache / ".no_exist" / rev / "adapter_config.json").write_bytes(b"")
    return cache


def test_hf_copy_keeps_only_main_revision(tmp_path):
    src = _fake_hf_cache(tmp_path / "src", "8b41", ["d14c"])
    dst = tmp_path / "dst" / src.name
    shutil.copytree(src, dst, ignore=_hf_keep_main_revision(src))
    assert sorted(p.name for p in (dst / "snapshots").iterdir()) == ["8b41"]
    assert sorted(p.name for p in (dst / ".no_exist").iterdir()) == ["8b41"]
    assert (dst / "refs" / "main").read_text(encoding="utf-8") == "8b41"
    assert (dst / "snapshots" / "8b41" / "model.safetensors").is_file()


def test_hf_copy_keeps_everything_when_main_ref_is_missing(tmp_path):
    """refs/main 이 없으면 무엇이 최신인지 모른다 — 거르지 않는다(안전 쪽)."""
    src = _fake_hf_cache(tmp_path / "src", "8b41", ["d14c"])
    (src / "refs" / "main").unlink()
    dst = tmp_path / "dst" / src.name
    shutil.copytree(src, dst, ignore=_hf_keep_main_revision(src))
    assert sorted(p.name for p in (dst / "snapshots").iterdir()) == ["8b41", "d14c"]


def test_prune_unreferenced_blobs_only_for_symlink_layout(tmp_path):
    cache = tmp_path / "models--org--m"
    (cache / "blobs").mkdir(parents=True)
    (cache / "snapshots" / "aaaa").mkdir(parents=True)
    (cache / "blobs" / "keep").write_bytes(b"1")
    (cache / "blobs" / "orphan").write_bytes(b"2")
    try:
        os.symlink("../../blobs/keep", cache / "snapshots" / "aaaa" / "config.json")
    except (OSError, NotImplementedError):
        pytest.skip("이 환경은 심볼릭 링크를 만들 수 없다")
    assert _prune_unreferenced_blobs(cache) == 1
    assert (cache / "blobs" / "keep").exists() and not (cache / "blobs" / "orphan").exists()


def test_prune_leaves_real_file_layout_alone(tmp_path):
    """Windows 복사본(스냅샷에 실파일)은 blobs 를 건드리지 않는다."""
    cache = tmp_path / "models--org--m"
    (cache / "blobs").mkdir(parents=True)
    (cache / "snapshots" / "aaaa").mkdir(parents=True)
    (cache / "blobs" / "x").write_bytes(b"1")
    (cache / "snapshots" / "aaaa" / "config.json").write_bytes(b"{}")
    assert _prune_unreferenced_blobs(cache) == 0
    assert (cache / "blobs" / "x").exists()
