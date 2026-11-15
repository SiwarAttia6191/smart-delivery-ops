"""The LangGraph Studio entry point: a zero-arg factory exposing the
copilot's graph for visualization (#visualize — the user's LangSmith
Studio research).

The Studio UI renders the supervisor's nodes and edges (agent → tools →
budget/token stops), lets you chat with the agent, and highlights nodes
as they execute. The session is bound ONCE for the Studio process's
lifetime: the tool nodes execute against the REAL database with the
default tenant (the demo data), which is exactly what makes the Studio
chat a working copilot instead of a diagram.

The provider chain respects COPILOT_PROVIDER like every other rung —
with the default .env the flagship is the live OpenAI model; a
placeholder key degrades to the deterministic scripted replay, so the
visualization never blocks on credentials.

Run: `langgraph dev` (reads langgraph.json at the repo root) →
https://smith.langchain.com/studio/?baseUrl=http://127.0.0.1:2024
"""

from apps.copilot.graph import bind_session, build_graph
from apps.copilot.providers.base import ProviderReply
from apps.copilot.providers.factory import build_provider_chain
from apps.copilot.providers.scripted import ScriptedProvider
from apps.copilot.tools import build_registry
from core.database import SessionLocal


def _studio_provider():
    """The same chain every rung uses — with a deterministic fallback: a
    placeholder OpenAI key must not block the visualization."""
    try:
        return build_provider_chain()[0]
    except Exception as exc:  # noqa: BLE001 - the studio never blocks on credentials
        print(f"[studio] provider chain unavailable ({exc}) — the scripted replay serves")
        return ScriptedProvider(
            [
                ProviderReply(
                    content=(
                        "Studio chat runs the deterministic replay — the graph "
                        "visualization is the point. Set a real COPILOT_PROVIDER key "
                        "for live-model answers."
                    )
                )
            ]
        )


def make_graph():
    provider = _studio_provider()
    registry = build_registry()
    session = SessionLocal()
    bind_session(session)
    return build_graph(provider, registry, checkpointer=None)


graph = make_graph()
