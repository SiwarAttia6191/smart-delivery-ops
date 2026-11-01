"""promotions: per-tenant coupon codes + RLS (#159 item 3)

The discounts mirror of the pricing engine. ``coupons`` is a TENANT
table: code uniqueness and every lookup are scoped by ``tenant_id`` (the
same code can run in two tenants with different values), so it joins the
RLS set like the six business tables in migration 003 — ENABLE + FORCE +
the ``tenant_isolation`` policy on the ``app.current_tenant`` /
``app.tenant_bypass`` GUCs that core/tenancy.py pins per transaction.

SQLite (the parity test) has no RLS and skips that block, same as 003;
the explicit tenant filters in promotion_service keep it correct there.

Revision ID: b2c3d4e5f6a7
Revises: f6a7b8c9d0e1
Create Date: 2026-11-01

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "coupons",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("code", sa.String(length=60), nullable=False),
        sa.Column("discount_kind", sa.String(length=10), nullable=False),
        sa.Column("discount_value", sa.Float(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("max_redemptions", sa.Integer(), nullable=True),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_id", "code", name="uq_coupons_tenant_code"),
    )
    op.create_index("ix_coupons_tenant_id", "coupons", ["tenant_id"])
    op.create_index("ix_coupons_code", "coupons", ["code"])

    if _is_postgres():
        op.execute("ALTER TABLE coupons ENABLE ROW LEVEL SECURITY")
        # FORCE: the table owner is subject too — the app cannot
        # accidentally grant itself a bypass.
        op.execute("ALTER TABLE coupons FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON coupons
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON coupons")
        op.execute("ALTER TABLE coupons NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE coupons DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_coupons_code", table_name="coupons")
    op.drop_index("ix_coupons_tenant_id", table_name="coupons")
    op.drop_table("coupons")
