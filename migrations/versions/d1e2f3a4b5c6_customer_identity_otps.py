"""customer_otps: the one-time codes behind customer phone login (#309)

The customers table carries the phone; this table carries the proof that
its holder answered a challenge. Codes are stored hashed, single-use,
and short-lived (the service layer owns the 10-minute TTL). Tenant-scoped
like every business row.

Revision ID: d1e2f3a4b5c6
Revises: ca2d3f4a5b6c
Create Date: 2026-11-19

"""

import sqlalchemy as sa
from alembic import op

revision = "d1e2f3a4b5c6"
down_revision = "ca2d3f4a5b6c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_otps",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Integer(),
            sa.ForeignKey("tenants.id", name="fk_customer_otps_tenant_id_tenants"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "customer_id",
            sa.Integer(),
            sa.ForeignKey("customers.id", name="fk_customer_otps_customer_id_customers"),
            nullable=False,
            index=True,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("customer_otps")
