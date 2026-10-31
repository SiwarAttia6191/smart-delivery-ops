"""Ops Copilot — the AI layer over the governed API (#120 Phase A).

The agent is a LangGraph supervisor graph (graph.py) whose tools wrap the
platform's OWN read logic (tools.py), executed with the REQUEST's session
so row-level security scopes every answer to the caller's tenant. The
LLM provider is pluggable (providers/): Phase A ships a deterministic
scripted provider so the entire tool-calling path is provable in CI with
no key and no network; Phase B adds the live provider behind the same
interface.
"""
