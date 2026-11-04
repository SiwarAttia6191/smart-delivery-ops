"""per-tenant knowledge: knowledge_chunks.tenant_id + RLS (#154)

The knowledge store joins the tenant tables: ``tenant_id`` NULL means
deployment-SHARED knowledge (the runbook, the decision records — every
tenant's copilot reads them), a set value is that tenant's PRIVATE
knowledge (uploaded through the portal/API). The policy therefore admits
shared rows to everyone and private rows only to their owner — the same
GUC pair core/tenancy.py pins per transaction.

Existing rows stay NULL (shared) — no backfill needed. SQLite (the parity
test) skips the RLS block; the explicit tenant filter in search_docs keeps
it correct there.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-04

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: str | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.add_column(sa.Column("tenant_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_knowledge_chunks_tenant_id_tenants", "tenants", ["tenant_id"], ["id"]
        )
    op.create_index("ix_knowledge_chunks_tenant_id", "knowledge_chunks", ["tenant_id"])

    if _is_postgres():
        op.execute("ALTER TABLE knowledge_chunks ENABLE ROW LEVEL SECURITY")
        # FORCE: the table owner is subject too — the app cannot
        # accidentally grant itself a bypass.
        op.execute("ALTER TABLE knowledge_chunks FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY knowledge_scoping ON knowledge_chunks
            USING (
                tenant_id IS NULL
                OR tenant_id = current_setting('app.current_tenant', true)::int
                OR current_setting('app.tenant_bypass', true) = '1'
            )
            WITH CHECK (
                tenant_id IS NULL
                OR tenant_id = current_setting('app.current_tenant', true)::int
                OR current_setting('app.tenant_bypass', true) = '1'
            )
            """
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("DROP POLICY IF EXISTS knowledge_scoping ON knowledge_chunks")
        op.execute("ALTER TABLE knowledge_chunks NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE knowledge_chunks DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_knowledge_chunks_tenant_id", table_name="knowledge_chunks")
    with op.batch_alter_table("knowledge_chunks") as batch:
        batch.drop_column("tenant_id")
