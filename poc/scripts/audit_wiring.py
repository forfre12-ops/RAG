# -*- coding: utf-8 -*-
"""배선 감사 — 정의됐는데 아무도 부르지 않는 것을 전수로 센다.

왜 필요한가(2026-08-26). "배선 안 된 게 많지 않냐"는 물음에 기억나는 이름 몇 개를
grep 해서 답했다. 그건 하한선이지 총계가 아닌데 총계처럼 말했고, 빠뜨린 것(골든셋 →
학습셋)을 사용자가 짚어야 했다. 세는 일은 기억이 아니라 도구가 해야 한다.

세 가지를 본다:
  ① 죽은 정의   src/koipa 안에서 정의됐는데 참조가 한 곳도 없는 함수·메서드
  ② 수동 구간   src/ 가 문자열·주석으로만 언급하는 scripts/*.py — 사람이 손으로 돌려야 이어진다
  ③ 꺼진 기능   현재 프로파일에서 opt-in 플래그의 실제 값

참조 판정은 **AST** 로 한다(ast.Name·ast.Attribute). 문자열 매칭이면 함수가 자기 이름을
로그 문자열에 쓰거나 docstring 에 언급되기만 해도 '쓰이는 중'으로 보인다 — 실제로 그 두
경우(build_training_rows·build_p1_v5_clean)를 놓쳤다.

⚠ 이 스크립트는 판정하지 않는다. 프레임워크가 이름으로 부르는 것(FastAPI 라우트 핸들러,
Celery 태스크, pydantic validator, 콜백)은 소스에 호출부가 없으므로 제외 목록으로 관리한다.
남는 것을 사람이 읽고 '설계상 미사용'인지 '배선 누락'인지 판단한다.

사용: TESTING=1 python scripts/audit_wiring.py
"""
from __future__ import annotations

import ast
import io
import re
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
)
NAME_EXEMPT_PREFIX = ("test_", "_test", "__")
PROTOCOL = {
    "__init__", "__repr__", "__str__", "__enter__", "__exit__", "__aenter__",
    "__aexit__", "__call__", "__eq__", "__hash__", "__len__", "__iter__",
    "__getitem__", "__contains__", "__post_init__", "dispatch", "lifespan",
    "file_response", "handle_starttag", "handle_endtag",
}


def _py_files(root: Path):
    for p in root.rglob("*.py"):
        if "__pycache__" not in str(p):
            yield p


def _tree(p: Path):
    try:
        return ast.parse(p.read_text("utf-8", errors="ignore"))
    except SyntaxError:
        return None


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
    """root 안의 **코드**에서 token 이 참조되는 횟수(문자열·주석 제외)."""
    n = 0
    for p in _py_files(root):
        tree = _tree(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == token:
                n += 1
            elif isinstance(node, ast.Attribute) and node.attr == token:
                n += 1
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                for a in node.names:
                    if (a.asname or a.name).split(".")[-1] == token:
                        n += 1
    return n


def text_mentions_in(root: Path, token: str) -> int:
    """root 안의 원문에서 token 이 등장하는 총 횟수(주석·문자열 포함)."""
    pat = re.compile(r"\b" + re.escape(token) + r"\b")
    return sum(len(pat.findall(p.read_text("utf-8", errors="ignore"))) for p in _py_files(root))


def main() -> int:
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
    # models.py 를 뺀 나머지에서의 참조 수
    outside = {}
    for name in cols:
        n = 0
        for root in (SRC, SCRIPTS, TESTS):
            for p in _py_files(root):
                if p == models:
                    continue
                t = _tree(p)
                if t is None:
                    continue
                for node in ast.walk(t):
                    if isinstance(node, ast.Attribute) and node.attr == name:
                        n += 1
                    elif isinstance(node, ast.keyword) and node.arg == name:
                        n += 1
                    elif isinstance(node, ast.Constant) and node.value == name:
                        n += 1        # 문자열 키로 접근하는 경우(dict·SQL 바인딩)
        outside[name] = n
    dead_cols = sorted((c, cols[c]) for c, n in outside.items() if n == 0)
    for c, t in dead_cols:
        print(f"  {c:<34} {t}")
    print(f"\n  {len(dead_cols)}건 / 검사한 컬럼 {len(cols)}개\n")

    print("=" * 76)
    print(" ④ opt-in 플래그 — 현재 프로파일 실제 값")
    print("=" * 76)
    try:
        sys.path.insert(0, str(_ROOT / "src"))
        from koipa.config import settings  # noqa: PLC0415
        print(f"  프로파일: {getattr(settings, 'deploy_profile', '?')}\n")
        on, off = [], []
        for n in sorted(dir(settings)):
            if not (n.endswith(("_enabled", "_on")) or n.startswith("enable_")):
                continue
            v = getattr(settings, n, None)
            if isinstance(v, bool):
                (on if v else off).append(n)
        for n in on:
            print(f"   ON   {n}")
        for n in off:
            print(f"   off  {n}")
        print(f"\n  켜짐 {len(on)} · 꺼짐 {len(off)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  설정 로드 실패: {type(exc).__name__}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
