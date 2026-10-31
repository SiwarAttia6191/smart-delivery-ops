"""The deterministic provider (#120 Phase A): replays a scripted plan.

This is what makes the ENTIRE agent path CI-provable with no key and no
network — the graph, the tool execution, the budgets and the fallback all
run against this provider in tests exactly as they will run against the
live provider in Phase B. A script is a list of steps; each step is one
ProviderReply. When the steps run out, the last reply repeats (so a
script ending in a final answer stays consistent).
"""

from typing import Any

from apps.copilot.providers.base import ProviderReply


class ScriptedProvider:
    name = "scripted"

    def __init__(self, steps: list[ProviderReply]) -> None:
        if not steps:
            raise ValueError("a scripted provider needs at least one step")
        self.steps = steps
        self._cursor = 0

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ProviderReply:
        reply = self.steps[min(self._cursor, len(self.steps) - 1)]
        self._cursor += 1
        return reply
