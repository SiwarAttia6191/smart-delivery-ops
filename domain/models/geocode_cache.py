"""GeocodeCache — address text → coordinates, cached forever (#221 follow-up).

One row per normalized address (the sha256 of the lowercased text — the
key, so "Calle Gran Vía 42" and "calle gran vía  42" hit the same row).
A TECHNICAL cache like route_cache, deliberately NOT tenant data and NOT
in the RLS set: the coordinates of an address are city facts shared by
every tenant. Populated by the Nominatim geocoder on first miss; the
city_update path clears it (see the service docstring).

#343: rows carry the geocoder's self-verification VERDICT — precision
(ok / number_mismatch / street_mismatch / off_network / unverified),
the snap distance from the road network (OSRM /nearest) and the reverse
geocode's own display name. A wrong point can still be cached (the
witnesses advise, the customer decides) but it can never again cache
itself as unquestioned truth: every surface that shows the point shows
the verdict too. Pre-#343 rows read precision=NULL → "unverified".
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
    # #343 verification verdict (see the class docstring) — NULL = pre-#343 row
    precision: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reverse_display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    snap_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<GeocodeCache {self.address!r} -> ({self.lat},{self.lon})>"
