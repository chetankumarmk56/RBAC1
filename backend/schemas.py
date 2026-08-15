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
    # Models this role may run, most capable first, and how far its rows reach.
    models: list[str] = []
    row_scope: str = "all"


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserInfo


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Omit to start a new conversation; the stream reports the id it created.
    conversation_id: int | None = None
    # The model the user picked. Omit to use the most capable model their role holds.
    # A model the role does not hold is refused before any provider is called.
    model: str | None = None


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
    # The model that answered, e.g. 'claude-sonnet'; on a model denial, the one refused.
    model: str | None = None
    # Columns removed by the field-level policy before the agent saw the data.
    withheld_fields: list[str] = Field(default_factory=list)
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
    # Models this role may run, most capable first.
    models: list[str] = []
    # Row reach: all | department | team | self.
    row_scope: str = "all"
    # dataset key -> the fields of that dataset granted to this role. This is what the
    # console's checkboxes show, so it stays the raw grant.
    fields: dict[str, list[str]] = {}
    # dataset key -> the fields the role does not actually get. A superset of the
    # ungranted ones: a granted column is in here too when it reconstructs an
    # ungranted one, which is why the console needs it as well as `fields`.
    fields_withheld: dict[str, list[str]] = {}


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


class ModelSummary(BaseModel):
    key: str
    label: str
    provider: str
    blurb: str
    # False when the server has no credentials for that provider, so granting the
    # model would have no effect until they are configured.
    available: bool
    roles_with_access: list[str]


class FieldSummary(BaseModel):
    key: str
    label: str
    # Identity columns that are always returned and cannot be withheld.
    locked: bool


class DatasetSummary(BaseModel):
    key: str
    label: str
    blurb: str
    required_permission: str
    tool: str
    fields: list[FieldSummary]
    # Roles that can read the dataset at all — i.e. hold its permission.
    roles_with_access: list[str]


class ScopeOption(BaseModel):
    key: str
    description: str


class AccessMatrix(BaseModel):
    roles: list[RoleSummary]
    tools: list[ToolSummary]
    models: list[ModelSummary]
    datasets: list[DatasetSummary]
    scopes: list[ScopeOption]


class AccessChangeRequest(BaseModel):
    role_name: str
    tool_name: str
    granted: bool


class ModelAccessRequest(BaseModel):
    role_name: str
    model_key: str
    granted: bool


class FieldAccessRequest(BaseModel):
    role_name: str
    dataset: str
    field: str
    granted: bool


class DataScopeRequest(BaseModel):
    role_name: str
    scope: str


class AccessChangeResponse(BaseModel):
    changed: bool
    message: str
    matrix: AccessMatrix


# --------------------------------------------------------------------------- #
# The caller's own model options — drives the picker in the chat composer
# --------------------------------------------------------------------------- #

class ModelOption(BaseModel):
    key: str
    label: str
    provider: str
    blurb: str
    # Whether the caller's role holds this model. Locked models are still listed:
    # picking one produces a real, audited refusal from the server.
    allowed: bool
    available: bool


class ModelOptions(BaseModel):
    models: list[ModelOption]
    # The model used when the user picks "Auto" — the most capable one they hold.
    default_model: str | None = None
