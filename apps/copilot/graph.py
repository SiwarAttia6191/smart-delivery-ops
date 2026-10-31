"""The LangGraph supervisor (#120 Phase A): the agent's control flow as a
graph, not a while-loop.

    START → agent ──(wants tools, budget left)──> tools ──> agent
                  ├─(wants tools, budget spent)──> budget_stop ──> END
                  └─(final answer)───────────────> END

The state is plain dicts (our own message shape) — the provider adapter
translates to/from SDK formats in Phase B, so the graph is provider-
agnostic by construction. Two hard limits live here: the tool-call
budget (graceful degradation with the data collected so far, never a
crash) and LangGraph's recursion limit as the backstop behind it.
"""

import logging
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from apps.copilot.guardrails import MAX_TOOL_CALLS
from apps.copilot.providers.base import Provider
from apps.copilot.tools import ToolSpec, execute_tool, tool_schemas
from apps.copilot.tracing import trace_run

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "system.md").read_text(encoding="utf-8")
PROMPT_VERSION = "v1"  # bump when prompts/system.md changes


def _reduce_messages(
    left: list[dict[str, Any]], right: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return left + right


class CopilotState(TypedDict):
    messages: Annotated[list[dict[str, Any]], _reduce_messages]
    tool_calls_used: int
    answer: str | None
    pending_tool_calls: list[dict[str, Any]]


def build_graph(provider: Provider, registry: dict[str, ToolSpec]):
    schemas = tool_schemas(registry)

    def agent_node(state: CopilotState) -> dict[str, Any]:
        reply = provider.chat(state["messages"], schemas)
        update: dict[str, Any] = {"messages": [reply.model_dump()]}
        if reply.wants_tools:
            update["pending_tool_calls"] = [tc.model_dump() for tc in reply.tool_calls]
        else:
            update["answer"] = reply.content
        return update

    def tools_node(state: CopilotState) -> dict[str, Any]:
        executed: list[dict[str, Any]] = []
        for call in state["pending_tool_calls"]:
            result = execute_tool(registry, _current_session(), call["name"], call.get("args", {}))
            executed.append({"role": "tool", "name": call["name"], "content": result})
        used = state["tool_calls_used"] + len(state["pending_tool_calls"])
        return {"messages": executed, "tool_calls_used": used, "pending_tool_calls": []}

    def budget_stop_node(state: CopilotState) -> dict[str, Any]:
        return {
            "answer": "I reached my tool-call budget for this question. "
            "Here is what the tools returned so far — the messages above hold "
            "the raw results, and the analytics endpoints serve them directly.",
            "pending_tool_calls": [],
        }

    def route_after_agent(state: CopilotState) -> str:
        if not state.get("pending_tool_calls"):
            return END
        if state["tool_calls_used"] >= MAX_TOOL_CALLS:
            return "budget_stop"
        return "tools"

    graph = StateGraph(CopilotState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("budget_stop", budget_stop_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent", route_after_agent, {"tools": "tools", "budget_stop": "budget_stop", END: END}
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("budget_stop", END)
    return graph.compile()


# The request-scoped session, bound by the endpoint for the duration of
# one synchronous graph invocation (safe: each request completes its run
# before returning; Phase B's streaming moves this to a context variable).
_session: Any = None


def _current_session() -> Any:
    if _session is None:
        raise RuntimeError("no session bound — invoke the graph via the endpoint")
    return _session


def bind_session(session: Any) -> None:
    global _session
    _session = session


def run_copilot(
    provider: Provider, registry: dict[str, ToolSpec], db: Any, question: str
) -> dict[str, Any]:
    """One question in, the final answer + trace summary out. The fallback
    chain's last rung lives here: if the graph itself fails, the caller
    gets a degraded answer pointing at the real endpoints — never a 500
    from the model."""
    bind_session(db)
    graph = build_graph(provider, registry)
    initial = {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        "tool_calls_used": 0,
        "answer": None,
        "pending_tool_calls": [],
    }
    try:
        final = graph.invoke(
            initial,
            config={"recursion_limit": 25},  # the backstop behind MAX_TOOL_CALLS
        )
    except Exception as exc:  # noqa: BLE001 - the fallback chain's last rung
        logger.error("copilot graph failed (%s); answering without the model", exc)
        return {
            "answer": "The copilot could not complete this question. "
            "Try the analytics endpoints directly — the data is all there.",
            "tool_calls_used": 0,
            "provider": provider.name,
            "degraded": True,
            "prompt_version": PROMPT_VERSION,
        }
    result = {
        "answer": final.get("answer"),
        "tool_calls_used": final.get("tool_calls_used", 0),
        "provider": provider.name,
        "degraded": False,
        "prompt_version": PROMPT_VERSION,
        "messages": final.get("messages", []),
    }
    trace_run(question, result)
    return result
