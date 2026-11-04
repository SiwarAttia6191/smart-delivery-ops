"""long-term memory: the memories table, RLS-scoped (#208)

The 13th tenant policy — a tenant's remembered facts are theirs alone.
SQLite (the parity test) skips the RLS block; the explicit tenant filter
in the memory tool keeps it correct there.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e2
Create Date: 2026-11-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "f6a7b8c9d0e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("content", sa.String(length=500), nullable=False),
        sa.Column("source_conversation_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_memories_tenant_id", "memories", ["tenant_id"])

    if _is_postgres():
        op.execute("ALTER TABLE memories ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE memories FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON memories
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
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON memories")
        op.execute("ALTER TABLE memories NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE memories DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_memories_tenant_id", table_name="memories")
    op.drop_table("memories")
