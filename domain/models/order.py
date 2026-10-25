"""Order aggregate: lifecycle state, geo snapshot, pricing, and ETA."""

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from domain.models.base import Base


class OrderStatus(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    in_transit = "in_transit"
    delivered = "delivered"
    cancelled = "cancelled"


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return str(uuid.uuid4())


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    restaurant_id: Mapped[int] = mapped_column(ForeignKey("restaurants.id"), index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), nullable=True)

    status: Mapped[OrderStatus] = mapped_column(
        Enum(OrderStatus, native_enum=False, length=20),
        default=OrderStatus.pending,
        index=True,
    )

    # Geo snapshot at creation time (pickup = restaurant location then).
    pickup_latitude: Mapped[float] = mapped_column(Float)
    pickup_longitude: Mapped[float] = mapped_column(Float)
    dropoff_latitude: Mapped[float] = mapped_column(Float)
    dropoff_longitude: Mapped[float] = mapped_column(Float)

    # Computed at creation (Phase 1 rules; Phase 3 adds ML-driven pricing/ETA).
    distance_km: Mapped[float] = mapped_column(Float)
    delivery_fee: Mapped[float] = mapped_column(Float)
    surge_multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    eta_minutes: Mapped[int] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    customer = relationship("Customer", lazy="selectin")
    restaurant = relationship("Restaurant", lazy="selectin")
    driver = relationship("Driver", lazy="selectin")
    events = relationship(
        "OrderEvent", back_populates="order", lazy="selectin", order_by="OrderEvent.id"
    )

    @property
    def delivery_minutes(self) -> float | None:
        """Minutes between creation and delivery, for analytics."""
        if self.delivered_at is None:
            return None
        return round((self.delivered_at - self.created_at).total_seconds() / 60, 2)
