"""hardening: deployment_state + incarnation token + drivers tz (m4/m5, #76)

Two Phase-3-review findings become schema:

1. ``deployment_state`` — deployment-level key/value state; seeded with the
   ``incarnation`` token (a random hex generated once per ops-database
   lifetime). The relay stamps it onto every outgoing wire message and the
   warehouse raw layer dedupes on (incarnation, event_id) — a
   wipe-and-rebuild of the ops database (fresh id sequences) can no longer
   silently dedupe its new events away against an older incarnation's rows
   (review finding m4).
2. ``drivers.created_at`` becomes timestamptz — the last naive timestamp in
   the schema; sweep correctness relied on server UTC (Postgres-only ALTER
   with an explicit AT TIME ZONE 'UTC' conversion, since the column always
   held UTC).

Revision ID: d9e8f7a6b5c4
Revises: c4f5a6b7d8e9
Create Date: 2026-10-31

"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9e8f7a6b5c4"
down_revision: str | None = "c4f5a6b7d8e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "deployment_state",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(length=128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    # Generated in Python so both dialects seed identically; the token is
    # deployment-unique, not environment-deterministic.
    op.execute(
        "INSERT INTO deployment_state (key, value, updated_at) "
        f"VALUES ('incarnation', '{uuid.uuid4().hex}', CURRENT_TIMESTAMP)"
    )

    if _is_postgres():
        # explicit UTC conversion: the naive column always held UTC (server
        # default), so this is a relabel, not a time shift
        op.execute(
            "ALTER TABLE drivers ALTER COLUMN created_at TYPE timestamptz "
            "USING created_at AT TIME ZONE 'UTC'"
        )
    else:
        with op.batch_alter_table("drivers") as batch:
            batch.alter_column(
                "created_at",
                existing_type=sa.DateTime(),
                type_=sa.DateTime(timezone=True),
            )


def downgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TABLE drivers ALTER COLUMN created_at TYPE timestamp")
    else:
        with op.batch_alter_table("drivers") as batch:
            batch.alter_column(
                "created_at",
                existing_type=sa.DateTime(timezone=True),
                type_=sa.DateTime(),
            )
    op.drop_table("deployment_state")
