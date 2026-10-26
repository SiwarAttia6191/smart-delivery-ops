"""Dead letters for driver events that the outbox relay cannot deliver."""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DriverRelayDeadLetter(Base):
    __tablename__ = "driver_relay_dead_letters"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[int] = mapped_column(index=True)
    driver_id: Mapped[int] = mapped_column(index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    error: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
