"""tenant isolation: users binding + pricing overrides + RLS policies (#36)

Three coordinated moves that make the second tenant REAL:

1. ``users.tenant_id`` — every identity binds to exactly one tenant; the
   JWT's ``tid`` claim is issued FROM this column, so a token can never
   claim a tenant its user doesn't belong to. Same add→backfill→tighten
   sequence as revision 002 (existing demo users land on the default).
2. ``tenants.pricing_overrides`` — per-tenant fee/surge configuration
   (the enterprise-tier story): JSON, merged over the global Settings by
   tenant_service; NULL runs the deployment defaults.
3. Postgres Row-Level Security on the six tenant tables — the acceptance
   criterion that matters: isolation enforced by the DATABASE ENGINE, not
   by remembering WHERE clauses. Policies compare ``tenant_id`` against
   the ``app.current_tenant`` GUC (pinned per transaction by
   core/tenancy.py's session listener from the JWT claim) and accept the
   ``app.tenant_bypass`` GUC for the internal services that must see
   across tenants (relay, benchmarks). Postgres-only block: SQLite (the
   parity test) has no RLS, and the app metadata carries no policy state,
   so parity is unaffected.

Revision ID: c4f5a6b7d8e9
Revises: a1f2c3d4e5b6
Create Date: 2026-10-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f5a6b7d8e9"
down_revision: str | None = "a1f2c3d4e5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = (
    "customers",
    "drivers",
    "restaurants",
    "orders",
    "order_events",
    "driver_events",
)


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # ── 1. users bind to a tenant (add → backfill → tighten, like 002) ──
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
    op.execute("UPDATE users SET tenant_id = (SELECT id FROM tenants WHERE slug = 'default')")
    with op.batch_alter_table("users") as batch:
        batch.alter_column("tenant_id", existing_type=sa.Integer(), nullable=False)
        batch.create_foreign_key("fk_users_tenant_id_tenants", "tenants", ["tenant_id"], ["id"])
    op.create_index(op.f("ix_users_tenant_id"), "users", ["tenant_id"], unique=False)

    # ── 2. per-tenant pricing overrides ──
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("pricing_overrides", sa.JSON(), nullable=True))

    if _is_postgres():
        # Repair a latent bug revision 002 planted and only #36 could
        # expose: it INSERTed the default tenant with explicit id=1 but
        # never advanced tenants_id_seq, so the FIRST app-side insert
        # allocates nextval()=1 and dies on the primary key. (SQLite has
        # no sequences, which is why the parity test never saw it.)
        op.execute("SELECT setval('tenants_id_seq', GREATEST((SELECT MAX(id) FROM tenants), 1))")

    # ── 3. database-enforced isolation (Postgres RLS) ──
    if _is_postgres():
        for table in TENANT_TABLES:
            op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            # FORCE: even the table owner (the app role) is subject — the
            # app cannot accidentally grant itself a bypass.
            op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
            op.execute(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (
                    tenant_id = current_setting('app.current_tenant', true)::int
                    OR current_setting('app.tenant_bypass', true) = '1'
                )
                WITH CHECK (
                    tenant_id = current_setting('app.current_tenant', true)::int
                    OR current_setting('app.tenant_bypass', true) = '1'
                )
                """
            )


def downgrade() -> None:
    if _is_postgres():
        for table in TENANT_TABLES:
            op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
            op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("pricing_overrides")
    op.drop_index(op.f("ix_users_tenant_id"), table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_constraint("fk_users_tenant_id_tenants", type_="foreignkey")
        batch.drop_column("tenant_id")
