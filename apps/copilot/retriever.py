"""The RAG retriever tool (#120 Phase A, second PR): search_docs over the
knowledge corpus, plus the shared chunker.

One tool, two engines for the math: on Postgres the pgvector `<=>`
cosine-distance operator does the ranking server-side; on SQLite (the
tests) the chunks are loaded and cosine runs in Python. Same rows, same
tool contract, and the embeddings are the deterministic hash vectors
until Phase B's real embeddings land.
"""

import json
import math
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from apps.copilot.providers.embeddings import embed

NL = chr(10)
NN = NL + NL


def _parse_vector(literal: str) -> list[float]:
    return [float(v) for v in literal.strip("[]").split(",") if v]


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def chunk_document(text: str) -> list[str]:
    """Heading-aware chunking: split on markdown headings first, then
    pack sections to a budget so a chunk stays a coherent topic. Shared
    by the ingest script and the tests."""
    sections: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.startswith("#") and current:
            sections.append(NL.join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append(NL.join(current).strip())

    chunks: list[str] = []
    buffer = ""
    for section in sections:
        if len(buffer) + len(section) <= 900 or not buffer:
            buffer = (buffer + NN + section).strip()
        else:
            chunks.append(buffer)
            buffer = section
    if buffer:
        chunks.append(buffer)
    return [c for c in chunks if c]


def search_docs(
    db: Session, query: str, limit: int = 3, source_prefix: str | None = None
) -> list[dict[str, Any]]:
    """The retriever: the question's nearest chunks from the knowledge
    corpus, with their sources so the answer can cite them. NOTE: no
    score threshold — the deterministic hash embeddings rank consistently
    but not semantically; real relevance arrives with Phase B's embedding
    model, and the tool contract is "the nearest chunks".

    `source_prefix` scopes the search to a corpus family — "obsidian:"
    for the operator's own vault notes, "docs:" for the shipped docs —
    because with the hash embedder the scores compress to a narrow band
    and cross-corpus ranking is weak; scoping is the honest fix until
    real embeddings land."""
    from domain.models import KnowledgeChunk

    limit = max(1, min(int(limit), 10))
    query_embedding = embed(query)

    if db.get_bind().dialect.name == "postgresql":
        sql = (
            "SELECT source, chunk_index, content, "
            "1 - (embedding <=> CAST(:q AS vector)) AS score "
            "FROM knowledge_chunks"
        )
        params: dict[str, Any] = {"q": query_embedding, "limit": limit}
        if source_prefix:
            sql += " WHERE source LIKE :prefix"
            params["prefix"] = f"{source_prefix}%"
        sql += " ORDER BY embedding <=> CAST(:q AS vector) LIMIT :limit"
        rows = db.execute(text(sql), params).mappings().all()
        hits = [dict(r) for r in rows]
    else:
        chunks = db.query(KnowledgeChunk).all()
        if source_prefix:
            chunks = [c for c in chunks if c.source.startswith(source_prefix)]
        query_vec = _parse_vector(query_embedding)
        scored = sorted(
            ((c, _cosine(_parse_vector(c.embedding), query_vec)) for c in chunks),
            key=lambda pair: pair[1],
            reverse=True,
        )[:limit]
        hits = [
            {
                "source": c.source,
                "chunk_index": c.chunk_index,
                "content": c.content,
                "score": round(s, 4),
            }
            for c, s in scored
        ]

    return [
        {
            "source": h["source"],
            "chunk": h["chunk_index"],
            "content": h["content"][:600],
            "score": round(float(h["score"]), 4),
        }
        for h in hits
    ]


def search_docs_tool(db: Session, query: str) -> str:
    """The tool wrapper: JSON in the message."""
    return json.dumps({"results": search_docs(db, query)}, default=str)
