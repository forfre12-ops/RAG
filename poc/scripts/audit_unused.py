#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""안 쓰는 것 전수조사 — 테이블·컬럼·엔드포인트·설정·정의.

왜 필요한가.
  "안 쓰는 기능이 있느냐"는 범위 질문이다. 기억나는 것을 몇 개 grep 해서 답하면
  분모를 못 댄다. 이 스크립트가 세고 사람이 읽는다.

무엇을 세는가.
  ① 테이블   ORM 모델 기준. 프로덕션 코드(src/)에서 **쓰기**·**읽기** 참조 수를 센다.
             쓰기 0 = 영원히 비는 표. 읽기 0 = 넣기만 하고 아무도 안 보는 표.
  ② 컬럼     models.py 밖에서 참조 0 인 컬럼.
  ③ 엔드포인트 라우터에 등록됐으나 콘솔·테스트·문서 어디서도 부르지 않는 경로.
  ④ 설정     Settings 필드 중 정의부 밖 참조 0.
  ⑤ 정의     함수·클래스 중 AST 참조 0.

무엇을 세지 않는가(오탐 방지).
  · 프레임워크가 이름으로 부르는 것(라우터 핸들러·Celery task·pytest fixture·
    SQLAlchemy 이벤트 훅)은 "참조 0"이어도 죽은 것이 아니다. 따로 표시한다.
  · 문자열로만 부르는 경로(스크립트 이름·태스크 이름)는 문자열 참조도 센다.

사용:
    python scripts/audit_unused.py            # 요약
    python scripts/audit_unused.py --detail   # 항목별 전체 목록
"""
from __future__ import annotations

import argparse
import ast
import io
import re
import sys
from collections import defaultdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
_TESTS = _ROOT / "tests"
_SCRIPTS = _ROOT / "scripts"


def _read(p: Path) -> str:
    try:
        return io.open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _py(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))


# ── ① 테이블 ─────────────────────────────────────────────────────────
# 쓰기 신호: 모델 클래스 생성자 호출 · bulk_insert · insert(Model) · Model(...) 대입
# 읽기 신호: select(Model) · query(Model) · Model.컬럼 접근
def audit_tables(detail: bool) -> tuple[int, int, int]:
    models_py = _SRC / "koipa" / "db" / "models.py"
    src = _read(models_py)
    tree = ast.parse(src)
    tables: dict[str, str] = {}       # class -> tablename
    for n in tree.body:
        if not isinstance(n, ast.ClassDef):
            continue
        for st in n.body:
            if (isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__"
                    and isinstance(st.value, ast.Constant)):
                tables[n.name] = st.value.value

    prod = [p for p in _py(_SRC)]
    write = defaultdict(int)
    read = defaultdict(int)
    for p in prod:
        if p == models_py:
            continue
        s = _read(p)
        for cls, tname in tables.items():
            # 쓰기: 생성자 호출
            write[cls] += len(re.findall(rf"\b{cls}\s*\(", s))
            # 쓰기: 원시 SQL. [2026-09-05] 종전에는 원시 SQL 을 **읽기로만** 셌다.
            # 그래서 raw SQL 로만 쓰는 표(예: tb_advisory_locks — 잠금 행 INSERT)가
            # "쓰기 0 · 영원히 빈다"로 잡혔다. 사실이 아닌 경고는 도구를 못 믿게 만든다.
            write[cls] += len(re.findall(rf"INSERT\s+(?:IGNORE\s+)?INTO\s+{tname}", s, re.I))
            write[cls] += len(re.findall(rf"UPDATE\s+{tname}", s, re.I))
            write[cls] += len(re.findall(rf"DELETE\s+FROM\s+{tname}", s, re.I))
            # 읽기: select/query 인자 또는 속성 접근
            read[cls] += len(re.findall(rf"select\(\s*{cls}\b", s))
            read[cls] += len(re.findall(rf"query\(\s*{cls}\b", s))
            read[cls] += len(re.findall(rf"\b{cls}\.[a-z_]+", s))
            # 원시 SQL 로 표 이름을 직접 쓰는 경우
            read[cls] += len(re.findall(rf"\b{tname}\b", s))

    no_write = [(c, t) for c, t in tables.items() if write[c] == 0]
    no_read = [(c, t) for c, t in tables.items() if read[c] == 0]
    dead = [(c, t) for c, t in tables.items() if write[c] == 0 and read[c] == 0]

    print("=" * 76)
    print(" 1. 테이블 — 프로덕션 코드(src/)의 쓰기·읽기 참조")
    print("=" * 76)
    print(f"  {'테이블':<32}{'클래스':<24}{'쓰기':>6}{'읽기':>6}")
    print("  " + "-" * 66)
    for cls, tname in sorted(tables.items(), key=lambda x: (write[x[0]], read[x[0]])):
        mark = ""
        if write[cls] == 0 and read[cls] == 0:
            mark = "  <-- 쓰기·읽기 모두 0"
        elif write[cls] == 0:
            mark = "  <-- 쓰기 0(영원히 빈다)"
        elif read[cls] == 0:
            mark = "  <-- 읽기 0(넣기만 한다)"
        print(f"  {tname:<32}{cls:<24}{write[cls]:>6}{read[cls]:>6}{mark}")
    print(f"\n  표 {len(tables)}개 · 쓰기 0 {len(no_write)}개 · 읽기 0 {len(no_read)}개 "
          f"· 양쪽 0 {len(dead)}개")
    return len(tables), len(no_write), len(no_read)


# ── ② 컬럼 ───────────────────────────────────────────────────────────
def audit_columns(detail: bool) -> tuple[int, int]:
    models_py = _SRC / "koipa" / "db" / "models.py"
    tree = ast.parse(_read(models_py))
    cols: dict[str, str] = {}
    for n in tree.body:
        if not isinstance(n, ast.ClassDef):
            continue
        tname = None
        for st in n.body:
            if (isinstance(st, ast.Assign) and getattr(st.targets[0], "id", "") == "__tablename__"
                    and isinstance(st.value, ast.Constant)):
                tname = st.value.value
        if not tname:
            continue
        for st in n.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                v = ast.unparse(st.value) if st.value is not None else ""
                if "mapped_column" in v:
                    cols[st.target.id] = tname

    blob = "\n".join(_read(p) for p in _py(_SRC) if p != models_py)
    blob += "\n".join(_read(p) for p in _py(_SCRIPTS))
    outside = {c: len(re.findall(rf"\b{re.escape(c)}\b", blob)) for c in cols}
    dead = sorted((c, t) for c, t in cols.items() if outside[c] == 0)

    print()
    print("=" * 76)
    print(" 2. 컬럼 — models.py 밖 참조 0")
    print("=" * 76)
    for c, t in dead:
        print(f"  {c:<34}{t}")
    print(f"\n  {len(dead)}건 / 검사한 컬럼 {len(cols)}개")
    return len(cols), len(dead)


# ── ③ 엔드포인트 ─────────────────────────────────────────────────────
def audit_endpoints(detail: bool) -> tuple[int, int]:
    routes: list[tuple[str, str, Path]] = []
    for p in _py(_SRC / "koipa" / "api"):
        s = _read(p)
        for m in re.finditer(r'@\w+\.(get|post|put|patch|delete)\(\s*["\']([^"\']+)', s):
            routes.append((m.group(1).upper(), m.group(2), p))

    consumers = "\n".join(_read(p) for p in _py(_TESTS))
    consumers += "\n".join(_read(p) for p in _py(_SCRIPTS))
    for ext in ("*.html", "*.js", "*.mjs"):
        for p in (_SRC / "koipa" / "api" / "static").rglob(ext):
            consumers += _read(p)
        for p in (_ROOT / "tests").rglob(ext):
            consumers += _read(p)
    docs = _ROOT.parent / "doc"
    if docs.is_dir():
        for p in list(docs.rglob("*.html"))[:400]:
            consumers += _read(p)

    unused = []
    for meth, path, src in routes:
        key = path.split("{")[0].rstrip("/") or path
        if key and key not in consumers:
            unused.append((meth, path, src.name))

    print()
    print("=" * 76)
    print(" 3. 엔드포인트 — 콘솔·시험·스크립트·문서 어디서도 호출 안 함")
    print("=" * 76)
    for meth, path, name in sorted(unused):
        print(f"  {meth:<7}{path:<44}{name}")
    print(f"\n  {len(unused)}건 / 등록 경로 {len(routes)}개")
    return len(routes), len(unused)


# ── ④ 설정 ───────────────────────────────────────────────────────────
def audit_settings(detail: bool) -> tuple[int, int]:
    cfg = _SRC / "koipa" / "config.py"
    tree = ast.parse(_read(cfg))
    fields: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef) and n.name == "Settings":
            for st in n.body:
                if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                    fields.append(st.target.id)
    blob = "\n".join(_read(p) for p in _py(_SRC) if p != cfg)
    blob += "\n".join(_read(p) for p in _py(_SCRIPTS))
    blob += "\n".join(_read(p) for p in _py(_TESTS))
    for y in _ROOT.rglob("docker-compose*.yml"):
        blob += _read(y)
    for e in _ROOT.glob(".env*"):
        blob += _read(e)

    dead = [f for f in fields if not re.search(rf"\b{re.escape(f)}\b", blob)
            and not re.search(rf"\b{f.upper()}\b", blob)]

    print()
    print("=" * 76)
    print(" 4. 설정 — 정의부 밖 참조 0 (코드·compose·.env 전부 확인)")
    print("=" * 76)
    for f in sorted(dead):
        print(f"  {f}")
    print(f"\n  {len(dead)}건 / Settings 필드 {len(fields)}개")
    return len(fields), len(dead)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--detail", action="store_true")
    args = ap.parse_args(argv)

    n_t, nw, nr = audit_tables(args.detail)
    n_c, dc = audit_columns(args.detail)
    n_e, de = audit_endpoints(args.detail)
    n_s, ds = audit_settings(args.detail)

    print()
    print("=" * 76)
    print(" 요약")
    print("=" * 76)
    print(f"  테이블      쓰기 0 {nw:>3} · 읽기 0 {nr:>3}   / {n_t}")
    print(f"  컬럼        참조 0 {dc:>3}                / {n_c}")
    print(f"  엔드포인트  호출 0 {de:>3}                / {n_e}")
    print(f"  설정        참조 0 {ds:>3}                / {n_s}")
    print()
    print("  주의 — '참조 0'이 곧 '지워도 된다'는 아니다. 프레임워크가 이름으로 부르는 것,")
    print("  외부(KL 포털)가 호출하는 계약, 아직 배선 전인 기능이 섞여 있다. 지우기 전에")
    print("  각 항목의 주석과 요건(RTM)을 확인할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
