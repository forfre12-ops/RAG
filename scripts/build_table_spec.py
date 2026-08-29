# -*- coding: utf-8 -*-
"""테이블정의서·ERD 감리문서를 코드에서 직접 생성한다.

왜 생성기인가. 기존 「기술구현_백서_부록A_DB스키마」는 손으로 쓴 문서라 스키마가
바뀌어도 따라오지 않았다 — 실측 2026-08-25: 문서는 "2026-08-02 이후 변경 없음"이라고
적혀 있는데 그 뒤 8/22·8/23·8/24 세 커밋이 컬럼 3개를 더했고 문서에는 없었다.
그래서 정의서를 사람이 쓰지 않고 **코드에서 뽑는다**. 스키마가 바뀌면 이 스크립트를
다시 돌리면 되고, 설명이 빠진 컬럼은 생성 때 경고로 드러난다.

진실 소스(이 스크립트가 직접 읽는 파일):
    poc/src/koipa/db/models.py                          ORM 19테이블 · 컬럼·타입·키·인덱스
    poc/alembic/versions/a1b2c3d4e5f6_pg_rag_vectorstore.py    RAG 2테이블 DDL
    poc/alembic/versions/a7b8c9d0e1f2_rag_vectors_column_comments.py  RAG 컬럼 주석
    scripts/table_spec_meta.py                          한국어 논리명·용도·컬럼 설명

사용:
    python scripts/build_table_spec.py            # 생성 + 자기검증
    python scripts/build_table_spec.py --check    # 생성 없이 검증만(문서-코드 차이 보고)
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
import table_spec_meta as META  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / "poc" / "src" / "koipa" / "db" / "models.py"
MIG_RAG = ROOT / "poc" / "alembic" / "versions" / "a1b2c3d4e5f6_pg_rag_vectorstore.py"
MIG_RAG_COMMENT = ROOT / "poc" / "alembic" / "versions" / "a7b8c9d0e1f2_rag_vectors_column_comments.py"
SKELETON = ROOT / "doc" / "result" / "KL_AI자료_2026-08" / "기술구현_백서_부록A_DB스키마.html"
OUT = ROOT / "doc" / "result" / "KL_AI자료_2026-08" / "테이블정의서_ERD.html"


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
PARTITIONS = {
    "tb_chunks": "created_at",
    "tb_llm_usage": "called_at",
    "tb_audit_log": "occurred_at",
}


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
    ("tb_rag_vectors", 3, 180),
    ("tb_rag_aliases", 3, 220),
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
    # 애플리케이션이 지키는 참조(제약 없음) — 점선으로 구분해 그린다.
    soft = [("tb_chunks", "tb_documents", "doc_id"),
            ("tb_classification_evidence", "tb_chunks", "chunk_id"),
            ("tb_rag_aliases", "tb_rag_vectors", "collection")]

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
    header = re.search(r"<header class=\"nav\">.*?</header>", skel, re.S).group(0)
    header = re.sub(r'<div class="nav-actions">.*?</div>\s*</div>',
                    '<div class="nav-actions">'
                    '<a class="nav-link" href="index.html">← 목록</a>'
                    '<a class="nav-link" href="#erd">ERD</a>'
                    '<a class="nav-link hide-sm" href="#tables">테이블정의</a>'
                    '<a class="nav-link hide-sm" href="#fk">관계정의</a>'
                    "</div>\n  </div>", header, flags=re.S)

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
.ts-edge{stroke:#94a3b8;stroke-width:1.1;fill:none;}
.ts-edge.ts-soft{stroke-dasharray:4 3;stroke:#cbd5e1;}
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
.srcbox{border-left:3px solid #18181b;background:#fafafa;padding:11px 14px;margin:14px 0;
  font-size:12.5px;line-height:1.7;color:#3f3f46;}
.tbl-wrap{overflow-x:auto;}
</style>""")
    A("</head>\n<body>\n<div id=\"top\"></div>")
    A(header)
    A('<div class="page"><article style="grid-column:1/-1;max-width:1180px;margin:0 auto;">')

    # ── 표지
    A('<div class="meta-row">'
      '<span class="badge">감리 산출물</span>'
      '<span class="badge outline">테이블정의서 · ERD</span>'
      f'<span style="color:var(--text-dim);font-size:12px;">·</span>'
      f'<span style="color:var(--text-dim);font-size:12px;">PostgreSQL 16 · '
      f'{len(tables)}테이블 · {total_cols}컬럼</span>'
      f'<span style="color:var(--text-dim);font-size:12px;">·</span>'
      f'<span style="color:var(--text-dim);font-size:12px;">{today} 생성</span></div>')
    A('<h1 class="title">테이블정의서 · ERD</h1>')
    A('<p class="lede">KOIPA AI 영업비밀 등급분류 시스템이 소유한 데이터베이스 개체 전체의 '
      '물리 정의와 관계도다. 등급체계·문서·라벨링·추론·학습·보정·합성·운영·검색 9개 그룹, '
      f'{len(tables)}개 테이블 {total_cols}개 컬럼을 다룬다.</p>')
    A(f'''<div class="srcbox">
<b>이 문서는 소스코드에서 자동 생성한다.</b> 손으로 쓰지 않는다.
스키마가 바뀌면 <code>python scripts/build_table_spec.py</code> 를 다시 돌린다.<br>
<b>기준 소스</b> — <code>poc/src/koipa/db/models.py</code>(ORM 19테이블) ·
<code>poc/alembic/versions/a1b2c3d4e5f6_pg_rag_vectorstore.py</code>(RAG 2테이블)<br>
<b>기준 커밋</b> — <code>{commit}</code> · <b>생성일</b> {today}<br>
컬럼의 이름·물리타입·NULL 허용·기본값·키·인덱스·제약은 모두 위 파일에서 읽은 값이고,
한국어 논리명과 설명만 <code>scripts/table_spec_meta.py</code> 에 사람이 적는다.
설명이 없는 컬럼은 생성 때 경고로 잡히므로 컬럼을 추가하고 문서를 빠뜨릴 수 없다.
</div>''')

    # ── 그룹·테이블 목록
    A('<hr class="hero-sep">')
    A('<section id="tables-index"><h2><span class="num">01</span> 테이블 목록</h2>')
    A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
      "<th>그룹</th><th>물리명</th><th>논리명</th><th>컬럼</th><th>기본키</th>"
      "<th>파티션</th><th>용도</th></tr></thead><tbody>")
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
              f'<td class="c-type">{("월별 RANGE(" + part + ")") if part else "—"}</td>'
              f"<td>{e(purpose)}</td></tr>")
    A("</tbody></table></div></section>")

    # ── ERD
    A('<section id="erd"><h2><span class="num">02</span> ERD 관계도</h2>')
    A('<p class="spec-note">박스 21개는 이 시스템이 소유한 테이블 전부다. 실선은 데이터베이스 '
      'FK 제약, 점선은 제약 없이 애플리케이션이 정합을 보증하는 참조다(파티션 테이블·RAG '
      '저장소는 FK 를 걸지 않는다). 진한 테두리는 대부분의 참조가 모이는 중심 테이블 '
      '<code>tb_documents</code> 다.</p>')
    A('<div style="overflow-x:auto;border:1px solid rgba(0,0,0,.12);padding:16px;'
      'margin:14px 0;background:#fafafa;">')
    A(erd)
    A("</div>")
    A('<p class="spec-note">관계 수 — FK 제약 '
      f'{len(fks)}개, 제약 없는 참조 3개. 전체 목록은 §04 참조.</p>')
    A("</section>")

    # ── 테이블별 정의
    A('<section id="tables"><h2><span class="num">03</span> 테이블별 정의</h2>')
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
              "<th>컬럼</th><th>물리 타입</th><th>NULL</th><th>키</th>"
              "<th>기본값</th><th>설명</th></tr></thead><tbody>")
            for c in t["cols"]:
                keys = []
                if c["name"] in t["pk"] or c["pk"]:
                    keys.append('<span class="k-pk">PK</span>')
                if c["fk"]:
                    keys.append('<span class="k-fk">FK</span>')
                if c["unique"]:
                    keys.append('<span class="k-uq">UQ</span>')
                d = col_desc(name, c)
                if c["fk"]:
                    tail = f' <span style="color:#71717a">→ {e(c["fk"])}'
                    tail += f' ON DELETE {e(c["ondelete"])}' if c["ondelete"] else ""
                    tail += "</span>"
                    d = (e(d) + tail) if d else tail
                else:
                    d = e(d)
                A(f'<tr><td class="c-name">{c["name"]}</td>'
                  f'<td class="c-type">{e(c["type"])}</td>'
                  f'<td class="c-nn">{"●" if c["notnull"] else ""}</td>'
                  f'<td class="c-key">{" ".join(keys)}</td>'
                  f'<td class="c-type">{_default_cell(c)}</td>'
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
                lines.append(f"<code>{e(_fmt_constraint(ix) if ix.startswith(chr(73)+chr(110)+chr(100)+chr(101)+chr(120)+chr(40)) else ix)}</code>")
            if part:
                lines.append(f"<b>PARTITION</b> <code>RANGE ({part})</code> — 월별 자식 "
                             "파티션으로 자동 라우팅. ORM 은 부모만 매핑한다.")
            if lines:
                A('<p class="spec-idx">' + "<br>".join(lines) + "</p>")
    A("</section>")

    # ── 관계 정의
    A('<section id="fk"><h2><span class="num">04</span> 관계 정의</h2>')
    A(f'<p class="spec-note">데이터베이스에 실제로 걸린 FK 제약 {len(fks)}건 전부다. '
      "소스는 ORM 선언이며 이 표는 코드에서 그대로 뽑았다.</p>")
    A('<div class="tbl-wrap"><table class="spec"><thead><tr>'
      "<th>자식 테이블</th><th>자식 컬럼</th><th>부모</th><th>ON DELETE</th>"
      "</tr></thead><tbody>")
    for ch, col, tgt, od in fks:
        A(f'<tr><td class="c-name">{ch}</td><td class="c-name">{col}</td>'
          f'<td class="c-name">{e(tgt)}</td><td class="c-type">{e(od)}</td></tr>')
    A("</tbody></table></div>")
    A('<p class="spec-note" style="margin-top:12px;"><b>제약 없는 참조 3건</b> — '
      "<code>tb_chunks.doc_id → tb_documents</code> · "
      "<code>tb_classification_evidence.chunk_id → tb_chunks</code> · "
      "<code>tb_rag_aliases.collection → tb_rag_vectors.collection</code>. "
      "앞의 둘은 상대가 RANGE 파티션 테이블이라 제약을 걸지 않고 애플리케이션이 정합을 "
      "보증한다. RAG 저장소는 Alembic SQL 로만 생성해 ORM 관계에 들어오지 않는다.</p>")
    A("</section>")

    # ── 범위 밖
    A('<section id="scope"><h2><span class="num">05</span> 범위</h2>')
    A('<p class="spec-note">이 정의서는 <b>본 시스템이 생성·소유하는 개체</b>만 다룬다. '
      "KL 원천 문서 저장소·EDMS·회원/권한·자가진단은 외부 시스템이며, 연동 키는 "
      "<code>tb_documents.external_ref</code> 와 <code>tb_documents.metadata</code> 로 다룬다. "
      "월별 자식 파티션(<code>tb_chunks_2026_07</code> 등)은 부모 정의를 그대로 상속하므로 "
      "개별 정의를 싣지 않는다.</p>")
    A("</section>")

    A("</article></div>")
    A('<footer><span>테이블정의서 · ERD — KOIPA AI 영업비밀 등급분류 시스템</span>'
      f"<span>기준 커밋 {commit} · {today} 생성</span></footer>")
    A("</body>\n</html>")
    return "\n".join(o), missing


# ──────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="파일을 쓰지 않고 검증만 한다")
    args = ap.parse_args()

    tables = parse_models() + parse_rag()
    for t in tables:
        t["partition"] = PARTITIONS.get(t["name"])

    unknown = [t["name"] for t in tables if t["name"] not in META.TABLES]
    stale = [n for n in META.TABLES if n not in {t["name"] for t in tables}]

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                            capture_output=True, text=True).stdout.strip() or "unknown"
    today = _dt.date.today().isoformat()
    doc, missing = render(tables, build_erd(tables), commit, today)

    total = sum(len(t["cols"]) for t in tables)
    print(f"테이블 {len(tables)} · 컬럼 {total}")
    ok = True
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
        OUT.write_text(doc, encoding="utf-8")
        print(f"  → {OUT.relative_to(ROOT)} ({len(doc):,} bytes)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
