# -*- coding: utf-8 -*-
"""배선 감사 — 정의됐는데 아무도 부르지 않는 것을 전수로 센다.

왜 필요한가(2026-08-26). "배선 안 된 게 많지 않냐"는 물음에 기억나는 이름 몇 개를
grep 해서 답했다. 그건 하한선이지 총계가 아닌데 총계처럼 말했고, 빠뜨린 것(골든셋 →
학습셋)을 사용자가 짚어야 했다. 세는 일은 기억이 아니라 도구가 해야 한다.

세 가지를 본다:
  ① 죽은 정의   src/koipa 안에서 정의됐는데 참조가 한 곳도 없는 함수·메서드
  ② 수동 구간   src/ 가 문자열·주석으로만 언급하는 scripts/*.py — 사람이 손으로 돌려야 이어진다
  ③ 꺼진 기능   배포 프로파일별 opt-in 플래그 값(lite/onprem/full 나란히)

참조 판정은 **AST** 로 한다(ast.Name·ast.Attribute). 문자열 매칭이면 함수가 자기 이름을
로그 문자열에 쓰거나 docstring 에 언급되기만 해도 '쓰이는 중'으로 보인다 — 실제로 그 두
경우(build_training_rows·build_p1_v5_clean)를 놓쳤다.

⚠ 이 스크립트는 판정하지 않는다. 프레임워크가 이름으로 부르는 것(FastAPI 라우트 핸들러,
Celery 태스크, pydantic validator, 콜백)은 소스에 호출부가 없으므로 제외 목록으로 관리한다.
남는 것을 사람이 읽고 '설계상 미사용'인지 '배선 누락'인지 판단한다.

사용: TESTING=1 python scripts/audit_wiring.py     (실측 14.5초 · 2026-09-09)

⚠ [2026-09-09] **이 도구는 한동안 돌지 않았다.** ② 는 스크립트마다, ③ 은 컬럼마다
  소스 전체를 다시 파싱해 3분·10분을 넘겨도 끝나지 않았고, argparse 가 없어 `--help`
  조차 전수 감사를 그대로 돌렸다. 한 번 훑어 표를 만들고 조회하도록 바꿨다.
  옛 알고리즘과 스크립트 317개·컬럼 177개 전수 대조 — **어긋난 수 0건**.
  세는 도구가 안 돌면 다음 사람이 다시 손으로 센다. 느려지면 그때 다시 고칠 것.
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import os
import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SRC = _ROOT / "src" / "koipa"
SCRIPTS = _ROOT / "scripts"
TESTS = _ROOT / "tests"

# 프레임워크가 이름으로 호출 → 소스에 호출부가 없다.
DECOR_EXEMPT = (
    "router.", "app.get", "app.post", "app.on_event", "app.exception_handler",
    "app.middleware", "celery_app.task", "shared_task", "field_validator",
    "model_validator", "validator", "root_validator", "property", "setter",
    "staticmethod", "classmethod", "contextmanager", "asynccontextmanager",
    "lru_cache", "cache", "overload", "fixture", "hookimpl", "listens_for",
    # [2026-09-05] Celery 시그널 훅. @worker_process_init.connect 로 붙는 함수는
    # 호출부가 소스에 없다 - _init_worker_logging 이 죽은 정의로 잘못 세어졌다.
    "worker_process_init.connect", "worker_ready.connect", "task_prerun.connect",
    "task_postrun.connect", "task_failure.connect", "connect",
)
NAME_EXEMPT_PREFIX = ("test_", "_test", "__")
PROTOCOL = {
    "__init__", "__repr__", "__str__", "__enter__", "__exit__", "__aenter__",
    "__aexit__", "__call__", "__eq__", "__hash__", "__len__", "__iter__",
    "__getitem__", "__contains__", "__post_init__", "dispatch", "lifespan",
    "file_response", "handle_starttag", "handle_endtag",
    # [2026-09-05] HuggingFace Trainer 가 이름으로 부르는 훅. TrainerCallback 서브클래스와
    # Trainer 서브클래스의 오버라이드라 소스에 호출부가 없다 - 넷 다 오탐이었다.
    "on_train_begin", "on_train_end", "on_step_end", "on_epoch_end",
    "on_evaluate", "on_log", "on_save", "compute_loss",
}


def _py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" not in str(p):
            yield p


# 파일당 한 번만 읽고 한 번만 파싱한다.
#
# 왜(2026-09-09). ② 수동 구간이 스크립트 **하나마다** src/koipa 전체를 다시 파싱하고
# 다시 읽었다(code_refs_in·text_mentions_in). 스크립트가 316개라 같은 트리를 316번
# 만들었고, 그 결과 이 도구는 3분을 넘겨도 끝나지 않아 **아무도 돌리지 못했다.**
# 세는 도구가 안 돌면 다음 사람이 다시 손으로 센다 — 이 도구가 생긴 이유가 그것이다.
_TEXT_CACHE: dict[Path, str] = {}
_TREE_CACHE: dict[Path, "ast.AST | None"] = {}


def _text(p: Path) -> str:
    if p not in _TEXT_CACHE:
        _TEXT_CACHE[p] = p.read_text("utf-8", errors="ignore")
    return _TEXT_CACHE[p]


def _tree(p: Path):
    if p in _TREE_CACHE:
        return _TREE_CACHE[p]
    try:
        tree = ast.parse(_text(p))
    except SyntaxError:
        tree = None
    _TREE_CACHE[p] = tree
    return tree


_TOKEN = re.compile(r"\w+")
_CODE_TOKENS: dict[Path, dict[str, int]] = {}
_TEXT_TOKENS: dict[Path, dict[str, int]] = {}


def _code_tokens(root: Path) -> dict[str, int]:
    """root 안 **코드**의 식별자 빈도(문자열·주석 제외). 루트당 한 번만 센다."""
    if root not in _CODE_TOKENS:
        _CODE_TOKENS[root] = ref_counts([root])
    return _CODE_TOKENS[root]


def _text_tokens(root: Path) -> dict[str, int]:
    """root 안 **원문**의 낱말 빈도(주석·문자열 포함). 루트당 한 번만 센다.

    낱말 경계(``\\b``)로 세던 것과 결과가 같다 — ``\\w+`` 는 밑줄을 낱말에 포함하므로
    ``xx_audit_wiring`` 은 어느 쪽에서도 ``audit_wiring`` 으로 세지 않는다.
    """
    if root not in _TEXT_TOKENS:
        counts: dict[str, int] = {}
        for p in _py_files(root):
            for tok in _TOKEN.findall(_text(p)):
                counts[tok] = counts.get(tok, 0) + 1
        _TEXT_TOKENS[root] = counts
    return _TEXT_TOKENS[root]


def collect_defs(root: Path) -> dict[str, list[tuple[str, int]]]:
    defs: dict[str, list[tuple[str, int]]] = {}
    for p in _py_files(root):
        tree = _tree(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in PROTOCOL or node.name.startswith(NAME_EXEMPT_PREFIX):
                continue
            decs = " ".join(ast.unparse(d) for d in node.decorator_list)
            if any(e in decs for e in DECOR_EXEMPT):
                continue
            defs.setdefault(node.name, []).append((str(p.relative_to(_ROOT)), node.lineno))
    return defs


def ref_counts(roots: list[Path]) -> dict[str, int]:
    """실제 참조만 센다 — 문자열·주석·docstring 은 AST 에 없으므로 애초에 제외된다."""
    counts: dict[str, int] = {}
    for root in roots:
        if not root.exists():
            continue
        for p in _py_files(root):
            tree = _tree(p)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Name):
                    counts[node.id] = counts.get(node.id, 0) + 1
                elif isinstance(node, ast.Attribute):
                    counts[node.attr] = counts.get(node.attr, 0) + 1
                elif isinstance(node, (ast.ImportFrom, ast.Import)):
                    for a in node.names:
                        nm = (a.asname or a.name).split(".")[-1]
                        counts[nm] = counts.get(nm, 0) + 1
    return counts


def code_refs_in(root: Path, token: str) -> int:
    """root 안의 **코드**에서 token 이 참조되는 횟수(문자열·주석 제외). 표 조회다."""
    return _code_tokens(root).get(token, 0)


def text_mentions_in(root: Path, token: str) -> int:
    """root 안의 원문에서 token 이 등장하는 총 횟수(주석·문자열 포함). 표 조회다."""
    return _text_tokens(root).get(token, 0)


def main() -> int:
    # 인자를 받지 않지만 파서를 둔다 — 종전에는 `--help` 를 **조용히 무시하고** 전수
    # 감사를 그대로 돌려, 사용법을 물어본 사람이 몇 분을 기다렸다(2026-09-09).
    argparse.ArgumentParser(description=__doc__.splitlines()[0], epilog="인자 없음").parse_args()
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    defs = collect_defs(SRC)
    refs = ref_counts([SRC, SCRIPTS, TESTS])

    dead = [(name, f, ln) for name, sites in defs.items()
            if refs.get(name, 0) == 0 for f, ln in sites]

    print("=" * 76)
    print(" ① 죽은 정의 — 정의됐는데 코드 참조가 0건 (AST 기준)")
    print("=" * 76)
    for name, f, ln in sorted(dead, key=lambda x: x[1]):
        print(f"  {name:<42} {f}:{ln}")
    print(f"\n  {len(dead)}건 / 검사한 정의 {len(defs)}개\n")

    print("=" * 76)
    print(" ② 수동 구간 — src/ 가 문자열·주석으로만 언급하는 scripts/*.py")
    print("=" * 76)
    manual = []
    for sp in sorted(SCRIPTS.glob("*.py")):
        stem = sp.stem
        if code_refs_in(SRC, stem) == 0:
            m = text_mentions_in(SRC, stem)
            if m:
                manual.append((stem, m))
    for stem, m in sorted(manual, key=lambda x: -x[1]):
        print(f"  {stem:<46} 언급 {m}회 · 코드 참조 0회")
    print(f"\n  {len(manual)}건\n")

    print("=" * 76)
    print(" ③ 죽은 컬럼 — 모델에 정의됐는데 models.py 밖에서 참조 0건")
    print("=" * 76)
    models = SRC / "db" / "models.py"
    cols: dict[str, str] = {}          # 컬럼명 → 테이블명
    tree = _tree(models)
    if tree is not None:
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            table = None
            names: list[str] = []
            for st in node.body:
                if isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__":
                    table = getattr(st.value, "value", None)
                elif isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    src_txt = ast.unparse(st.value) if st.value else ""
                    if "mapped_column" in src_txt or "relationship" in src_txt:
                        names.append(st.target.id)
            if table:
                for n in names:
                    cols[n] = table
    # models.py 를 뺀 나머지에서의 참조 수 — **한 번 훑어 표를 만들고** 컬럼마다 조회한다.
    # 종전에는 컬럼 하나마다 세 폴더의 AST 를 통째로 다시 걸었다(컬럼 × 파일 × 노드).
    # 이 절이 이 도구가 몇 분씩 걸리던 주된 이유였다(2026-09-09).
    seen: dict[str, int] = {}
    for root in (SRC, SCRIPTS, TESTS):
        if not root.exists():
            continue
        for p in _py_files(root):
            if p == models:
                continue
            t = _tree(p)
            if t is None:
                continue
            for node in ast.walk(t):
                if isinstance(node, ast.Attribute):
                    seen[node.attr] = seen.get(node.attr, 0) + 1
                elif isinstance(node, ast.keyword) and node.arg:
                    seen[node.arg] = seen.get(node.arg, 0) + 1
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    # 문자열 키로 접근하는 경우(dict·SQL 바인딩)
                    seen[node.value] = seen.get(node.value, 0) + 1
    outside = {name: seen.get(name, 0) for name in cols}
    dead_cols = sorted((c, cols[c]) for c, n in outside.items() if n == 0)
    for c, t in dead_cols:
        print(f"  {c:<34} {t}")
    print(f"\n  {len(dead_cols)}건 / 검사한 컬럼 {len(cols)}개\n")

    print("=" * 76)
    print(" 4. opt-in 플래그 - 배포 프로파일별 실제 값")
    print("=" * 76)
    # 프로파일 하나만 찍으면 오해가 난다. 기본값 lite-noapi 는 이 사업의 배포 대상이
    # 아니라서 "안전 게이트가 잔뜩 꺼져 있다"고 잘못 읽힌다. 셋을 나란히 놓는다.
    PROFILES = ("lite-noapi", "onprem-local", "full-train")
    DEPLOYED = ("onprem-local", "full-train")
    try:
        sys.path.insert(0, str(_ROOT / "src"))
        table: dict[str, dict[str, bool]] = {}
        for prof in PROFILES:
            os.environ["DEPLOY_PROFILE"] = prof
            import koipa.config as _cfg  # noqa: PLC0415
            importlib.reload(_cfg)
            st = _cfg.settings
            row = {}
            for n in sorted(dir(st)):
                if not (n.endswith(("_enabled", "_on")) or n.startswith("enable_")):
                    continue
                v = getattr(st, n, None)
                if isinstance(v, bool):
                    row[n] = v
            table[prof] = row
        names = sorted({n for r in table.values() for n in r})
        print(f"  {'플래그':<38}{'lite-noapi':>12}{'onprem-local':>14}{'full-train':>12}")
        print("  " + "-" * 74)
        for n in names:
            cells = "".join(
                f"{('ON' if table[p].get(n) else 'off'):>{w}}"
                for p, w in zip(PROFILES, (12, 14, 12))
            )
            # 배포 프로파일 둘 다에서 꺼진 것에만 표시를 단다.
            mark = "  <-- 배포본에서 꺼짐" if not any(table[p].get(n) for p in DEPLOYED) else ""
            print(f"  {n:<38}{cells}{mark}")
        off_in_deploy = [n for n in names if not any(table[p].get(n) for p in DEPLOYED)]
        print(f"\n  배포 프로파일(onprem-local/full-train) 양쪽에서 꺼진 플래그 "
              f"{len(off_in_deploy)} / {len(names)}")
        print("  (꺼짐 자체가 결함은 아니다. 코드 주석에 적힌 의도를 확인할 것)")
    except Exception as exc:  # noqa: BLE001
        print(f"  설정 로드 실패: {type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("DEPLOY_PROFILE", None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
