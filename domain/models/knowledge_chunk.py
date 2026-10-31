"""KnowledgeChunk — the RAG store (#120 Phase A, RAG retriever).

Deployment-level operational knowledge (runbook, incident summaries,
benchmark reports), chunked and embedded by scripts/ingest_docs.py and
served to the copilot through the search_docs tool. Deliberately NOT
tenant-scoped: the runbook is shared knowledge (incident summaries could
gain a tenant dimension when Phase C produces them per-tenant).

The embedding is stored as the pgvector TEXT literal ('[0.1,0.2,...]')
— the column is a real VECTOR(1536) on Postgres (migration 005 creates
the extension and the table) and plain TEXT on SQLite (tests), where the
search tool computes cosine in Python. Same rows, same tool, two
engines for the math.
"""

from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from domain.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(200), index=True)  # e.g. docs/runbook.md
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[str] = mapped_column(Text)  # the pgvector literal '[0.1,0.2,...]'
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<KnowledgeChunk {self.source}#{self.chunk_index}>"
