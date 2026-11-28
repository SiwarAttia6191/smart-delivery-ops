"""route_cache pickup/dropoff snap gaps: measured at capture (#371)

OSRM snaps input points to the nearest drivable road before routing, so
the stored geometry starts/ends on the ROAD, not at the requested door.
The gap between "where the customer/restaurant point is" and "where the
road reaches" is now a STORED FACT per route - measured at capture, the
ground-truth version of the drill's post-hoc estimate - and an absurd
gap (endpoints landing far from the requested points) is the capture
gate's second rejection signal: the mixed-generation-graph signature.
Nullable columns: pre-#371 rows read NULL.

Revision ID: d2e3f4a5b6c7
Revises: c9d0e1f2a3b4
Create Date: 2026-11-28

"""

import sqlalchemy as sa
from alembic import op

revision = "d2e3f4a5b6c7"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("route_cache") as batch:
        batch.add_column(sa.Column("pickup_snap_m", sa.Float(), nullable=True))
        batch.add_column(sa.Column("dropoff_snap_m", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("route_cache") as batch:
        batch.drop_column("dropoff_snap_m")
        batch.drop_column("pickup_snap_m")
