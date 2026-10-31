"""The embedding provider (#120 Phase A, RAG): pluggable like the chat
provider. The DEFAULT is a deterministic hash-based embedder — the same
text always produces the same 1536-dim vector, with no key and no
network, which is what the tool contract and the tests need until Phase
B's real embeddings land behind this same function.
"""

import hashlib

EMBEDDING_DIM = 1536


def embed(text: str) -> str:
    """Text in, the pgvector TEXT literal out ('[0.1,0.2,...]'). The hash
    embedder derives the vector from the text's sha512 stream: consistent
    per text, uniformly spread, and free. Phase B swaps in the real
    embedding model (the ingest script re-runs to re-embed)."""
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < EMBEDDING_DIM:
        digest = hashlib.sha512(digest).digest()
        values.extend(b / 255.0 for b in digest)
    return "[" + ",".join(f"{v:.5f}" for v in values[:EMBEDDING_DIM]) + "]"
