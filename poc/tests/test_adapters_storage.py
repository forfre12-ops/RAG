"""Storage 어댑터 — LocalStorage put/get/uri."""

from __future__ import annotations

from pathlib import Path

import pytest

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


# --- F: S3 호환 백엔드 키 정규화/traversal 가드 (인프라 불필요 — 순수 staticmethod) ---

@pytest.mark.parametrize(
    "store_path",
    [
        "lloydk.adapters.storage.seaweedfs_store:SeaweedFSStore",
        "lloydk.adapters.storage.minio_store:MinioStorage",
    ],
)
def test_s3_store_norm_key_strips_leading_slash(store_path: str):
    import importlib

    mod_name, cls_name = store_path.split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    # 선행 슬래시 제거(절대키처럼 보이는 입력 방어).
    assert cls._norm_key("/a/b/c") == "a/b/c"
    assert cls._norm_key("a/b") == "a/b"


@pytest.mark.parametrize(
    "store_path",
    [
        "lloydk.adapters.storage.seaweedfs_store:SeaweedFSStore",
        "lloydk.adapters.storage.minio_store:MinioStorage",
    ],
)
def test_s3_store_norm_key_blocks_traversal(store_path: str):
    import importlib

    mod_name, cls_name = store_path.split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    with pytest.raises(ValueError):
        cls._norm_key("../etc/passwd")
    with pytest.raises(ValueError):
        cls._norm_key("a/../../b")
