#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""고도화 후보 전수조사 — **아직 안 한 것**을 센다.

왜 또 만드나(2026-09-10). 기존 감사기 셋은 전부 **없앨 것**을 센다.

    audit_unused.py     표·컬럼·엔드포인트·설정 중 안 쓰는 것
    audit_wiring.py     정의 단위 참조 0
    audit_code_debt.py  죽은 모듈·세대 중복·주석처리 코드

"남은 고도화가 무엇이냐"는 반대 방향의 물음이다 — **있어야 하는데 아직 없는 것**과
**만들었는데 안 켠 것**. 그 물음에 답하는 도구가 없어서 매번 기억으로 나열했고,
기억은 분모를 못 낸다.

무엇을 세는가.

  ① 미래형 표시   소스에 "나중에 한다"고 적힌 자리. 마커별로 나눠 센다 —
                  마커를 손으로 고르면 셈이 틀리므로 무엇을 쳤는지 결과에 남긴다.
  ② 미구현 스텁   NotImplementedError · 본문이 pass/... 뿐인 함수.
  ③ 완화 표시     "임시"·"우회"·"한계"처럼 지금 방식이 최선이 아니라고 적은 자리.

무엇을 안 세는가(중복 회피).

  · 배포 프로파일에서 꺼진 opt-in 플래그는 `audit_wiring.py` 가 이미 센다(④ 절).
  · 죽은 정의·수동 구간도 같은 도구가 센다.
  이 도구는 그 둘을 다시 세지 않고, 결과에 "그쪽을 보라"고만 적는다.

대상 선정.
  `git ls-files -z` 로 추적본 전체를 받는다. 폴더나 확장자를 손으로 고르지 않는다 —
  손으로 고르면 빠지고, 빠진 줄 모른다(2026-09-08 에 세 번 연속 그렇게 틀렸다).
  `-z` 는 필수다. 한글 경로가 인용부호로 감싸여 나와 누락되기 때문이다.

    cd poc && python scripts/audit_enhancements.py
    cd poc && python scripts/audit_enhancements.py --json out.json
"""
from __future__ import annotations

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
_REPO = _POC.parent

# 마커는 세 갈래로 나눠 센다. 합쳐 세면 "TODO 200건"이 되어 무엇을 봐야 하는지 알 수 없다.
_FUTURE = {
    "TODO": r"\bTODO\b",
    "FIXME": r"\bFIXME\b",
    "XXX": r"\bXXX\b",
    "미구현": r"미구현",
    "미배선": r"미배선",
    "미적용": r"미적용",
    "추후": r"추후",
    "향후": r"향후",
}
_MITIGATION = {
    "임시": r"임시(조치|방편|로)?",
    "우회": r"우회",
    "한계": r"한계",
    "HACK": r"\bHACK\b",
    "차선": r"차선",
}

# 소스만 본다. 문서(.html/.md)는 주장 감사기(audit_doc_claims.py)의 몫이다.
_CODE_EXT = {".py", ".mjs", ".js", ".yml", ".yaml", ".sh"}


def tracked_files() -> list[Path]:
    """git 추적본 전체. -z 필수 — 한글 경로가 인용부호로 감싸여 누락된다."""
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(_REPO),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    names = [n for n in out.decode("utf-8", "replace").split("\0") if n]
    return [_REPO / n for n in names]


def _py_prose(text: str) -> list[tuple[int, str]]:
    """파이썬 파일에서 **사람이 쓴 산문만** 뽑는다 — 주석과 docstring.

    왜 이렇게까지 하나(2026-09-10 실측). 줄 단위로 훑었더니 시연 문서 본문이 그대로
    걸렸다 — `v8_factor_frames.py` 의 "선점 효과는 향후 시장 상황을 보고 판단한다" 는
    **분류기에 먹일 데이터**이지 "나중에 하겠다"는 표시가 아니다. 그대로 세면
    고도화 후보 100건 중 상당수가 데이터다. 세는 자리를 주석·docstring 으로 좁힌다.
    """
    out: list[tuple[int, str]] = []
    try:
        import io
        import tokenize
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                ln = getattr(node, "lineno", 1)
                for off, line in enumerate(doc.splitlines()):
                    out.append((ln + off, line))
    return out


# 파이썬이 아닌 파일은 AST 가 없다. 주석 접두만으로 거른다 — 정확도는 낮지만
# 데이터 문자열을 통째로 세는 것보다는 낫다. 그 한계를 결과에 적는다.
_COMMENT_PREFIX = re.compile(r"^\s*(#|//|/\*|\*)")


def scan_markers(files: list[Path]) -> tuple[dict, dict, int, int]:
    """마커별 (건수, 위치). 스캔한 파일 수와 산문 줄 수를 함께 돌려준다 — 분모다."""
    fut: dict[str, list] = defaultdict(list)
    mit: dict[str, list] = defaultdict(list)
    scanned, prose_lines = 0, 0
    pats_f = {k: re.compile(v) for k, v in _FUTURE.items()}
    pats_m = {k: re.compile(v) for k, v in _MITIGATION.items()}
    for f in files:
        if f.suffix.lower() not in _CODE_EXT or not f.is_file():
            continue
        scanned += 1
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = f.relative_to(_REPO).as_posix()
        if f.suffix == ".py":
            prose = _py_prose(text)
        else:
            prose = [(i, ln) for i, ln in enumerate(text.splitlines(), 1)
                     if _COMMENT_PREFIX.match(ln)]
        prose_lines += len(prose)
        for i, line in prose:
            for name, rx in pats_f.items():
                if rx.search(line):
                    fut[name].append((rel, i, line.strip()[:110]))
            for name, rx in pats_m.items():
                if rx.search(line):
                    mit[name].append((rel, i, line.strip()[:110]))
    return dict(fut), dict(mit), scanned, prose_lines


def scan_stubs(files: list[Path]) -> tuple[list, int]:
    """NotImplementedError · 본문이 pass/... 뿐인 함수. 파이썬만 — AST 가 있어야 정확하다."""
    hits, checked = [], 0
    for f in files:
        if f.suffix != ".py" or not f.is_file():
            continue
        try:
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        rel = f.relative_to(_REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            checked += 1
            body = [b for b in node.body if not (
                isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant)
                and isinstance(b.value.value, str))]      # docstring 제외
            if not body:
                hits.append((rel, node.lineno, node.name, "본문이 docstring 뿐"))
                continue
            if len(body) == 1:
                only = body[0]
                if isinstance(only, ast.Pass):
                    hits.append((rel, node.lineno, node.name, "pass 뿐"))
                elif (isinstance(only, ast.Raise) and only.exc is not None
                      and isinstance(only.exc, (ast.Call, ast.Name))):
                    nm = (only.exc.func if isinstance(only.exc, ast.Call) else only.exc)
                    if isinstance(nm, ast.Name) and nm.id == "NotImplementedError":
                        hits.append((rel, node.lineno, node.name, "NotImplementedError"))
    return hits, checked


def _section(title: str) -> None:
    print("\n" + "=" * 78)
    print(" " + title)
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    ap.add_argument("--top", type=int, default=12, help="마커별로 보여줄 줄 수")
    a = ap.parse_args()

    files = tracked_files()
    fut, mit, scanned, prose_lines = scan_markers(files)
    stubs, checked_fn = scan_stubs(files)

    _section("① 미래형 표시 — 소스에 '나중에 한다'고 적힌 자리")
    tot_f = sum(len(v) for v in fut.values())
    for name in _FUTURE:
        rows = fut.get(name, [])
        if not rows:
            continue
        print(f"\n  [{name}] {len(rows)}건")
        for rel, ln, txt in rows[:a.top]:
            print(f"    {rel}:{ln}  {txt}")
        if len(rows) > a.top:
            print(f"    … 외 {len(rows) - a.top}건")
    print(f"\n  합계 {tot_f}건 / 코드 파일 {scanned}개의 주석·docstring {prose_lines}줄")
    print(f"  (쳐 본 마커: {', '.join(_FUTURE)})")
    print("  ⚠ 세는 자리는 **주석과 docstring 뿐**이다 — 데이터 문자열은 뺀다.")
    print("     .py 는 tokenize+AST 로 가르고, 그 밖(.mjs/.yml/.sh)은 주석 접두로만 거른다.")

    _section("② 미구현 스텁 — 본문이 비었거나 NotImplementedError")
    by_kind = Counter(k for *_, k in stubs)
    for kind, n in by_kind.most_common():
        print(f"  {kind:24s} {n}건")
    for rel, ln, name, kind in stubs[:a.top]:
        print(f"    {rel}:{ln}  {name}  ({kind})")
    if len(stubs) > a.top:
        print(f"    … 외 {len(stubs) - a.top}건")
    print(f"\n  {len(stubs)}건 / 검사한 함수 {checked_fn}개")

    _section("③ 완화 표시 — 지금 방식이 최선이 아니라고 적은 자리")
    tot_m = sum(len(v) for v in mit.values())
    for name in _MITIGATION:
        rows = mit.get(name, [])
        if rows:
            print(f"  {name:8s} {len(rows):4d}건")
    print(f"\n  합계 {tot_m}건")
    print(f"  (쳐 본 마커: {', '.join(_MITIGATION)})")

    _section("④ 이 도구가 안 세는 것 — 다른 도구가 센다")
    print("  배포 프로파일에서 꺼진 opt-in 플래그   scripts/audit_wiring.py ④절")
    print("  죽은 정의 · 수동 구간 · 죽은 컬럼      scripts/audit_wiring.py ①②③절")
    print("  안 쓰는 표·엔드포인트·설정            scripts/audit_unused.py")
    print("  죽은 모듈 · 세대 중복 · 주석처리 코드   scripts/audit_code_debt.py")
    print("  문서 주장과 코드의 어긋남              scripts/audit_doc_claims.py")

    if a.json:
        Path(a.json).write_text(json.dumps({
            "scanned_code_files": scanned,
            "prose_lines": prose_lines,
            "checked_functions": checked_fn,
            "future_markers": {k: len(v) for k, v in fut.items()},
            "future_total": tot_f,
            "stubs": [{"file": r, "line": l, "name": n, "kind": k} for r, l, n, k in stubs],
            "mitigation_markers": {k: len(v) for k, v in mit.items()},
            "mitigation_total": tot_m,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\n  JSON: {a.json}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
