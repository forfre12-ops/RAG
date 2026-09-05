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


_SELF = Path(__file__).resolve()


def _py(root: Path) -> list[Path]:
    """검사 대상 .py 목록. **이 파일 자신은 뺀다.**

    [2026-09-05] EXPLAINED_COLS 에 칼럼 이름을 적자마자 '참조 0' 이 7건에서 0건이 됐다 —
    blob 에 poc/scripts/*.py 가 통째로 들어가고 거기 이 파일이 포함되기 때문이다.
    사유를 적었다는 이유로 죽은 것이 살아 있는 것처럼 보이면 도구가 거짓말을 한다.
    """
    return sorted(p for p in root.rglob("*.py")
                  if "__pycache__" not in str(p) and p.resolve() != _SELF)


# ── 설명이 끝난 '참조 0' ──────────────────────────────────────────────
#
# 아래는 "안 쓰는 것"이 아니라 **애플리케이션이 읽고 쓸 일이 없는 것**이다. 사유를 여기
# 적어 두고 요약에서 갈라 센다. 목록에는 계속 보이되 사유가 함께 나온다 — 감추는 것이
# 아니라 이미 답한 것을 표시하는 것이다. 그러지 않으면 다음 사람이 같은 조사를 되풀이한다.
EXPLAINED_COLS = {
    "evidence_id": "IDENTITY 기본키 — DB 가 채운다",
    "usage_id": "IDENTITY 기본키 — DB 가 채운다",
    "labeled_at": "server_default now() — DB 가 채운다",
    "logged_at": "server_default now() — DB 가 채운다",
    "active_key": "Computed 칼럼 — is_active 에서 DB 가 계산. UNIQUE 인덱스가 활성 1건 제약을 만든다",
    "model_type": "server_default 'classifier' — 코드가 넣는 값이 하나뿐이고 읽지 않는다(기록용)",
    "split_method": "실 데이터에 값이 있어 보류(2026-08-29 판단 유지)",
}
EXPLAINED_TABLES = {
    "tb_evaluation_factors": "판정 요건(S·V·M) 시드 표 — alembic 이 채우고 런타임은 읽기만",
    # [2026-09-05] 해소됨 — 워커가 적재 전에 upsert_prompt() 로 등록하고 sample 행이
    # 외래키로 참조한다(쓰기 1 · 읽기 1). 예외 목록에 남겨 두면 이력을 잃으므로 사유만 고친다.
    "tb_prompt_versions": "합성 프롬프트 버전 — 워커가 등록하고 sample 행이 FK 로 참조(2026-09-05 배선)",
    "tb_advisory_locks": "잠금 행 — 마이그레이션이 미리 넣고 런타임은 FOR UPDATE 로 잡기만",
    "tb_document_factor_scores": "쓰기·읽기 0 · 실 DB 0행 — 정의서에서도 뺐다(EXCLUDED_TABLES)",
}


# ── ① 테이블 ─────────────────────────────────────────────────────────
# 쓰기 신호: 모델 클래스 생성자 호출 · bulk_insert · insert(Model) · Model(...) 대입
# 읽기 신호: select(Model) · query(Model) · Model.컬럼 접근
def audit_tables(detail: bool) -> tuple[int, int, int, int]:
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
            write[cls] += len(re.findall(rf"INSERT\s+(?:IGNORE\s+)?INTO\s+{tname}\b", s, re.I))
            write[cls] += len(re.findall(rf"UPDATE\s+{tname}\b", s, re.I))
            write[cls] += len(re.findall(rf"DELETE\s+FROM\s+{tname}\b", s, re.I))
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
        why = EXPLAINED_TABLES.get(tname)
        if mark and why:
            mark += f"  [설명됨] {why}"
        print(f"  {tname:<32}{cls:<24}{write[cls]:>6}{read[cls]:>6}{mark}")
    unexplained = sorted({t for _c, t in list(no_write) + list(no_read)
                          if t not in EXPLAINED_TABLES})
    print(f"\n  표 {len(tables)}개 · 쓰기 0 {len(no_write)}개 · 읽기 0 {len(no_read)}개 "
          f"· 양쪽 0 {len(dead)}개 · 설명 안 된 것 {len(unexplained)}개")
    return len(tables), len(no_write), len(no_read), len(unexplained)


# ── ② 컬럼 ───────────────────────────────────────────────────────────
def audit_columns(detail: bool) -> tuple[int, int, int]:
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
        why = EXPLAINED_COLS.get(c)
        print(f"  {c:<34}{t:<30}" + (f"[설명됨] {why}" if why else ""))
    unexplained = [c for c, _t in dead if c not in EXPLAINED_COLS]
    print(f"\n  {len(dead)}건 / 검사한 컬럼 {len(cols)}개 · 설명 안 된 것 {len(unexplained)}개")
    return len(cols), len(dead), len(unexplained)


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
    # [2026-09-05] config.py 를 통째로 빼면 **같은 파일 안의 가드**가 읽는 값을 못 본다.
    # 실측: console_login_prefill_allow_unsafe 는 config.py 의
    # assert_production_credentials() 가 읽는데(하드닝 배포에서 무인증 login.html 을 막는
    # 검사) "참조 0" 으로 잡혔다. Settings 클래스 **본문만** 빼고 나머지는 본다.
    cfg_src = _read(cfg)
    cfg_lines = cfg_src.split("\n")
    for n in ast.walk(ast.parse(cfg_src)):
        if isinstance(n, ast.ClassDef) and n.name == "Settings":
            end = getattr(n, "end_lineno", None) or len(cfg_lines)
            for i in range(n.lineno - 1, min(end, len(cfg_lines))):
                cfg_lines[i] = ""
    blob = "\n".join(cfg_lines)
    blob += "\n".join(_read(p) for p in _py(_SRC) if p != cfg)
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

    n_t, nw, nr, tu = audit_tables(args.detail)
    n_c, dc, cu = audit_columns(args.detail)
    n_e, de = audit_endpoints(args.detail)
    n_s, ds = audit_settings(args.detail)

    print()
    print("=" * 76)
    print(" 요약")
    print("=" * 76)
    print(f"  테이블      쓰기 0 {nw:>3} · 읽기 0 {nr:>3}   / {n_t}"
          f"   (설명 안 된 것 {tu})")
    print(f"  컬럼        참조 0 {dc:>3}                / {n_c}"
          f"   (설명 안 된 것 {cu})")
    print(f"  엔드포인트  호출 0 {de:>3}                / {n_e}")
    print(f"  설정        참조 0 {ds:>3}                / {n_s}")
    print()
    print("  주의 — '참조 0'이 곧 '지워도 된다'는 아니다. 프레임워크가 이름으로 부르는 것,")
    print("  외부(KL 포털)가 호출하는 계약, 아직 배선 전인 기능이 섞여 있다. 지우기 전에")
    print("  각 항목의 주석과 요건(RTM)을 확인할 것.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
