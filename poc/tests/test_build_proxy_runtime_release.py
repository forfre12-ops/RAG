"""Deterministic and fail-closed proxy runtime release tests."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
import tarfile

import pytest

from scripts import build_proxy_runtime_release as release


_ROOT = Path(__file__).resolve().parents[1]


def _write_cli(root: Path, body: str) -> Path:
    path = root / "scripts" / "main.py"
    path.parent.mkdir(parents=True)
    path.write_text(body, encoding="utf-8")
    return path


_WORKING_CLI = """\
import argparse

def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--value')
    parser.parse_args(argv)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
"""


def test_default_allowlist_contains_runtime_closure_but_no_sensitive_data():
    files = release.collect_release_files(_ROOT)
    paths = {item.path for item in files}

    assert "scripts/judge_proxy_candidates.py" in paths
    assert "scripts/run_proxy_generation_shards.py" in paths
    assert "scripts/run_proxy_judging_shards.py" in paths
    assert "scripts/finalize_proxy_classifier.py" in paths
    assert "scripts/attest_legacy_training_corpus.py" in paths
    assert "docs/LEGACY_RAW_MODEL_PROVENANCE.md" in paths
    assert "src/lloydk/proxy_training_finalization.py" in paths
    assert "scripts/__init__.py" in paths
    assert "scripts/build_synthetic_golden.py" not in paths
    assert all(
        f"{module.replace('.', '/')}.py" in paths
        for module in release.REQUIRED_ENTRYPOINTS
    )
    assert not any("__pycache__" in path for path in paths)
    assert not any(path.endswith((".pyc", ".jsonl")) for path in paths)
    assert not any("korea_policy_runs" in path for path in paths)
    assert not any("public_s3_challenges" in path for path in paths)
    assert not any("public-s3-300-blind" in path for path in paths)


def test_release_is_byte_reproducible_with_fixed_metadata(tmp_path: Path):
    source = tmp_path / "source"
    cli = _write_cli(source, _WORKING_CLI)
    first = tmp_path / "first.tar.gz"
    second = tmp_path / "second.tar.gz"
    kwargs = {
        "source_date_epoch": 123,
        "allowlist_files": ("scripts/main.py",),
        "python_trees": (),
        "required_entrypoints": ("scripts.main",),
    }

    first_result = release.build_runtime_release(source, first, **kwargs)
    os.utime(cli, (1_999_999_999, 1_999_999_999))
    second_result = release.build_runtime_release(source, second, **kwargs)

    assert first.read_bytes() == second.read_bytes()
    assert first_result.archive_sha256 == second_result.archive_sha256
    assert first_result.content_sha256 == second_result.content_sha256
    assert int.from_bytes(first.read_bytes()[4:8], "little") == 123
    with tarfile.open(first, "r:gz") as archive:
        members = archive.getmembers()
        assert [member.name for member in members] == sorted(
            member.name for member in members
        )
        assert all(member.isfile() for member in members)
        assert all(member.mtime == 123 for member in members)
        assert all(member.uid == member.gid == 0 for member in members)
        assert all(member.mode == 0o644 for member in members)


def test_missing_local_import_fails_before_publication(tmp_path: Path):
    source = tmp_path / "source"
    _write_cli(
        source,
        "import scripts.build_synthetic_golden\n" + _WORKING_CLI,
    )
    output = tmp_path / "broken.tar.gz"

    with pytest.raises(
        release.ProxyRuntimeReleaseError,
        match="entrypoint import closure failed",
    ):
        release.build_runtime_release(
            source,
            output,
            allowlist_files=("scripts/main.py",),
            python_trees=(),
            required_entrypoints=("scripts.main",),
        )
    assert not output.exists()


def test_source_drift_during_verification_fails_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    source = tmp_path / "source"
    cli = _write_cli(source, _WORKING_CLI)
    output = tmp_path / "drifted.tar.gz"

    def mutate_source(
        candidate,
        *,
        source_root,
        required_entrypoints,
        allowlist_files,
        python_trees,
        generated_files,
    ):
        cli.write_text(
            _WORKING_CLI + "\n# changed during verification\n", encoding="utf-8"
        )
        return {
            "archive_sha256": "a" * 64,
            "content_sha256": "b" * 64,
            "manifest_sha256": "c" * 64,
            "files": 2,
        }

    monkeypatch.setattr(release, "verify_release_archive", mutate_source)
    with pytest.raises(
        release.ProxyRuntimeReleaseError,
        match="source changed while the runtime release was being verified",
    ):
        release.build_runtime_release(
            source,
            output,
            allowlist_files=("scripts/main.py",),
            python_trees=(),
            required_entrypoints=("scripts.main",),
        )
    assert not output.exists()


@pytest.mark.parametrize(
    ("relative", "payload", "message"),
    [
        (".env", b"TOKEN=value\n", "prohibited release path"),
        ("config/secrets/token.txt", b"value\n", "prohibited release path"),
        ("data/raw/document.txt", b"body\n", "prohibited release path"),
        ("data/records.jsonl", b"{}\n", "prohibited release path"),
        (
            "datasets/public-s3-300-blind-20260808-v2/manifest.json",
            b"{}\n",
            "prohibited release path",
        ),
        (
            "scripts/main.py",
            b"-----BEGIN PRIVATE KEY-----\nsecret\n",
            "private-key material",
        ),
    ],
)
def test_secret_raw_and_sealed_payloads_are_rejected_even_if_allowlisted(
    tmp_path: Path,
    relative: str,
    payload: bytes,
    message: str,
):
    source = tmp_path / "source"
    path = source / Path(relative)
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)

    with pytest.raises(release.ProxyRuntimeReleaseError, match=message):
        release.collect_release_files(
            source,
            allowlist_files=(relative,),
            python_trees=(),
            generated_files={},
        )


def test_manifest_content_hash_and_archive_tampering_are_verified(tmp_path: Path):
    source = tmp_path / "source"
    _write_cli(source, _WORKING_CLI)
    output = tmp_path / "runtime.tar.gz"
    result = release.build_runtime_release(
        source,
        output,
        allowlist_files=("scripts/main.py",),
        python_trees=(),
        required_entrypoints=("scripts.main",),
    )

    with gzip.open(output, "rb") as stream:
        assert stream.read(1)
    with tarfile.open(output, "r:gz") as archive:
        manifest_member = archive.getmember(
            f"{release.ARCHIVE_ROOT}/RELEASE_MANIFEST.json"
        )
        manifest_payload = archive.extractfile(manifest_member).read()
        manifest = json.loads(manifest_payload)
        repacked_files = []
        for item in manifest["files"]:
            member = archive.getmember(f"{release.ARCHIVE_ROOT}/{item['path']}")
            member_payload = archive.extractfile(member).read()
            if item["path"] == "scripts/main.py":
                member_payload += b"\n# tampered\n"
            repacked_files.append(
                release.ReleaseFile(
                    item["path"],
                    member_payload,
                    hashlib.sha256(member_payload).hexdigest(),
                )
            )
    descriptor = manifest["files"]
    expected_content = hashlib.sha256(
        release._canonical_json_bytes(descriptor)
    ).hexdigest()
    assert result.content_sha256 == manifest["content_hash"]["sha256"]
    assert result.content_sha256 == expected_content

    tampered = tmp_path / "tampered.tar.gz"
    tampered.write_bytes(
        release._archive_bytes(
            repacked_files,
            manifest_payload,
            source_date_epoch=manifest["source_date_epoch"],
        )
    )
    with pytest.raises(release.ProxyRuntimeReleaseError, match="attestation mismatch"):
        release.verify_release_archive(
            tampered,
            source_root=source,
            required_entrypoints=("scripts.main",),
            allowlist_files=("scripts/main.py",),
            python_trees=(),
        )


def test_published_release_is_never_overwritten(tmp_path: Path):
    source = tmp_path / "source"
    _write_cli(source, _WORKING_CLI)
    output = tmp_path / "runtime.tar.gz"
    kwargs = {
        "allowlist_files": ("scripts/main.py",),
        "python_trees": (),
        "required_entrypoints": ("scripts.main",),
    }
    release.build_runtime_release(source, output, **kwargs)
    original = output.read_bytes()

    with pytest.raises(release.ProxyRuntimeReleaseError, match="refusing to overwrite"):
        release.build_runtime_release(source, output, **kwargs)

    assert output.read_bytes() == original


def test_real_allowlisted_release_passes_all_import_and_help_closures(tmp_path: Path):
    output = tmp_path / "proxy-runtime.tar.gz"
    result = release.build_runtime_release(_ROOT, output)

    assert result.file_count >= 200
    assert len(result.archive_sha256) == 64
    assert output.stat().st_size < 4 * 1024 * 1024
    with tarfile.open(output, "r:gz") as archive:
        names = {member.name for member in archive.getmembers()}
    assert f"{release.ARCHIVE_ROOT}/scripts/judge_proxy_candidates.py" in names
    assert not any("public-s3-300-blind" in name for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)
