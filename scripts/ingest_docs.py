"""Copilot RAG: the knowledge corpus ingester (#120 Phase A, second PR).

Chunks the operational documents (runbook, decision records, benchmark
reports) into knowledge_chunks with EMBEDDINGS. The embedding provider is
pluggable like the chat provider: the DEFAULT is a deterministic
hash-based embedder (same text -> same 1536-dim vector, no key, no
network — the CI/test path and a real anchor while the corpus is small);
Phase B swaps in real embeddings behind the same interface, and the
ingest re-runs to re-embed.

Usage (repo root, stack up):
    python scripts/ingest_docs.py            # ingest the default corpus
    python scripts/ingest_docs.py --reset    # clear + re-ingest
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from apps.copilot.corpus import add_chunk  # noqa: E402
from apps.copilot.providers.embeddings import embed  # noqa: E402
from apps.copilot.retriever import chunk_document  # noqa: E402
from core.database import engine  # noqa: E402
from domain.models import KnowledgeChunk  # noqa: E402

CHUNK_CHARS = 900
CORPUS = [
    "docs/runbook.md",
    *sorted(str(p).replace("\\", "/") for p in Path("docs/decisions").glob("*.md")),
]


def ingest(reset: bool = False) -> int:
    session = sessionmaker(bind=engine)()
    try:
        if reset:
            session.query(KnowledgeChunk).delete()
            session.commit()
            print("knowledge_chunks cleared")

        total = 0
        for doc_path in CORPUS:
            doc = Path(doc_path)
            if not doc.exists():
                print(f"  {doc_path}: missing, skipped")
                continue
            chunks = chunk_document(doc.read_text(encoding="utf-8"))
            for idx, chunk in enumerate(chunks):
                exists = (
                    session.query(KnowledgeChunk)
                    .filter_by(source=doc_path, chunk_index=idx)
                    .one_or_none()
                )
                if exists is None:
                    add_chunk(session, doc_path, idx, chunk, embed(chunk))
                    total += 1
            session.commit()
            print(f"  {doc_path}: {len(chunks)} chunks")
        print(f"ingested {total} new chunks (corpus: {len(CORPUS)} documents)")
        return total
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reset", action="store_true", help="clear knowledge_chunks first")
    args = parser.parse_args()
    ingest(reset=args.reset)
