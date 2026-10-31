"""The LangGraph supervisor (#120 Phase A, #121 Phase B): the agent's
control flow as a graph, not a while-loop.

    START → agent ──(wants tools, budgets left)──> tools ──> agent
                  ├─(tool budget spent)─────────> budget_stop ──> END
                  ├─(token budget spent)────────> token_stop ───> END
                  └─(final answer)───────────────> END

The state is plain dicts (our own message shape) — the provider adapter
translates to/from SDK formats, so the graph is provider-agnostic by
construction. Three hard limits live here: the tool-call budget, the
per-chat token budget (usage reported by the live provider), and
LangGraph's recursion limit as the backstop behind both. run_copilot
walks the FALLBACK CHAIN: flagship model → mini model → the canned
degraded answer, reporting which rung served and why earlier ones fell.
"""

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from apps.copilot.guardrails import MAX_TOOL_CALLS
from apps.copilot.providers.base import Provider
from apps.copilot.tools import ToolSpec, execute_tool, tool_schemas
from apps.copilot.tracing import trace_run
from core.config import get_settings

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
    total_tokens: int
    answer: str | None
    pending_tool_calls: list[dict[str, Any]]


def build_graph(provider: Provider, registry: dict[str, ToolSpec]):
    schemas = tool_schemas(registry)

    def agent_node(state: CopilotState) -> dict[str, Any]:
        reply = provider.chat(state["messages"], schemas)
        update: dict[str, Any] = {"messages": [reply.model_dump()]}
        if reply.usage:
            update["total_tokens"] = state.get("total_tokens", 0) + int(
                reply.usage.get("total_tokens", 0)
            )
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

    def token_stop_node(state: CopilotState) -> dict[str, Any]:
        return {
            "answer": "I reached my token budget for this question. "
            "The analytics endpoints serve the underlying data directly.",
            "pending_tool_calls": [],
        }

    def route_after_agent(state: CopilotState) -> str:
        if not state.get("pending_tool_calls"):
            return END
        if state["tool_calls_used"] >= MAX_TOOL_CALLS:
            return "budget_stop"
        if state.get("total_tokens", 0) >= get_settings().copilot_max_tokens_per_chat:
            return "token_stop"
        return "tools"

    graph = StateGraph(CopilotState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_node("budget_stop", budget_stop_node)
    graph.add_node("token_stop", token_stop_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges(
        "agent",
        route_after_agent,
        {"tools": "tools", "budget_stop": "budget_stop", "token_stop": "token_stop", END: END},
    )
    graph.add_edge("tools", "agent")
    graph.add_edge("budget_stop", END)
    graph.add_edge("token_stop", END)
    return graph.compile()


# The request-scoped session, bound by the endpoint for the duration of
# one synchronous graph invocation (safe: each request completes its run
# before returning; streaming moves this to a context variable).
_session: Any = None


def _current_session() -> Any:
    if _session is None:
        raise RuntimeError("no session bound — invoke the graph via the endpoint")
    return _session


def bind_session(session: Any) -> None:
    global _session
    _session = session


def _summarize_failures(failures: list[tuple[str, str]]) -> str:
    return " | ".join(f"{name} failed: {err}" for name, err in failures)[:400]


def run_copilot(
    providers: Provider | Sequence[Provider], registry: dict[str, ToolSpec], db: Any, question: str
) -> dict[str, Any]:
    """One question in, the final answer + trace summary out.

    `providers` is the FALLBACK CHAIN (#121): each rung gets a fresh
    graph invocation; the first that completes serves. When a rung dies
    mid-graph its partial state is lost (LangGraph semantics), so later
    rungs re-collect tool data — the reported tool_calls_used always
    describes the attempt that actually served. If every rung fails, the
    caller gets the canned degraded answer with `degraded_reason`
    naming each failure — never a 500 from the model."""
    chain: Sequence[Provider] = providers if isinstance(providers, Sequence) else (providers,)
    bind_session(db)
    failures: list[tuple[str, str]] = []
    for provider in chain:
        graph = build_graph(provider, registry)
        initial = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": question},
            ],
            "tool_calls_used": 0,
            "total_tokens": 0,
            "answer": None,
            "pending_tool_calls": [],
        }
        try:
            final = graph.invoke(
                initial,
                config={"recursion_limit": 25},  # the backstop behind both budgets
            )
        except Exception as exc:  # noqa: BLE001 - the fallback chain's rung
            logger.error("copilot rung %s failed (%s); trying the next rung", provider.name, exc)
            failures.append((provider.name, f"{type(exc).__name__}: {exc}"[:180]))
            continue
        result = {
            "answer": final.get("answer"),
            "tool_calls_used": final.get("tool_calls_used", 0),
            "usage_total": final.get("total_tokens", 0) or None,
            "provider": provider.name,
            "degraded": False,
            "degraded_reason": None,
            "prompt_version": PROMPT_VERSION,
            "messages": final.get("messages", []),
        }
        trace_run(question, result)
        return result
    # every rung failed — degrade loudly, pointing at the real endpoints
    logger.error("copilot: all %d provider rungs failed for one question", len(failures))
    result = {
        "answer": "The copilot could not complete this question. "
        "Try the analytics endpoints directly — the data is all there.",
        "tool_calls_used": 0,
        "usage_total": None,
        "provider": chain[-1].name if chain else "none",
        "degraded": True,
        "degraded_reason": _summarize_failures(failures),
        "prompt_version": PROMPT_VERSION,
        "messages": [],
    }
    trace_run(question, result)
    return result
