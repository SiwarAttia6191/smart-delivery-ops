"""Restaurant entity (pickup point)."""

from datetime import UTC, datetime

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Restaurant(Base):
    __tablename__ = "restaurants"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    # the pickup identity (#358): where the door actually is, and who vouched
    # for it - "imported" (onboarding data), "geocoded" (resolved from the
    # typed address) or "owner_verified" (the owner dragged the pin onto
    # their door: the person who knows the door is the final witness)
    address: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pin_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Restaurant {self.id} {self.name!r}>"
