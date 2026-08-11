"""소스 계보 가드 — 미커밋 트리에서 finalize 를 막는가.

이 가드가 왜 있는지는 lloydk.source_provenance 모듈 주석 참조(2026-08-09 실측:
미커밋 pipeline.py 가드 118줄이 dev200 F1 을 0.816 → 0.990 으로 바꿨고, 계약 해시는
post-model 서빙 규칙을 명시적으로 제외하므로 그 번들을 막지 못한다).

임시 git 저장소를 실제로 만들어 검증한다 — mock 으로 하면 "git status 출력을 어떻게
파싱하는가"만 보게 되고, 정작 확인해야 할 **미추적 파일이 dirty 로 잡히는가**를 못 본다.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from lloydk.source_provenance import (
    BYPASS_ENV,
    SourceProvenanceError,
    git_provenance,
    require_clean_source_tree,
)


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git 미설치 — 계보 가드 테스트 불가"
)


def _run(cwd, *args):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """src/ 에 파일 하나가 커밋된 깨끗한 저장소."""
    _run(tmp_path, "git", "init", "-q")
    _run(tmp_path, "git", "config", "user.email", "t@example.com")
    _run(tmp_path, "git", "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _run(tmp_path, "git", "add", "-A")
    _run(tmp_path, "git", "commit", "-q", "-m", "init")
    return tmp_path


def test_clean_tree_passes_and_reports_commit(repo):
    provenance = require_clean_source_tree(repo)
    assert provenance["available"] is True
    assert provenance["dirty"] is False
    assert provenance["bypassed"] is False
    assert len(str(provenance["commit"])) == 40


def test_modified_tracked_file_blocks(repo):
    (repo / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(SourceProvenanceError) as exc:
        require_clean_source_tree(repo)
    assert "src/mod.py" in str(exc.value)


def test_untracked_file_blocks(repo):
    """핵심 케이스 — 2026-08-12 실측: 빌더 34개와 소스 모듈 하나가 미추적이었다.

    '수정 없음'이 아니라 '존재하지 않음'이고, HEAD 이미지에는 그 코드가 없다.
    normal 모드 git status 는 디렉터리 하나로 뭉뚱그려 새 파일을 묻어 버린다.
    """
    (repo / "src" / "nested").mkdir()
    (repo / "src" / "nested" / "builder.py").write_text("y = 1\n", encoding="utf-8")
    with pytest.raises(SourceProvenanceError) as exc:
        require_clean_source_tree(repo)
    assert "src/nested/builder.py" in str(exc.value)


def test_dirt_outside_watched_paths_does_not_block(repo):
    """datasets/ 본문·artifacts/ 산출물은 dirty 판정 대상이 아니다.

    넣으면 finalize 가 자기 출력 때문에 항상 실패한다.
    """
    (repo / "artifacts").mkdir()
    (repo / "artifacts" / "out.json").write_text("{}", encoding="utf-8")
    provenance = require_clean_source_tree(repo)
    assert provenance["dirty"] is False


def test_bypass_passes_but_leaves_a_mark(repo, monkeypatch):
    """우회는 막지 않되 흔적을 남긴다 — 조용한 우회는 없다."""
    (repo / "src" / "mod.py").write_text("x = 3\n", encoding="utf-8")
    monkeypatch.setenv(BYPASS_ENV, "1")
    provenance = require_clean_source_tree(repo)
    assert provenance["bypassed"] is True
    assert provenance["dirty"] is True
    assert provenance["dirty_paths"]


def test_non_repo_is_unprovable_not_clean(tmp_path):
    """git 저장소가 아니면 '깨끗하다'가 아니라 '증명할 수 없다' — 통과시키면 안 된다.

    실제로 배포 서버의 ~/poc 에는 git 이 없다. 거기서 finalize 를 돌리면
    계보를 남길 방법이 없으므로 막아야 한다.
    """
    provenance = git_provenance(tmp_path)
    assert provenance["available"] is False
    assert provenance["dirty"] is True
    with pytest.raises(SourceProvenanceError):
        require_clean_source_tree(tmp_path)


def test_bypass_env_only_accepts_explicit_truthy(repo, monkeypatch):
    (repo / "src" / "mod.py").write_text("x = 4\n", encoding="utf-8")
    monkeypatch.setenv(BYPASS_ENV, "0")
    with pytest.raises(SourceProvenanceError):
        require_clean_source_tree(repo)
    monkeypatch.setenv(BYPASS_ENV, "")
    with pytest.raises(SourceProvenanceError):
        require_clean_source_tree(repo)


def test_error_message_names_the_real_mechanism(repo):
    """감사자가 이 메시지만 읽고도 '계약 해시가 막아 준다'는 오해를 안 하게."""
    (repo / "src" / "mod.py").write_text("x = 5\n", encoding="utf-8")
    with pytest.raises(SourceProvenanceError) as exc:
        require_clean_source_tree(repo)
    message = str(exc.value)
    assert "계약 해시는 이걸 막지 못한다" in message
    assert BYPASS_ENV in message
