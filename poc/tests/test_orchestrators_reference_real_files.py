"""일괄 실행기가 **없는 파일**을 부르지 않는가.

왜 이 파일이 있는가(2026-09-06). `scripts/run_all_pocs.py` 가 이걸 부르고 있었다:

    scripts/p2_compare_embeddings.py --mode dryrun

그 파일은 2026-09-04 커밋 319069b9(유사문서 조회 제거)가 지웠다. 실행기는 안 고쳤다.
그래서 PoC 종합 판정이 "FAIL" 로 나왔고, 사유는 **파일이 없어서**였다. 품질 문제처럼
보이는 자리에 배선 문제가 앉아 있었다.

같은 커밋이 `run_perf_scenarios.py` 도 깨뜨렸다(capture_env 인자). 둘 다 시험이 없어
그 커밋은 **"판정면 회귀 0"** 으로 통과했다 — 회귀 게이트는 스크립트를 돌리지 않는다.

이 시험은 돌리지 않고 **가리키는 파일이 있는지**만 본다. 그것만으로 이 부류는 막힌다.
"""
from __future__ import annotations

import ast
import pathlib

_POC = pathlib.Path(__file__).resolve().parents[1]
_SCRIPTS = _POC / "scripts"

# 하위 프로세스로 다른 스크립트를 부르는 실행기들.
_ORCHESTRATORS = ["run_all_pocs.py"]


def _referenced_script_names(py: pathlib.Path) -> list[str]:
    """`_HERE / "이름.py"` 꼴로 가리키는 스크립트 이름을 모은다."""
    tree = ast.parse(py.read_text("utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        # _HERE / "foo.py"  →  BinOp(Name, Div, Constant)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
            right = node.right
            if isinstance(right, ast.Constant) and isinstance(right.value, str):
                if right.value.endswith(".py"):
                    names.append(right.value)
    return names


def test_every_orchestrated_script_exists():
    """가리키는 파일이 전부 있는가.

    없으면 그 단계는 "FAIL" 로 집계되고, 리포트를 읽는 사람은 품질 문제로 읽는다.
    """
    missing = []
    for orch in _ORCHESTRATORS:
        path = _SCRIPTS / orch
        assert path.exists(), "실행기 자체가 없다: %s" % orch
        for name in _referenced_script_names(path):
            if not (_SCRIPTS / name).exists():
                missing.append("%s → %s" % (orch, name))
    assert not missing, "없는 파일을 부른다: %s" % missing


def test_orchestrator_actually_references_something():
    """참조를 하나도 못 뽑으면 위 시험이 늘 통과한다 — 빈 검사가 된다."""
    names = _referenced_script_names(_SCRIPTS / "run_all_pocs.py")
    assert len(names) >= 3, "PoC 실행기가 부르는 스크립트가 %d개뿐: %s" % (len(names), names)


def test_inproc_runner_provides_auth():
    """in-process PoC 는 자기 API 를 부른다 — 키가 없으면 401 로 떨어진다.

    [2026-09-06] P5 가 {"detail":"invalid api key"} 로 실패하고 있었다. 기능 실패처럼
    보이지만 환경 문제였다. run_perf_scenarios 는 dryrun 에서 같은 값을 심는다.
    """
    src = (_SCRIPTS / "run_all_pocs.py").read_text("utf-8")
    assert "_ensure_inproc_env" in src, "in-process 실행 환경을 갖추는 자리가 없다"
    assert "API_KEY" in src, "API 키를 안 심으면 자기 API 호출이 401 이다"
    assert "env=env" in src, "환경을 만들어 놓고 하위 프로세스에 안 넘기면 소용없다"
