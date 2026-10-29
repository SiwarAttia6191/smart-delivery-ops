"""API user — the identity a JWT is issued to (Phase 5, #28).

Deliberately minimal: username, bcrypt hash, role. No email/profile
columns until something needs them. Roles come from core.security.ROLES;
the table is created by create_all on boot (a NEW table — adding columns
to EXISTING tables is Alembic territory, which is why the tenancy columns
wait for migrations).
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.username} ({self.role})>"
