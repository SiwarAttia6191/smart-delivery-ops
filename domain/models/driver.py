"""Driver entity: vehicle, live location, and availability."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    vehicle: Mapped[str] = mapped_column(String(20), default="motorcycle")
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Driver {self.id} {self.name!r} available={self.is_available}>"
