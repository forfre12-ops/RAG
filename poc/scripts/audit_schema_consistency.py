#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""AI 솔루션 스키마의 명명·형식 일관성을 센다 — 감리 DB 영역 지적의 자체 점검.

왜 이 도구가 있는가(2026-09-10). 감리는 「데이터베이스 설계서(AI 솔루션) v1.0」을
테이블설계서 기반으로 정합성 점검해 컬럼명↔컬럼ID 불일치 · DataType 불일치 ·
NOT NULL 불일치(13건) 등을 지적했다(인쇄 71·72~79쪽). 그런데 그 설계서에는 이미 없어진
테이블(tb_rag_vectors · tb_rag_aliases)이 들어 있다 — 지적 건수를 그대로 받아 고칠 수
없다. 현행 코드에 같은 규칙을 적용해 다시 센다.

규칙(감리 도표 72~75 의 점검 축과 같다):
    R1  컬럼ID 기준 컬럼명 불일치   같은 컬럼ID 가 테이블마다 다른 논리명
    R2  컬럼명 기준 컬럼ID 불일치   같은 논리명이 테이블마다 다른 컬럼ID
    R3  컬럼ID 기준 DataType 불일치
    R4  컬럼ID 기준 NOT NULL 불일치
    R5  논리명 미기술 컬럼
    R6  정의서 밖 테이블 — 마이그레이션이 raw SQL 로 만들어 models.py 에 없는 테이블
    R7  정의서 ↔ 실제 DB NOT NULL 불일치 (--db-url 을 줄 때만) — 정의서 생성기는 models.py 글자를 읽으므로
        마이그레이션을 끝까지 올린 DB 의 information_schema 가 정답이다. [2026-09-11] 처음 대조하니
        6칼럼이 달랐다(표 단위 PK 를 NULL 허용으로 적는 등).

논리명 = koipa/db/standard_names.py 의 칼럼 논리명(표준용어집 용어). [2026-09-11] 표준 명명
       (migration 7b3e9d2a4f10) 전에는 scripts/table_spec_meta.py 설명의 첫 구분자 앞부분을 썼다
       — 그 사전은 옛 이름이 키라 새 이름으로는 찾지 못한다(찾지 못하면 전 칼럼이 R5 로 셈).
출처 = models.py(build_table_spec.parse_models 로 읽는다) + standard_names + alembic/versions.

⚠ 규칙 위반이 곧 결함은 아니다. 같은 이름이 뜻이 다른 경우(예: weight)는 이름을 바꿀지
  사람이 정한다. 이 도구는 세기만 한다.

사용:
    python scripts/audit_schema_consistency.py            # 요약
    python scripts/audit_schema_consistency.py --list     # 위반 상세
    python scripts/audit_schema_consistency.py --json out.json
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
_ROOT = _POC.parent
sys.path.insert(0, str(_ROOT / "scripts"))
sys.path.insert(0, str(_POC / "src"))

import build_table_spec as B  # noqa: E402
from koipa.db.standard_names import TABLES as STD_TABLES, logical_names  # noqa: E402

# 옛 표 이름 → 표준 표 이름. 7b3e9d2a4f10 은 이름을 f-문자열 반복문으로 바꿔 정규식으로 못 읽는다.
_RENAMED = {old: new for old, (new, _) in STD_TABLES.items()}

_SEP = re.compile(r"\s+—\s+|\.\s|\(|,")
_CREATE = re.compile(r"CREATE TABLE(?: IF NOT EXISTS)?\s+([a-z_][a-z0-9_]*)|op\.create_table\(\s*[\"']([a-z_][a-z0-9_]*)")
_DROP = re.compile(r"DROP TABLE(?: IF EXISTS)?\s+([a-z_][a-z0-9_]*)|op\.drop_table\(\s*[\"']([a-z_][a-z0-9_]*)")


def logical(desc: str) -> str:
    s = (desc or "").strip()
    if not s:
        return ""
    return _SEP.split(s, maxsplit=1)[0].strip().rstrip(".")


def _alembic_live_tables() -> tuple[set[str], set[str]]:
    """upgrade() 본문에서 만들고 지운 테이블. downgrade() 는 되돌리기라 뺀다.

    지움은 두 가지로 잡는다 — DROP TABLE / op.drop_table 문장, 그리고 테이블 이름을 튜플로
    모아 반복문으로 지우는 형태(테이블 삭제 문장 앞뒤 3줄 안의 'tb_…' 문자열). 첫 판은 뒤의
    형태를 못 봐서 이미 지운 tb_rag_vectors·tb_rag_aliases 를 살아 있다고 셌다(2026-09-10).
    ⚠ 순서를 따지지 않는 근사다 — 지웠다가 다시 만든 테이블은 '지움'으로 센다.
    """
    created: set[str] = set()
    dropped: set[str] = set()
    for f in sorted((_POC / "alembic" / "versions").glob("*.py")):
        src = f.read_text(encoding="utf-8", errors="replace")
        up = src.split("def downgrade", 1)[0]
        for m in _CREATE.finditer(up):
            created.add(m.group(1) or m.group(2))
        for m in _DROP.finditer(up):
            dropped.add(m.group(1) or m.group(2))
        lines = up.splitlines()
        for i, line in enumerate(lines):
            low = line.lower()
            if "drop table" in low or "drop_table" in low:
                window = "\n".join(lines[max(0, i - 3): i + 4])
                dropped.update(re.findall(r"[\"'](tb_[a-z0-9_]+)[\"']", window))
        # 이름을 파일 상단 상수(TABLES = [...])에 모아 두고 f-문자열로 지우는 형태.
        # 삭제 문장과 상수가 20줄 넘게 떨어져 있어 위 창으로는 못 잡는다(a3b4c5d6e7f8).
        # 상수 이름에 TABLE 이 든 것만 본다 — (표, 칼럼, 타입) 튜플 목록까지 지움으로 세지 않도록.
        if re.search(r"drop table[^\n]*\{", up, re.I):
            for m in re.finditer(r"^[A-Z_]*TABLE[A-Z_]*\s*=\s*[\[(](.*?)[\])]", up, re.M | re.S):
                dropped.update(re.findall(r"[\"'](tb_[a-z0-9_]+)[\"']", m.group(1)))
    return created, dropped


def audit() -> dict:
    tables = B.parse_models()
    ln = logical_names()
    by_id_names: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    by_name_ids: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    by_id_types: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    by_id_nn: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    missing: list[str] = []
    ncols = 0
    for t in tables:
        for c in t["cols"]:
            ncols += 1
            cid, where = c["name"], t["name"]
            name = ln.get(where, ("", {}))[1].get(cid, "")
            if not name:
                missing.append(f"{where}.{cid}")
            else:
                by_id_names[cid][name].append(where)
                by_name_ids[name][cid].append(where)
            by_id_types[cid][c["type"]].append(where)
            by_id_nn[cid]["NOT NULL" if c["notnull"] else "NULL 허용"].append(where)

    def _multi(d: dict) -> dict:
        return {k: {kk: sorted(vv) for kk, vv in v.items()} for k, v in d.items() if len(v) > 1}

    created, dropped = _alembic_live_tables()
    # [2026-09-11] 원시 SQL 로만 만든 표(문서 벡터)도 정의서가 싣는다(build_table_spec.parse_vector_tables).
    # models.py 만 보면 그 표를 늘 '정의서 밖'으로 센다.
    spec = {t["name"] for t in tables} | {t["name"] for t in B.parse_vector_tables()}
    part = re.compile(r"^(.+)_(?:\d{4}_\d{2}|default)$")

    def _now(n: str) -> str:
        """옛 이름을 표준 이름으로 — 파티션 자식은 부모 접두만 바꾼다(7b3e9d2a4f10 과 같은 규칙)."""
        if n in _RENAMED:
            return _RENAMED[n]
        m = part.match(n)
        if m and m.group(1) in _RENAMED:
            return _RENAMED[m.group(1)] + n[len(m.group(1)):]
        return n

    live = {_now(n) for n in (created - dropped)} - spec
    # 월별·기본 파티션 자식(tad_cm_chnk_mng_2026_05 · tad_am_adt_log_mng_default …)은 부모
    # 테이블의 일부다. 정의서는 부모를 적으므로 밖에 있는 테이블로 세지 않는다.
    partitions = sorted(n for n in live if (m := part.match(n)) and m.group(1) in spec)
    outside = sorted(live - set(partitions))
    r = {
        "denominator": {"tables": len(tables), "columns": ncols},
        "R1_id_to_names": _multi(by_id_names),
        "R2_name_to_ids": _multi(by_name_ids),
        "R3_id_to_types": _multi(by_id_types),
        "R4_id_to_notnull": _multi(by_id_nn),
        "R5_missing_name": missing,
        "R6_outside_spec": outside,
        "partitions": partitions,
        "dropped_tables": sorted(dropped),
    }
    return r


def db_nullability_mismatch(url: str) -> list[str]:
    """정의서(models.py 파서 + 벡터 표)의 NOT NULL 을 실제 DB(information_schema)와 칼럼마다 대조한다."""
    import psycopg  # noqa: PLC0415

    tables = B.parse_models() + B.parse_vector_tables()
    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        db = {(t, c): n == "NO" for t, c, n in conn.execute(
            "select table_name, column_name, is_nullable from information_schema.columns "
            "where table_schema = 'public'")}
    out: list[str] = []
    for t in tables:
        for c in t["cols"]:
            k = (t["name"], c["name"])
            if k not in db:
                out.append(f"{t['name']}.{c['name']}: DB 에 없음")
            elif db[k] != c["notnull"]:
                spec = "NOT NULL" if c["notnull"] else "NULL 허용"
                real = "NOT NULL" if db[k] else "NULL 허용"
                out.append(f"{t['name']}.{c['name']}: 정의서 {spec} · DB {real}")
    return out


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="AI 솔루션 스키마 일관성 계수")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--db-url", default="",
                    help="마이그레이션을 끝까지 올린 PostgreSQL — 주면 정의서 NOT NULL 을 실제 DB 와 대조한다(R7)")
    a = ap.parse_args(argv)
    r = audit()
    if a.db_url:
        r["R7_db_nullability"] = db_nullability_mismatch(a.db_url)
    d = r["denominator"]
    print(f"분모: 테이블 {d['tables']}개 · 컬럼 {d['columns']}개 (models.py)")
    labels = {
        "R1_id_to_names": "R1 컬럼ID 기준 컬럼명 불일치",
        "R2_name_to_ids": "R2 컬럼명 기준 컬럼ID 불일치",
        "R3_id_to_types": "R3 컬럼ID 기준 DataType 불일치",
        "R4_id_to_notnull": "R4 컬럼ID 기준 NOT NULL 불일치",
        "R5_missing_name": "R5 논리명 미기술",
        "R6_outside_spec": "R6 정의서 밖 테이블",
    }
    if "R7_db_nullability" in r:
        labels["R7_db_nullability"] = "R7 정의서↔실DB NOT NULL 불일치"
    for k, lab in labels.items():
        print(f"  {lab:28s} {len(r[k]):3d}건")
    if a.list:
        for k, lab in labels.items():
            if not r[k]:
                continue
            print(f"\n[{lab}]")
            if isinstance(r[k], dict):
                for key, vals in r[k].items():
                    print(f"  {key}")
                    for v, where in vals.items():
                        print(f"      {v!s:40s} ← {', '.join(where)}")
            else:
                for x in r[k]:
                    print(f"  {x}")
    if a.json:
        Path(a.json).write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
