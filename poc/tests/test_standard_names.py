"""표준 명명 대응표(db/standard_names.py)가 ORM·마이그레이션과 어긋나지 않는지 지킨다.

왜 있는가(2026-09-11). 표·칼럼 물리명을 KOIPA 표준용어집 이름으로 바꿨다(7b3e9d2a4f10).
대응표는 세 곳에 있다 — 정본(standard_names) · ORM(models.py 의 mapped_column 첫 인자) ·
마이그레이션 안의 사본. 셋 중 하나만 고치면 설계서·ERD 생성기가 실제 DB 와 다른 이름을
적거나, 새 DB 와 옛 DB 의 이름이 갈린다. 여기서 셋을 대조한다. DB 없이 돈다.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

from koipa.db import Base
from koipa.db import models  # noqa: F401 — 표 등록
from koipa.db.standard_names import (
    COLUMNS,
    POST_BASE_COLUMNS,
    POST_BASE_RENAMES,
    POST_BASE_TABLES,
    TABLES,
    logical_names,
)

_VERSIONS = Path(__file__).resolve().parents[1] / "alembic" / "versions"
_MIG = _VERSIONS / "7b3e9d2a4f10_standard_naming.py"
# 표준 명명 뒤에 안 쓰는 표·칼럼을 지운 판(2026-09-26). 무엇을 지웠는지의 정본은 이 판이다.
_DROP_MIG = _VERSIONS / "b7d3f5a19c24_drop_unused_tables_and_columns.py"

# ORM 에 선언하지 않는 것 — 마이그레이션이 raw SQL 로 만든다(alembic/env.py 와 같은 목록).
_MIGRATION_ONLY_TABLES = {"tad_dm_doc_vctr_mng"}

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_migration():
    return _load(_MIG, "mig_7b3e9d2a4f10")


def test_orm_tables_are_exactly_the_standard_tables():
    orm = set(Base.metadata.tables)
    std = ({new for new, _ in TABLES.values()} | set(POST_BASE_TABLES)) - _MIGRATION_ONLY_TABLES
    assert orm == std, f"ORM 에만 {orm - std} · 대응표에만 {std - orm}"


def test_orm_columns_match_standard_columns():
    """표마다 ORM 의 DB 칼럼명 집합 == 대응표의 표준 칼럼 집합."""
    for old, cols in COLUMNS.items():
        new_table = TABLES[old][0]
        if new_table in _MIGRATION_ONLY_TABLES:
            continue
        orm = {c.name for c in Base.metadata.tables[new_table].columns}
        std = {new for _, new, _ in cols}
        assert orm == std, f"{new_table}: ORM 에만 {orm - std} · 대응표에만 {std - orm}"
    for table, cols in POST_BASE_COLUMNS.items():          # 마이그레이션 사본 뒤에 새로 만든 표
        orm = {c.name for c in Base.metadata.tables[table].columns}
        std = {name for name, _ in cols}
        assert orm == std, f"{table}: ORM 에만 {orm - std} · 대응표에만 {std - orm}"


def test_migration_copy_matches_standard_names():
    """마이그레이션은 앱 코드를 import 하지 않고 사본을 든다 — 사본이 정본과 같아야 한다.

    ⚠ 7b3e9d2a4f10 은 **이미 서버에서 돈 판**이라 고치지 않는다. 그 뒤에 다시 바꾼 이름은
      뒤 마이그레이션이 처리하고 `POST_BASE_RENAMES` 에 남는다. 그래서 대조 전에 그
      나중 이름을 되돌려 **그 판이 만든 이름**으로 맞춘다. 그 뒤에 **지운** 표·칼럼은
      사본에 남아 있다 — 지운 판(b7d3f5a19c24)의 목록으로 사본에서 빼고 대조한다.
    """
    mig = _load_migration()
    drop = _load(_DROP_MIG, "mig_b7d3f5a19c24")
    dropped_tables = set(drop.TABLES)
    dropped_cols = {(table, column) for table, column, _ in drop.COLUMNS}
    assert dropped_tables <= set(mig.TABLES.values()), "지운 표가 표준 명명 사본에 없다"
    assert {old: new for old, new in mig.TABLES.items() if new not in dropped_tables} == {
        old: new for old, (new, _) in TABLES.items()
    }
    later = {
        (table, now): made_by_base
        for table, rows in POST_BASE_RENAMES.items()
        for made_by_base, now, _rev, _why in rows
    }
    changed = {
        old: tuple((a, later.get((old, b), b)) for a, b, _ in cols
                   if a != later.get((old, b), b))
        for old, cols in COLUMNS.items()
    }
    surviving = {}
    for old, pairs in mig.COLUMNS.items():
        table = mig.TABLES[old]
        if table in dropped_tables:
            continue
        kept = tuple((a, b) for a, b in pairs if (table, b) not in dropped_cols)
        if kept:
            surviving[old] = kept
    assert surviving == {k: v for k, v in changed.items() if v}


def test_dropped_tables_and_columns_are_gone_from_orm_and_standard_names():
    """b7d3f5a19c24 가 지운 것이 ORM 에도 정본 대응표에도 남아 있지 않다.

    지웠다고 하면서 한쪽에 선언이 남으면 정의서·ERD 가 실DB 에 없는 이름을 적는다.
    """
    drop = _load(_DROP_MIG, "mig_b7d3f5a19c24")
    std_tables = {new for new, _ in TABLES.values()}
    std_cols = {(TABLES[old][0], new) for old, cols in COLUMNS.items() for _, new, _ in cols}
    orm_cols = {(name, c.name) for name, table in Base.metadata.tables.items() for c in table.columns}
    for table in drop.TABLES:
        assert table not in std_tables, f"{table}: 대응표에 남아 있다"
        assert table not in Base.metadata.tables, f"{table}: ORM 에 남아 있다"
    for table, column, _definition in drop.COLUMNS:
        assert (table, column) not in std_cols, f"{table}.{column}: 대응표에 남아 있다"
        assert (table, column) not in orm_cols, f"{table}.{column}: ORM 에 남아 있다"


def test_post_base_renames_have_a_migration_and_a_reason():
    """나중에 바꾼 이름은 **어느 마이그레이션이 바꿨는지와 왜 바꿨는지**가 같이 있어야 한다.

    이 기록이 없으면 다음 사람이 정본과 서버 DB 가 왜 다른지 알 수 없다.
    """
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    revisions = {p.name.split("_", 1)[0] for p in versions.glob("*.py")}
    for table, rows in POST_BASE_RENAMES.items():
        for base_name, now_name, rev, why in rows:
            assert base_name != now_name, f"{table}: 바뀐 것이 없다"
            assert rev in revisions, f"{table}.{now_name}: 마이그레이션 {rev} 이 없다"
            assert len(why) > 20, f"{table}.{now_name}: 사유가 너무 짧다"
            std = {new for _, new, _ in COLUMNS[table]}
            assert now_name in std, f"{table}: 정본이 {now_name} 을 쓰지 않는다"


def test_post_base_tables_are_separate_from_the_migration_copy():
    """사후 추가 표는 7b3e9d2a4f10 사본과 대조하지 않는다(그 판은 고치지 않는다) — 기준판 표와 겹치면 안 된다.

    새 표는 `POST_BASE_TABLES` 에 두고 자기 마이그레이션 id 를 적는다. 그 마이그레이션이 실제로 그 표를 만드는지 본다.
    """
    base = {new for new, _ in TABLES.values()}
    assert not (set(POST_BASE_TABLES) & base), "사후 추가 표가 기준판 표와 같은 이름이다"
    assert set(POST_BASE_COLUMNS) == set(POST_BASE_TABLES), "표마다 칼럼 대응이 있어야 한다"
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    for table, (ko, rev) in POST_BASE_TABLES.items():
        assert ko, f"{table} 논리명 없음"
        files = list(versions.glob(f"{rev}_*.py"))
        assert len(files) == 1, f"{table}: 마이그레이션 {rev} 이 없거나 둘 이상이다"
        src = files[0].read_text(encoding="utf-8")
        assert table in src, f"{table}: 마이그레이션 {rev} 이 이 표를 만들지 않는다"
        for col, _ko in POST_BASE_COLUMNS[table]:
            assert f'"{col}"' in src, f"{table}.{col}: 마이그레이션 {rev} 에 없는 칼럼이다"


def test_names_are_valid_lowercase_identifiers_and_unique_per_table():
    for old, (new, ko) in TABLES.items():
        assert _NAME.match(new), new
        assert new.startswith("tad_") and new.endswith("_mng"), new
        assert ko, f"{new} 논리명 없음"
    for old, cols in COLUMNS.items():
        news = [new for _, new, _ in cols]
        assert len(news) == len(set(news)), f"{old}: 표준 칼럼명 중복"
        for _, new, ko in cols:
            assert _NAME.match(new), f"{old}.{new}"
            assert ko, f"{old}.{new} 논리명 없음"
    for table in POST_BASE_TABLES:
        assert _NAME.match(table) and table.startswith("tad_") and table.endswith("_mng"), table
        names = [n for n, _ in POST_BASE_COLUMNS[table]]
        assert len(names) == len(set(names)), f"{table}: 표준 칼럼명 중복"
        for n, ko in POST_BASE_COLUMNS[table]:
            assert _NAME.match(n) and ko, f"{table}.{n}"


def test_logical_names_cover_every_table():
    ln = logical_names()
    assert set(ln) == {new for new, _ in TABLES.values()} | set(POST_BASE_TABLES)
    assert sum(len(cols) for _, cols in ln.values()) == (
        sum(len(c) for c in COLUMNS.values()) + sum(len(c) for c in POST_BASE_COLUMNS.values()))
