"""restaurant portal: menu_items table + RLS (#159 item 4)

``menu_items`` is a TENANT table (each restaurant's menu belongs to its
tenant), joining the RLS set as the NINTH table — ENABLE + FORCE + the
``tenant_isolation`` policy on the GUCs core/tenancy.py pins per
transaction. SQLite (the parity test) has no RLS and skips the block;
the explicit tenant filters in the menu routes keep it correct there.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-03

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "menu_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("restaurant_id", sa.Integer(), sa.ForeignKey("restaurants.id"), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("is_available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_menu_items_tenant_id", "menu_items", ["tenant_id"])
    op.create_index("ix_menu_items_restaurant_id", "menu_items", ["restaurant_id"])

    if _is_postgres():
        op.execute("ALTER TABLE menu_items ENABLE ROW LEVEL SECURITY")
        # FORCE: the table owner is subject too — the app cannot
        # accidentally grant itself a bypass.
        op.execute("ALTER TABLE menu_items FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON menu_items
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON menu_items")
        op.execute("ALTER TABLE menu_items NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE menu_items DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_menu_items_restaurant_id", table_name="menu_items")
    op.drop_index("ix_menu_items_tenant_id", table_name="menu_items")
    op.drop_table("menu_items")
