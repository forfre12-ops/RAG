#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""문서가 **이미 지운 항목**을 현행처럼 적은 자리를 센다 — 삭제한 DB 표·칼럼과 삭제한 API 입력·응답 필드.

왜 이 도구가 있는가(2026-09-27). 2026-09-26 에 안 쓰는 표 4개·칼럼 18개·API 입력/응답 항목을 지웠는데, KL 에 나간 문서 사본
곳곳에 `external_ref` 입력·옛 표 이름이 그대로 남아 있었다. 기존 검사기는 이것을 못 잡는다 — `audit_doc_runtime.py` 는 경로·수치만
보고, `audit_doc_claims.py` 는 개수만 세고, `audit_schema_consistency.py` 는 코드 쪽 명명만 본다. 삭제는 "없는 것"이라 문서에
남아 있어도 어느 검사에도 걸리지 않는다.

참값(코드에서 뽑는다 — 이 도구는 문서를 참값으로 쓰지 않는다)
    삭제한 표·칼럼   alembic 삭제 판의 모듈 수준 `TABLES`(리스트)·`COLUMNS`(3-튜플 리스트) **리터럴**을 AST 로 읽는다(임포트 안 함).
                     표·칼럼을 지우는 새 판은 같은 형식으로 쓰면 자동으로 여기 들어온다(tests/test_standard_names.py 의 `_DROP_MIGS` 와 같은 약속).
    옛 이름          표준 명명 판(7b3e9d2a4f10)의 옛→새 대응을 뒤집어, 문서가 옛 이름(tb_*·옛 칼럼명)으로 적은 것도 잡는다.
    살아 있는 이름   ORM 칼럼 물리명·속성명과 OpenAPI 스키마 속성명. 삭제한 이름이 다른 곳에서는 살아 있으면(예: doc_id) 뺀다 —
                     칼럼명만으로는 어느 표의 것인지 글에서 못 가르므로, 오탐을 만드는 이름은 처음부터 검사 대상에서 제외한다.
    삭제한 API 항목  삭제 판이 없으므로 아래 `REMOVED_API` 에 적는다(지운 커밋·날짜와 함께).

"현행처럼 적었다"의 판정(셋 중 하나라도 해당하면 세지 않는다 — `--all` 로 보인다):
  ① 그 이름 앞뒤 150자 안에 삭제·정정·당시 같은 **이력 표지**(`_HISTORY`)가 있다("삭제했다"는 문장).
  ② 개정 이력 절(제목에 「개정 이력」이 든 마지막 절)의 안이다.
  ③ **같은 문서가 그 이름이 지워졌다고 어딘가에서 밝혔다**(①에 해당하는 자리가 한 곳이라도 있다) — 정정 상자를 달았으면 본문의 옛 서술은 그 상자가 덮는다.
  그래서 낡은 문서를 고치는 길은 둘이다: 서술을 현행으로 고치거나, 정정 상자로 지웠다는 사실을 밝힌다.

⚠ 한계. 글의 뜻은 못 읽는다 — 이력 표지가 없어도 "옛 판을 인용한 것"일 수 있고, 표지가 있어도 현행처럼 적은 것일 수 있다.
  이 도구는 후보를 세고, 여는 것은 사람이 한다. 판 날짜를 밝힌 스냅샷 문서(부록A 2026-07-17 판 등)는 `SNAPSHOTS` 에 올려 따로 센다.

사용:
    python scripts/audit_removed_items.py                    # 기본 범위: doc/ 의 현행 문서 + poc/docs/ (doc/releases·doc/archive 제외)
    python scripts/audit_removed_items.py --root <폴더>      # 폴더 지정(여러 번)
    python scripts/audit_removed_items.py --all              # 이력 표지가 붙은 자리도 보인다
    python scripts/audit_removed_items.py --json out.json
종료 코드 1 = 현행처럼 적힌 자리가 있다.
"""
from __future__ import annotations

import argparse
import ast
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
_ROOT = _POC.parent
_VERSIONS = _POC / "alembic" / "versions"
_NAMING = "7b3e9d2a4f10_standard_naming.py"

# 삭제한 API 항목 — (이름, 어디에 있던 것인지, 지운 날). 표·칼럼은 삭제 판에서 읽으므로 여기엔 적지 않는다.
# 근거: aaea98c3(2026-09-26) 커밋 메시지. 경로(/guide/documents 등)는 audit_doc_runtime.py 가 "없는 경로"로 잡는다.
REMOVED_API: tuple[tuple[str, str, str], ...] = (
    ("external_ref", "IF-02 문서 등록 입력(POST /documents)", "2026-09-26"),
    ("ocr_used", "IF-02 문서 등록 응답", "2026-09-26"),
    ("extraction_complete", "IF-02 문서 등록 응답", "2026-09-26"),
    ("pages_processed", "IF-02 문서 등록 응답", "2026-09-26"),
    ("GuideUploadResponse", "가이드 문서 등록 응답(POST /guide/documents)", "2026-09-26"),
    ("GuideVersionList", "가이드 버전 조회 응답(GET /guide/documents/{guide_id})", "2026-09-26"),
)

# 판 날짜를 밝힌 스냅샷 — 옛 이름이 당연히 남아 있다. 현행처럼 적힌 자리로 세지 않고 따로 센다.
SNAPSHOTS: tuple[str, ...] = (
    "기술구현_백서_부록A_DB스키마.html",     # 2026-07-17 판 + 「현행 기준 안내」 상자
    "07_DB_스키마_v2.sql",                    # 옛 설계 SQL(옛 이름 tb_*)
    "의사결정_대장.html",                     # 결정 기록 — 그날의 결정을 적은 것
    "테스트_전략_및_이력.html",               # 시험 이력
    "시험케이스_및_결과서.html",              # 시험 결과 기록
    "DB_대리키_표준화_전환계획.html",         # 날짜 박힌 전환 계획(그 시점의 표 목록)
)
# 이름 규칙으로 가리는 스냅샷 — 병행 트랙(Codex) 초안·설계와 날짜가 박힌 결과 문서. 소유자·날짜가 있는 기록이라 이 도구가 고치라 하지 않는다.
SNAPSHOT_PATTERNS: tuple[str, ...] = (
    r"_V\d+_(DRAFT|DESIGN)\.md$",
    r"_(RESULT|PASS)_2026-\d\d-\d\d\.md$",
)
# 날짜가 박힌 내부 기록 폴더(감사·전수조사 보고서) — 그날의 상태를 적은 것이라 옛 이름이 당연히 남는다.
SNAPSHOT_DIRS: tuple[str, ...] = ("internal",)

_SKIP_DIRS = {"releases", "archive"}          # 동결 폴더
_EXT = {".html", ".md", ".yaml", ".sql"}

# 이력 표지 — 이 낱말이 이름 곁에 있으면 "지웠다·당시 값이다"를 말하는 자리로 본다.
_HISTORY = re.compile(
    r"삭제|제거|뺐|빼[고서었]|지웠|지운|지워|걷었|걷어|걷는|폐기|정정|개정|당시|종전|옛[ 이]|이력|무시|받지 않|받던|넣던|쓰던|더 이상|없어졌|사라졌|이전 판|스냅샷|판 기준|"
    r"removed|deleted|dropped|deprecated",
)
_WINDOW = 150

# 살아 있어도 검사 대상에서 빼는 너무 흔한 이름
_TOO_COMMON = {"id", "name", "type", "status", "title", "count", "version", "score", "value"}


# ── 참값 뽑기 ────────────────────────────────────────────────────────────────
def _literal(tree: ast.Module, name: str):
    """모듈 수준 `name = <리터럴>`(주석 달린 대입 포함)을 값으로 읽는다. 리터럴이 아니면 None."""
    for node in tree.body:
        value = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            value = node.value
        if value is not None:
            try:
                return ast.literal_eval(value)
            except (ValueError, SyntaxError):
                return None
    return None


def dropped_by_migrations(versions: Path = _VERSIONS) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """삭제 판이 지운 표 {표: 판 파일}·칼럼 {(표, 칼럼): 판 파일}. 형식이 맞는 판만 읽는다(표준 명명 판은 dict 라 걸러진다)."""
    tables: dict[str, str] = {}
    columns: dict[tuple[str, str], str] = {}
    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        t, c = _literal(tree, "TABLES"), _literal(tree, "COLUMNS")
        if not (isinstance(t, list) and isinstance(c, list)):
            continue
        if not all(isinstance(x, str) for x in t) or not all(isinstance(x, tuple) and len(x) == 3 for x in c):
            continue
        for table in t:
            tables[table] = path.name
        for table, column, _definition in c:
            columns[(table, column)] = path.name
    return tables, columns


def old_names(versions: Path = _VERSIONS) -> tuple[dict[str, str], dict[tuple[str, str], str]]:
    """표준 명명 판을 뒤집어 (새 표 → 옛 표)·((새 표, 새 칼럼) → 옛 칼럼)."""
    tree = ast.parse((versions / _NAMING).read_text(encoding="utf-8"))
    tables = _literal(tree, "TABLES") or {}          # 옛 표 → 새 표
    cols = _literal(tree, "COLUMNS") or {}           # 옛 표 → ((옛 칼럼, 새 칼럼), ...)
    new2old_t = {new: old for old, new in tables.items()}
    new2old_c: dict[tuple[str, str], str] = {}
    for old_table, pairs in cols.items():
        new_table = tables.get(old_table)
        for old_col, new_col in pairs:
            if new_table:
                new2old_c[(new_table, new_col)] = old_col
    return new2old_t, new2old_c


def live_names() -> set[str]:
    """지금 살아 있는 이름 — ORM 칼럼 물리명·속성명과 OpenAPI 스키마 속성명."""
    sys.path.insert(0, str(_POC / "src"))
    names: set[str] = set()
    from koipa.db import Base  # noqa: PLC0415
    from koipa.db import models  # noqa: F401, PLC0415
    for table in Base.metadata.tables.values():
        for col in table.columns:
            names.add(col.name)
            names.add(col.key)
    try:
        from koipa.api.app import app  # noqa: PLC0415
        for schema in app.openapi().get("components", {}).get("schemas", {}).values():
            names.update((schema.get("properties") or {}).keys())
    except Exception as exc:  # noqa: BLE001
        print("  [경고] OpenAPI 스키마를 못 읽었다(%s) — 살아 있는 이름 제외가 ORM 만으로 줄어든다" % type(exc).__name__)
    return names


def build_registry(live: set[str] | None = None) -> dict[str, tuple[str, str]]:
    """검사할 이름 → (종류, 설명). 살아 있는 이름·너무 흔한 이름은 뺀다."""
    live = live_names() if live is None else live
    dropped_tables, dropped_cols = dropped_by_migrations()
    old_table, _old_col = old_names()
    reg: dict[str, tuple[str, str]] = {}
    for table, mig in dropped_tables.items():
        reg[table] = ("표", "삭제한 표 — %s" % mig.split("_", 1)[0])
        if table in old_table:
            reg[old_table[table]] = ("표", "삭제한 표의 옛 이름(%s)" % table)
    # 칼럼은 표준 이름(tad 체계라 다른 뜻으로 쓰일 일이 없다)만 본다. 옛 이름(split_method·factor_id 등)은 청크 메타·다른 표의 기본키 같은 다른 대상과
    # 이름이 겹쳐 오탐이 많았다(2026-09-27 탐색). 옛 이름이 API 항목이기도 한 것(external_ref)은 REMOVED_API 로 잡는다.
    for (table, column), mig in dropped_cols.items():
        reg.setdefault(column, ("칼럼", "삭제한 칼럼 %s.%s — %s" % (table, column, mig.split("_", 1)[0])))
    for name, where, day in REMOVED_API:
        reg.setdefault(name, ("API", "삭제한 API 항목 — %s (%s)" % (where, day)))
    return {n: v for n, v in reg.items() if n not in live and n.lower() not in _TOO_COMMON and len(n) > 3}


# ── 문서 훑기 ────────────────────────────────────────────────────────────────
def _strip_blocks(text: str) -> str:
    """<script>·<style> 는 화면용 덩어리라 뺀다 — 줄 번호를 지키려고 같은 길이의 공백으로 덮는다."""
    def blank(m: re.Match) -> str:
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))
    return re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", blank, text)


def _snippet(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - 45), min(len(text), end + 45)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text[lo:hi])).strip()


# 제목 안에 번호 <span> 이 들어 있어도 잡는다(`<h2><span class="num">07</span> 개정 이력</h2>`). 정의서 생성기는 <section id="revisions"> 로 감싼다.
_REV_HEADING = re.compile(r'(?is)<h[1-4][^>]*>.{0,120}?개정\s*이력|<section[^>]*id="revisions"')


def _drop_revision_section(text: str) -> str:
    """개정 이력 절을 뺀다 — 거기엔 과거 값과 지운 항목이 적히는 것이 정상이다. 제목(h1~h4)에 「개정 이력」이 든 **마지막** 절부터 끝까지.

    같은 길이의 공백으로 덮어 줄 번호를 지킨다. 목차·내비게이션의 「개정 이력」 링크는 제목 태그가 아니라 걸리지 않는다.
    """
    last = None
    for m in _REV_HEADING.finditer(text):
        last = m
    if last is None:
        return text
    tail = text[last.start():]
    return text[: last.start()] + "".join(ch if ch == "\n" else " " for ch in tail)


def scan_text(text: str, registry: dict[str, tuple[str, str]]) -> list[dict]:
    """한 문서의 자리 목록 — {line, name, kind, why, history, snippet}. `history` = ①이력 표지 곁 · ③문서 안에서 밝힌 이름."""
    text = _drop_revision_section(_strip_blocks(text))
    pattern = re.compile(r"(?<![A-Za-z0-9_])(%s)(?![A-Za-z0-9_])" % "|".join(sorted(map(re.escape, registry), key=len, reverse=True)))
    hits = []
    for m in pattern.finditer(text):
        name = m.group(1)
        near = text[max(0, m.start() - _WINDOW): m.end() + _WINDOW]
        kind, why = registry[name]
        hits.append({
            "line": text.count("\n", 0, m.start()) + 1,
            "name": name, "kind": kind, "why": why,
            "history": bool(_HISTORY.search(re.sub(r"<[^>]+>", " ", near))),
            "snippet": _snippet(text, m.start(), m.end()),
        })
    # ③ 문서가 그 이름이 지워졌다고 한 곳이라도 밝혔으면 그 이름의 나머지 자리도 이력으로 본다(정정 상자가 본문을 덮는다).
    disclosed = {h["name"] for h in hits if h["history"]}
    for h in hits:
        if h["name"] in disclosed:
            h["history"] = True
    return hits


def collect_files(roots: list[Path]) -> list[Path]:
    files = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in _EXT and not (_SKIP_DIRS & set(path.parts)):
                files.append(path)
    return files


def audit(roots: list[Path], registry: dict[str, tuple[str, str]]) -> dict:
    per_file: dict[str, list[dict]] = {}
    for path in collect_files(roots):
        hits = scan_text(io.open(path, encoding="utf-8", errors="ignore").read(), registry)
        if hits:
            per_file[str(path)] = hits
    return {"registry": len(registry), "files": per_file}


# ── 출력 ─────────────────────────────────────────────────────────────────────
def _classify(result: dict) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    """(현행처럼 적힘, 스냅샷, 이력 표지 있음) — 파일별 자리 목록."""
    current: dict[str, list[dict]] = {}
    snapshot: dict[str, list[dict]] = {}
    history: dict[str, list[dict]] = {}
    for path, hits in result["files"].items():
        name = Path(path).name
        is_snapshot = (name in SNAPSHOTS or bool(set(SNAPSHOT_DIRS) & set(Path(path).parts))
                       or any(re.search(pat, name) for pat in SNAPSHOT_PATTERNS))
        cur = [h for h in hits if not h["history"]]
        his = [h for h in hits if h["history"]]
        if his:
            history[path] = his
        if cur:
            (snapshot if is_snapshot else current)[path] = cur
    return current, snapshot, history


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="삭제한 항목을 현행처럼 적은 문서 자리를 센다")
    ap.add_argument("--root", action="append", help="검사할 폴더(여러 번 가능). 기본: doc/ · poc/docs/")
    ap.add_argument("--all", action="store_true", help="이력 표지가 붙은 자리도 보인다")
    ap.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                    help="이 패턴(경로 일부 문자열)이 든 파일은 뺀다 — 예: --ignore _DRAFT (여러 번 가능)")
    ap.add_argument("--json", help="결과를 JSON 으로 저장")
    args = ap.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    roots = [Path(r) for r in args.root] if args.root else [_ROOT / "doc", _POC / "docs"]
    registry = build_registry()
    result = audit(roots, registry)
    if args.ignore:
        result["files"] = {p: h for p, h in result["files"].items() if not any(g in p for g in args.ignore)}
    current, snapshot, history = _classify(result)

    print("=" * 74)
    print(" 삭제한 항목 — 검사 이름 %d개(표·칼럼·API) · 문서 %d개 훑음" % (len(registry), len(collect_files(roots))))
    print("=" * 74)
    kinds = defaultdict(int)
    for hits in current.values():
        for h in hits:
            kinds[h["kind"]] += 1
    n_cur = sum(len(v) for v in current.values())
    print(" 현행처럼 적힌 자리 %d곳 / 문서 %d개  (표 %d · 칼럼 %d · API %d)" % (
        n_cur, len(current), kinds["표"], kinds["칼럼"], kinds["API"]))
    print(" 판 날짜를 밝힌 스냅샷의 자리 %d곳 · 이력 표지가 붙은 자리 %d곳(세지 않음)" % (
        sum(len(v) for v in snapshot.values()), sum(len(v) for v in history.values())))

    def show(title: str, group: dict[str, list[dict]]) -> None:
        if not group:
            return
        print("\n[%s]" % title)
        for path, hits in sorted(group.items()):
            try:
                rel = Path(path).resolve().relative_to(_ROOT)
            except ValueError:
                rel = Path(path)
            print("  %s  (%d곳)" % (rel, len(hits)))
            for h in hits:
                print("      %5d  %-28s %s" % (h["line"], h["name"], h["snippet"][:88]))

    show("현행처럼 적힌 자리", current)
    if args.all:
        show("판 날짜를 밝힌 스냅샷", snapshot)
        show("이력 표지가 붙은 자리", history)

    if args.json:
        io.open(args.json, "w", encoding="utf-8").write(json.dumps(
            {"현행": current, "스냅샷": snapshot, "이력": history}, ensure_ascii=False, indent=1))
    return 1 if current else 0


if __name__ == "__main__":
    sys.exit(main())
