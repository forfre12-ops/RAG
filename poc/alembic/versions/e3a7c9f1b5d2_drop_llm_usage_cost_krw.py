"""LLM 사용량 표의 원화 비용 칼럼 kcur_cst 를 뺀다 — 값을 넣는 코드가 없고 원화 환산 설정도 요건도 없다.

Revision ID: e3a7c9f1b5d2
Revises: a1d4c7e9b302
Create Date: 2026-09-27

왜. `tad_lm_llm_usqty_mng.kcur_cst`(ORM `LlmUsage.cost_krw`)는 만든 뒤 한 번도 채워진 적이 없다.

    저장소   `LlmUsageRepo.record(cost_krw=None)` 이 받기만 하고, 부르는 곳(`record_llm_usage`)이 넘기지 않는다.
    설정     원화 환산율(환율)이 어디에도 없다.
    요건     RTM 에 원화 비용 요건이 없다.

그래서 이 칼럼은 늘 NULL 이었고, 그것을 합산하는 뷰 `v_monthly_llm_cost.total_cost_krw` 도 늘 NULL 이었다.
달러 비용(`usd_cst`)은 그대로다. 같은 커밋에서 ORM·표준 명명 표·저장소 인자를 함께 걷었다.

뷰. `v_monthly_llm_cost` 는 이 칼럼을 합산하므로 칼럼보다 먼저 지워야 한다(PostgreSQL 은 뷰가 붙든 칼럼을 못 지운다 —
`CASCADE` 를 붙이지 않는다). 칼럼을 지운 뒤 `total_cost_krw` 만 뺀 같은 정의로 다시 만든다. 나머지 열·이름·순서는 그대로다.
이 뷰를 읽는 코드는 저장소에 없다(원시 SQL 로 운영자가 조회하는 용도) — 운영자 쿼리가 `total_cost_krw` 를 골라 쓰고 있었다면 그 열이 없어진다.

안전장치. **값이 든 행이 하나라도 있으면 판이 멈춘다.** 코드 경로상 채워질 수 없지만 운영·고객사 DB 는 이 저장소에서 볼 수 없어
실측하지 못했다 — 누군가 손으로 채웠다면 조용히 지우지 않는다(PostgreSQL 은 DDL 도 트랜잭션이라 통째로 되돌아간다).
이미 지운 DB 에서 다시 돌아도 죽지 않는다.

되돌리기. 칼럼을 빈 채로(NULL) 다시 만들고 뷰를 `total_cost_krw` 포함 정의로 되돌린다.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e3a7c9f1b5d2"
down_revision = "a1d4c7e9b302"
branch_labels = None
depends_on = None

TABLE = "tad_lm_llm_usqty_mng"
COLUMN = "kcur_cst"
VIEW = "v_monthly_llm_cost"

# 지운 것의 목록 — b7d3f5a19c24 와 같은 형식이다. tests/test_standard_names.py 가 이 목록으로 "지운 칼럼이 정본·ORM 에 남지 않았는지"와
# "7b3e9d2a4f10 사본에서 빼고 대조할 것"을 판단한다. 표는 지우지 않는다.
TABLES: list[str] = []
COLUMNS: list[tuple[str, str, str]] = [("tad_lm_llm_usqty_mng", "kcur_cst", "NUMERIC(12,2)")]

# 뷰 정의 — 표준 명명(7b3e9d2a4f10) 뒤 현행 판(pg_get_viewdef 로 읽은 것). 열 이름은 영문 그대로 유지한다.
_VIEW_SELECT_HEAD = """
    SELECT date_trunc('month'::text, clot_dt) AS month,
           bllng_se_cd AS billing_phase,
           offr_id AS provider,
           mdl_nm AS model,
           clot_prps AS purpose,
           count(*) AS call_count,
           sum(inpt_tkn_cnt) AS total_input_tokens,
           sum(otpt_tkn_cnt) AS total_output_tokens,
           sum(usd_cst) AS total_cost_usd,
"""
_VIEW_KRW = "           sum(kcur_cst) AS total_cost_krw,\n"
_VIEW_SELECT_TAIL = """
           avg(rspns_dly_hr)::integer AS avg_latency_ms,
           count(*) FILTER (WHERE NOT scs_yn) AS error_count
      FROM tad_lm_llm_usqty_mng
     GROUP BY (date_trunc('month'::text, clot_dt)), bllng_se_cd, offr_id, mdl_nm, clot_prps
"""


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "select 1 from information_schema.columns "
                "where table_schema = 'public' and table_name = :t and column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def _create_view(with_krw: bool) -> None:
    body = _VIEW_SELECT_HEAD + (_VIEW_KRW if with_krw else "") + _VIEW_SELECT_TAIL.lstrip("\n")
    op.execute(sa.text(f"CREATE VIEW {VIEW} AS {body}"))


def upgrade() -> None:
    conn = op.get_bind()
    if not _has_column(conn, TABLE, COLUMN):
        # 이미 지운 DB — 뷰만 현행 정의인지 보장한다.
        op.execute(sa.text(f"DROP VIEW IF EXISTS {VIEW}"))
        _create_view(with_krw=False)
        return
    filled = conn.execute(sa.text(f"SELECT count(*) FROM {TABLE} WHERE {COLUMN} IS NOT NULL")).scalar()
    if filled:
        raise RuntimeError(
            f"{TABLE}.{COLUMN} 에 값이 든 행이 {filled}개 있다 — 이 칼럼을 채우는 코드는 없으므로 손으로 넣은 값이다. "
            "지우기 전에 그 값을 어디에 둘지 정한 뒤 이 판을 다시 돌릴 것."
        )
    # 뷰부터 — 칼럼을 붙들고 있어 CASCADE 없이는 칼럼을 못 지운다.
    op.execute(sa.text(f"DROP VIEW IF EXISTS {VIEW}"))
    op.execute(sa.text(f"ALTER TABLE {TABLE} DROP COLUMN {COLUMN}"))
    _create_view(with_krw=False)


def downgrade() -> None:
    """빈 채로 되돌린다 — 지운 것은 늘 NULL 이었으므로 잃은 값은 없다."""
    conn = op.get_bind()
    op.execute(sa.text(f"DROP VIEW IF EXISTS {VIEW}"))
    if not _has_column(conn, TABLE, COLUMN):
        op.execute(sa.text(f"ALTER TABLE {TABLE} ADD COLUMN {COLUMN} NUMERIC(12,2)"))
    _create_view(with_krw=True)
