"""order priority: urgent deliveries bias dispatch (#276)

orders gains priority (normal | urgent). Urgent orders bias the
dispatch: the matching multiplies each driver's effective distance by a
per-vehicle SPEED FACTOR (motorcycle 0.85, car 1.0, bicycle 1.15), so
urgency trades a little efficiency for speed - an urgent order takes
the motorcycle slightly farther over the bicycle slightly closer.
Normal orders match unchanged.

Revision ID: ca1d2e3f4a5b
Revises: ba1c2d3e4f5a
Create Date: 2026-11-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca1d2e3f4a5b"
down_revision: str | None = "ba1c2d3e4f5a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "orders",
        sa.Column("priority", sa.String(length=10), nullable=False, server_default="normal"),
    )


def downgrade() -> None:
    op.drop_column("orders", "priority")
