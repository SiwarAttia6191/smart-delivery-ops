"""order reviews: the reviews table, RLS-scoped (#216)

The 14th tenant policy — a restaurant's reviews are its alone. The
order_id UNIQUE constraint makes the one-review-per-order rule a
DATABASE rule, not an application convention. SQLite (the parity test)
skips the RLS block; the explicit tenant filter in the review service
keeps it correct there.

Revision ID: b9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-11-08

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d0e1f2a3b4"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    op.create_table(
        "reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.String(length=36), sa.ForeignKey("orders.id"), nullable=False),
        sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("comment", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_reviews_tenant_id", "reviews", ["tenant_id"])
    op.create_index("ix_reviews_order_id", "reviews", ["order_id"], unique=True)

    if _is_postgres():
        op.execute("ALTER TABLE reviews ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE reviews FORCE ROW LEVEL SECURITY")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON reviews
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
        op.execute(
            "ALTER TABLE reviews ADD CONSTRAINT ck_reviews_rating CHECK (rating BETWEEN 1 AND 5)"
        )


def downgrade() -> None:
    if _is_postgres():
        op.execute("ALTER TABLE reviews DROP CONSTRAINT IF EXISTS ck_reviews_rating")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON reviews")
        op.execute("ALTER TABLE reviews NO FORCE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE reviews DISABLE ROW LEVEL SECURITY")
    op.drop_index("ix_reviews_order_id", table_name="reviews")
    op.drop_index("ix_reviews_tenant_id", table_name="reviews")
    op.drop_table("reviews")
