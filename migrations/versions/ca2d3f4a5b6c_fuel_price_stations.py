"""fuel_price_stations: the station-level price cache (#274 V2)

One row per station per fuel kind per country, refreshed by the fetch
script (replace-on-refresh per country, the vault-bridge pattern). A
TECHNICAL cache like geocode_cache: deliberately NOT tenant data and
NOT in the RLS set — the price of gasoline at a public station is a
city fact shared by every tenant.

The near-driver query reads this table with a bounding box + haversine;
the quote's fuel estimate upgrades to the nearest station's price when
the cache holds stations for the deployment's country, and falls back
to the manifest's official constant when it doesn't (the V1 truth).

Revision ID: ca2d3f4a5b6c
Revises: ca1d2e3f4a5b
Create Date: 2026-11-17

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "ca2d3f4a5b6c"
down_revision: str | None = "ca1d2e3f4a5b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fuel_price_stations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("station_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("fuel_kind", sa.String(length=20), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
    )
    op.create_index("ix_fuel_price_stations_country", "fuel_price_stations", ["country"])
    op.create_index(
        "ix_fuel_stations_country_kind", "fuel_price_stations", ["country", "fuel_kind"]
    )
    op.create_index("ix_fuel_stations_lat_lon", "fuel_price_stations", ["latitude", "longitude"])


def downgrade() -> None:
    op.drop_index("ix_fuel_stations_lat_lon", table_name="fuel_price_stations")
    op.drop_index("ix_fuel_stations_country_kind", table_name="fuel_price_stations")
    op.drop_table("fuel_price_stations")
