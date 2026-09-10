#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DB 표준명 개명(migration 7b3e9d2a4f10) 뒤에 옛 물리명이 어디에 남았는지 센다.

왜 이 도구가 있는가(2026-09-11). 표·칼럼 물리명이 tad_*_mng · 표준 약어로 바뀌었다
(정본 koipa/db/standard_names.py). 파이썬 속성명은 그대로라 ORM 경로는 안전하지만,
**원시 SQL · 셸 스크립트 · 인프라 SQL · 콘솔 문자열**에 옛 이름이 남으면 실행할 때 깨진다.
개명 작업 중에도 `tb_` 접두로만 셌다가 접두 없는 SQL 조각을 쓰는 가짜 DB 를 놓친 적이 있다.
그래서 이 도구는 파일 형식을 고르지 않고 저장소 전체를 본다.

세는 것:
    T  옛 표 이름(월별·기본 파티션 자식 포함) — 어느 파일 형식이든
    C  옛 칼럼 이름 — **SQL 문장 안에서만.** 파이썬은 문자열 토큰 하나를 통째로 보고 판단한다
       (여러 줄 SQL 에서 키워드 없는 줄에 적힌 칼럼도 잡는다). SQL 판단은 문장 구조로 한다
       (SELECT…FROM · INSERT INTO · UPDATE x SET · JOIN x · WHERE x = …) — 첫 판은 영어 문장 속
       from·update 를 SQL 로 알아 JSON 요약문의 run_id·change_summary 를 옛 칼럼으로 셌다.
       흔한 단어(action·name·success …)는 빼고 길이 5 이상·밑줄 포함 칼럼만 본다.
분류(남아 있는 게 정상인 곳을 가른다):
    history   poc/alembic/versions — 지난 판은 그때 이름으로 적혀 있어야 한다
    mapping   standard_names.py · table_spec_meta.py · 생성기 — 옛→새 대응표 자체
    doc       doc/ · poc/doc/ · poc/docs/ · *.md — 산출물. 재생성 전까지 옛 이름이다
    archive   poc/scripts/archive — 보관만 하는 옛 스크립트
    data      poc/datasets — 데이터 파일
    code      그 밖 — 실행되는 코드·스크립트·인프라·시험
종류: .py 는 comment · docstring · string · code 로, 그 밖 파일은 text 로 적는다.
**실행 경로 의심 = code 분류 중 string · code · text.** 주석·docstring 은 실행되지 않는다.

분모: `git ls-files -z`(저장소 전체, 한글 경로 누락 방지) 중 텍스트 파일.

사용:
    python scripts/audit_old_db_names.py            # 요약
    python scripts/audit_old_db_names.py --list     # 실행 경로 의심 줄 목록
    python scripts/audit_old_db_names.py --all      # 모든 분류의 줄 목록
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import subprocess
import sys
import tokenize
from collections import Counter, defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
_ROOT = _POC.parent
sys.path.insert(0, str(_POC / "src"))

from koipa.db.standard_names import COLUMNS, TABLES  # noqa: E402

_TEXT = {".py", ".sh", ".sql", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".html", ".js", ".mjs",
         ".json", ".md", ".txt", ".env", ".conf", ".j2", ".tmpl", ""}
_OLD_T = re.compile(r"\b(" + "|".join(sorted(map(re.escape, TABLES), key=len, reverse=True))
                    + r")(?:_\d{4}_\d{2}|_default)?\b")
_CHANGED_COLS = sorted({oc for cols in COLUMNS.values() for oc, nc, _ in cols
                        if oc != nc and len(oc) >= 5 and "_" in oc}, key=len, reverse=True)
_OLD_C = re.compile(r"\b(" + "|".join(map(re.escape, _CHANGED_COLS)) + r")\b") if _CHANGED_COLS else None
# SQL 문장 모양 — 영어 문장 속 from·update 를 SQL 로 오인하지 않도록 구조로 본다.
_SQLISH = re.compile(
    r"\bSELECT\b[\s\S]*?\bFROM\b|\bINSERT\s+INTO\b|\bUPDATE\s+[A-Za-z_][\w.]*\s+SET\b|\bDELETE\s+FROM\b"
    r"|\bALTER\s+TABLE\b|\bCREATE\s+(?:UNIQUE\s+)?(?:TABLE|INDEX|TRIGGER|VIEW)\b"
    r"|\bJOIN\s+[A-Za-z_][\w.]*|\bWHERE\s+[A-Za-z_][\w.]*\s*(?:=|<|>|!=|<>|\bIS\b|\bIN\b|\bLIKE\b|\bBETWEEN\b)",
    re.I,
)

_MAPPING = ("poc/src/koipa/db/standard_names.py", "scripts/table_spec_meta.py",
            "scripts/build_table_spec.py", "poc/scripts/build_erd.py",
            "poc/scripts/audit_old_db_names.py", "poc/scripts/audit_schema_consistency.py")
_RISKY_KINDS = ("string", "code", "text")
# 마이그레이션을 올리기 **전** DB 에서 돌리는 점검 — 그때는 옛 이름이 맞다(파일 머리말에 조건을 적었다).
_PRE_RENAME = ("poc/scripts/sql/check_not_null_readiness.sql",)


def _category(rel: str) -> str:
    if rel.startswith("poc/alembic/versions/"):
        return "history"
    if rel in _PRE_RENAME:
        return "pre_rename"
    if rel in _MAPPING:
        return "mapping"
    if rel.startswith(("doc/", "poc/doc/", "poc/docs/")) or rel.endswith(".md"):
        return "doc"
    if rel.startswith("poc/scripts/archive/"):
        return "archive"
    if rel.startswith("poc/datasets/"):
        return "data"
    return "code"


def _files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True, check=True).stdout
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def _py_spans(src: str) -> list[tuple[str, tuple[int, int], tuple[int, int], str]]:
    """주석·문자열 토큰의 (종류, 시작, 끝, 글자). 종류 = comment | docstring | string.

    종류는 **일치한 위치**로 매긴다. 첫 판은 줄 단위로 매겨, 새 이름 문자열 뒤 주석에
    적힌 옛 이름을 "문자열"로 셌다(test_db_models.py 21줄 오검출).
    """
    doc: set[int] = set()
    try:
        for node in ast.walk(ast.parse(src)):
            body = getattr(node, "body", None)
            if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                    and isinstance(getattr(body[0], "value", None), ast.Constant) \
                    and isinstance(body[0].value.value, str):
                doc.add(body[0].lineno)
    except SyntaxError:
        pass
    spans = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                spans.append(("comment", tok.start, tok.end, tok.string))
            elif tok.type == tokenize.STRING:
                spans.append(("docstring" if tok.start[0] in doc else "string", tok.start, tok.end, tok.string))
    except (tokenize.TokenError, IndentationError):
        pass
    return spans


def _kind_at(spans: list, line: int, col: int) -> str:
    for kind, start, end, _ in spans:
        if start <= (line, col) < end:
            return kind
    return "code"


_RANK = {"comment": 0, "docstring": 1, "text": 2, "string": 3, "code": 4}


def _find(rel: str, src: str, lines: list[str]) -> dict[int, tuple[str, set[str]]]:
    """줄 → (그 줄의 일치 중 실행에 가장 가까운 종류, 이름들)."""
    is_py = rel.endswith(".py")
    spans = _py_spans(src) if is_py else []
    found: dict[int, list] = defaultdict(list)
    for i, line in enumerate(lines, 1):
        for m in _OLD_T.finditer(line):
            found[i].append((_kind_at(spans, i, m.start()) if is_py else "text", m.group(0)))
    if _OLD_C:
        if is_py:
            for kind, start, _end, s in spans:
                if kind != "comment" and _SQLISH.search(s):
                    for m in _OLD_C.finditer(s):
                        found[start[0] + s.count("\n", 0, m.start())].append((kind, m.group(0)))
        else:
            for i, line in enumerate(lines, 1):
                if rel.endswith(".sql") or _SQLISH.search(line):
                    for m in _OLD_C.finditer(line):
                        found[i].append(("text", m.group(0)))
    return {i: (max((k for k, _ in v), key=_RANK.get), {n for _, n in v}) for i, v in found.items()}


def scan() -> dict:
    files = [f for f in _files() if Path(f).suffix.lower() in _TEXT]
    hits: dict[str, list] = defaultdict(list)
    per_cat: Counter = Counter()
    for rel in files:
        try:
            src = (_ROOT / rel).read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        lines = src.splitlines()
        found = _find(rel, src, lines)
        if not found:
            continue
        cat = _category(rel)
        for i in sorted(found):
            kind, names = found[i]
            per_cat[(cat, kind)] += 1
            text = lines[i - 1] if 0 < i <= len(lines) else ""
            hits[cat].append((rel, i, kind, sorted(names), text.strip()[:150]))
    risky = [h for h in hits.get("code", []) if h[2] in _RISKY_KINDS]
    return {"denominator_files": len(files), "per_cat": per_cat, "hits": hits, "risky": risky,
            "changed_cols_checked": len(_CHANGED_COLS)}


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="옛 DB 물리명 잔존 계수")
    ap.add_argument("--list", action="store_true", help="실행 경로 의심 줄 목록")
    ap.add_argument("--all", action="store_true", help="모든 분류의 줄 목록")
    a = ap.parse_args(argv)
    r = scan()
    print(f"분모: 텍스트 파일 {r['denominator_files']}개 (git ls-files -z) · 옛 표 {len(TABLES)} · "
          f"옛 칼럼(SQL 문장 안, 흔한 단어 제외) {r['changed_cols_checked']}")
    for cat in ("code", "pre_rename", "archive", "data", "mapping", "history", "doc"):
        row = {k: v for (c, k), v in r["per_cat"].items() if c == cat}
        files = len({h[0] for h in r["hits"].get(cat, [])})
        print(f"  {cat:8s} 파일 {files:3d} · 줄 {sum(row.values()):4d}  {dict(sorted(row.items()))}")
    rf = len({h[0] for h in r["risky"]})
    print(f"  ⇒ 실행 경로 의심(code 의 string·code·text): 파일 {rf} · 줄 {len(r['risky'])}")
    if a.list:
        print("\n[실행 경로 의심]")
        for rel, i, kind, names, s in r["risky"]:
            print(f"  {rel}:{i} ({kind}) {names}  {s}")
    if a.all:
        for cat, hs in r["hits"].items():
            print(f"\n[{cat}]")
            for rel, i, kind, names, s in hs:
                print(f"  {rel}:{i} ({kind}) {names}  {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
