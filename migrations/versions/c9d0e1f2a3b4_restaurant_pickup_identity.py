"""restaurants.address + pin_source: the pickup identity (#358)

The dropoff carried a precision layer (#343) while the pickup's identity
stayed operator-only: restaurants were seeded with raw coordinates, no
address, and nobody recorded WHO vouched for the point. Every point now
carries its provenance - imported / geocoded / owner_verified - and the
owner can set it with their own hands (PATCH /restaurants/{id}/pickup-point).
Nullable columns: pre-#358 rows read pin_source=NULL -> the drill treats
them as imported.

Revision ID: c9d0e1f2a3b4
Revises: b7c8d9e0f1a2
Create Date: 2026-11-28

"""

import sqlalchemy as sa
from alembic import op

revision = "c9d0e1f2a3b4"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("restaurants") as batch:
        batch.add_column(sa.Column("address", sa.String(300), nullable=True))
        batch.add_column(sa.Column("pin_source", sa.String(20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("restaurants") as batch:
        batch.drop_column("pin_source")
        batch.drop_column("address")
