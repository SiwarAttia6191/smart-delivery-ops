"""GeocodeCache — address text → coordinates, cached forever (#221 follow-up).

One row per normalized address (the sha256 of the lowercased text — the
key, so "Calle Gran Vía 42" and "calle gran vía  42" hit the same row).
A TECHNICAL cache like route_cache, deliberately NOT tenant data and NOT
in the RLS set: the coordinates of an address are city facts shared by
every tenant. Populated by the Nominatim geocoder on first miss; the
city_update path clears it (see the service docstring).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GeocodeCache(Base):
    __tablename__ = "geocode_cache"

    address_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    address: Mapped[str] = mapped_column(String(300))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    display_name: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GeocodeCache {self.address!r} -> ({self.lat},{self.lon})>"
