"""Memory — a durable fact the copilot remembers across conversations (#208).

The fourth memory layer: thread-scoped checkpoints (short-term), the
buffer window (recent turns), the RAG corpus (documents) — and this: the
tenant's long-term facts, stored verbatim when the operator says
"remember that ..." and injected into every future answer for that
tenant. LLM-based fact extraction is the funded follow-up; verbatim
storage is the honest free-tier version.

Tenant-scoped like every business row (RLS, migration 011 — the 13th
tenant policy): tenant A's memories are invisible to tenant B by the
engine, not by convention.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"), index=True)
    content: Mapped[str] = mapped_column(String(500))
    source_conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Memory {self.id} tenant={self.tenant_id} {self.content!r}>"
