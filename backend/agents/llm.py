"""LLM façade with provider failover.

Claude is the primary. When a Claude call fails for any operational reason — no API
key, a network error, a rate limit, a 5xx, or a refusal — the same call is retried on
Gemini. Callers see one interface and never handle the switch themselves.

Which provider actually answered is recorded per request (see `begin_request` /
`providers_used`) and surfaced in the chat trace, so a fallback is visible rather than
silent.
"""

import logging
from contextvars import ContextVar

from agents.provider_base import LLMUnavailable, Provider
from agents.provider_claude import ClaudeProvider
from agents.provider_gemini import GeminiProvider
from config import settings

logger = logging.getLogger(__name__)

__all__ = [
    "LLMUnavailable",
    "begin_request",
    "complete_json",
    "complete_text",
    "configured_providers",
    "providers_used",
    "select_tool",
]

CLAUDE = ClaudeProvider()
GEMINI = GeminiProvider()

# Tracks which providers served the current request. A ContextVar keeps concurrent
# requests from seeing each other's entries.
_used: ContextVar[tuple[str, ...]] = ContextVar("llm_providers_used", default=())


def begin_request() -> None:
    """Start a fresh provider-usage record. Call once per user message."""
    _used.set(())


def providers_used() -> list[str]:
    """Providers that answered during this request, in order, de-duplicated."""
    return list(dict.fromkeys(_used.get()))


def _record(name: str) -> None:
    _used.set((*_used.get(), name))


def _chain() -> list[Provider]:
    """Providers to try, in order. Unconfigured ones are skipped."""
    chain: list[Provider] = []
    if CLAUDE.configured():
        chain.append(CLAUDE)
    if GEMINI.configured() and (settings.llm_fallback_enabled or not chain):
        chain.append(GEMINI)
    return chain


def configured_providers() -> list[str]:
    """Names of the providers currently usable — for the health endpoint."""
    return [provider.name for provider in _chain()]


def _call(operation: str, **kwargs):
    chain = _chain()
    if not chain:
        raise LLMUnavailable(
            "No LLM provider is configured. Set ANTHROPIC_API_KEY (and optionally "
            "GEMINI_API_KEY for fallback) in backend/.env."
        )

    failures: list[str] = []
    for provider in chain:
        try:
            result = getattr(provider, operation)(**kwargs)
        except LLMUnavailable as exc:
            failures.append(f"{provider.name}: {exc}")
            logger.warning("provider %s failed on %s: %s", provider.name, operation, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — one provider misbehaving must not break the chain
            failures.append(f"{provider.name}: unexpected error: {exc}")
            logger.exception("provider %s raised on %s", provider.name, operation)
            continue

        if provider is not chain[0]:
            logger.info("%s answered after %s failed", provider.name, chain[0].name)
        _record(provider.name)
        return result

    raise LLMUnavailable("; ".join(failures))


def complete_json(*, system: str, user: str, schema: dict, effort: str = "low") -> dict:
    """Call an LLM and get back a dict matching `schema`."""
    return _call("complete_json", system=system, user=user, schema=schema, effort=effort)


def complete_text(*, system: str, user: str, effort: str = "medium") -> str:
    """Call an LLM for a plain-text answer."""
    return _call("complete_text", system=system, user=user, effort=effort)


def select_tool(
    *, system: str, user: str, tools: list[dict], effort: str = "medium"
) -> tuple[str | None, dict, str]:
    """Ask an LLM to pick one tool.

    Returns (tool_name, tool_input, assistant_text). tool_name is None when the model
    answered with text instead of choosing a tool.
    """
    return _call("select_tool", system=system, user=user, tools=tools, effort=effort)
