"""geocode_cache: the geocoder's address cache (#221 follow-up)

A TECHNICAL cache like route_cache — deliberately NOT tenant data and
NOT in the RLS set: the coordinates of an address are city facts shared
by every tenant. One row per normalized address, populated on first
geocode miss, cleared by the city-update path.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-11-05

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "geocode_cache",
        sa.Column("address_key", sa.String(length=64), primary_key=True),
        sa.Column("address", sa.String(length=300), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("geocode_cache")
