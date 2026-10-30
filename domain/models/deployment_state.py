"""DeploymentState — deployment-level key/value state (m4, #76).

Currently exactly one row matters: ``incarnation`` — a random token
generated once per ops-database lifetime and stamped onto every event at
the relay. The warehouse's raw layer dedupes on (incarnation, event_id),
so a wipe-and-rebuild of the ops database (fresh id sequences) can never
silently dedupe its new events away against an older incarnation's rows.
Deliberately NOT a tenant table (deployment-level plumbing, like
relay_state) and deliberately migrations-owned (it must exist before the
first event is ever relayed).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DeploymentState(Base):
    __tablename__ = "deployment_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(128))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<DeploymentState {self.key}>"


#: The one key the platform uses today.
INCARNATION_KEY = "incarnation"
#: Wire/raw-layer fallback for events that predate the token (m4).
LEGACY_INCARNATION = "legacy"
