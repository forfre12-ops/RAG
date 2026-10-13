"""소스에 적힌 표 이름이 실제 ORM·마이그레이션에 있는 표인지 지킨다.

왜(2026-09-26). 안 쓰는 표 4개와 칼럼 18개를 지울 때, ORM 밖의 원시 SQL 문자열 두 줄이 지워진 표·칼럼을
그대로 가리키고 있었다 — `POST /admin/demo/purge` 가 `DELETE FROM tad_lm_lrn_datst_mng ...` 와
`DELETE FROM tad_sm_syn_doc_mng WHERE doc_id ...` 를 실행하려 했다. 이 경로의 시험은 가짜 DB 로 SQL 문자열만
로그에 남겨서 초록이었고, 모델 클래스를 이름으로 찾는 정적 점검도 문자열 안의 표 이름은 보지 못한다.
그대로 나갔으면 데모 초기화가 운영에서 첫 실행에 실패했을 것이다.

그래서 소스(`src/koipa`)에 나오는 표준 표 이름(`tad_xx_..._mng`)을 전부 모아 ORM 이 아는 표와 대조한다.
DB 없이 돈다. 칼럼까지는 보지 못한다 — 칼럼은 실제 스키마에 실행해 보는 시험(test_demo_purge)이 맡는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from koipa.db import Base
from koipa.db import models  # noqa: F401 — 표 등록

_SRC = Path(__file__).resolve().parents[1] / "src" / "koipa"

# ORM 에 선언하지 않고 마이그레이션이 만드는 표(alembic/env.py 와 같은 목록).
_MIGRATION_ONLY_TABLES = {"tad_dm_doc_vctr_mng"}

# 파티션 자식(`..._mng_2026_05`)은 `_mng` 뒤에 글자가 더 붙어 이 식에 걸리지 않는다.
_TABLE = re.compile(r"\btad_[a-z]{2}_[a-z0-9_]+_mng\b")


def _references() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in sorted(_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for name in _TABLE.findall(line):
                found.setdefault(name, []).append(f"{path.relative_to(_SRC.parent).as_posix()}:{lineno}")
    return found


def test_every_table_named_in_the_source_exists():
    known = set(Base.metadata.tables) | _MIGRATION_ONLY_TABLES
    dangling = {name: where for name, where in _references().items() if name not in known}
    assert not dangling, (
        "소스가 없는 표를 가리킨다 — 지운 표의 원시 SQL 이 남았을 수 있다:\n"
        + "\n".join(f"  {name}  {', '.join(where[:3])}" for name, where in sorted(dangling.items()))
    )


def test_the_scan_sees_the_raw_sql_it_is_meant_to_guard():
    """검사가 비어서 통과하는 것을 막는다 — 데모 초기화의 원시 SQL 이 실제로 잡혀야 한다."""
    refs = _references()
    assert "tad_cm_clsf_rslt_mng" in refs and any("api/admin.py" in w for w in refs["tad_cm_clsf_rslt_mng"])
    assert len(refs) >= 5, sorted(refs)

