"""The model catalogue — which LLMs exist, and which role may use which.

Model access is RBAC, not configuration: the allowed set lives in `role_models` in
PostgreSQL and is resolved per request in `rbac/service.py`, exactly like a
permission. A user may ask for a specific model; if their role does not hold it the
request is refused *before* any provider is called, and the refusal is audited.

This module is the vocabulary and the seeded baseline. At request time the allowed
set is read from the database, never from this file.
"""

from dataclasses import dataclass

from config import settings
from rbac.permissions import (
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_HR,
    ROLE_SUPER_ADMIN,
    ROLE_SUPERVISOR,
)


@dataclass(frozen=True)
class ModelSpec:
    key: str  # stable identifier stored in `role_models`
    label: str
    provider: str  # claude | gemini
    rank: int  # preference order, highest first
    blurb: str

    @property
    def model_id(self) -> str:
        """The provider's own model id, from settings so it stays configurable."""
        return {
            "claude-opus": settings.claude_opus_model,
            "claude-sonnet": settings.claude_sonnet_model,
            "claude-haiku": settings.claude_haiku_model,
            "gemini": settings.gemini_model,
        }[self.key]


CLAUDE_OPUS = ModelSpec(
    key="claude-opus",
    label="Claude Opus",
    provider="claude",
    rank=40,
    blurb="Most capable. Best on multi-step reasoning; slowest and dearest.",
)
CLAUDE_SONNET = ModelSpec(
    key="claude-sonnet",
    label="Claude Sonnet",
    provider="claude",
    rank=30,
    blurb="Balanced. The everyday default for routing and answering.",
)
CLAUDE_HAIKU = ModelSpec(
    key="claude-haiku",
    label="Claude Haiku",
    provider="claude",
    rank=20,
    blurb="Fastest and cheapest Claude. Fine for lookups and short answers.",
)
GEMINI = ModelSpec(
    key="gemini",
    label="Gemini",
    provider="gemini",
    rank=10,
    blurb="Second provider. Answers when the Claude tiers are unavailable.",
)

MODEL_CATALOGUE: list[ModelSpec] = [CLAUDE_OPUS, CLAUDE_SONNET, CLAUDE_HAIKU, GEMINI]

MODELS_BY_KEY: dict[str, ModelSpec] = {model.key: model for model in MODEL_CATALOGUE}

ALL_MODEL_KEYS: list[str] = [model.key for model in MODEL_CATALOGUE]


def get_model(key: str) -> ModelSpec | None:
    return MODELS_BY_KEY.get(key)


def model_label(key: str) -> str:
    model = MODELS_BY_KEY.get(key)
    return model.label if model else key


def preference_order(keys: set[str] | frozenset[str]) -> list[ModelSpec]:
    """Allowed models, most capable first — the order the pipeline falls back through."""
    return sorted(
        (model for model in MODEL_CATALOGUE if model.key in keys),
        key=lambda model: model.rank,
        reverse=True,
    )


def model_permission(key: str) -> str:
    """The pseudo-permission recorded in `audit_logs` for a model decision.

    Model access is not stored as a row in `permissions`, but the audit trail is
    keyed on a permission string, so a denial reads the same as any other.
    """
    return f"model:{key}"


# Seeded baseline. A super admin edits this at runtime from the Access control page;
# `seed.py` is the only thing that reads it.
ROLE_MODELS: dict[str, list[str]] = {
    # Team leads get the mid tier and below.
    ROLE_SUPERVISOR: [CLAUDE_SONNET.key, CLAUDE_HAIKU.key, GEMINI.key],
    # Analysts run high-volume lookups — cheap tiers only, so asking for Opus is a
    # one-click demonstration of a model denial.
    ROLE_ANALYST: [CLAUDE_HAIKU.key, GEMINI.key],
    ROLE_HR: [CLAUDE_SONNET.key, CLAUDE_HAIKU.key, GEMINI.key],
    ROLE_ADMIN: ALL_MODEL_KEYS,
    ROLE_SUPER_ADMIN: ALL_MODEL_KEYS,
}
