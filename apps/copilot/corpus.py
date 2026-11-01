"""Shared corpus-writer for the knowledge store (#120 RAG, Obsidian bridge).

One INSERT path for every corpus source — the shipped docs and personal
vault notes alike — so the pgvector dialect branch (the explicit CAST the
ORM bind needs) lives in exactly one place.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


def add_chunk(session: Session, source: str, idx: int, content: str, embedding: str) -> None:
    """One knowledge row. On Postgres the column is a real VECTOR(1536) and
    pgvector registers no implicit cast from text — the explicit CAST (the
    same trick the search query uses) sends the literal as text and lets
    the extension parse it. On SQLite (tests) the TEXT column takes the
    plain ORM insert."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text(
                "INSERT INTO knowledge_chunks (source, chunk_index, content, embedding) "
                "VALUES (:source, :chunk_index, :content, CAST(:embedding AS vector))"
            ),
            {"source": source, "chunk_index": idx, "content": content, "embedding": embedding},
        )
    else:
        from domain.models import KnowledgeChunk

        session.add(
            KnowledgeChunk(source=source, chunk_index=idx, content=content, embedding=embedding)
        )
