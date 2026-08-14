"""The chat endpoints. Everything the user can do goes through here.

`/api/chat` returns the finished answer in one response. `/api/chat/stream` sends the
same work as Server-Sent Events, emitting one `status` frame per pipeline stage so the
UI can narrate planner -> agent -> tool -> RBAC -> database while it happens, and
persists both turns to the caller's conversation history.
"""

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from agents.executor import ChatOutcome, Progress, run_chat, run_chat_events
from agents.llm import model_is_available
from api.conversations import NEW_CHAT_TITLE, owned_conversation, title_from
from auth.dependencies import get_current_principal
from db.session import SessionLocal, get_db
from models import ChatMessage, Conversation
from rbac.model_catalog import MODEL_CATALOGUE
from rbac.service import Principal
from schemas import ChatRequest, ChatResponse, ChatTrace, ModelOption, ModelOptions

router = APIRouter(prefix="/api", tags=["chat"])


def _trace(outcome: ChatOutcome) -> ChatTrace:
    return ChatTrace(
        intent=outcome.intent,
        agent=outcome.agent,
        reasoning=outcome.reasoning,
        tool=outcome.tool,
        required_permission=outcome.required_permission,
        decision=outcome.decision,
        reason=outcome.reason,
        row_count=outcome.row_count,
        scope_note=outcome.scope_note,
        provider=outcome.provider,
        model=outcome.model,
        withheld_fields=outcome.withheld_fields,
        steps=outcome.steps,
    )


@router.get("/models", response_model=ModelOptions)
def models(principal: Principal = Depends(get_current_principal)) -> ModelOptions:
    """The model picker's options for this caller.

    Models the role does not hold are listed as locked rather than hidden: picking
    one produces a real refusal from the server, which is the point of the demo.
    """
    return ModelOptions(
        models=[
            ModelOption(
                key=model.key,
                label=model.label,
                provider=model.provider,
                blurb=model.blurb,
                allowed=principal.may_use_model(model.key),
                available=model_is_available(model),
            )
            for model in MODEL_CATALOGUE
        ],
        default_model=principal.models[0] if principal.models else None,
    )


@router.post("/chat", response_model=ChatResponse)
def chat(
    payload: ChatRequest,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ChatResponse:
    # `principal` carries the role, permissions, models and data policy loaded from
    # PostgreSQL for the bearer token. The request body contributes only the message
    # text and a *requested* model — a client cannot pass a role, a permission or a
    # tool name, and the requested model is checked against the role before use.
    outcome = run_chat(db, principal, payload.message, payload.model)
    return ChatResponse(reply=outcome.reply, trace=_trace(outcome))


def _frame(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _resolve_conversation(
    db: Session, principal: Principal, conversation_id: int | None, message: str
) -> Conversation:
    """Reuse the caller's conversation, or start one. Ownership is enforced."""
    if conversation_id is not None:
        conversation = owned_conversation(db, principal, conversation_id)
    else:
        conversation = Conversation(user_id=principal.user_id, title=NEW_CHAT_TITLE)
        db.add(conversation)
        db.flush()

    if conversation.title == NEW_CHAT_TITLE:
        conversation.title = title_from(message)
    db.commit()
    db.refresh(conversation)
    return conversation


@router.post("/chat/stream")
def chat_stream(
    payload: ChatRequest,
    principal: Principal = Depends(get_current_principal),
) -> StreamingResponse:
    """The same pipeline, narrated as it runs and saved to history."""

    def events() -> Iterator[str]:
        # A session of its own: the request-scoped one from `get_db` is torn down
        # when the endpoint returns, which is before this generator finishes.
        # `principal` is a plain frozen dataclass, so it carries over safely.
        with SessionLocal() as db:
            try:
                conversation = _resolve_conversation(
                    db, principal, payload.conversation_id, payload.message
                )
                # Tell the client which conversation this belongs to, so a new chat
                # can attach itself before the answer arrives.
                yield _frame(
                    {
                        "type": "conversation",
                        "id": conversation.id,
                        "title": conversation.title,
                    }
                )

                db.add(
                    ChatMessage(
                        conversation_id=conversation.id, role="user", content=payload.message
                    )
                )
                db.commit()

                for item in run_chat_events(db, principal, payload.message, payload.model):
                    if isinstance(item, Progress):
                        yield _frame(
                            {"type": "status", "stage": item.stage, "text": item.text, **item.detail}
                        )
                    elif isinstance(item, ChatOutcome):
                        trace = _trace(item)
                        db.add(
                            ChatMessage(
                                conversation_id=conversation.id,
                                role="assistant",
                                content=item.reply,
                                trace=trace.model_dump(mode="json"),
                            )
                        )
                        # Touch the conversation so it sorts to the top of history.
                        # Set explicitly: `onupdate` only fires when some other
                        # column changed, which it hasn't on a follow-up message.
                        conversation.updated_at = func.now()
                        db.add(conversation)
                        db.commit()

                        yield _frame(
                            {"type": "done", "reply": item.reply, "trace": trace.model_dump()}
                        )
            except Exception as exc:  # noqa: BLE001 — a stream must end with a frame, not a traceback
                yield _frame({"type": "error", "message": str(exc)})

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # stop proxies buffering the stream
        },
    )
