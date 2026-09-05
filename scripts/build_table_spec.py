# -*- coding: utf-8 -*-
"""테이블정의서·ERD 감리문서를 코드에서 직접 생성한다.

왜 생성기인가. 기존 「기술구현_백서_부록A_DB스키마」는 손으로 쓴 문서라 스키마가
바뀌어도 따라오지 않았다 — 실측 2026-08-25: 문서는 "2026-08-02 이후 변경 없음"이라고
적혀 있는데 그 뒤 8/22·8/23·8/24 세 커밋이 컬럼 3개를 더했고 문서에는 없었다.
그래서 정의서를 사람이 쓰지 않고 **코드에서 뽑는다**. 스키마가 바뀌면 이 스크립트를
다시 돌리면 되고, 설명이 빠진 컬럼은 생성 때 경고로 드러난다.

진실 소스(이 스크립트가 직접 읽는 파일):
    poc/src/koipa/db/models.py                          ORM 20테이블 · 컬럼·타입·키·인덱스
    (RAG 2표 DDL 파서는 남겨 두었으나 2026-09-05 부로 생성물에 넣지 않는다)
    scripts/table_spec_meta.py                          한국어 논리명·용도·컬럼 설명

사용:
    python scripts/build_table_spec.py            # 생성 + 자기검증
    python scripts/build_table_spec.py --check    # 생성 없이 검증만(문서-코드 차이 보고)

⚠ 순서가 있다. 이 스크립트는 문서를 통째로 다시 쓰므로 §02 관계도의 <svg> 도 임시본으로
   덮는다. 반드시 뒤이어 아래를 돌려 정본 관계도를 다시 넣는다.

    python poc/scripts/build_erd.py --apply --spec

   (정본 관계도 = 상자에 선이 가리지 않게 통로로 우회시키고, 상자에 마우스를 올리거나
    키보드로 고르면 그 표에 붙은 관계선만 파랗게 칠하는 CSS 를 <svg> 안에 담은 것)
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import html
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# [2026-09-03] Windows 기본 콘솔(cp949)에서 진단 문구의 em dash 가 UnicodeEncodeError 를 내며
# **파일을 쓰기 전에** 죽었다. 표준 출력만 UTF-8 로 돌린다 — 호출자가 환경변수를 붙이지
# 않아도 돌아야 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):      # 파이프·리다이렉트 등 재설정 불가한 경우
        pass

import table_spec_meta as META  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "poc" / "src" / "koipa" / "db" / "models.py"
MIG_RAG = ROOT / "poc" / "alembic" / "versions" / "a1b2c3d4e5f6_pg_rag_vectorstore.py"
MIG_RAG_COMMENT = ROOT / "poc" / "alembic" / "versions" / "a7b8c9d0e1f2_rag_vectors_column_comments.py"
# [2026-08-29] 서식 정본. 지시에 따라 감리 회신서의 style·nav 를 그대로 쓴다.
# 종전 골격(부록A)은 자체 클래스 68개·타원 배지·인쇄 규격 없음이라 정본과 어긋났다.
SKELETON = ROOT / "doc" / "result" / "KL_회신_2026-08-28" / "KL_질의사항_회신서.html"
# 같은 문서가 제출 묶음마다 사본으로 놓인다. 한 곳만 쓰면 나머지가 뒤처진다 —
# 실측 2026-08-29: 칼럼 10개를 뺀 뒤 KL_AI자료 사본만 239 로 갱신되고 회신 첨부본은
# 249 인 채로 남아 두 사본이 어긋났다. build_erd.py 와 같이 존재하는 사본 전부에 쓴다.
#
# [2026-09-05] 감리문서 사본을 여기 넣었다. `doc/감리문서/테이블_정의서.html` 은 손으로
# 쓴 사본이라 2026-08-26 판 1 에서 멈춰 있었고, 그 사이 판이 5 까지 갔다. 실측으로
# 그 사본은 없어진 표 2종(tb_rag_vectors·tb_rag_aliases)과 없어진 칼럼 9개를 아직
# 싣고 있었다. 파일 이름만 다르고(밑줄 위치) 내용은 같은 문서이므로 함께 생성한다.
OUTS = [
    ROOT / "doc" / "result" / "KL_AI자료_2026-08" / "테이블정의서_ERD.html",
    ROOT / "doc" / "result" / "KL_회신_2026-08-28" / "첨부" / "테이블정의서_ERD.html",
    ROOT / "doc" / "result" / "KL_AI자료_2026-08" / "첨부문서" / "테이블정의서_ERD.html",
    ROOT / "doc" / "감리문서" / "테이블_정의서.html",
]

# 개정 이력. 손으로 붙여 두면 생성기가 다시 돌 때 지워지므로 여기에 둔다.
REVISIONS = [
    ("1", "2026-08-26", "d3fe51c3",
     "최초 작성. ORM 메타데이터와 마이그레이션 정의에서 생성해 감리 산출물로 편입"),
    ("2", "2026-08-29", "fca08e04",
     "관계선이 상자에 가려지던 도식을 다시 그리고, 검색용 표 2종을 포함해 21표로 확장. "
     "칼럼 목록이 두 곳에 중복 수록돼 있던 것을 이 문서로 일원화. "
     "표별 인덱스·FK 개수를 §01 에 추가"),

    ("3", "2026-08-29", "이 문서 머리말의 커밋",
     "어떤 코드도 읽지 않고 실 데이터도 전부 비어 있던 <b>칼럼 10개를 삭제</b>"
     "(249 → 239). 대리키를 <code>SERIAL</code> 에서 표준 "
     "<code>GENERATED ALWAYS AS IDENTITY</code> 로 전환"),
    ("4", "2026-09-03", "이 문서 머리말의 커밋",
     "<b>유사문서 조회 폐기</b> — 검색용 표 2종(21 → 19표)에 이어 표 1개와 칼럼 9개를 더 "
     "뺐다(19표 226칼럼 → <b>18표 214칼럼</b>). "
     "<b>⚠ 뺀 것은 소스에 아직 남아 있다</b> — 정의서가 코드보다 앞선 상태이며 소스 정리는 "
     "다음 차수다.<br>"
     "납품 DB 를 <b>MariaDB 10.11</b> 로 맞췄다. 물리 타입·기본값·인덱스·파티션·채번을 "
     "MariaDB 정본으로 적고 현행 PostgreSQL 표기를 대조용으로 병기했다. 스토리지 엔진·문자셋·"
     "콜레이션 선언을 머리말에 넣었다.<br>"
     "§02 <b>서버별 배포 구분</b>을 신설했다 — 근거는 "
     "<code>poc/scripts/audit_table_placement.py</code> 전수 조사. "
     "절 번호가 03 에서 겹치던 것을 바로잡고, 제약 없는 참조 목록과 제외 칼럼을 "
     "<code>table_spec_meta.py</code> 한 곳에 두어 도식·캡션·본문이 같은 값을 쓰도록 "
     "고쳤습니다"),
    ("5", "2026-09-05", "이 문서 머리말의 커밋",
     "<b>4차에서 '소스에 아직 남아 있다'고 적었던 것을 실제로 걷었다</b> — 커밋 "
     "<code>319069b9</code> 가 소스·ORM 에서, 마이그레이션 "
     "<code>a3b4c5d6e7f8</code> 이 DB 에서 유사문서 조회 칼럼 9개와 검색용 표 2종을 "
     "떨궜다. 이제 정의서와 코드가 같은 것을 가리킨다.<br>"
     "<b>파티션을 걷었다</b> — 223 실서버 30일 실측(<code>tb_audit_log</code> 71,161행 · "
     "30MB · 연 환산 87만행)에서 파티션이 필요한 규모가 아니었고, 오래된 파티션을 떼는 "
     "코드가 0건이라 효용의 절반은 애초에 쓰지 않고 있었다. 시간축 인덱스 3개"
     "(<code>b4c5d6e7f8a9</code>)와 보존기간 삭제"
     "(<code>services/retention.py</code> · 기본 꺼짐)로 대신한다. "
     "<code>tb_audit_log</code> 의 복합 기본키는 그대로 둔다.<br>"
     "<b>표 <code>tb_advisory_locks</code> 를 넣었다</b>(18 → 19표 · 216칼럼) — 감사 "
     "해시체인과 모델 활성화의 임계구역을 두 DB 에서 같은 방식으로 잠그기 위한 표다."),
]


# ──────────────────────────────────────────────────────────────────────
# 1. ORM 파싱
# ──────────────────────────────────────────────────────────────────────

_TYPE_MAP = {
    "Integer": "INTEGER",
    "BigInteger": "BIGINT",
    "SmallInteger": "SMALLINT",
    "REAL": "REAL",
    "Boolean": "BOOLEAN",
    "Text": "TEXT",
    "JSONB": "JSONB",
    "INET": "INET",
}


# 제약 없는 참조. 정본은 table_spec_meta.SOFT_REFS 이며 build_erd.py 도 같은 목록을 본다.
# 여기 형식은 (자식, 부모, 칼럼) 이고 정본은 (자식, 칼럼, 부모) 이므로 순서를 맞춘다.
SOFT_REFS = [(ch, pa, col) for ch, col, pa in getattr(META, "SOFT_REFS", [])]

def _phys_type(expr: str) -> str:
    """mapped_column 첫 인자에서 PostgreSQL 물리 타입 문자열을 만든다."""
    expr = expr.strip()
    if m := re.match(r"String\((\d+)\)", expr):
        return f"VARCHAR({m.group(1)})"
    if m := re.match(r"Numeric\((\d+),\s*(\d+)\)", expr):
        return f"NUMERIC({m.group(1)},{m.group(2)})"
    if expr.startswith("DateTime"):
        return "TIMESTAMPTZ" if "timezone=True" in expr else "TIMESTAMP"
    if expr.startswith("UUID"):
        return "UUID"
    if m := re.match(r"ARRAY\((\w+)\)", expr):
        inner = _TYPE_MAP.get(m.group(1), m.group(1).upper())
        return f"{inner}[]"
    return _TYPE_MAP.get(expr, expr)


# ── MariaDB 전환 (2026-09-02) ────────────────────────────────────────────────
#
# 유사문서 검색을 쓰지 않기로 하면서 pgvector 가 필요 없어졌고, 납품 DB 를 KL 포털이
# 쓰는 MariaDB 로 맞춘다. 아래는 **현행 PostgreSQL 물리 타입 -> MariaDB 타입** 대응이다.
#
# 타입을 그냥 갈아끼우면 안 되는 자리가 셋이라 여기 근거를 남긴다.
#
#   TIMESTAMPTZ  MariaDB 에는 시간대를 담는 타입이 없다. DATETIME(6) 에 **UTC 로 저장**하고
#                시간대 변환은 응용에서 한다. TIMESTAMP 를 쓰면 2038 년 상한에 걸린다.
#   ARRAY        MariaDB 에는 배열 타입이 없다. 해당 칼럼은 tb_chunks.section_path 하나뿐이고
#                읽는 쪽이 목록으로만 다루므로 JSON 배열로 담는다.
#   UUID         MariaDB 10.7+ 의 UUID 타입은 정렬·인덱스 특성이 다르다. 이식성을 위해
#                CHAR(36) 로 적는다. 저장 효율이 문제가 되면 BINARY(16) 으로 바꿀 수 있다.
#
# JSONB -> JSON 은 이름만 같고 성질이 다르다. MariaDB 의 JSON 은 LONGTEXT + 검증 제약이라
# PostgreSQL JSONB 처럼 색인된 이진 형태가 아니다. JSON 안의 키로 자주 거르는 질의가
# 있으면 생성 칼럼(generated column) + 인덱스가 필요하다.
_MARIA_MAP = {
    "INTEGER": "INT",
    "BIGINT": "BIGINT",
    "SMALLINT": "SMALLINT",
    "REAL": "FLOAT",
    "BOOLEAN": "TINYINT(1)",
    "TEXT": "TEXT",
    "JSONB": "JSON",
    "INET": "VARCHAR(45)",
    "UUID": "CHAR(36)",
    "TIMESTAMPTZ": "DATETIME(6)",
    "TIMESTAMP": "DATETIME(6)",
}


def _maria_type(pg: str) -> str:
    """현행 PostgreSQL 물리 타입 문자열 -> MariaDB 타입 문자열."""
    pg = (pg or "").strip()
    if not pg:
        return ""
    if pg.endswith("[]"):                       # ARRAY -> JSON 배열
        return "JSON"
    if pg.startswith("VARCHAR("):
        return pg
    if m := re.match(r"NUMERIC\((\d+),(\d+)\)", pg):
        return f"DECIMAL({m.group(1)},{m.group(2)})"
    return _MARIA_MAP.get(pg, pg)

def _sql_default(expr: str) -> str:
    """server_default 표현식을 DB 가 보는 기본값 문자열로 정규화한다."""
    expr = expr.strip()
    if expr == "func.now()":
        return "now()"
    if expr.startswith("func."):
        return expr[5:]
    if expr.startswith("text("):
        try:
            lit = ast.literal_eval(expr[5:-1].strip())
        except (ValueError, SyntaxError):
            lit = expr[5:-1].strip().strip("\"'")
        # '<값>'::<타입> 의 캐스트 꼬리는 읽는 데 방해만 된다.
        return re.sub(r"::[\w ]+$", "", str(lit)).strip()
    return expr


# MariaDB 기본값 대응. 타입과 같은 이유로 여기 한 곳에서만 정한다.
#
#   now()               CURRENT_TIMESTAMP(6)  — 마이크로초 정밀도를 DATETIME(6) 과 맞춘다
#   true / false        1 / 0                 — MariaDB BOOLEAN 은 TINYINT(1) 의 별칭이다
#   gen_random_uuid()   응용 생성              — MariaDB UUID() 는 v1(시간 기반)이라 v4 와
#                                              성질이 다르다(인덱스 국부성·예측 가능성).
#                                              DB 기본값으로 바꿔 끼우지 않고 응용이 만든다.
_MARIA_DEFAULT = {
    "now()": "CURRENT_TIMESTAMP(6)",
    "true": "1",
    "false": "0",
    "gen_random_uuid()": "(응용 생성)",
}


def _maria_default(pg: str) -> str:
    pg = (pg or "").strip()
    if not pg:
        return ""
    return _MARIA_DEFAULT.get(pg, pg)


# MariaDB 인덱스 대응. PostgreSQL 전용 문법을 쓰는 7개만 여기서 정하고 나머지는 그대로다.
#
# 부분 인덱스(WHERE) 는 MariaDB 에 없다. 다섯 중 넷은 성능용이라 복합·일반 인덱스로 충분하고,
# idx_mv_active 하나만 **불변식**("활성 모델 버전은 항상 1개")을 인덱스로 보증하고 있어
# 대체 설계가 필요하다.
#   idx_doc_hash  는 조건을 떼도 같다 — MariaDB 는 UNIQUE 칼럼에 NULL 을 여러 개 허용한다.
# 두 인덱스는 MariaDB 정본에 두지 않는다.
#   idx_lk_keyword_trgm  pg_trgm 트라이그램 인덱스에 대응하는 것이 MariaDB 에 없다.
#                        FULLTEXT+ngram 은 MySQL 번들 파서라 MariaDB 에서 보장되지 않고,
#                        접두 인덱스는 중간일치를 못 탄다. 현행 병기 칸에만 남긴다.
#   idx_doc_metadata     JSON 경로 색인에 대응하는 것이 없다. 뽑을 키를 정하면 그때
#                        생성 칼럼으로 선언하고 인덱스를 건다.
_MARIA_INDEX = {
    "idx_lk_keyword_trgm": "(MariaDB 정본에 두지 않습니다 — 대응 인덱스 없음)",
    "idx_doc_metadata": "(MariaDB 정본에 두지 않습니다 — 대응 인덱스 없음)",
    "idx_doc_hash":
        "UNIQUE INDEX idx_doc_hash (file_hash)",
    "idx_doc_pending":
        "INDEX idx_doc_pending (processing_status, uploaded_at)",
    "idx_cls_staging":
        "INDEX idx_cls_staging (status, classified_at DESC)",
    "idx_mv_active":
        "UNIQUE INDEX idx_mv_active (active_key)",
    "idx_corr_unconsumed":
        "INDEX idx_corr_unconsumed (consumed_in_run)",
}


def _maria_constraint(pg: str) -> str:
    """현행 인덱스 표기 -> MariaDB 표기. 바뀌지 않는 것은 그대로 돌려준다."""
    m = re.search(r"INDEX\s+([a-z_0-9]+)", pg or "")
    if m and m.group(1) in _MARIA_INDEX:
        return _MARIA_INDEX[m.group(1)]
    return pg

def _default_cell(col: dict) -> str:
    """DB 기본값을 우선 보이고, 없을 때만 애플리케이션 기본값을 표시한다."""
    if col["default"]:
        return html.escape(col["default"])
    app = (col.get("app_default") or "").strip()
    if app in ("", "None", "dict", "list"):
        return ""
    return "앱 " + html.escape(app.strip("\"'"))


def _fmt_cols(items: list[str]) -> str:
    out = []
    for x in items:
        x = x.strip()
        if m := re.fullmatch(r'desc\("(\w+)"\)', x):
            out.append(f"{m.group(1)} DESC")
        else:
            out.append(x.strip('"'))
    return ", ".join(out)


def _fmt_constraint(item: str) -> str:
    """__table_args__ 의 파이썬 선언을 SQL 어법으로 옮긴다."""
    kind = item.split("(", 1)[0]
    args = _split_args(_balanced(item, item.find("(") + 1))
    cols, name, opts = [], "", []
    for a in args:
        if a.startswith("name="):
            name = a.split("=", 1)[1].strip("\"'")
        elif a == "unique=True":
            opts.append("UNIQUE")
        elif a.startswith("postgresql_using="):
            opts.append("USING " + a.split("=", 1)[1].strip("\"'"))
        elif a.startswith("postgresql_where="):
            opts.append("WHERE " + _sql_default(a.split("=", 1)[1]))
        elif a.startswith("postgresql_ops="):
            continue
        else:
            cols.append(a)
    if kind == "CheckConstraint":
        expr = cols[0].strip("\"'") if cols else ""
        return f"CHECK {name} — {expr}" if name else f"CHECK — {expr}"
    if kind == "UniqueConstraint":
        return f"UNIQUE {name} ({_fmt_cols(cols)})" if name else f"UNIQUE ({_fmt_cols(cols)})"
    if not name and cols:
        name = cols.pop(0).strip("\"'")
    head = " ".join(o for o in opts if o == "UNIQUE")
    tail = " ".join(o for o in opts if o != "UNIQUE")
    return (f"{head + ' ' if head else ''}INDEX {name} ({_fmt_cols(cols)})"
            + (f" {tail}" if tail else "")).strip()


def _split_args(s: str) -> list[str]:
    """괄호 깊이를 지키며 최상위 콤마로 인자를 자른다."""
    out, buf, depth, quote = [], "", 0, None
    for ch in s:
        if quote:
            buf += ch
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            buf += ch
        elif ch in "([{":
            depth += 1
            buf += ch
        elif ch in ")]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            out.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        out.append(buf.strip())
    return out


def _balanced(src: str, start: int) -> str:
    """src[start] 가 여는 괄호 다음이라고 보고, 짝이 맞는 지점까지 돌려준다."""
    depth, buf = 1, ""
    for ch in src[start:]:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                break
        buf += ch
    return buf


def parse_models() -> list[dict]:
    src = MODELS.read_text(encoding="utf-8")
    tables = []
    for block in re.split(r"\n(?=class \w+\(Base\):)", src):
        tm = re.search(r'__tablename__\s*=\s*"([a-z_]+)"', block)
        if not tm:
            continue
        tbl = {"name": tm.group(1), "cols": [], "pk": [], "indexes": [],
               "uniques": [], "checks": [], "partition": None}
        for m in re.finditer(
            r"^    ([a-z_0-9]+)\s*:\s*Mapped\[(.+)\]\s*=\s*mapped_column\(", block, re.M
        ):
            name, pytype = m.group(1), m.group(2).strip()
            args = _split_args(_balanced(block, m.end()))
            col = {"attr": name, "name": name, "pytype": pytype, "type": "",
                   "notnull": False, "pk": False, "unique": False,
                   "fk": None, "ondelete": None, "default": "", "app_default": ""}
            for a in args:
                if a.startswith('"') and a.endswith('"') and not col["type"]:
                    # mapped_column("metadata", JSONB, ...) — DB 컬럼명 재지정
                    col["name"] = a.strip('"')
                elif a.startswith("ForeignKey("):
                    inner = _split_args(a[len("ForeignKey("):-1])
                    col["fk"] = inner[0].strip('"')
                    for x in inner[1:]:
                        if x.startswith("ondelete="):
                            col["ondelete"] = x.split("=", 1)[1].strip('"')
                elif a == "primary_key=True":
                    col["pk"] = True
                elif a == "nullable=False":
                    col["notnull"] = True
                elif a == "unique=True":
                    col["unique"] = True
                elif a.startswith("server_default="):
                    col["default"] = _sql_default(a.split("=", 1)[1])
                elif a.startswith("default="):
                    col["app_default"] = a.split("=", 1)[1]
                elif a.startswith("Identity("):
                    col["identity"] = True
                elif not col["type"] and not a.startswith(("autoincrement", "index=", "comment=")):
                    col["type"] = _phys_type(a)
            if col["pk"]:
                col["notnull"] = True
            tbl["cols"].append(col)
        ta = re.search(r"__table_args__\s*=\s*\(", block)
        if ta:
            body = _balanced(block, ta.end())
            for item in _split_args(body):
                item = re.sub(r"#[^\n]*", "", item)
                item = re.sub(r"\s+", " ", item).strip()
                if item.startswith("PrimaryKeyConstraint("):
                    tbl["pk"] = [x.strip('"') for x in _split_args(item[21:-1])]
                elif item.startswith("UniqueConstraint("):
                    tbl["uniques"].append(item)
                elif item.startswith("CheckConstraint("):
                    tbl["checks"].append(item)
                elif item.startswith("Index("):
                    tbl["indexes"].append(item)
        if not tbl["pk"]:
            tbl["pk"] = [c["name"] for c in tbl["cols"] if c["pk"]]
        tables.append(tbl)

    # FK 로만 선언된 컬럼은 타입이 비어 있다 — 참조 대상에서 채운다.
    by_name = {t["name"]: t for t in tables}
    for t in tables:
        for c in t["cols"]:
            if not c["type"] and c["fk"]:
                tgt_tbl, tgt_col = c["fk"].split(".")
                for tc in by_name.get(tgt_tbl, {"cols": []})["cols"]:
                    if tc["name"] == tgt_col:
                        c["type"] = tc["type"] or "INTEGER"
            if not c["type"]:
                c["type"] = "—"
    return tables


def parse_rag() -> list[dict]:
    """RAG 2테이블은 ORM 매핑이 없다 — Alembic DDL 과 COMMENT 사전에서 읽는다."""
    ddl = MIG_RAG.read_text(encoding="utf-8")
    comments = {}
    ctree = ast.parse(MIG_RAG_COMMENT.read_text(encoding="utf-8"))
    for node in ctree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            nm = node.targets[0].id
            if nm in ("_VECTOR_COLS", "_ALIAS_COLS"):
                comments[nm] = ast.literal_eval(node.value)

    out = []
    for tname, ckey in (("tb_rag_vectors", "_VECTOR_COLS"), ("tb_rag_aliases", "_ALIAS_COLS")):
        m = re.search(rf"CREATE TABLE IF NOT EXISTS {tname} \((.*?)\n\s*\)", ddl, re.S)
        if not m:
            continue
        tbl = {"name": tname, "cols": [], "pk": [], "indexes": [],
               "uniques": [], "checks": [], "partition": None}
        for line in m.group(1).split("\n"):
            line = re.sub(r"--.*", "", line).strip().rstrip(",")
            if not line:
                continue
            if line.upper().startswith("PRIMARY KEY"):
                tbl["pk"] = [x.strip() for x in line[line.find("(") + 1:line.rfind(")")].split(",")]
                continue
            cm = re.match(r"([a-z_]+)\s+(.+)", line)
            if not cm:
                continue
            name, rest = cm.group(1), cm.group(2).strip()
            # f-string DDL 의 {{}} 이스케이프와 차원 자리표시자를 실제 값으로 되돌린다.
            rest = rest.replace("{EMBED_DIM}", "1024").replace("{{", "{").replace("}}", "}")
            notnull = "NOT NULL" in rest.upper()
            inline_pk = bool(re.search(r"PRIMARY KEY", rest, re.I))
            if inline_pk:
                tbl["pk"].append(name)
            default = ""
            if dm := re.search(r"DEFAULT\s+(.+)$", rest, re.I):
                default = re.sub(r"::[\w ]+$", "", dm.group(1).strip()).strip()
            typ = re.split(r"\s+NOT NULL|\s+DEFAULT|\s+GENERATED|\s+PRIMARY KEY",
                           rest, flags=re.I)[0].strip()
            tbl["cols"].append({
                "attr": name, "name": name, "pytype": "", "type": typ.upper(),
                "notnull": notnull, "pk": False, "unique": False, "fk": None,
                "ondelete": None, "default": default, "app_default": "",
                "desc_override": comments.get(ckey, {}).get(name, ""),
            })
        for c in tbl["cols"]:
            if c["name"] in tbl["pk"]:
                c["pk"] = True
                c["notnull"] = True
        # 파이썬 문자열 이어붙이기("...로 끝나고 다음 줄이 "...로 시작)를 먼저 봉합한다.
        flat = re.sub(r'"\s*\n\s*"', "", ddl)
        for im in re.finditer(rf'CREATE INDEX IF NOT EXISTS (\w+)\s+ON {tname}\s*([^";]*)', flat):
            body = re.sub(r"\s+", " ", im.group(2)).strip()
            note = " — pg_bigm 확장이 설치된 경우에만 생성" if "gin_bigm_ops" in body else ""
            tbl["indexes"].append(f"INDEX {im.group(1)} {body}{note}")
        out.append(tbl)
    return out


# 파티션 부모는 ORM 이 표현하지 않는다 — models.py 도크스트링이 명시한 사실을 옮긴다.
# 월별 RANGE 파티션을 두는 표. [2026-09-03] 셋에서 하나로 줄였다.
#
# 실측(223 · 2026-09-03): tb_audit_log 63,697행 / tb_llm_usage 214행 / tb_chunks 193행.
# 뒤 둘은 파티션 16개를 두고도 파티션당 열 몇 행이라 이득이 없고, MariaDB 로 가면 비용만
# 는다 — 파티션 키가 **기본키에 포함돼야** 해서 단일 키를 복합키로 바꿔야 하고,
# tb_chunks 는 tb_classification_evidence.chunk_id 참조까지 영향을 받는다.
#
# tb_audit_log 만 남긴다. 보존기간이 지난 파티션을 통째로 DROP 하는 것이 목적이고,
# 행이 계속 쌓이는 유일한 표다.
#
# ⚠ tb_chunks 는 회원사 운영이 시작되면 늘어난다(문서 1건당 청크 수십 개). 운영 규모에서
#   다시 판단할 것 — 지금 안 두는 것이지 영영 두지 않는다는 뜻이 아니다.
# [2026-09-05] 비웠다. 커밋 90c0d96a 로 **파티션 없이 가기로** 했고, 실측으로 확인했다.
#   docker exec koipa-poc-mariadb-1 mariadb -ukoipa koipa -e
#     "SELECT TABLE_NAME, PARTITION_NAME FROM information_schema.PARTITIONS
#       WHERE TABLE_SCHEMA='koipa' AND TABLE_NAME IN (...)"
#   → tb_audit_log · tb_llm_usage · tb_chunks 셋 다 PARTITION_NAME 이 NULL.
# 대신 시간축 인덱스(b4c5d6e7f8a9)와 보존기간 삭제(services/retention.py)를 쓴다.
# PostgreSQL 계열 마이그레이션에는 파티션이 남아 있으나 납품 정본은 MariaDB 다.
PARTITIONS: dict[str, str] = {}


# ──────────────────────────────────────────────────────────────────────
# 2. ERD 레이아웃
# ──────────────────────────────────────────────────────────────────────

BOX_W, BOX_H, COL_X = 196, 26, {0: 24, 1: 300, 2: 576, 3: 852}

# (테이블, 열, y) — 부모가 왼쪽, 자식이 오른쪽에 오도록 손으로 배치한다.
LAYOUT = [
    ("tb_classification_levels", 0, 40),
    ("tb_evaluation_factors", 0, 96),
    ("tb_documents", 0, 176),
    ("tb_model_versions", 0, 300),
    ("tb_prompt_versions", 0, 356),
    ("tb_level_keywords", 1, 40),
    ("tb_document_labels", 1, 104),
    ("tb_document_factor_scores", 1, 144),
    ("tb_chunks", 1, 184),
    ("tb_classifications", 1, 232),
    ("tb_training_runs", 1, 300),
    ("tb_sample_documents", 1, 356),
    ("tb_classification_evidence", 2, 208),
    ("tb_corrections", 2, 256),
    ("tb_training_epochs", 2, 304),
    ("tb_training_datasets", 2, 352),
    ("tb_llm_usage", 3, 40),
    ("tb_audit_log", 3, 80),
    ("tb_guides", 3, 120),
    ("tb_advisory_locks", 3, 160),
]


def build_erd(tables: list[dict]) -> str:
    pos = {n: (COL_X[c], y) for n, c, y in LAYOUT}
    placed = set(pos)

    edges = []  # (자식, 부모, 라벨)
    for t in tables:
        for c in t["cols"]:
            if c["fk"]:
                parent = c["fk"].split(".")[0]
                edges.append((t["name"], parent, c["name"]))
    soft = SOFT_REFS

    # 같은 (자식,부모) 쌍의 여러 FK 는 선 하나로 합치고 라벨만 모은다.
    merged: dict[tuple[str, str], list[str]] = {}
    for ch, pa, col in edges:
        merged.setdefault((ch, pa), []).append(col)

    parts = [
        '<svg viewBox="0 0 1080 420" width="100%" role="img" '
        'aria-label="전체 테이블 관계도" style="min-width:940px">',
        '<defs><marker id="tsarr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        'markerHeight="7" orient="auto-start-reverse">'
        '<path d="M0,0 L10,5 L0,10 z" fill="#94a3b8"/></marker></defs>',
    ]

    def edge_path(ch, pa, dashed=False):
        if ch not in placed or pa not in placed:
            return None
        px, py = pos[pa]
        cx, cy = pos[ch]
        y1, y2 = py + BOX_H / 2, cy + BOX_H / 2
        if px < cx:
            x1, x2 = px + BOX_W, cx
        else:  # 자기참조·역방향
            x1, x2 = px, cx
        mid = (x1 + x2) / 2
        d = f"M{x1},{y1} C{mid},{y1} {mid},{y2} {x2},{y2}"
        cls = "ts-edge ts-soft" if dashed else "ts-edge"
        return (f'<path class="{cls}" data-from="{pa}" data-to="{ch}" d="{d}" '
                f'marker-end="url(#tsarr)"/>')

    for (ch, pa) in merged:
        if ch == pa:  # 자기참조
            x, y = pos[ch]
            parts.append(
                f'<path class="ts-edge" data-from="{pa}" data-to="{ch}" '
                f'd="M{x},{y + 6} C{x - 22},{y - 2} {x - 22},{y + BOX_H + 2} {x},{y + BOX_H - 6}" '
                f'marker-end="url(#tsarr)"/>')
            continue
        if p := edge_path(ch, pa):
            parts.append(p)
    for ch, pa, _ in soft:
        if p := edge_path(ch, pa, dashed=True):
            parts.append(p)

    for name, col, y in LAYOUT:
        x = COL_X[col]
        grp = META.TABLES[name][0]
        hub = name == "tb_documents"
        fill = "#eef2ff" if hub else "#ffffff"
        stroke = "#4f46e5" if hub else "#94a3b8"
        parts.append(
            f'<g class="ts-node" data-t="{name}">'
            f'<rect x="{x}" y="{y}" width="{BOX_W}" height="{BOX_H}" rx="3" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{2 if hub else 1}"/>'
            f'<text x="{x + 9}" y="{y + 17}" font-size="11.5" font-family="ui-monospace,monospace" '
            f'fill="#1e293b">{name}</text>'
            f'<text x="{x + BOX_W - 9}" y="{y + 17}" font-size="9.5" text-anchor="end" '
            f'fill="#94a3b8">{grp}</text></g>')

    for cx, label in ((COL_X[0], "부모(기준)"), (COL_X[1], "자식"),
                      (COL_X[2], "손자(파생)"), (COL_X[3], "독립(운영·검색)")):
        parts.append(f'<text x="{cx}" y="18" font-size="10.5" fill="#64748b" '
                     f'font-weight="600">{label}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────────
# 3. 렌더링
# ──────────────────────────────────────────────────────────────────────

# MariaDB 정본에만 존재하는 생성 칼럼. 현행 PostgreSQL 에는 없다(부분 인덱스로 대신하므로).
# 인덱스가 참조하는 칼럼이 표에 없으면 그 인덱스는 만들어지지 않는다 — 실제로
# idx_mv_active 하나가 '활성 모델 1건' 불변식을 지키는 유일한 장치다.
GENERATED_COLUMNS = {
    "tb_model_versions": [{
        "name": "active_key",
        "type": "TINYINT(1) GENERATED ALWAYS AS (IF(is_active=1,1,NULL)) VIRTUAL",
        "legacy": "(없음 — 현행은 부분 인덱스)",
        "notnull": False, "pk": False, "fk": "", "ondelete": "", "unique": False,
        "default": "", "identity": False,
        "desc": "활성일 때만 1, 아니면 NULL. UNIQUE 인덱스 idx_mv_active 가 "
                "활성 1건만 허용하도록 보증합니다",
    }],
}

def _placement_cell(name: str) -> str:
    """배치 칸. 지재원 전용만 표시를 달고 공통은 담백하게 둔다."""
    # 근거를 title 툴팁에 담지 않는다 — 인쇄·PDF 에서 사라진다. 배치 값만 표시하고
    # 사유는 §02 에 한 번 적는다.
    where, _why = getattr(META, "PLACEMENT", {}).get(name, ("둘 다", ""))
    if where == "지재원":
        return '<td class="c-key"><span class="k-pk">지재원만</span></td>'
    if where == "고객사":
        return '<td class="c-key"><span class="k-fk">고객사만</span></td>'
    return '<td class="c-key">둘 다</td>'

def col_desc(table: str, col: dict) -> str:
    if col.get("desc_override"):
        return col["desc_override"]
    d = META.COLS.get(table, {}).get(col["name"])
    if d:
        return d
    return META.COMMON.get(col["name"], "")


def render(tables: list[dict], erd: str, commit: str, today: str) -> str:
    skel = SKELETON.read_text(encoding="utf-8")
    style = re.search(r"<style>(.*?)</style>", skel, re.S).group(1)
    header = re.search(r"<nav class=\"nav\">.*?</nav>", skel, re.S).group(0)
    # 문서 이름만 바꿔 단다. 목록 링크는 걷는다 — 같은 파일이 폴더 여러 곳에 놓이는데
    # 상대 경로가 폴더마다 달라 한쪽에서는 반드시 깨진다(2026-08-29 실제로 깨져 있었다).
    header = re.sub(r'<span class="brand-sub">[^<]*</span>',
                    '<span class="brand-sub">테이블정의서 · ERD</span>', header)

    by_name = {t["name"]: t for t in tables}
    e = html.escape
    total_cols = sum(len(t["cols"]) for t in tables)
    missing = [(t["name"], c["name"]) for t in tables for c in t["cols"] if not col_desc(t["name"], c)]

    fks = []
    for t in tables:
        for c in t["cols"]:
            if c["fk"]:
                fks.append((t["name"], c["name"], c["fk"], c["ondelete"] or "NO ACTION"))

    o: list[str] = []
    A = o.append
    A("<!DOCTYPE html>\n<html lang=\"ko\">\n<head>\n<meta charset=\"UTF-8\" />")
    A('<meta name="viewport" content="width=device-width, initial-scale=1.0" />')
    A("<title>테이블정의서 · ERD | KOIPA AI 영업비밀 등급분류 시스템</title>")
    A(f"<style>{style}</style>")
    A("""<style>

.ts-edge
.ts-node rect{transition:stroke .12s;}
.ts-node:hover rect{stroke:#1e293b;stroke-width:2;}
table.spec{width:100%;border-collapse:collapse;font-size:12.5px;}
table.spec th{background:#f4f4f5;text-align:left;font-weight:600;font-size:11.5px;
  padding:6px 8px;border:1px solid #d4d4d8;white-space:nowrap;}
table.spec td{padding:5px 8px;border:1px solid #e4e4e7;vertical-align:top;}
table.spec td.c-name{font-family:ui-monospace,monospace;font-size:11.5px;white-space:nowrap;}
table.spec td.c-type{font-family:ui-monospace,monospace;font-size:11px;color:#3f3f46;white-space:nowrap;}
table.spec td.c-key{text-align:center;font-size:10.5px;font-weight:700;white-space:nowrap;}
table.spec td.c-nn{text-align:center;}
.k-pk{color:#b45309;}.k-fk{color:#1d4ed8;}.k-uq{color:#15803d;}
.spec-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin:26px 0 4px;}
.spec-head h3{margin:0;font-family:ui-monospace,monospace;}
.spec-logical{font-size:13px;color:#52525b;}
.spec-badge{display:inline-block;background:#18181b;color:#fff;font-size:9.5px;font-weight:700;
  letter-spacing:.04em;padding:2px 7px;border-radius:3px;font-family:ui-monospace,monospace;}
.spec-note{font-size:12.5px;color:#52525b;margin:2px 0 8px;line-height:1.6;}
.spec-idx{font-size:11.5px;color:#52525b;margin:6px 0 0;line-height:1.7;}
.spec-idx code{font-size:11px;}

.tbl-wrap{overflow-x:auto;}
.num{font-family:ui-monospace,monospace;font-size:10.5px;font-weight:700;color:var(--dim);
  padding:2px 7px;background:var(--mid);border:1px solid var(--line);margin-right:6px}
.group-header{display:flex;align-items:center;gap:10px;margin:40px 0 10px}
.group-header h2{margin:0}
@media print{
  table.spec{font-size:8pt}
  .tbl-wrap{overflow:visible}
  section{break-inside:auto}
}
</style>""")
    A("</head>\n<body>\n<div id=\"top\"></div>")
    A(header)
    A('<div class="wrap">')

    # ── 표지 (정본 서식: eyebrow · h1 · meta)
    A('  <div class="top">')
    A('    <div class="eyebrow">감리 산출물 · 데이터베이스 정의</div>')
    A('    <h1>테이블정의서 · ERD</h1>')
    A(f'    <div class="meta">MariaDB 10.11 · InnoDB · utf8mb4 / utf8mb4_bin · '
      f'{len(tables)}테이블 {total_cols}칼럼 · '
      f'등급체계·문서·라벨링·추론·학습·보정·합성·운영 8개 그룹 · {today} 생성</div>')
    A('  </div>')
    # ── 그룹·테이블 목록
    A('<section id="tables-index"><h2><span class="num">01</span> 테이블 목록</h2>')
    A('<p class="spec-note"><b>물리 타입 전환 규약</b> — '
      '<code>DATETIME(6)</code> 는 UTC 로 저장하며 시간대 변환은 응용이 수행합니다. '
      '<code>CHAR(36)</code> UUID 는 소문자 표준형으로만 저장합니다'
      '(콜레이션이 <code>utf8mb4_bin</code> 이므로 대소문자가 섞이면 다른 값이 됩니다). '
      '<code>JSON</code> 은 LONGTEXT + 검증 제약이므로 키로 거르는 질의에는 생성 칼럼과 '
      '인덱스가 필요합니다. 배열은 JSON 배열로 담습니다.</p>')
    A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
      "<th>그룹</th><th>물리명</th><th>논리명</th><th>컬럼</th><th>기본키</th>"
      "<th>배치</th><th>파티션</th></tr></thead><tbody>")
    for gid, gname, _ in META.GROUPS:
        for name, (g, logical, purpose) in META.TABLES.items():
            if g != gid or name not in by_name:
                continue
            t = by_name[name]
            part = PARTITIONS.get(name)
            A(f'<tr><td class="c-key">{gid} {e(gname)}</td>'
              f'<td class="c-name"><a href="#t-{name}">{name}</a></td>'
              f"<td>{e(logical)}</td><td class=\"c-nn\">{len(t['cols'])}</td>"
              f"<td class=\"c-type\">{e(', '.join(t['pk']))}</td>"
              + _placement_cell(name)
              + f'<td class="c-type">{("월별 RANGE(" + part + ")") if part else "—"}</td></tr>')
    A("</tbody></table></div></section>")

    # ── 서버별 배포 구분
    PL = getattr(META, "PLACEMENT", {})
    jjw_only = [n for n in by_name if PL.get(n, ("둘 다", ""))[0] == "지재원"]
    cust_only = [n for n in by_name if PL.get(n, ("둘 다", ""))[0] == "고객사"]
    both = [n for n in by_name if PL.get(n, ("둘 다", ""))[0] not in ("지재원", "고객사")]
    common = both + cust_only          # 고객사 서버가 만드는 표
    A('<section id="placement"><h2><span class="num">02</span> 서버별 배포 구분</h2>')
    A('<p class="spec-note">서버별로 생성하는 표를 구분합니다.</p>')
    A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
      '<th>구분</th><th>표 수</th><th>어느 서버에 만드는가</th>'
      '<th>표</th></tr></thead><tbody>')
    A(f'<tr><td class="c-name">둘 다</td><td class="c-nn">{len(both)}</td>'
      '<td>지재원 · 고객사 양쪽</td>'
      f'<td class="c-type">{e(", ".join(sorted(both)))}</td></tr>')
    A(f'<tr><td class="c-name">지재원만</td><td class="c-nn">{len(jjw_only)}</td>'
      '<td>지재원 서버에만</td>'
      f'<td class="c-type">{e(", ".join(sorted(jjw_only))) or "—"}</td></tr>')
    if cust_only:
        A(f'<tr><td class="c-name">고객사만</td><td class="c-nn">{len(cust_only)}</td>'
          '<td>고객사 서버에만</td>'
          f'<td class="c-type">{e(", ".join(sorted(cust_only)))}</td></tr>')
    A("</tbody></table></div>")
    A(f'<p class="spec-note" style="margin-top:12px">지재원 '
      f'{len(both) + len(jjw_only)}종 · 고객사 {len(both) + len(cust_only)}종을 생성합니다.</p>')

    # [2026-09-03] 표별 상세표를 여기 두지 않는다 — §01 목록에 이미 배치 칸이 있어
    # 같은 행을 근거 문구까지 그대로 되풀이하던 자리였다.
    A("</section>")

    # ── ERD
    A('<section id="erd"><h2><span class="num">03</span> ERD 관계도</h2>')
    A(f'<p class="spec-note">실선은 FK 제약, 점선은 제약 없는 참조입니다.</p>')
    A('<div style="overflow-x:auto;border:1px solid rgba(0,0,0,.12);padding:16px;'
      'margin:14px 0;background:#fafafa;">')
    A(erd)
    A("</div>")
    A(f'<p class="spec-note">FK 제약 {len(fks)}건 · 제약 없는 참조 {len(SOFT_REFS)}건 '
      '(§05 참조).</p>')
    A("</section>")

    # ── 테이블별 정의
    A('<section id="tables"><h2><span class="num">04</span> 테이블별 정의</h2>')
    for gid, gname, gdesc in META.GROUPS:
        members = [n for n, (g, _, _) in META.TABLES.items() if g == gid and n in by_name]
        if not members:
            continue
        A(f'<div class="group-header" id="g-{gid}"><span class="spec-badge">{gid}</span> '
          f"<b>{e(gname)}</b> — {e(gdesc)}</div>")
        for name in members:
            t = by_name[name]
            _, logical, purpose = META.TABLES[name]
            part = PARTITIONS.get(name)
            A(f'<div class="spec-head" id="t-{name}"><h3>{name}</h3>'
              f'<span class="spec-logical">{e(logical)}</span>'
              + (f'<span class="spec-badge">RANGE PARTITION · {part}</span>' if part else "")
              + "</div>")
            A(f'<p class="spec-note">{e(purpose)}</p>')
            A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
              "<th>컬럼</th><th>물리 타입 (MariaDB)</th>"
              "<th>현행 (PostgreSQL)</th>"
              "<th>NULL</th><th>키</th>"
              "<th>기본값 (MariaDB)</th><th>현행 (PostgreSQL)</th>"
              "<th>설명</th></tr></thead><tbody>")
            for c in t["cols"] + GENERATED_COLUMNS.get(name, []):
                keys = []
                if c["name"] in t["pk"] or c["pk"]:
                    keys.append('<span class="k-pk">PK</span>')
                if c["fk"]:
                    keys.append('<span class="k-fk">FK</span>')
                if c["unique"]:
                    keys.append('<span class="k-uq">UQ</span>')
                d = c.get("desc") or col_desc(name, c)
                if c["fk"]:
                    tail = f' <span style="color:#71717a">→ {e(c["fk"])}'
                    tail += f' ON DELETE {e(c["ondelete"])}' if c["ondelete"] else ""
                    tail += "</span>"
                    d = (e(d) + tail) if d else tail
                else:
                    d = e(d)
                A(f'<tr><td class="c-name">{c["name"]}</td>'
                  f'<td class="c-type">{e(c["type"] if c.get("legacy") else _maria_type(c["type"]))}</td>'
                  f'<td class="c-type">{e(c.get("legacy") or c["type"])}</td>'
                  f'<td class="c-nn">{"●" if c["notnull"] else ""}</td>'
                  f'<td class="c-key">{" ".join(keys)}</td>'
                  f'<td class="c-type">{"AUTO_INCREMENT" if c.get("identity") else (e(_maria_default(_sql_default(c["default"]))) if c.get("default") else _default_cell(c))}</td>'
                  f'<td class="c-type">{"GENERATED ALWAYS AS IDENTITY" if c.get("identity") else _default_cell(c)}</td>'
                  f"<td>{d}</td></tr>")
            A("</tbody></table></div>")
            lines = []
            if t["pk"]:
                lines.append("<b>PK</b> " + ", ".join(f"<code>{e(x)}</code>" for x in t["pk"]))
            for u in t["uniques"]:
                lines.append(f"<code>{e(_fmt_constraint(u))}</code>")
            for ck in t["checks"]:
                lines.append(f"<code>{e(_fmt_constraint(ck))}</code>")
            for ix in t["indexes"]:
                raw = _fmt_constraint(ix) if ix.startswith(chr(73)+chr(110)+chr(100)+chr(101)+chr(120)+chr(40)) else ix
                maria = _maria_constraint(raw)
                if maria != raw:
                    lines.append(f"<code>{e(maria)}</code><br>"
                                 f'<span style="color:#71717a">현행 PostgreSQL — '
                                 f"<code>{e(raw)}</code></span>")
                else:
                    lines.append(f"<code>{e(raw)}</code>")
            if part:
                lines.append(
                    f"<b>PARTITION</b> <code>PARTITION BY RANGE COLUMNS ({part})</code> — 월별. "
                    "파티션 키가 기본키에 포함되어야 하므로 기본키는 (기존 키 + 파티션 키) "
                    "복합키입니다. 마지막 파티션으로 "
                    "<code>PARTITION pmax VALUES LESS THAN (MAXVALUE)</code> 를 두어 범위 밖 "
                    "행을 받고, 월별 파티션은 일간 작업이 "
                    "<code>ALTER TABLE … REORGANIZE PARTITION pmax INTO (…)</code> 로 "
                    "미리 연장합니다.")
                lines.append(
                    f'<span style="color:#71717a">현행 PostgreSQL — '
                    f"<code>PARTITION BY RANGE ({part})</code></span>")
            if lines:
                A('<p class="spec-idx">' + "<br>".join(lines) + "</p>")
    A("</section>")

    # ── 관계 정의
    A('<section id="fk"><h2><span class="num">05</span> 관계 정의</h2>')
    A(f'<p class="spec-note">FK 제약 {len(fks)}건입니다.</p>')
    A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
      "<th>자식 테이블</th><th>자식 컬럼</th><th>부모</th><th>ON DELETE</th>"
      "</tr></thead><tbody>")
    for ch, col, tgt, od in fks:
        A(f'<tr><td class="c-name">{ch}</td><td class="c-name">{col}</td>'
          f'<td class="c-name">{e(tgt)}</td><td class="c-type">{e(od)}</td></tr>')
    A("</tbody></table></div>")
    A(f'<p class="spec-note" style="margin-top:12px;"><b>제약 없는 참조 {len(SOFT_REFS)}건</b> — '
      + " · ".join(f"<code>{ch}.{col} → {pa}</code>" for ch, pa, col in SOFT_REFS)
      + ". 애플리케이션이 정합을 보증합니다.</p>")
    A("</section>")

    # ── 범위 밖
    A('<section id="scope"><h2><span class="num">06</span> 범위</h2>')
    A('<p class="spec-note">본 시스템이 생성·소유하는 개체만 수록합니다. '
      "KL 원천 문서 저장소·EDMS·회원/권한·자가진단은 외부 시스템이며, 연동 키는 "
      "<code>tb_documents.external_ref</code> · <code>tb_documents.metadata</code> 입니다.</p>")
    A("</section>")

    # ── 개정 이력
    A('<section id="revisions"><h2><span class="num">07</span> 개정 이력</h2>')
    A('<div class="tw"><table>')
    A('<thead><tr><th style="width:8%">판</th><th style="width:16%">일자</th>'
      '<th style="width:18%">근거 커밋</th><th>내용</th></tr></thead><tbody>')
    for rev, day, ref, what in REVISIONS:
        # 머리말 블록을 걷어 "이 문서 머리말의 커밋" 이 가리킬 곳이 없어졌다.
        # 생성 시점의 HEAD 를 그대로 적는다.
        cell = f"<code>{commit}</code>" if ref.startswith("이 문서") else f"<code>{ref}</code>"
        A(f"<tr><td>{rev}</td><td>{day}</td><td>{cell}</td><td>{what}</td></tr>")
    A("</tbody></table></div>")
    A("</section>")

    A('<div class="foot">테이블정의서 · ERD — 한국지식재산보호원 AI 영업비밀 등급분류 시스템 · '
      f'{today} 생성</div>')
    A("</div>")
    A("</body>\n</html>")
    return "\n".join(o), missing


# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 검증만 한다")
    ap.add_argument("--force", action="store_true",
                    help="생성물에 없는 요소(클릭형 관계도·인쇄 규격)가 기존 문서에 있어도 덮어쓴다")
    args = ap.parse_args()

    # [2026-09-02] 유사문서 검색을 쓰지 않기로 해 RAG 2표(tb_rag_vectors·tb_rag_aliases)를
    # 정의서에서 뺀다. 두 표는 ORM 매핑이 없고 외래키가 0개라 나머지 19표에 영향이 없다.
    # parse_rag() 는 지우지 않고 남겨 둔다 — 되살릴 때 다시 쓰기 위해서다.
    tables = parse_models()

    # [2026-09-03] 정의서에서 빼는 표·칼럼을 여기서 걷는다.
    # 소스(poc/src)를 고치지 않기로 했으므로 models.py 는 그대로 두고 문서에서만 뺀다.
    # 사유는 table_spec_meta.EXCLUDED_* 에 적혀 있다.
    ex_tables = getattr(META, "EXCLUDED_TABLES", {})
    ex_cols = getattr(META, "EXCLUDED_COLUMNS", {})
    dropped_tables = sorted(t["name"] for t in tables if t["name"] in ex_tables)
    tables = [t for t in tables if t["name"] not in ex_tables]
    dropped_cols = 0
    for t in tables:
        drop = ex_cols.get(t["name"])
        if not drop:
            continue
        before = len(t["cols"])
        t["cols"] = [c for c in t["cols"] if c["name"] not in drop]
        dropped_cols += before - len(t["cols"])
        # 빠진 칼럼을 가리키던 인덱스·제약도 함께 걷는다 — 남기면 없는 칼럼을 가리킨다.
        for key in ("indexes", "uniques", "checks"):
            if key in t:
                t[key] = [x for x in t[key] if not any(d in x for d in drop)]
    # 빠진 표로 가던 FK 는 상대가 없으므로 함께 걷는다.
    for t in tables:
        for c in t["cols"]:
            if c.get("fk") and c["fk"].split(".")[0] in ex_tables:
                c["fk"] = ""
                c["ondelete"] = ""

    for t in tables:
        t["partition"] = PARTITIONS.get(t["name"])

    # 제외 목록이 코드보다 뒤처지면 조용히 아무것도 안 뺀다 — 그것을 오류로 잡는다.
    live = {t["name"] for t in parse_models()}
    ghost_t = [n for n in ex_tables if n not in live]
    ghost_c = [f"{tn}.{cn}" for tn, cols in ex_cols.items() for cn in cols
               if tn in live and cn not in {c["name"] for t in parse_models()
                                            if t["name"] == tn for c in t["cols"]}]

    unknown = [t["name"] for t in tables if t["name"] not in META.TABLES]
    stale = [n for n in META.TABLES if n not in {t["name"] for t in tables}
             and n not in ex_tables]

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "unknown"
    today = _dt.date.today().isoformat()
    doc, missing = render(tables, build_erd(tables), commit, today)

    total = sum(len(t["cols"]) for t in tables)
    print(f"테이블 {len(tables)} · 컬럼 {total}")
    if dropped_tables or dropped_cols:
        print(f"  [제외] 표 {len(dropped_tables)}개 · 칼럼 {dropped_cols}개 "
              f"— 소스에는 남아 있고 정의서에서만 뺀다 ({', '.join(dropped_tables) or '표 없음'})")
    ok = True
    if ghost_t or ghost_c:
        print("  [오류] 제외 목록이 코드보다 뒤처졌다 — 이미 없는 것을 빼려 한다:",
              ", ".join(ghost_t + ghost_c))
        ok = False
    if unknown:
        print("  [오류] 코드에 있는데 table_spec_meta.TABLES 에 없음:", ", ".join(unknown))
        ok = False
    if stale:
        print("  [오류] table_spec_meta.TABLES 에만 있고 코드에 없음:", ", ".join(stale))
        ok = False
    if missing:
        print(f"  [경고] 설명 없는 컬럼 {len(missing)}개:")
        for t, c in missing[:20]:
            print(f"        {t}.{c}")
        ok = False
    if ok:
        print("  설명 누락 0 · 코드와 정의서 테이블 집합 일치")

    if not args.check:
        for out in OUTS:
            if not out.exists():
                # 있는 사본만 갱신한다. 없는 자리에 새로 만들면 제출 묶음의 구성이
                # 소리 없이 바뀐다(build_erd.py 와 같은 규칙).
                print(f"  [없음] {out.relative_to(ROOT)}")
                continue
            # [2026-08-29] 덮어쓰기 가드. 배포 중인 정의서는 이 생성기가 아직 못 만드는
            # 것을 담고 있다 — 클릭형 관계도(erd-box)와 감리 정본 서식(@page 인쇄 규격).
            # 확인 없이 돌리면 그것들이 조용히 사라진다(2026-08-29 실제로 사라졌다).
            # 생성기가 그 둘을 낼 수 있게 되면 이 가드를 지운다.
            cur = out.read_text(encoding="utf-8", errors="replace")
            lost = [n for n, mark in (("클릭형 관계도", "erd-box"), ("인쇄 규격", "@page"))
                    if mark in cur and mark not in doc]
            if lost and not args.force:
                print(f"  [보호] {out.relative_to(ROOT)} — 덮어쓰지 않았다. "
                      f"생성물에 없는 것: {' · '.join(lost)}. 그래도 쓰려면 --force")
                ok = False
                continue
            out.write_text(doc, encoding="utf-8")
            print(f"  → {out.relative_to(ROOT)} ({len(doc):,} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
