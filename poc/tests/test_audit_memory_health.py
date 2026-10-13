"""audit_memory_health.py — 메모리 검사기의 경로 대조(G)가 점으로 시작하는 폴더를 제대로 보는지.

왜 있는가(2026-09-27). 예전 정규화는 `tok.lstrip("./")` 이었다. 그건 "./" 접두가 아니라 "." 과 "/" **문자 집합**을 떼므로
`.github/workflows/ci.yml` 이 `github/workflows/ci.yml` 이 되어 늘 '없음'으로 떴고, 실제로 있는 파일을 죽은 경로 후보로 세고 있었다.
고친 뒤에도 같은 자리를 다시 밟지 않게, 정규화 함수와 실제 리포(임시 git 저장소)를 대조하는 경로 둘을 시험한다.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "scripts"))

import audit_memory_health as tool  # noqa: E402

_GIT = ["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "core.autocrlf=false"]


def _git(repo: Path, *args: str) -> None:
    subprocess.run([*_GIT, "-C", str(repo), *args], check=True, capture_output=True)


def _memory(root: Path, body: str) -> Path:
    mem = root / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [메모](note.md)—시험용\n", encoding="utf-8")
    (mem / "note.md").write_text(
        "---\nname: note\ndescription: 시험용 메모리\n---\n" + body + "\n", encoding="utf-8")
    return mem


def test_norm_path_keeps_a_leading_dot_folder():
    assert tool._norm_path(".github/workflows/ci.yml") == ".github/workflows/ci.yml"
    assert tool._norm_path(".claude/settings.json") == ".claude/settings.json"


def test_norm_path_strips_only_a_dot_slash_prefix_and_backslashes():
    assert tool._norm_path("./poc/a.py") == "poc/a.py"
    assert tool._norm_path("././poc/a.py") == "poc/a.py"
    assert tool._norm_path("poc\\scripts\\a.py") == "poc/scripts/a.py"
    assert tool._norm_path("../poc/a.py") == "../poc/a.py"          # 위 폴더 참조는 그대로 둔다


def test_dot_folder_file_that_exists_is_not_reported_as_gone(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / ".github" / "workflows").mkdir(parents=True)
    (repo / ".github" / "workflows" / "ci.yml").write_text("on: push\n", encoding="utf-8")
    (repo / "poc").mkdir()
    (repo / "poc" / "a.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    mem = _memory(tmp_path, "CI 는 .github/workflows/ci.yml 이고 코드는 poc/a.py 다. poc/gone.py 는 어디에도 없다.")

    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo)])
    assert tool.main() == 0                                            # 구조 결함 없음
    out = capsys.readouterr().out
    assert "언급 3건 중 두 방법 모두 없음 1건" in out                    # 점 폴더의 파일은 있다 — 없는 것은 gone.py 하나뿐
    assert "X poc/gone.py" in out and "X .github" not in out


def test_history_flag_separates_deleted_files_from_names_never_in_history(tmp_path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    (repo / "poc").mkdir(parents=True)
    (repo / "poc" / "old.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "poc" / "keep.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add")
    _git(repo, "rm", "-q", "poc/old.py")
    _git(repo, "commit", "-q", "-m", "drop old")
    mem = _memory(tmp_path, "poc/keep.py 는 산다. poc/old.py 는 지웠다. poc/typo.py 는 이력에도 없다.")

    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo), "--history"])
    assert tool.main() == 0
    out = capsys.readouterr().out
    assert "언급 3건 중 두 방법 모두 없음 2건" in out
    assert "삭제됨" in out and "poc/old.py" in out
    assert "이력에도 없음" in out and "poc/typo.py" in out
    assert "지워진 파일 1건" in out and "git 이력에도 없음 1건" in out
    # --history 를 안 주면 라벨 없이 X 로만 찍는다(기본 출력은 바뀌지 않는다)
    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo)])
    tool.main()
    plain = capsys.readouterr().out
    assert "삭제됨" not in plain and "X poc/old.py" in plain


def test_broken_index_link_is_a_structural_defect(tmp_path, monkeypatch, capsys):
    """대조군 — 이 검사기가 아무것도 안 잡는 것이 아님을 보인다(색인이 없는 파일을 가리키면 종료 코드 1)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    mem = _memory(tmp_path, "본문")
    (mem / "MEMORY.md").write_text("- [메모](note.md)—시험용\n- [없음](missing.md)—없는 파일\n", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo)])
    assert tool.main() == 1
    assert "missing.md" in capsys.readouterr().out


def test_missing_memory_dir_exits_with_2(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(tmp_path / "nope")])
    assert tool.main() == 2


@pytest.mark.parametrize("tok", ["poc/a.py", "a.py", ".github/x.yml", "doc/kor.html"])
def test_path_regex_takes_dot_folders_whole(tok):
    m = tool.PATH_RE.search(f"파일 {tok} 을 본다")
    assert m and m.group(1) == tok


def _strict_yaml_case(tmp_path: Path, description_line: str) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [메모](note.md)—시험용\n", encoding="utf-8")
    (mem / "note.md").write_text(f"---\nname: note\n{description_line}\n---\n본문\n", encoding="utf-8")
    return mem, repo


def test_frontmatter_that_strict_yaml_cannot_read_is_a_structural_defect(tmp_path, monkeypatch, capsys):
    """정규식으로는 통과하지만 YAML 표준으로는 안 읽히는 description(따옴표로 시작해 따옴표로 안 끝남)을 구조 결함으로 센다.

    2026-09-27 에 메모리 7개가 이 모양이었다 — 읽는 쪽이 느슨해서 통했을 뿐이다.
    """
    pytest.importorskip("yaml")
    mem, repo = _strict_yaml_case(tmp_path, 'description: "근거없이"를 말하지 말 것')
    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo)])
    assert tool.main() == 1
    assert "엄격 YAML" in capsys.readouterr().out


def test_description_quoted_with_escaped_inner_quotes_passes_strict_yaml(tmp_path, monkeypatch, capsys):
    """대조군 — 같은 값을 큰따옴표로 감싸고 안쪽 큰따옴표를 이스케이프하면 결함이 아니다."""
    pytest.importorskip("yaml")
    mem, repo = _strict_yaml_case(tmp_path, 'description: "\\"근거없이\\"를 말하지 말 것"')
    monkeypatch.setattr(sys, "argv", ["audit_memory_health.py", "--memory-dir", str(mem), "--repo", str(repo)])
    assert tool.main() == 0
    assert "엄격 YAML" not in capsys.readouterr().out
