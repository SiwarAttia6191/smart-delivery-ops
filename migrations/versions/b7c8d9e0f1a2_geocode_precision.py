"""geocode_cache precision verdicts (#343): the geocoder is guilty until proven innocent

Every first resolution is now verified against two independent witnesses
(Nominatim /reverse at the point + the OSRM /nearest road name from our
own router graph) before it is cached — the Valverde incident (a forward
geocode confidently landing on the PARALLEL street) can never again cache
itself as truth without a verdict attached. Nullable columns: pre-#343
rows read precision=NULL → surfaced as "unverified" until the city drill
(scripts/verify_city_addresses.py) re-runs them.

Revision ID: b7c8d9e0f1a2
Revises: e2f3a4b5c6d7
Create Date: 2026-11-21

"""

import sqlalchemy as sa
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("geocode_cache") as batch:
        batch.add_column(sa.Column("precision", sa.String(20), nullable=True))
        batch.add_column(sa.Column("reverse_display_name", sa.String(300), nullable=True))
        batch.add_column(sa.Column("snap_distance_m", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("geocode_cache") as batch:
        batch.drop_column("snap_distance_m")
        batch.drop_column("reverse_display_name")
        batch.drop_column("precision")
