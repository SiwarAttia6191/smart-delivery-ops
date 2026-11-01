"""RAG + Phase B provider tests (#120, second PR): the knowledge corpus,
the retriever tool on both dialects, and the live provider's message
translation — all deterministic, no keys, no network."""

import json

import pytest

from apps.copilot.providers.base import ProviderReply
from apps.copilot.providers.embeddings import embed
from apps.copilot.retriever import search_docs
from apps.copilot.tools import build_registry, execute_tool

REGISTRY = build_registry()


class TestSourceScoping:
    def test_source_prefix_scopes_the_search(self, db):
        """The Obsidian bridge: vault notes land namespaced obsidian:...;
        a prefixed search only sees that family (the hash embedder's
        compressed scores make cross-corpus ranking weak — scoping is
        the honest fix until real embeddings land)."""
        from domain.models import KnowledgeChunk

        db.add(
            KnowledgeChunk(
                source="docs/runbook.md",
                chunk_index=0,
                content="restart the relay worker",
                embedding=embed("restart the relay worker"),
            )
        )
        db.add(
            KnowledgeChunk(
                source="obsidian:My notes.md",
                chunk_index=0,
                content="my scaling note: three workers",
                embedding=embed("my scaling note: three workers"),
            )
        )
        db.commit()

        scoped = search_docs(db, "workers", source_prefix="obsidian:")
        assert scoped and all(h["source"].startswith("obsidian:") for h in scoped)

        unscoped = search_docs(db, "workers", limit=10)
        assert {h["source"] for h in unscoped} >= {"docs/runbook.md", "obsidian:My notes.md"}


class TestVaultSync:
    def test_sync_replaces_changed_and_tombstones_deleted(self, db, tmp_path):
        """The production semantics: unchanged notes cost nothing, changed
        notes replace their stale chunks, and notes deleted from the vault
        are tombstoned out of the store instead of haunting answers."""
        from sqlalchemy import text

        from scripts.ingest_obsidian import ingest

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "a.md").write_text("# A\n\nalpha content", encoding="utf-8")
        (vault / "b.md").write_text("# B\n\nbeta content", encoding="utf-8")
        assert ingest(vault, session=db) == 2
        assert ingest(vault, session=db) == 0  # unchanged: zero work

        (vault / "a.md").write_text("# A\n\nalpha CHANGED", encoding="utf-8")
        (vault / "b.md").unlink()
        assert ingest(vault, session=db) == 1

        sources = {
            row
            for row in db.execute(
                text("SELECT DISTINCT source FROM knowledge_chunks WHERE source LIKE 'obsidian:%'")
            ).scalars()
        }
        assert sources == {"obsidian:a.md"}
        contents = [
            row
            for row in db.execute(
                text("SELECT content FROM knowledge_chunks WHERE source = 'obsidian:a.md'")
            ).scalars()
        ]
        assert any("CHANGED" in c for c in contents)


class TestEmbeddings:
    def test_deterministic_and_dimensioned(self):
        a, b = embed("how do I reset the relay?"), embed("how do I reset the relay?")
        assert a == b  # same text → same vector
        assert len(a.strip("[]").split(",")) == 1536

    def test_different_text_differs(self):
        assert embed("runbook") != embed("pricing")


class TestSearchDocs:
    def test_returns_relevant_chunks_with_sources(self, db):
        from apps.copilot.retriever import chunk_document

        chunks = chunk_document(
            "# Relay recovery\n\nIf the relay stalls, restart the worker and "
            "watch sdo_outbox_unpublished drain to zero before trusting supply counts."
        )
        from domain.models import KnowledgeChunk

        for idx, content in enumerate(chunks):
            db.add(
                KnowledgeChunk(
                    source="docs/test.md",
                    chunk_index=idx,
                    content=content,
                    embedding=embed(content),
                )
            )
        db.commit()

        hits = search_docs(db, "how do I recover a stalled relay?")
        assert hits, "the retriever returned nothing"
        assert hits[0]["source"] == "docs/test.md"
        assert "relay" in hits[0]["content"].lower()

    def test_registered_as_a_tool(self, db):
        raw = execute_tool(REGISTRY, db, "search_docs", {"query": "anything"})
        assert '"result"' in raw  # empty corpus on SQLite is fine; the contract holds


class TestChunker:
    def test_heading_aware_packing(self):
        from apps.copilot.retriever import chunk_document

        doc = "\n\n".join(f"# Section {i}\n\n" + "word " * 120 for i in range(4))
        chunks = chunk_document(doc)
        assert all(len(c) <= 1200 for c in chunks)
        assert chunks[0].startswith("# Section 0")


class TestOpenAIProvider:
    def test_placeholder_key_is_rejected_loudly(self):
        from apps.copilot.providers.openai_client import OpenAIProvider

        with pytest.raises(ValueError, match="placeholder"):
            OpenAIProvider(api_key="xxx")

    def test_message_translation(self):
        """Our dict shape → the SDK's, without a network call."""
        from apps.copilot.providers.openai_client import OpenAIProvider

        sdk = OpenAIProvider._to_sdk_messages(
            [
                {"role": "system", "content": "sys"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"name": "orders_recent", "args": {"limit": 5}}],
                },
                {"role": "tool", "name": "orders_recent", "content": json.dumps({"result": []})},
            ]
        )
        assert sdk[0] == {"role": "system", "content": "sys"}
        assert sdk[1]["tool_calls"][0]["function"]["name"] == "orders_recent"
        assert sdk[2]["role"] == "tool"

    def test_sdk_tools_translation(self):
        from apps.copilot.providers.openai_client import OpenAIProvider

        sdk = OpenAIProvider._to_sdk_tools(
            [{"name": "t1", "description": "d", "parameters": {"type": "object", "properties": {}}}]
        )
        assert sdk[0]["type"] == "function"
        assert sdk[0]["function"]["name"] == "t1"

    def test_reply_translation(self):
        """A mocked SDK response → ProviderReply with parsed tool args."""
        from apps.copilot.providers.openai_client import OpenAIProvider

        class _Completions:
            @staticmethod
            def create(**kwargs):
                class _Fn:
                    name = "orders_recent"
                    arguments = '{"limit": 5}'

                class _TC:
                    function = _Fn()

                class _Msg:
                    content = "checking"
                    tool_calls = [_TC()]

                class _Choice:
                    message = _Msg()

                class _Resp:
                    choices = [_Choice()]

                return _Resp()

        class _Chat:
            completions = _Completions()

        class _FakeClient:
            chat = _Chat()

        provider = OpenAIProvider.__new__(OpenAIProvider)  # skip __init__/key
        provider._client = _FakeClient()  # type: ignore[assignment]
        provider.model = "gpt-4o-mini"
        reply = provider.chat([], [])
        assert isinstance(reply, ProviderReply)
        assert reply.wants_tools
        assert reply.tool_calls[0].name == "orders_recent"
        assert reply.tool_calls[0].args == {"limit": 5}
