"""The copilot chat endpoint (#120 Phase A, #121 Phase B): JWT-
authenticated, tenant-scoped by the middleware (the graph's tools run on
the request's session, so RLS scopes every answer), provider chain
pluggable by settings — the live flagship→mini models under
COPILOT_PROVIDER=openai, the deterministic script otherwise.
"""

import json
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from apps.api.deps import current_claims, get_db
from apps.copilot import guardrails
from apps.copilot.graph import (
    _SYSTEM_PROMPT,
    PROMPT_VERSION,
    bind_session,
    build_graph,
    run_copilot,
)
from apps.copilot.providers.factory import build_provider_chain
from apps.copilot.tools import build_registry
from core.config import get_settings
from domain.models import Conversation, ConversationMessage
from domain.services import tenant_service

router = APIRouter(prefix="/copilot", tags=["copilot"])

_registry = build_registry()


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=guardrails.MAX_QUESTION_CHARS)
    conversation_id: str | None = Field(
        None, description="continue a thread (#155): the last-k turns join the context"
    )


class ResumeRequest(BaseModel):
    conversation_id: str = Field(min_length=1)
    question: str = Field(min_length=1, max_length=guardrails.MAX_QUESTION_CHARS)


class ChatResponse(BaseModel):
    answer: str
    tool_calls_used: int
    provider: str
    degraded: bool
    degraded_reason: str | None = None
    prompt_version: str
    usage_total: int | None = None
    conversation_id: str | None = None


def _conversation_context(
    conversation_id: str | None, db: Session
) -> tuple[Conversation, list[dict[str, str]]]:
    """Conversation memory (#155): load the thread's last-k turns into the
    graph context, creating the thread when no id is given. A conversation
    id from another tenant reads as not-found (RLS + explicit filter)."""
    settings = get_settings()
    if conversation_id:
        tid = tenant_service.context_tenant_id(db)
        conversation = (
            db.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.tenant_id == tid)
            .one_or_none()
        )
        if conversation is None:
            raise HTTPException(http_status.HTTP_404_NOT_FOUND, detail="conversation not found")
        turns = (
            db.query(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.id)
            .all()
        )
        history = [
            {"role": m.role, "content": m.content} for m in turns[-settings.copilot_memory_turns :]
        ]
        return conversation, history
    conversation = Conversation(
        id=str(uuid.uuid4()), tenant_id=tenant_service.context_tenant_id(db)
    )
    db.add(conversation)
    db.commit()
    return conversation, []


def _persist_exchange(db: Session, conversation: Conversation, question: str, answer: str) -> None:
    db.add_all(
        [
            ConversationMessage(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                role="user",
                content=question,
            ),
            ConversationMessage(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                role="assistant",
                content=answer,
            ),
        ]
    )
    db.commit()


@router.post("/chat/resume")
def chat_resume(
    payload: ResumeRequest,
    db: Session = Depends(get_db),
    claims: Annotated[dict[str, Any] | None, Depends(current_claims)] = None,
) -> ChatResponse:
    """Approve a proposed mutating action (#121): re-asks the SAME question
    with the approval flag pinned, so the gated tool executes inside the
    RLS-scoped session and the exchange lands in the thread's history."""
    from apps.copilot.tools import _approval_granted, _caller_role

    conversation, history = _conversation_context(payload.conversation_id, db)
    token = _approval_granted.set(True)
    role_token = _caller_role.set((claims or {}).get("role", "viewer"))
    try:
        result = run_copilot(_build_providers(), _registry, db, payload.question, history=history)
    finally:
        _approval_granted.reset(token)
        _caller_role.reset(role_token)
    _persist_exchange(db, conversation, payload.question, result["answer"] or "")
    return ChatResponse(
        conversation_id=conversation.id,
        answer=result["answer"],
        tool_calls_used=result["tool_calls_used"],
        provider=result["provider"],
        degraded=result["degraded"],
        degraded_reason=result.get("degraded_reason"),
        prompt_version=result["prompt_version"],
        usage_total=result.get("usage_total"),
    )


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

    from apps.copilot.tools import _caller_role

    conversation, history = _conversation_context(payload.conversation_id, db)
    role_token = _caller_role.set((claims or {}).get("role", "viewer"))
    try:
        result = run_copilot(
            _build_providers(), _registry, db, question, history=history, thread_id=conversation.id
        )
    finally:
        _caller_role.reset(role_token)
    _persist_exchange(db, conversation, question, result["answer"] or "")
    return ChatResponse(
        conversation_id=conversation.id,
        answer=result["answer"],
        tool_calls_used=result["tool_calls_used"],
        provider=result["provider"],
        degraded=result["degraded"],
        degraded_reason=result.get("degraded_reason"),
        prompt_version=result["prompt_version"],
        usage_total=result.get("usage_total"),
    )


def _sse(event: str, data: dict) -> str:
    return "event: " + event + "\ndata: " + json.dumps(data, default=str) + "\n\n"


@router.post("/chat/stream")
def chat_stream(
    payload: ChatRequest,
    db: Session = Depends(get_db),
    claims: Annotated[dict[str, Any] | None, Depends(current_claims)] = None,
) -> StreamingResponse:
    """The same copilot over Server-Sent Events (#156): progress events per
    graph node (tool hops are visible AS they happen), then one final
    `answer` event carrying the full ChatResponse. The primary rung
    streams; if it dies the full fallback chain serves the answer event —
    a degraded stream, never a dead one."""
    try:
        question = guardrails.screen_question(payload.question)
    except ValueError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    conversation, history = _conversation_context(payload.conversation_id, db)
    from apps.copilot.tools import _caller_role

    caller_role = (claims or {}).get("role", "viewer")
    providers = _build_providers()

    def event_stream():
        # the role is pinned INSIDE the generator: a StreamingResponse's
        # body executes after the route returns, so an outer pin would be
        # reset before any tool ever ran
        role_token = _caller_role.set(caller_role)
        try:
            yield from _event_stream_inner(db, question, conversation, history, providers)
        finally:
            try:
                _caller_role.reset(role_token)
            except ValueError:
                # a generator resumption can land in a different Context
                # under ASGI — force the least-privileged default back so
                # the role never leaks into the next request; every route
                # re-pins its own caller's role at request start anyway
                _caller_role.set("viewer")

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _event_stream_inner(db, question, conversation, history, providers):
    """The streaming body, split from the route so the caller's role can be
    pinned INSIDE the generator: a StreamingResponse's body executes after
    the route returns, so an outer ContextVar pin would be reset before any
    tool ever ran."""
    yield _sse("context", {"conversation_id": conversation.id, "provider": providers[0].name})
    final: dict[str, Any] = {}
    try:
        bind_session(db)
        graph = build_graph(providers[0], _registry)
        initial = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                *history,
                {"role": "user", "content": question},
            ],
            "tool_calls_used": 0,
            "total_tokens": 0,
            "answer": None,
            "pending_tool_calls": [],
        }
        # stream_mode="updates" yields one {node: delta} dict per hop
        for update in graph.stream(initial, config={"recursion_limit": 25}, stream_mode="updates"):
            for node, delta in update.items():
                final.update(delta)
                yield _sse("node", {"node": node})
    except Exception as exc:  # noqa: BLE001 - degrade into the fallback chain
        yield _sse("degraded", {"reason": f"{type(exc).__name__}: {exc}"[:180]})
    if not final.get("answer"):
        result = run_copilot(providers, _registry, db, question, history=history)
    else:
        result = {
            "answer": final.get("answer"),
            "tool_calls_used": final.get("tool_calls_used", 0),
            "usage_total": final.get("total_tokens") or None,
            "provider": providers[0].name,
            "degraded": False,
            "degraded_reason": None,
            "prompt_version": PROMPT_VERSION,
        }
    _persist_exchange(db, conversation, question, result["answer"] or "")
    yield _sse(
        "answer",
        ChatResponse(
            conversation_id=conversation.id,
            answer=result["answer"],
            tool_calls_used=result["tool_calls_used"],
            provider=result["provider"],
            degraded=result["degraded"],
            degraded_reason=result.get("degraded_reason"),
            prompt_version=result["prompt_version"],
            usage_total=result.get("usage_total"),
        ).model_dump(),
    )
