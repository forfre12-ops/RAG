"""Storage 어댑터 — LocalStorage put/get/uri."""

from __future__ import annotations

from pathlib import Path

from lloydk.adapters.storage import LocalStorage


def test_local_storage_put_get_roundtrip(tmp_path: Path):
    st = LocalStorage(root=str(tmp_path))
    uri = st.put("bucket1", "dir/file.txt", b"hello")
    assert uri.startswith("file://")
    assert st.exists("bucket1", "dir/file.txt")
    assert st.get("bucket1", "dir/file.txt") == b"hello"
    assert not st.exists("bucket1", "missing.txt")


def test_local_storage_overwrites_existing(tmp_path: Path):
    st = LocalStorage(root=str(tmp_path))
    st.put("b", "k", b"v1")
    st.put("b", "k", b"v2")
    assert st.get("b", "k") == b"v2"


def test_local_storage_creates_parent_dirs(tmp_path: Path):
    st = LocalStorage(root=str(tmp_path))
    st.put("b", "deep/nested/path/file.bin", b"x")
    assert (tmp_path / "b" / "deep" / "nested" / "path" / "file.bin").exists()
