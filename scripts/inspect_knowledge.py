"""A window into the copilot's knowledge store (#120 RAG, Obsidian bridge).

Shows what the retriever can actually see — every knowledge_chunks row
grouped by source, with chunk counts and a content preview — so "what
does the copilot know?" has a one-command answer:

    python scripts/inspect_knowledge.py                 # everything
    python scripts/inspect_knowledge.py --prefix obsidian:   # your vault
    python scripts/inspect_knowledge.py --prefix docs:       # shipped docs
    make knowledge-list                                 # via the Makefile
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from core.database import engine  # noqa: E402


def inspect(prefix: str | None, session) -> None:
    sql = "SELECT source, COUNT(*) AS chunks, MIN(content) AS preview FROM knowledge_chunks"
    params: dict[str, str] = {}
    if prefix:
        sql += " WHERE source LIKE :pattern"
        params["pattern"] = f"{prefix}%"
    sql += " GROUP BY source ORDER BY source"
    rows = session.execute(text(sql), params).all()

    if not rows:
        print("the knowledge store is empty for this filter")
        return

    total = 0
    for source, chunks, preview in rows:
        total += chunks
        first_line = (preview or "").strip().splitlines()[0][:72]
        print(f"{source}  ({chunks} chunk{'s' if chunks != 1 else ''})")
        print(f"    ↳ {first_line}…")
    print(f"\n{len(rows)} source(s), {total} chunk(s) in the store")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prefix", default=None, help="filter by source prefix (obsidian:, docs:, ...)"
    )
    args = parser.parse_args()
    session = sessionmaker(bind=engine)()
    try:
        inspect(args.prefix, session)
    finally:
        session.close()
