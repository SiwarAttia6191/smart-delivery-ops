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
from apps.copilot.providers.factory import build_provider_chain
from apps.copilot.tools import build_registry

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
    """Delegate to the shared factory (#123): the chat endpoint and the
    scheduled eval run must never drift on provider construction."""
    return build_provider_chain()


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
