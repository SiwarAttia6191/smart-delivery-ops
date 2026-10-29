"""tenancy foundation: tenants table + tenant_id on business tables (#35)

The tenancy dimension arrives the professional way — the sequence this
revision demonstrates is the reason the migration chain exists at all:

1. CREATE the ``tenants`` table,
2. INSERT the deployment's default tenant (deterministic id, referenced
   by the backfill),
3. ADD ``tenant_id`` as NULLABLE on the six business tables,
4. BACKFILL every existing row to the default tenant (the live demo DB's
   1,449+ orders included),
5. only then TIGHTEN to NOT NULL + foreign key, with indexes.

Never add the column NOT NULL in one step on a table that already has
rows - the backfill has to happen between. batch_alter_table keeps the
sequence legal on SQLite too (the parity test runs this revision there);
on Postgres it issues native ALTERs.

Revision ID: a1f2c3d4e5b6
Revises: 368dffbee1b8
Create Date: 2026-10-30

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a1f2c3d4e5b6"
down_revision: str | None = "368dffbee1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Business tables that carry the ownership dimension. Deliberately NOT
# here: route_cache / demand_forecasts (deployment-level aggregates),
# users (the user↔tenant binding arrives with #36's JWT claims), and the
# relay machinery (relay_state, dead letters — tenant-agnostic plumbing).
TENANT_TABLES = (
    "customers",
    "drivers",
    "restaurants",
    "orders",
    "order_events",
    "driver_events",
)

DEFAULT_SLUG = "default"


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("city", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tenants_slug"), "tenants", ["slug"], unique=True)
    op.execute(
        "INSERT INTO tenants (id, slug, name, created_at) "
        f"VALUES (1, '{DEFAULT_SLUG}', 'Default tenant', CURRENT_TIMESTAMP)"
    )

    for table in TENANT_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
    for table in TENANT_TABLES:
        op.execute(
            f"UPDATE {table} SET tenant_id = (SELECT id FROM tenants WHERE slug = '{DEFAULT_SLUG}')"
        )
    for table in TENANT_TABLES:
        with op.batch_alter_table(table) as batch:
            batch.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
            batch.create_foreign_key(
                f"fk_{table}_tenant_id_tenants", "tenants", ["tenant_id"], ["id"]
            )
        op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"], unique=False)


def downgrade() -> None:
    # Data-destroying by definition: dropping the ownership dimension.
    for table in reversed(TENANT_TABLES):
        op.drop_index(op.f(f"ix_{table}_tenant_id"), table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_tenant_id_tenants", type_="foreignkey")
            batch.drop_column("tenant_id")
    op.drop_index(op.f("ix_tenants_slug"), table_name="tenants")
    op.drop_table("tenants")
