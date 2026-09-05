# -*- coding: utf-8 -*-
"""코드 부채 전수조사 — 죽은 모듈·세대 중복·주석처리 코드·설명 과다·중복 본문.

왜 또 만드나(2026-09-05).
  `audit_unused.py` 는 **표·컬럼·엔드포인트·설정**을 센다. `audit_wiring.py` 는 **정의 단위**
  참조 0 을 센다. 둘 다 파일보다 작은 단위다. "불필요한 소스코드가 있느냐"는 물음은 그보다
  큰 단위 — **파일이 통째로 죽었는지, 같은 일을 하는 세대가 몇 벌 쌓였는지** — 를 묻는데
  그걸 세는 도구가 없었다. 기억으로 후보를 떠올려 grep 하면 하한선만 나온다.

무엇을 세는가.
  ① 죽은 모듈     src/koipa 의 .py 중 코드·스크립트·시험·설정 어디서도 import 되지 않는 것
  ② 세대 중복     scripts/ 에서 같은 어간에 v2·v3_1·_v4 같은 세대 접미사가 붙어 쌓인 파일 군
  ③ 주석처리 코드 주석줄 중 실제로 파이썬으로 파싱되는 것(설명이 아니라 죽은 코드다)
  ④ 설명 과다     파일별 (docstring+주석) 비율 상위 — 코드보다 설명이 긴 파일
  ⑤ 중복 본문     서로 다른 파일에서 AST 구조가 완전히 같은 함수 본문
  ⑥ 거대 단위     라인 수 상위 파일·함수
  ⑦ 표식          TODO·FIXME·XXX·HACK 잔여

무엇을 판정하지 않는가.
  이 스크립트는 **세기만 한다.** "죽었다"와 "지워도 된다"는 다르다 — 폐쇄망 번들이 파일명을
  문자열로 부르거나, 감리 산출물이 그 경로를 인용하거나, 아직 배선 전인 것이 섞인다.
  남는 목록을 사람이 읽고 판단한다.

사용:
    python scripts/audit_code_debt.py            # 요약
    python scripts/audit_code_debt.py --detail   # 항목별 전체 목록
    python scripts/audit_code_debt.py --json out.json
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

# 한국어 Windows 콘솔은 cp949 다. em dash 하나에 출력이 통째로 죽어 결과를 못 읽는 일이
# 실제로 있었다(audit_unused.py, 2026-09-05). 문자를 쫓지 말고 출구를 고정한다.
for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        setattr(sys, _s, io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
_PKG = _SRC / "koipa"
_SCRIPTS = _ROOT / "scripts"
_TESTS = _ROOT / "tests"
_SELF = Path(__file__).resolve()


def _read(p: Path) -> str:
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _py(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        p for p in root.rglob("*.py")
        if "__pycache__" not in str(p) and ".venv" not in str(p)
    )


def _mod_name(p: Path) -> str:
    """src/koipa/a/b.py -> koipa.a.b · src/koipa/a/__init__.py -> koipa.a"""
    rel = p.relative_to(_SRC).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


# ---------------------------------------------------------------- ① 죽은 모듈

def audit_dead_modules() -> dict:
    """src/koipa 의 모듈 중 아무도 import 하지 않는 것.

    참조는 세 갈래로 센다.
      · AST import  — `import koipa.a.b` / `from koipa.a.b import x` / `from .b import x`
      · 문자열      — "koipa.a.b" 가 코드·compose·Dockerfile·pyproject 문자열에 있는 경우
                      (celery include, uvicorn app 경로, entry_points 가 이렇게 부른다)
      · 패키지 재수출 — 상위 __init__ 이 `from .b import x` 로 끌어올린 경우
    """
    modules = {_mod_name(p): p for p in _py(_PKG)}
    referenced: set[str] = set()

    def _note(dotted: str) -> None:
        # koipa.a.b.func 처럼 심볼까지 붙은 경로도 모듈까지 잘라 인정한다.
        parts = dotted.split(".")
        while parts:
            cand = ".".join(parts)
            if cand in modules:
                referenced.add(cand)
                return
            parts.pop()

    scan_py = _py(_SRC) + _py(_SCRIPTS) + _py(_TESTS)
    for path in scan_py:
        if path.resolve() == _SELF:
            continue
        text = _read(path)
        try:
            tree = ast.parse(text)
        except SyntaxError:
            tree = None
        here = _mod_name(path) if path.is_relative_to(_SRC) else ""
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        _note(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.level and here:
                        # 상대 import 를 절대 경로로 편다.
                        base = here.split(".")
                        # __init__ 이면 자기 패키지가 base, 아니면 부모가 base
                        if path.name != "__init__.py":
                            base = base[:-1]
                        base = base[: len(base) - (node.level - 1)] if node.level > 1 else base
                        mod = ".".join(base + ([node.module] if node.module else []))
                        _note(mod)
                        for alias in node.names:
                            _note(f"{mod}.{alias.name}")
                    elif node.module:
                        _note(node.module)
                        for alias in node.names:
                            _note(f"{node.module}.{alias.name}")

    # 문자열 참조 — compose·Dockerfile·pyproject·ini·yml·env 까지 훑는다.
    extra_globs = ("*.toml", "*.cfg", "*.ini", "*.yml", "*.yaml", "Dockerfile*", "*.env", "*.sh", "*.json")
    blob_parts = [_read(p) for p in scan_py if p.resolve() != _SELF]
    for pattern in extra_globs:
        for p in _ROOT.rglob(pattern):
            if ".venv" in str(p) or "node_modules" in str(p) or "__pycache__" in str(p):
                continue
            blob_parts.append(_read(p))
    blob = "\n".join(blob_parts)
    for name in modules:
        if name in referenced:
            continue
        # 점 경로 또는 콜론 경로(uvicorn koipa.api.app:app)로 언급되는지
        if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", blob):
            referenced.add(name)

    dead = sorted(set(modules) - referenced)
    # 진입점은 아무도 import 하지 않아도 죽은 것이 아니다.
    entry = {"koipa", "koipa.api.app", "koipa.workers.celery_app"}
    return {
        "total": len(modules),
        "dead": [d for d in dead if d not in entry],
        "paths": {d: str(modules[d].relative_to(_ROOT)) for d in dead},
        "lines": {d: len(_read(modules[d]).splitlines()) for d in dead},
    }


# ---------------------------------------------------------------- ② 세대 중복

_GEN_SUFFIX = re.compile(r"^(?P<stem>.+?)[._](?:v)?(?P<gen>\d+(?:[._]\d+)*)$")


def audit_generation_dupes() -> dict:
    """같은 어간에 세대 접미사가 붙어 쌓인 파일 군(scripts/ + src/)."""
    groups: dict[str, list[tuple[str, Path]]] = defaultdict(list)
    for root in (_SCRIPTS, _PKG):
        for p in _py(root):
            stem = p.stem
            m = _GEN_SUFFIX.match(stem)
            if not m:
                continue
            groups[f"{p.parent.relative_to(_ROOT)}/{m.group('stem')}"].append(
                (m.group("gen"), p)
            )
    out = []
    for key, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        out.append({
            "stem": key,
            "count": len(members),
            "lines": sum(len(_read(p).splitlines()) for _, p in members),
            "members": [str(p.relative_to(_ROOT)) for _, p in sorted(members)],
        })
    out.sort(key=lambda d: -d["lines"])
    return {"groups": out,
            "files": sum(g["count"] for g in out),
            "lines": sum(g["lines"] for g in out)}


# ---------------------------------------------------------- ③ 주석처리된 코드

_COMMENT_CODE_HINT = re.compile(
    r"^\s*(?:import |from \w+ import |def |class |return |raise |print\(|if .+:|for .+:|"
    r"while .+:|try:|except|with .+:|assert |yield |elif .+:|else:|@\w+|\w+\s*=\s*\S)"
)
# 설명 문장인데 코드처럼 보이는 것을 거른다(한글이 섞이면 설명이다).
_HANGUL = re.compile(r"[가-힣]")


def audit_commented_code(paths: list[Path]) -> dict:
    hits: list[dict] = []
    total_comment_lines = 0
    for p in paths:
        if p.resolve() == _SELF:
            continue
        lines = _read(p).splitlines()
        run: list[tuple[int, str]] = []

        def _flush() -> None:
            # 연속 2줄 이상이 코드로 파싱될 때만 '주석처리된 코드'로 센다.
            if len(run) < 2:
                run.clear()
                return
            body = "\n".join(t for _, t in run)
            try:
                ast.parse(body)
            except SyntaxError:
                run.clear()
                return
            hits.append({
                "file": str(p.relative_to(_ROOT)),
                "line": run[0][0],
                "lines": len(run),
                "head": run[0][1].strip()[:70],
            })
            run.clear()

        for i, raw in enumerate(lines, 1):
            stripped = raw.strip()
            if stripped.startswith("#"):
                total_comment_lines += 1
                text = stripped.lstrip("#")
                # 들여쓰기를 보존해야 블록이 파싱된다.
                body = raw.replace("#", " ", 1)
                if _COMMENT_CODE_HINT.match(body) and not _HANGUL.search(text):
                    run.append((i, body))
                    continue
            _flush()
        _flush()
    hits.sort(key=lambda h: -h["lines"])
    return {"hits": hits, "comment_lines": total_comment_lines}


# ------------------------------------------------------------- ④ 설명 밀도

def _doc_and_comment_lines(path: Path) -> tuple[int, int, int]:
    """(전체줄, docstring줄, 주석줄)"""
    text = _read(path)
    lines = text.splitlines()
    comment = sum(1 for ln in lines if ln.strip().startswith("#"))
    doc = 0
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return len(lines), 0, comment
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ds = ast.get_docstring(node, clean=False)
            if ds:
                doc += len(ds.splitlines())
    return len(lines), doc, comment


def audit_doc_density(paths: list[Path], *, min_lines: int = 120) -> dict:
    rows = []
    tot_lines = tot_doc = tot_comment = 0
    for p in paths:
        if p.resolve() == _SELF:
            continue
        n, d, c = _doc_and_comment_lines(p)
        tot_lines += n
        tot_doc += d
        tot_comment += c
        if n >= min_lines:
            rows.append({
                "file": str(p.relative_to(_ROOT)),
                "lines": n,
                "prose": d + c,
                "ratio": round((d + c) / n, 3),
            })
    rows.sort(key=lambda r: -r["ratio"])
    return {
        "rows": rows,
        "total_lines": tot_lines,
        "total_prose": tot_doc + tot_comment,
        "ratio": round((tot_doc + tot_comment) / tot_lines, 3) if tot_lines else 0.0,
    }


# ------------------------------------------------------------- ⑤ 중복 본문

def _norm_dump(node: ast.AST) -> str:
    """이름을 지운 구조 해시 — 변수명만 다른 복붙을 같은 것으로 본다."""
    class _Strip(ast.NodeTransformer):
        def visit_Name(self, n):  # noqa: N802
            return ast.copy_location(ast.Name(id="_", ctx=n.ctx), n)
        def visit_arg(self, n):  # noqa: N802
            return ast.copy_location(ast.arg(arg="_", annotation=None), n)
        def visit_Constant(self, n):  # noqa: N802
            return ast.copy_location(ast.Constant(value="_"), n)
    stripped = _Strip().visit(ast.parse(ast.unparse(node)))
    return ast.dump(stripped)


def audit_duplicate_bodies(paths: list[Path], *, min_stmts: int = 6) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    checked = 0
    for p in paths:
        if p.resolve() == _SELF:
            continue
        try:
            tree = ast.parse(_read(p))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = [n for n in node.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant))]
            if len(body) < min_stmts:
                continue
            checked += 1
            try:
                key = hashlib.sha1(
                    "".join(_norm_dump(n) for n in body).encode("utf-8")
                ).hexdigest()
            except (SyntaxError, RecursionError, ValueError):
                continue
            buckets[key].append({
                "file": str(p.relative_to(_ROOT)),
                "name": node.name,
                "line": node.lineno,
                "stmts": len(body),
            })
    dupes = []
    for key, members in buckets.items():
        files = {m["file"] for m in members}
        if len(members) < 2 or len(files) < 2:
            continue
        dupes.append({"stmts": members[0]["stmts"], "members": members})
    dupes.sort(key=lambda d: -(d["stmts"] * len(d["members"])))
    return {"checked": checked, "dupes": dupes}


# ------------------------------------------------------------- ⑥ 거대 단위

def audit_size(paths: list[Path]) -> dict:
    files, funcs = [], []
    for p in paths:
        if p.resolve() == _SELF:
            continue
        text = _read(p)
        n = len(text.splitlines())
        files.append({"file": str(p.relative_to(_ROOT)), "lines": n})
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(node, "end_lineno", node.lineno) or node.lineno
                funcs.append({
                    "file": str(p.relative_to(_ROOT)),
                    "name": node.name,
                    "line": node.lineno,
                    "lines": end - node.lineno + 1,
                })
    files.sort(key=lambda r: -r["lines"])
    funcs.sort(key=lambda r: -r["lines"])
    return {"files": files, "funcs": funcs}


# ---------------------------------------------------------------- ⑦ 표식

_MARK = re.compile(r"\b(TODO|FIXME|XXX|HACK|DEPRECATED)\b")


def audit_markers(paths: list[Path]) -> dict:
    hits = []
    for p in paths:
        if p.resolve() == _SELF:
            continue
        for i, ln in enumerate(_read(p).splitlines(), 1):
            m = _MARK.search(ln)
            if m and ("#" in ln or '"""' in ln):
                hits.append({
                    "file": str(p.relative_to(_ROOT)),
                    "line": i,
                    "kind": m.group(1),
                    "text": ln.strip()[:90],
                })
    by_kind: dict[str, int] = defaultdict(int)
    for h in hits:
        by_kind[h["kind"]] += 1
    return {"hits": hits, "by_kind": dict(by_kind)}


# ------------------------------------------------------------------ 보고

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="코드 부채 전수조사")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)

    src_paths = _py(_SRC)
    all_paths = _py(_SRC) + _py(_SCRIPTS) + _py(_TESTS)

    dead = audit_dead_modules()
    gens = audit_generation_dupes()
    cc_src = audit_commented_code(src_paths)
    cc_all = audit_commented_code(all_paths)
    dens = audit_doc_density(src_paths)
    dens_all = audit_doc_density(all_paths)
    dup = audit_duplicate_bodies(all_paths)
    size = audit_size(src_paths)
    marks = audit_markers(all_paths)

    w = "=" * 76
    print(w)
    print(" 1. 죽은 모듈 — src/koipa 안에서 아무도 import 하지 않는 .py")
    print(w)
    for name in dead["dead"]:
        print(f"  {name:<52} {dead['lines'].get(name, 0):>5}줄  {dead['paths'][name]}")
    print(f"\n  {len(dead['dead'])}건 / 모듈 {dead['total']}개")

    print()
    print(w)
    print(" 2. 세대 중복 — 같은 어간에 v2·v3_1 세대가 쌓인 파일 군")
    print(w)
    for g in gens["groups"][: None if args.detail else args.top]:
        print(f"  {g['stem']:<58} {g['count']:>2}벌 {g['lines']:>6}줄")
        if args.detail:
            for m in g["members"]:
                print(f"      {m}")
    print(f"\n  {len(gens['groups'])}군 · {gens['files']}파일 · {gens['lines']}줄")

    print()
    print(w)
    print(" 3. 주석처리된 코드 — 주석인데 파이썬으로 파싱되는 연속 2줄 이상")
    print(w)
    for h in cc_all["hits"][: None if args.detail else args.top]:
        print(f"  {h['file']}:{h['line']}  {h['lines']}줄  {h['head']}")
    print(f"\n  {len(cc_all['hits'])}건 / 주석줄 {cc_all['comment_lines']}줄"
          f"  (src 만: {len(cc_src['hits'])}건)")

    print()
    print(w)
    print(" 4. 설명 밀도 — (docstring+주석)/전체줄, 120줄 이상 파일")
    print(w)
    for r in dens["rows"][: None if args.detail else args.top]:
        print(f"  {r['ratio']:.3f}  {r['prose']:>5}/{r['lines']:<5}  {r['file']}")
    print(f"\n  src 전체 {dens['total_prose']}/{dens['total_lines']}줄 = {dens['ratio']:.3f}"
          f"  ·  src+scripts+tests = {dens_all['ratio']:.3f}")

    print()
    print(w)
    print(" 5. 중복 본문 — 서로 다른 파일에서 구조가 완전히 같은 함수")
    print(w)
    for d in dup["dupes"][: None if args.detail else args.top]:
        names = " · ".join(f"{m['file']}:{m['line']} {m['name']}" for m in d["members"])
        print(f"  {d['stmts']}문 x{len(d['members'])}  {names}")
    print(f"\n  {len(dup['dupes'])}군 / 검사한 함수 {dup['checked']}개(6문 이상)")

    print()
    print(w)
    print(" 6. 거대 단위 — src/ 파일·함수 라인 수 상위")
    print(w)
    print("  [파일]")
    for r in size["files"][: args.top]:
        print(f"    {r['lines']:>5}줄  {r['file']}")
    print("  [함수]")
    for r in size["funcs"][: args.top]:
        print(f"    {r['lines']:>5}줄  {r['file']}:{r['line']} {r['name']}")

    print()
    print(w)
    print(" 7. 표식 — TODO·FIXME·XXX·HACK·DEPRECATED")
    print(w)
    print(f"  {marks['by_kind']}")
    for h in marks["hits"][: None if args.detail else args.top]:
        print(f"    {h['file']}:{h['line']} [{h['kind']}] {h['text']}")
    print(f"\n  {len(marks['hits'])}건")

    print()
    print(w)
    print(" 요약")
    print(w)
    print(f"  죽은 모듈        {len(dead['dead']):>4}건 / {dead['total']} 모듈")
    print(f"  세대 중복        {gens['files']:>4}파일 {gens['lines']}줄 / {len(gens['groups'])}군")
    print(f"  주석처리 코드    {len(cc_all['hits']):>4}건")
    print(f"  설명 밀도(src)   {dens['ratio']:>6.3f}")
    print(f"  중복 본문        {len(dup['dupes']):>4}군 / 검사 {dup['checked']}함수")
    print(f"  표식             {len(marks['hits']):>4}건")
    print()
    print("  주의 — 이 수치는 '세었다'는 뜻이지 '지워도 된다'는 뜻이 아니다.")

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"dead_modules": dead, "generations": gens, "commented_code": cc_all,
                 "doc_density": dens, "duplicates": dup, "size": size, "markers": marks},
                ensure_ascii=False, indent=2),
            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
