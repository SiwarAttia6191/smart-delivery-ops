"""The provider factory (#121/#123): ONE place decides which provider
chain serves the copilot, so the chat endpoint and the scheduled eval
run can never drift apart on provider construction.

Settings-driven: `scripted` (deterministic, CI-safe) or `openai` (the
live flagship→mini chain on the same key). A missing/placeholder key
raises here — loud at construction beats silently serving the script.
"""

from typing import Any

from apps.copilot.providers.base import ProviderReply, ToolCall
from apps.copilot.providers.openai_client import OpenAIProvider
from apps.copilot.providers.scripted import ScriptedProvider
from core.config import get_settings

_SCRIPT = [
    ProviderReply(tool_calls=[ToolCall(name="analytics_summary", args={})]),
    ProviderReply(
        content="Here are the platform's current operational KPIs from "
        "the summary tool — orders, status mix, revenue, delivery "
        "minutes and driver availability, exactly as the governed "
        "API computes them. (Scripted provider — the live model "
        "lands in Phase B.)"
    ),
]


def build_scripted_provider() -> ScriptedProvider:
    """The deterministic replay: one governed lookup, then the honest
    narration. Proves the whole agent path in CI with no key, no network."""
    return ScriptedProvider(list(_SCRIPT))


def build_provider_chain() -> list[Any]:
    """The fallback chain (#121): flagship model first, then the mini
    model when configured differently, each an OpenAIProvider on the same
    key; the scripted provider serves alone. Which rung answered comes
    back in `provider` (`openai:<model>`), failures in `degraded_reason`."""
    settings = get_settings()
    if settings.copilot_provider == "scripted":
        return [build_scripted_provider()]
    if settings.copilot_provider == "openai":
        key = settings.openai_api_key or ""
        chain: list[Any] = [OpenAIProvider(api_key=key, model=settings.copilot_model)]
        if settings.copilot_model_mini != settings.copilot_model:
            chain.append(OpenAIProvider(api_key=key, model=settings.copilot_model_mini))
        return chain
    raise ValueError(f"unknown copilot provider '{settings.copilot_provider}'")
