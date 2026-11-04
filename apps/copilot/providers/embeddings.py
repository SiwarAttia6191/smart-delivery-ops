"""The embedding provider (#120 RAG, #153): pluggable like the chat
provider. ``embed()`` dispatches by settings.EMBEDDINGS_PROVIDER:

- ``hash`` (default): the deterministic keyless embedder — same text,
  same 1536-dim vector, no network. The tests' anchor and the keyless
  clone posture.
- ``openai``: text-embedding-3-large (the chosen model) with Matryoshka
  reduction to the store's VECTOR(1536) column — no schema migration to
  adopt it.

Two hard rules: vectors from DIFFERENT models never mix (switching the
provider requires re-embedding the corpus — ``make docs-ingest --re-embed``
and the vault equivalent), and the two entry points differ in failure
posture: ``embed_strict`` (the INGEST path) fails loudly on a provider
error — half-embedded stores are worse than a red task — while ``embed``
(the per-request QUERY path) degrades to the hash vector with a warning,
because a degraded ranking beats a dead copilot.
"""

import hashlib
import logging

from core.config import get_settings

logger = logging.getLogger(__name__)

EMBEDDING_DIM = 1536


def _hash_vector(text: str) -> str:
    digest = hashlib.sha512(text.encode("utf-8")).digest()
    values: list[float] = []
    while len(values) < EMBEDDING_DIM:
        digest = hashlib.sha512(digest).digest()
        values.extend(b / 255.0 for b in digest)
    return "[" + ",".join(f"{v:.5f}" for v in values[:EMBEDDING_DIM]) + "]"


class HashEmbeddings:
    name = "hash"

    def embed(self, text: str) -> str:
        return _hash_vector(text)


class OpenAIEmbeddings:
    """text-embedding-3-large behind the same secret as the chat provider.
    The client is lazy: constructed on first use, never at import."""

    name = "openai"

    def __init__(self) -> None:
        self._client: object | None = None

    def _lazy_client(self):
        if self._client is None:
            settings = get_settings()
            key = settings.openai_api_key
            if not key or key == "xxx":
                raise RuntimeError("openai embeddings selected but no API key configured")
            from openai import OpenAI

            self._client = OpenAI(api_key=key)
        return self._client

    def embed(self, text: str) -> str:
        settings = get_settings()
        response = self._lazy_client().embeddings.create(
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
            input=text,
        )
        return "[" + ",".join(f"{v:.5f}" for v in response.data[0].embedding) + "]"


def _provider():
    return (
        OpenAIEmbeddings() if get_settings().embeddings_provider == "openai" else HashEmbeddings()
    )


def embed(text: str) -> str:
    """The QUERY path: degrade to the hash vector on provider failure —
    a degraded ranking must never turn into a dead copilot."""
    if get_settings().embeddings_provider != "openai":
        return _hash_vector(text)
    try:
        return OpenAIEmbeddings().embed(text)
    except Exception as exc:  # noqa: BLE001 - the degrade IS the contract here
        logger.warning("openai embeddings failed (%s); hash fallback for this query", exc)
        return _hash_vector(text)


def embed_strict(text: str) -> str:
    """The INGEST path: fail loudly — a half-embedded store is worse
    than a red ingest task."""
    return _provider().embed(text)
