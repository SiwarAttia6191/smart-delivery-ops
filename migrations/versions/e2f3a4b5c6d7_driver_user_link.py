"""users.driver_id: the fleet-member binding behind driver tokens (#311)

A role="driver" user IS a fleet member: the JWT's did claim comes from
this column, so a driver token can only ever act on orders assigned to
their own driver row. Nullable — staff users ride without one.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-11-20

"""

import sqlalchemy as sa
from alembic import op

revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # batch mode: SQLite cannot ALTER-add constraints — the batch
    # recreates the table with the FK (the tenant-binding precedent,
    # c4f5a6b7d8e9)
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("driver_id", sa.Integer(), nullable=True))
        batch.create_index("ix_users_driver_id", ["driver_id"])
        batch.create_foreign_key("fk_users_driver_id_drivers", "drivers", ["driver_id"], ["id"])


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_column("driver_id")
