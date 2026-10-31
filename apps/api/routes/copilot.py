"""The copilot chat endpoint (#120 Phase A): JWT-authenticated, tenant-
scoped by the middleware (the graph's tools run on the request's session,
so RLS scopes every answer), provider pluggable by settings.

Phase A serves the SCRIPTED provider — the honest default until Phase B's
live provider lands behind the same interface.
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
    prompt_version: str


def _build_provider():
    settings = get_settings()
    if settings.copilot_provider == "scripted":
        return ScriptedProvider(
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
    if settings.copilot_provider == "openai":
        # The live model (#121 Phase B). A missing/placeholder key raises
        # here — loud at request time beats silently serving the script.
        return OpenAIProvider(
            api_key=settings.openai_api_key or "",
            model=settings.copilot_model,
        )
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

    result = run_copilot(_build_provider(), _registry, db, question)
    return ChatResponse(
        answer=result["answer"],
        tool_calls_used=result["tool_calls_used"],
        provider=result["provider"],
        degraded=result["degraded"],
        prompt_version=result["prompt_version"],
    )
