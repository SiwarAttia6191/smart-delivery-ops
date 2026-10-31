"""copilot knowledge: pgvector + knowledge_chunks (#120 Phase A, RAG)

The RAG retriever's store. Postgres gets the real thing: the `vector`
extension and a VECTOR(1536) column searched with the `<=>`
cosine-distance operator by the search_docs tool. SQLite (the parity
test) creates the same table with a TEXT embedding — the tool computes
cosine in Python there.

The corpus is deployment-level (the runbook is shared knowledge), so
this table is deliberately NOT tenant-scoped and NOT in the RLS set.

Revision ID: f6a7b8c9d0e1
Revises: d9e8f7a6b5c4
Create Date: 2026-10-31

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "d9e8f7a6b5c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgres():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            """
            CREATE TABLE knowledge_chunks (
                id SERIAL PRIMARY KEY,
                source VARCHAR(200) NOT NULL,
                chunk_index INTEGER NOT NULL DEFAULT 0,
                content TEXT NOT NULL,
                embedding vector(1536) NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source"])
    else:
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(length=200), nullable=False),
            sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("embedding", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_knowledge_chunks_source", "knowledge_chunks", ["source"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_source", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    if _is_postgres():
        op.execute("DROP EXTENSION IF EXISTS vector")
