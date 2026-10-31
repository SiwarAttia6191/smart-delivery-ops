"""The copilot chat endpoint (#120 Phase A, #121 Phase B): JWT-
authenticated, tenant-scoped by the middleware (the graph's tools run on
the request's session, so RLS scopes every answer), provider chain
pluggable by settings — the live flagship→mini models under
COPILOT_PROVIDER=openai, the deterministic script otherwise.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.deps import current_claims, get_db
from apps.copilot import guardrails
from apps.copilot.graph import run_copilot
from apps.copilot.providers.base import ProviderReply, ToolCall
from apps.copilot.providers.openai_client import OpenAIProvider
from apps.copilot.providers.scripted import ScriptedProvider
from apps.copilot.tools import build_registry
from core.config import get_settings

router = APIRouter(prefix="/copilot", tags=["copilot"])

_registry = build_registry()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=guardrails.MAX_QUESTION_CHARS)


class ChatResponse(BaseModel):
    answer: str
    tool_calls_used: int
    provider: str
    degraded: bool
    degraded_reason: str | None = None
    prompt_version: str
    usage_total: int | None = None


def _build_providers() -> list[Any]:
    """The fallback chain (#121): flagship model first, then the mini
    model when configured differently, each an OpenAIProvider on the same
    key; the scripted provider serves alone. Which rung answered comes
    back in `provider` (`openai:<model>`), failures in `degraded_reason`."""
    settings = get_settings()
    if settings.copilot_provider == "scripted":
        return [
            ScriptedProvider(
                [
                    # The default script: one governed lookup, then the honest
                    # narration. Phase B replaces this with the live provider
                    # behind the same interface.
                    ProviderReply(tool_calls=[ToolCall(name="analytics_summary", args={})]),
                    ProviderReply(
                        content="Here are the platform's current operational KPIs from "
                        "the summary tool — orders, status mix, revenue, delivery "
                        "minutes and driver availability, exactly as the governed "
                        "API computes them. (Scripted provider — the live model "
                        "lands in Phase B.)"
                    ),
                ]
            )
        ]
    if settings.copilot_provider == "openai":
        # The live chain (#121). A missing/placeholder key raises here —
        # loud at request time beats silently serving the script.
        key = settings.openai_api_key or ""
        chain: list[Any] = [OpenAIProvider(api_key=key, model=settings.copilot_model)]
        if settings.copilot_model_mini != settings.copilot_model:
            chain.append(OpenAIProvider(api_key=key, model=settings.copilot_model_mini))
        return chain
    raise ValueError(f"unknown copilot provider '{settings.copilot_provider}'")


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    claims: Annotated[dict[str, Any] | None, Depends(current_claims)] = None,
) -> ChatResponse:
    """Ask the Ops Copilot. Authenticated only (the premium feature);
    every tool answer is scoped to the caller's tenant by RLS."""
    try:
        question = guardrails.screen_question(payload.question)
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    result = run_copilot(_build_providers(), _registry, db, question)
    return ChatResponse(
        answer=result["answer"],
        tool_calls_used=result["tool_calls_used"],
        provider=result["provider"],
        degraded=result["degraded"],
        degraded_reason=result.get("degraded_reason"),
        prompt_version=result["prompt_version"],
        usage_total=result.get("usage_total"),
    )
