"""Worker progress bookkeeping for the outbox relay.

A single row (id=1) holds the highest order_events.id already published to
Kafka. The row is only written after a successful producer flush, so a crash
between produce and offset update replays a small tail — consumers dedupe by
event_id (at-least-once delivery).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RelayState(Base):
    __tablename__ = "relay_state"

    id: Mapped[int] = mapped_column(primary_key=True)  # singleton: always 1
    last_relayed_event_id: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
