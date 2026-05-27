"""baseline: init.sql v2 — 19 parent tables (pgvector removed, ES-only).

Revision ID: 80d75521b95a
Revises:
Create Date: 2026-05-27 21:03:03.028623

This is a NO-OP baseline. The schema is created by poc/migrations/init.sql
which is mounted into postgres container at /docker-entrypoint-initdb.d/.

For an existing DB that already has the schema:
    alembic stamp 80d75521b95a

For a fresh DB without the schema (rare — normally docker-compose handles it):
    psql -f migrations/init.sql && alembic stamp 80d75521b95a

Future schema changes must be expressed as new alembic revisions on top of
this baseline.
"""
from typing import Sequence, Union


revision: str = "80d75521b95a"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op — schema is bootstrapped by migrations/init.sql."""


def downgrade() -> None:
    """No-op — initial baseline cannot be downgraded."""
