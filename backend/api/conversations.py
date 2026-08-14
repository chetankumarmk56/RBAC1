"""Chat history.

Every query filters on the authenticated user's id, so a conversation belonging
to someone else is not merely hidden — it is unreachable. A request for one
returns 404 rather than 403, so the API never confirms that it exists.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from auth.dependencies import get_current_principal
from db.session import get_db
from models import ChatMessage, Conversation
from rbac.service import Principal
from schemas import ConversationDetail, ConversationSummary, StoredMessage

router = APIRouter(prefix="/api/conversations", tags=["conversations"])

NEW_CHAT_TITLE = "New chat"


def owned_conversation(db: Session, principal: Principal, conversation_id: int) -> Conversation:
    """Fetch a conversation the caller owns, or 404."""
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == principal.user_id,
        )
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


def title_from(message: str) -> str:
    """A readable title from the first user message."""
    cleaned = " ".join(message.split())
    return cleaned[:76] + "…" if len(cleaned) > 77 else cleaned or NEW_CHAT_TITLE


@router.get("", response_model=list[ConversationSummary])
def list_conversations(
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> list[ConversationSummary]:
    counts = (
        select(ChatMessage.conversation_id, func.count().label("n"))
        .group_by(ChatMessage.conversation_id)
        .subquery()
    )
    rows = db.execute(
        select(Conversation, func.coalesce(counts.c.n, 0))
        .outerjoin(counts, counts.c.conversation_id == Conversation.id)
        .where(Conversation.user_id == principal.user_id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
        .limit(100)
    ).all()

    return [
        ConversationSummary(
            id=conversation.id,
            title=conversation.title,
            updated_at=conversation.updated_at,
            message_count=count,
        )
        for conversation, count in rows
    ]


@router.get("/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: int,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    conversation = owned_conversation(db, principal, conversation_id)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.id)
    ).all()

    return ConversationDetail(
        id=conversation.id,
        title=conversation.title,
        updated_at=conversation.updated_at,
        messages=[
            StoredMessage(
                id=message.id,
                role=message.role,
                content=message.content,
                trace=message.trace,
                failed=message.failed,
            )
            for message in messages
        ],
    )


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: int,
    principal: Principal = Depends(get_current_principal),
    db: Session = Depends(get_db),
) -> None:
    conversation = owned_conversation(db, principal, conversation_id)
    db.execute(delete(Conversation).where(Conversation.id == conversation.id))
    db.commit()
