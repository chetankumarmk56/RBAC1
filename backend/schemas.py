"""Request and response models for the HTTP API."""

from datetime import datetime

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class UserInfo(BaseModel):
    email: str
    full_name: str
    role: str
    permissions: list[str]


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Omit to start a new conversation; the stream reports the id it created.
    conversation_id: int | None = None


class ChatTrace(BaseModel):
    """How the request travelled through the pipeline. Rendered under each reply."""

    intent: str = ""
    agent: str | None = None
    reasoning: str | None = None
    tool: str | None = None
    required_permission: str | None = None
    decision: str | None = None  # ALLOWED | DENIED | None
    reason: str | None = None
    row_count: int | None = None
    scope_note: str | None = None
    # Which LLM answered: 'claude', or 'gemini' when the fallback took over.
    provider: str | None = None
    steps: list[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    trace: ChatTrace


# --------------------------------------------------------------------------- #
# Chat history
# --------------------------------------------------------------------------- #

class ConversationSummary(BaseModel):
    id: int
    title: str
    updated_at: datetime
    message_count: int


class StoredMessage(BaseModel):
    id: int
    role: str  # user | assistant
    content: str
    trace: ChatTrace | None = None
    failed: bool = False


class ConversationDetail(BaseModel):
    id: int
    title: str
    updated_at: datetime
    messages: list[StoredMessage]


# --------------------------------------------------------------------------- #
# Access-control console — super admin only
# --------------------------------------------------------------------------- #

class RoleSummary(BaseModel):
    name: str
    description: str | None = None
    permissions: list[str]
    # Protected roles cannot have their permissions changed (lockout guard).
    protected: bool


class ToolSummary(BaseModel):
    name: str
    domain: str
    description: str
    required_permission: str
    # True for tools that change state rather than read it.
    mutates: bool
    # False for the tools that manage RBAC itself — those cannot be granted.
    configurable: bool
    roles_with_access: list[str]


class AccessMatrix(BaseModel):
    roles: list[RoleSummary]
    tools: list[ToolSummary]


class AccessChangeRequest(BaseModel):
    role_name: str
    tool_name: str
    granted: bool


class AccessChangeResponse(BaseModel):
    changed: bool
    message: str
    matrix: AccessMatrix
