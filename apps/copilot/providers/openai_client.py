"""The live provider (#120 Phase B): the real LLM behind the same
interface the scripted provider proved in CI.

Wired end to end with the placeholder key ("xxx" — see
secrets/openai_api_key.txt): any call fails auth, the graph's fallback
chain answers degraded with the tool data, and the switch is exactly
`copilot_provider=openai` + a real key. Uses an OpenAI-COMPATIBLE chat
completion with tool calling; the message shape adapts to/from the
provider protocol here, keeping the graph vendor-agnostic.
"""

import json
import logging
from typing import Any

from apps.copilot.providers.base import ProviderReply, ToolCall

logger = logging.getLogger(__name__)


class OpenAIProvider:
    name = "openai"

    def __init__(
        self, api_key: str, model: str = "gpt-4o-mini", base_url: str | None = None
    ) -> None:
        from openai import OpenAI

        if not api_key or api_key == "xxx":
            raise ValueError(
                "the OpenAI key is the placeholder — set a real key to enable this provider"
            )
        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.model = model

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ProviderReply:
        response = self._client.chat.completions.create(
            model=self.model,
            messages=self._to_sdk_messages(messages),  # type: ignore[arg-type]
            tools=self._to_sdk_tools(tools),  # type: ignore[arg-type]
        )
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            ToolCall(name=tc.function.name, args=json.loads(tc.function.arguments or "{}"))
            for tc in (message.tool_calls or [])
            if hasattr(tc, "function")
        ]
        return ProviderReply(content=message.content, tool_calls=tool_calls)

    @staticmethod
    def _to_sdk_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Our dict shape → the SDK's: assistant tool_calls and tool
        results map to the assistant.tool_calls / role=tool conventions."""
        sdk: list[dict[str, Any]] = []
        for m in messages:
            if m["role"] == "assistant" and m.get("tool_calls"):
                sdk.append(
                    {
                        "role": "assistant",
                        "content": m.get("content"),
                        "tool_calls": [
                            {
                                "id": f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc.get("args", {})),
                                },
                            }
                            for idx, tc in enumerate(m["tool_calls"])
                        ],
                    }
                )
            elif m["role"] == "tool":
                sdk.append(
                    {
                        "role": "tool",
                        "tool_call_id": f"call_{m.get('_seq', 0)}",
                        "content": m["content"],
                    }
                )
            else:
                sdk.append({"role": m["role"], "content": m.get("content")})
        return sdk

    @staticmethod
    def _to_sdk_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]
