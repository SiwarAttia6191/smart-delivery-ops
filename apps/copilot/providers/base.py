"""Provider interface (#120 Phase A): the copilot never talks to a vendor
SDK directly. Phase B's live provider implements this same shape, so the
graph, guardrails and tests are provider-agnostic by construction.
"""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    """One tool invocation requested by the provider."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)

    def model_dump(self) -> dict[str, Any]:
        return {"name": self.name, "args": self.args}


@dataclass
class ProviderReply:
    """One turn of the provider: either it wants tools, or it answers."""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.tool_calls)

    def model_dump(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "tool_calls": [tc.model_dump() for tc in self.tool_calls],
        }


class Provider(Protocol):
    """The minimal contract: a conversation in, one turn out."""

    name: str

    def chat(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> ProviderReply: ...
