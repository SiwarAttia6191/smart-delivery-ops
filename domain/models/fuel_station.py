"""FuelPriceStation — the station-level price cache (#274 V2).

One row per station per fuel kind per country, refreshed by the fetch
script (replace-on-refresh per country). A TECHNICAL cache like
geocode_cache: deliberately NOT tenant data and NOT in the RLS set —
the price of gasoline at a public station is a city fact shared by
every tenant. The near-driver query (bounding box + haversine) reads
this table; the manifest's official constant remains the fallback
wherever the cache holds no stations.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class FuelPriceStation(Base):
    __tablename__ = "fuel_price_stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country: Mapped[str] = mapped_column(String(2), index=True)
    station_id: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    fuel_kind: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    source: Mapped[str] = mapped_column(String(100))

    __table_args__ = (
        Index("ix_fuel_stations_country_kind", "country", "fuel_kind"),
        Index("ix_fuel_stations_lat_lon", "latitude", "longitude"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<FuelPriceStation {self.station_id} {self.fuel_kind} {self.price} {self.currency}>"
