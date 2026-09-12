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
    POST_BASE_RENAMES,
    TABLES,
    logical_names,
)

_MIG = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "7b3e9d2a4f10_standard_naming.py"

# ORM 에 선언하지 않는 것 — 마이그레이션이 raw SQL 로 만든다(alembic/env.py 와 같은 목록).
_MIGRATION_ONLY_TABLES = {"tad_dm_doc_vctr_mng"}
_GENERATED_COLUMNS = {("tad_lm_llm_usqty_mng", "whol_tkn_cnt")}

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


def _load_migration():
    spec = importlib.util.spec_from_file_location("mig_7b3e9d2a4f10", _MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_orm_tables_are_exactly_the_standard_tables():
    orm = set(Base.metadata.tables)
    std = {new for new, _ in TABLES.values()} - _MIGRATION_ONLY_TABLES
    assert orm == std, f"ORM 에만 {orm - std} · 대응표에만 {std - orm}"


def test_orm_columns_match_standard_columns():
    """표마다 ORM 의 DB 칼럼명 집합 == 대응표의 표준 칼럼 집합(DB 생성 칼럼은 ORM 밖)."""
    for old, cols in COLUMNS.items():
        new_table = TABLES[old][0]
        if new_table in _MIGRATION_ONLY_TABLES:
            continue
        orm = {c.name for c in Base.metadata.tables[new_table].columns}
        std = {new for _, new, _ in cols if (new_table, new) not in _GENERATED_COLUMNS}
        assert orm == std, f"{new_table}: ORM 에만 {orm - std} · 대응표에만 {std - orm}"


def test_migration_copy_matches_standard_names():
    """마이그레이션은 앱 코드를 import 하지 않고 사본을 든다 — 사본이 정본과 같아야 한다.

    ⚠ 7b3e9d2a4f10 은 **이미 서버에서 돈 판**이라 고치지 않는다. 그 뒤에 다시 바꾼 이름은
      뒤 마이그레이션이 처리하고 `POST_BASE_RENAMES` 에 남는다. 그래서 대조 전에 그
      나중 이름을 되돌려 **그 판이 만든 이름**으로 맞춘다.
    """
    mig = _load_migration()
    assert mig.TABLES == {old: new for old, (new, _) in TABLES.items()}
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
    assert mig.COLUMNS == {k: v for k, v in changed.items() if v}


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


def test_logical_names_cover_every_table():
    ln = logical_names()
    assert set(ln) == {new for new, _ in TABLES.values()}
    assert sum(len(cols) for _, cols in ln.values()) == sum(len(c) for c in COLUMNS.values())
