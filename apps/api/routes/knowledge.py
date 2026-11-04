"""Per-tenant knowledge endpoints (#154): the customer version of the bridge.

A tenant uploads markdown documents; they are chunked, embedded and stored
as PRIVATE knowledge (tenant_id = the caller's tenant, RLS-scoped —
migration 009 is the ninth... tenth tenant policy) that only their
copilot retrieves, alongside the deployment-shared corpus (runbook,
decision records, tenant_id NULL). Re-uploading the same title REPLACES
that document's chunks — the same sync discipline as the vault bridge.

Reads: POST/GET hang off the standard posture (writer mutations, open
tenant-scoped GETs). The source string is namespaced per tenant
(`tenant/{tid}/{title}.md`) so collisions between tenants are impossible
by construction, not by convention.
"""

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.deps import get_db, require_writer
from apps.copilot.corpus import add_chunk
from apps.copilot.providers.embeddings import embed
from apps.copilot.retriever import chunk_document
from domain.models import KnowledgeChunk
from domain.services import tenant_service

router = APIRouter(prefix="/knowledge", tags=["copilot"])


class KnowledgeUpload(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=50_000, description="markdown document")


class KnowledgeDocument(BaseModel):
    source: str
    chunks: int


def _tenant_source(tenant_id: int, title: str) -> str:
    safe = title.strip().replace("/", "-").replace("\\", "-")
    return f"tenant/{tenant_id}/{safe}.md"


@router.post("/documents", response_model=KnowledgeDocument, status_code=status.HTTP_201_CREATED)
def upload_document(
    payload: KnowledgeUpload,
    db: Session = Depends(get_db),
    _writer: dict = Depends(require_writer),
) -> KnowledgeDocument:
    """Upload a markdown document as the caller tenant's PRIVATE knowledge
    (writer). Re-uploading a title replaces its previous chunks."""
    tid = tenant_service.context_tenant_id(db)
    source = _tenant_source(tid, payload.title)

    # replace-on-reupload: the sync discipline the vault bridge uses
    db.query(KnowledgeChunk).filter(KnowledgeChunk.source == source).delete()

    for idx, chunk in enumerate(chunk_document(payload.content)):
        add_chunk(db, source, idx, chunk, embed(chunk), tenant_id=tid)
    db.commit()

    count = db.query(KnowledgeChunk).filter(KnowledgeChunk.source == source).count()
    return KnowledgeDocument(source=source, chunks=count)


@router.get("/documents", response_model=list[KnowledgeDocument])
def list_documents(db: Session = Depends(get_db)) -> list[KnowledgeDocument]:
    """The caller's tenant's private documents, plus the deployment-shared
    corpus (anonymous callers see the default tenant's view)."""
    tid = tenant_service.context_tenant_id(db)
    rows = (
        db.query(KnowledgeChunk.source, KnowledgeChunk.chunk_index)
        .filter((KnowledgeChunk.tenant_id == tid) | (KnowledgeChunk.tenant_id.is_(None)))
        .all()
    )
    by_source: dict[str, int] = {}
    for source, _ in rows:
        by_source[source] = by_source.get(source, 0) + 1
    return [KnowledgeDocument(source=s, chunks=n) for s, n in sorted(by_source.items())]
