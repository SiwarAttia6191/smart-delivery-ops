"""payments: the checkout seam — sandbox charges + RLS (#159 item 2)

``payments`` is a TENANT table (a tenant's receipts are theirs alone), so
it joins the RLS set like the business tables in migrations 003/006 —
ENABLE + FORCE + the ``tenant_isolation`` policy on the GUCs that
core/tenancy.py pins per transaction. SQLite (the parity test) has no RLS
and skips that block; the explicit tenant filters in payment_service keep
it correct there.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False, server_default="paid"),
        sa.Column("provider", sa.String(length=20), nullable=False, server_default="sandbox"),
        sa.Column("provider_ref", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_payments_tenant_id", "payments", ["tenant_id"])
    op.create_index("ix_payments_order_id", "payments", ["order_id"])

    if _is_postgres():
        op.execute("ALTER TABLE payments ENABLE ROW LEVEL SECURITY")
        # FORCE: the table owner is subject too — the app cannot
        # accidentally grant itself a bypass.
        op.execute("ALTER TABLE payments FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON payments
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON payments")
        op.execute("ALTER TABLE payments NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE payments DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_index("ix_payments_tenant_id", table_name="payments")
    op.drop_table("payments")
