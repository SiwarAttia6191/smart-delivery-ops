"""The live provider (#121 Phase B): the real LLM behind the same
interface the scripted provider proved in CI.

The switch is exactly `copilot_provider=openai` + a real key. Uses an
OpenAI-COMPATIBLE chat completion with tool calling; the message shape
adapts to/from the provider protocol here, keeping the graph vendor-
agnostic. Token usage from the vendor response is carried on every
reply so the graph can enforce the per-chat token budget and trace it.
"""

import json
import logging
from typing import Any

from apps.copilot.providers.base import ProviderReply, ToolCall

logger = logging.getLogger(__name__)


class OpenAIProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini",
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        max_retries: int | None = None,
    ) -> None:
        from openai import OpenAI

        if not api_key or api_key == "xxx":
            raise ValueError(
                "the OpenAI key is the placeholder — set a real key to enable this provider"
            )
        # timeout/max_retries: the chat path uses the SDK defaults; the
        # incident narrator (alert path) passes tight bounds because the
        # alert is already firing and must not wait on a chatty API.
        client_kwargs: dict[str, Any] = {"api_key": api_key, "base_url": base_url}
        if timeout_seconds is not None:
            client_kwargs["timeout"] = timeout_seconds
        if max_retries is not None:
            client_kwargs["max_retries"] = max_retries
        self._client = OpenAI(**client_kwargs)
        self.model = model
        # per-model name: the fallback chain reports WHICH rung served
        self.name = f"openai:{model}"

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
        usage = None
        reported = getattr(response, "usage", None)
        if reported is not None:
            usage = {
                "prompt_tokens": reported.prompt_tokens,
                "completion_tokens": reported.completion_tokens,
                "total_tokens": reported.total_tokens,
            }
        return ProviderReply(content=message.content, tool_calls=tool_calls, usage=usage)

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
