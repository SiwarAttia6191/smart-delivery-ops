"""Per-order route geometry cache (#58).

One row per distinct pickup→dropoff pair (coordinates rounded to 5 decimals,
~1 m — anything finer is GPS noise). The geometry is fetched from the
self-hosted OSRM container once per pair and reused by every order that
shares it: restaurants sit still, customers cluster, so the pair space is
small and the cache pays for itself immediately.

Served by GET /orders/{id}/route as GeoJSON; feeds the future tracking UI,
delivery proof, and free ground-truth pairs for the geo benchmark.
"""

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base

# 5 decimals ≈ 1.1 m: the coarsest rounding that still reads as "same door".
COORD_DECIMALS = 5


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RouteCache(Base):
    __tablename__ = "route_cache"

    pickup_lat: Mapped[float] = mapped_column(Float, primary_key=True)
    pickup_lon: Mapped[float] = mapped_column(Float, primary_key=True)
    dropoff_lat: Mapped[float] = mapped_column(Float, primary_key=True)
    dropoff_lon: Mapped[float] = mapped_column(Float, primary_key=True)

    # GeoJSON LineString positions: [[lon, lat], ...]
    coordinates: Mapped[list] = mapped_column(JSON)
    distance_m: Mapped[float] = mapped_column(Float)
    duration_s: Mapped[float] = mapped_column(Float)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RouteCache ({self.pickup_lat},{self.pickup_lon})->"
            f"({self.dropoff_lat},{self.dropoff_lon}) {self.distance_m:.0f}m>"
        )
