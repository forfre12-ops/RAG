"""Storage 어댑터 — LocalStorage put/get/uri."""

from __future__ import annotations

from pathlib import Path

import pytest

from koipa.adapters.storage import LocalStorage


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


def test_local_storage_missing_read_does_not_create_dirs(tmp_path: Path):
    st = LocalStorage(root=str(tmp_path))
    assert not st.exists("b", "deep/missing.txt")
    assert not (tmp_path / "b").exists()


def test_local_storage_put_leaves_no_temp_file(tmp_path: Path):
    st = LocalStorage(root=str(tmp_path))
    st.put("b", "deep/file.bin", b"x")
    parent = tmp_path / "b" / "deep"
    assert not list(parent.glob("*.tmp"))


# --- F: S3 호환 백엔드 키 정규화/traversal 가드 (인프라 불필요 — 순수 staticmethod) ---

@pytest.mark.parametrize(
    "store_path",
    [
        "koipa.adapters.storage.seaweedfs_store:SeaweedFSStore",
        "koipa.adapters.storage.minio_store:MinioStorage",
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
        "koipa.adapters.storage.seaweedfs_store:SeaweedFSStore",
        "koipa.adapters.storage.minio_store:MinioStorage",
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


class TestUriRoundTrip:
    """put() 이 돌려준 URI 를 파싱해 다시 get() 하면 같은 내용이 나와야 한다.

    [2026-08-02 실서버 실측 결함] LocalStorage.uri 가 저장 루트까지 URI 에 넣어
    `file://.storage/documents-normalized/…` 를 반환했다. 읽기 쪽은 URI 를
    `<scheme>://<bucket>/<key>` 로 파싱하므로 bucket 이 `.storage` 로 잡혀
    실제로는 `.storage/.storage/…` 를 읽다 매번 실패했다.

    조용히 실패하지 않아서 더 나빴다 — 본문을 못 읽으면 fail-secure 로 TS +
    needs_review 가 되므로, 인프라 결함이 '위험한 문서'로 세탁된다. 실서버에서
    100페이지 문서가 confidence 0.0 · scores 1개 · TS 로 나왔고 모델은 돌지도 않았다.
    왕복이 성립하는지를 계약으로 고정한다.
    """

    def test_uri_excludes_storage_root(self, tmp_path):
        from koipa.adapters.storage.local_store import LocalStorage

        st = LocalStorage(root=str(tmp_path / ".storage"))
        uri = st.put("documents-normalized", "abc/normalized.txt", b"hello")
        assert uri == "file://documents-normalized/abc/normalized.txt"
        assert ".storage" not in uri  # 루트가 새어 나오면 왕복이 깨진다

    def test_roundtrip_through_uri_parse(self, tmp_path):
        """읽기 쪽과 동일한 정규식으로 파싱해 get() 이 성립하는가."""
        import re

        from koipa.adapters.storage.local_store import LocalStorage

        st = LocalStorage(root=str(tmp_path / ".storage"))
        uri = st.put("documents-normalized", "h4sh/normalized.txt", "본문".encode())

        m = re.match(r"(?:s3|minio|file)://([^/]+)/(.+)", uri)
        assert m, uri
        assert st.get(m.group(1), m.group(2)).decode() == "본문"

    def test_legacy_uri_still_readable(self, tmp_path):
        """옛 형식(루트 포함) URI 도 루트 한 겹을 벗기면 읽힌다 — DB 마이그레이션 불요."""
        import re

        from koipa.adapters.storage.local_store import LocalStorage

        root = tmp_path / ".storage"
        st = LocalStorage(root=str(root))
        st.put("documents-normalized", "old/normalized.txt", b"legacy")

        legacy = f"file://{root.as_posix()}/documents-normalized/old/normalized.txt"
        m = re.match(r"(?:s3|minio|file)://([^/]+)/(.+)", legacy)
        bucket, key = m.group(1), m.group(2)
        # classify_service 의 호환 로직과 동일한 벗기기
        rp = st.root.as_posix()
        if bucket == rp.strip("/").split("/")[0] or key.startswith("documents-"):
            pass
        while bucket != "documents-normalized" and "/" in key:
            bucket, key = key.split("/", 1)
        assert st.get(bucket, key) == b"legacy"
