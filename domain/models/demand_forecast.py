"""Demand forecasts served to the pricing engine.

Rows are written by the forecast batch job (ml/pipelines/forecast_demand.py,
one-shot ml-bench container) and read per quote by
domain/services/demand_service.py. Time policy matches the warehouse: hours
are stored in UTC; the job derives them from the Africa/Tunis business hours
it forecasts (fixed UTC+1, no DST).

The table lives in the OPS database, not the warehouse: it is a serving
artifact the API reads on the quote path, and the API has no warehouse
connection.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, PrimaryKeyConstraint
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DemandForecast(Base):
    __tablename__ = "demand_forecasts"
    __table_args__ = (PrimaryKeyConstraint("zone_lat", "zone_lon", "forecast_hour_utc"),)

    zone_lat: Mapped[float] = mapped_column(Float)
    zone_lon: Mapped[float] = mapped_column(Float)
    forecast_hour_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    predicted_orders: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<DemandForecast zone=({self.zone_lat}, {self.zone_lon}) "
            f"hour={self.forecast_hour_utc:%Y-%m-%d %H:%M}Z "
            f"predicted={self.predicted_orders}>"
        )
