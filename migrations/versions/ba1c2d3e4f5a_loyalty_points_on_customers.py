"""loyalty points: accrue per delivered order, redeem via the coupon seam (#217)

customers gains loyalty_points (1 point per currency unit of the fee,
ticked in the delivery transition). No RLS change — the column rides the
customers table whose tenant policy already exists. Redemption is a
points-funded coupon (discount_kind=points): the checkout path debits
the balance, and the balance can never go negative.

Revision ID: ba1c2d3e4f5a
Revises: b9d0e1f2a3b4
Create Date: 2026-11-13

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ba1c2d3e4f5a"
down_revision: str | None = "b9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("loyalty_points", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("customers", "loyalty_points")
