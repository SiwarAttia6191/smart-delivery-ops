"""Long-term memory service (#208): the tenant's remembered facts.

Storage is verbatim (no LLM extraction on the free tier — that is the
funded follow-up); retrieval is newest-first and capped. Scoping is
tenant-first like every service: the explicit filter keeps SQLite
correct, RLS (migration 011, the 13th tenant policy) re-enforces it.
"""

from sqlalchemy.orm import Session

from domain.models import Memory
from domain.services import tenant_service

MAX_INJECTED = 10


def remember(session: Session, fact: str, conversation_id: str | None = None) -> Memory:
    row = Memory(
        tenant_id=tenant_service.context_tenant_id(session),
        content=fact.strip()[:500],
        source_conversation_id=conversation_id,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def recent_facts(session: Session, limit: int = MAX_INJECTED) -> list[str]:
    tid = tenant_service.context_tenant_id(session)
    rows = (
        session.query(Memory)
        .filter(Memory.tenant_id == tid)
        .order_by(Memory.id.desc())
        .limit(limit)
        .all()
    )
    return [r.content for r in rows]
