"""The Obsidian bridge (#AI backlog: personal knowledge as RAG corpus).

Points the copilot's retriever at an Obsidian vault: every markdown note
becomes searchable knowledge, so questions like "what did I note about
the drift watch?" come back answered FROM YOUR OWN NOTES, with the note
path cited. Same store, same chunker, same embedder as the platform
corpus — your notes just join it (sources are namespaced `obsidian:...`
so they never collide with the shipped docs).

Obsidian conventions respected: `.obsidian/` (app config) and `.trash/`
are skipped, `#tags` and `[[wikilinks]]` ride along as plain text (the
embedder sees them; a link-graph walk is future work). Private by
default: the vault never leaves the machine — the notes land in the
LOCAL pgvector store, and the default path is created fresh so an
existing vault is never touched without you naming it.

Usage (host venv, stack up):
    python scripts/ingest_obsidian.py                        # default vault
    python scripts/ingest_obsidian.py --vault "C:/path/MyVault"
    python scripts/ingest_obsidian.py --reset                # clear + re-ingest

In Docker (vault mounted):
    docker compose run --rm --no-deps \
      -v "C:/path/MyVault:/vault:ro" api python scripts/ingest_obsidian.py --vault /vault
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from apps.copilot.corpus import add_chunk  # noqa: E402
from apps.copilot.providers.embeddings import embed  # noqa: E402
from apps.copilot.retriever import chunk_document  # noqa: E402
from core.database import engine  # noqa: E402

DEFAULT_VAULT = Path.home() / "Documents" / "SmartDeliveryVault"
SKIP_DIRS = {".obsidian", ".trash", ".git"}
SOURCE_PREFIX = "obsidian"


def ingest(vault: Path, reset: bool = False) -> int:
    if not vault.exists():
        raise SystemExit(f"vault not found: {vault} — create it in Obsidian or pass --vault")
    session = sessionmaker(bind=engine)()
    try:
        if reset:
            from domain.models import KnowledgeChunk

            deleted = (
                session.query(KnowledgeChunk)
                .filter(KnowledgeChunk.source.like(f"{SOURCE_PREFIX}:%"))
                .delete(synchronize_session=False)
            )
            session.commit()
            print(f"cleared {deleted} previously ingested vault chunks")

        notes = sorted(
            path
            for path in vault.rglob("*.md")
            if not any(part in SKIP_DIRS for part in path.parts)
        )
        if not notes:
            print(f"no markdown notes in {vault} — nothing to ingest")
            return 0

        total = 0
        for note in notes:
            rel = note.relative_to(vault).as_posix()
            source = f"{SOURCE_PREFIX}:{rel}"
            chunks = chunk_document(note.read_text(encoding="utf-8"))
            new_chunks = 0
            for idx, chunk in enumerate(chunks):
                exists = session.execute(
                    text(
                        "SELECT 1 FROM knowledge_chunks "
                        "WHERE source = :source AND chunk_index = :idx LIMIT 1"
                    ),
                    {"source": source, "idx": idx},
                ).first()
                if exists is None:
                    add_chunk(session, source, idx, chunk, embed(chunk))
                    new_chunks += 1
                    total += 1
            session.commit()
            print(f"  {rel}: {len(chunks)} chunks ({new_chunks} new)")
        print(f"ingested {total} new chunks from {len(notes)} notes (vault: {vault})")
        return total
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="path to the Obsidian vault")
    parser.add_argument(
        "--reset", action="store_true", help="clear previously ingested vault notes first"
    )
    args = parser.parse_args()
    ingest(Path(args.vault), reset=args.reset)
