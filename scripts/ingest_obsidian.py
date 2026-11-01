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
from typing import Any

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


def ingest(vault: Path, reset: bool = False, session: Any = None) -> int:
    """Sync a vault into the knowledge store. `session` is injectable (the
    tests pass their fixture's session); the CLI opens its own on the
    configured engine."""
    if not vault.exists():
        raise SystemExit(f"vault not found: {vault} — create it in Obsidian or pass --vault")
    own_session = session is None
    if own_session:
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

        # Sync, not append (the production gap the first cut had): notes
        # DELETED from the vault must not haunt the store, and CHANGED
        # notes must replace their stale chunks. Tombstone everything the
        # vault no longer has; per note, replace when any chunk differs.
        stored_sources = set(
            session.execute(
                text("SELECT DISTINCT source FROM knowledge_chunks WHERE source LIKE :pattern"),
                {"pattern": f"{SOURCE_PREFIX}:%"},
            ).scalars()
        )
        current_sources = {f"{SOURCE_PREFIX}:{n.relative_to(vault).as_posix()}" for n in notes}
        tombstoned = sorted(stored_sources - current_sources)
        for source in tombstoned:
            session.execute(
                text("DELETE FROM knowledge_chunks WHERE source = :source"),
                {"source": source},
            )
        if tombstoned:
            print(f"  removed {len(tombstoned)} deleted note(s): {', '.join(tombstoned)}")

        if not notes:
            session.commit()
            print(f"no markdown notes in {vault} — vault synced (tombstones applied)")
            return 0

        total = 0
        for note in notes:
            rel = note.relative_to(vault).as_posix()
            source = f"{SOURCE_PREFIX}:{rel}"
            chunks = chunk_document(note.read_text(encoding="utf-8"))
            stored = session.execute(
                text(
                    "SELECT chunk_index, content FROM knowledge_chunks "
                    "WHERE source = :source ORDER BY chunk_index"
                ),
                {"source": source},
            ).all()
            if [(idx, content) for idx, content in stored] == list(enumerate(chunks)):
                continue  # unchanged note — zero work
            session.execute(
                text("DELETE FROM knowledge_chunks WHERE source = :source"),
                {"source": source},
            )
            for idx, chunk in enumerate(chunks):
                add_chunk(session, source, idx, chunk, embed(chunk))
                total += 1
            action = "re-ingested" if stored else "ingested"
            session.commit()
            print(f"  {rel}: {len(chunks)} chunks ({action})")
        session.commit()
        print(
            f"synced {total} chunks across {len(notes)} notes "
            f"({len(tombstoned)} tombstoned) (vault: {vault})"
        )
        return total
    finally:
        if own_session:
            session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault", default=str(DEFAULT_VAULT), help="path to the Obsidian vault")
    parser.add_argument(
        "--reset", action="store_true", help="clear previously ingested vault notes first"
    )
    args = parser.parse_args()
    ingest(Path(args.vault), reset=args.reset)
