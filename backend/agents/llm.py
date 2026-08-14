"""LLM façade with role-aware model selection and failover.

Which models may run at all is an RBAC decision: the caller's role holds a set of
models, and the executor opens each user message with an `LLMRun` carrying that set,
most capable first. Every call walks it in order — when one model fails for an
operational reason (no API key, a network error, a rate limit, a 5xx, a refusal) the
same call is retried on the next model the role holds. Callers see one interface and
never handle the switch themselves.

The run is passed explicitly rather than held in a ContextVar: the streaming endpoint
drives the pipeline generator from a thread pool, and each resumption gets its own
copy of the context, so anything stashed in a ContextVar between two `yield`s is lost.
`LLMRun` also collects which models answered, which the chat trace reports.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from agents.provider_base import LLMUnavailable, Provider
from agents.provider_claude import ClaudeProvider
from agents.provider_gemini import GeminiProvider
from config import settings
from rbac.model_catalog import MODELS_BY_KEY, ModelSpec

logger = logging.getLogger(__name__)

__all__ = [
    "LLMRun",
    "LLMUnavailable",
    "complete_json",
    "complete_text",
    "configured_providers",
    "model_is_available",
    "select_tool",
]

PROVIDERS: dict[str, Provider] = {"claude": ClaudeProvider(), "gemini": GeminiProvider()}


@dataclass
class LLMRun:
    """One user message's LLM session."""

    # Models this run may use, in preference order.
    models: tuple[str, ...] = ()
    # Models that actually answered, in call order.
    used: list[str] = field(default_factory=list)

    @classmethod
    def of(cls, models: Sequence[str]) -> "LLMRun":
        return cls(models=tuple(models))

    def models_answered(self) -> list[str]:
        """Model keys that answered, in order, de-duplicated."""
        return list(dict.fromkeys(self.used))

    def providers_answered(self) -> list[str]:
        """Providers that answered, in order, de-duplicated."""
        names = [MODELS_BY_KEY[key].provider for key in self.models_answered() if key in MODELS_BY_KEY]
        return list(dict.fromkeys(names))

    def chain(self) -> list[tuple[ModelSpec, Provider]]:
        """Models to try, in order. Ones whose provider is unconfigured are skipped."""
        chain: list[tuple[ModelSpec, Provider]] = []
        for key in self.models:
            model = MODELS_BY_KEY.get(key)
            if model is None:  # a stale grant for a model no longer in the catalogue
                continue
            provider = PROVIDERS.get(model.provider)
            if provider is None or not provider.configured():
                continue
            # With failover disabled, the second provider is a last resort rather
            # than a fallback: it runs only when no Claude tier is usable.
            if model.provider == "gemini" and not settings.llm_fallback_enabled and chain:
                continue
            chain.append((model, provider))
        return chain


def model_is_available(model: ModelSpec) -> bool:
    """True when the provider behind a model has the credentials it needs."""
    provider = PROVIDERS.get(model.provider)
    return provider is not None and provider.configured()


def configured_providers() -> list[str]:
    """Names of the providers that have credentials — for the health endpoint."""
    return [name for name, provider in PROVIDERS.items() if provider.configured()]


def _call(run: LLMRun, operation: str, **kwargs):
    chain = run.chain()
    if not chain:
        if run.models:
            raise LLMUnavailable(
                "None of the models your role is allowed to use are configured on this "
                "server. Set ANTHROPIC_API_KEY (and optionally GEMINI_API_KEY) in backend/.env."
            )
        raise LLMUnavailable(
            "No language model is enabled for your role. Ask a super admin to grant one "
            "on the Access control page."
        )

    failures: list[str] = []
    for model, provider in chain:
        try:
            result = getattr(provider, operation)(model=model.model_id, **kwargs)
        except LLMUnavailable as exc:
            failures.append(f"{model.key}: {exc}")
            logger.warning("model %s failed on %s: %s", model.key, operation, exc)
            continue
        except Exception as exc:  # noqa: BLE001 — one model misbehaving must not break the chain
            failures.append(f"{model.key}: unexpected error: {exc}")
            logger.exception("model %s raised on %s", model.key, operation)
            continue

        if model.key != chain[0][0].key:
            logger.info("%s answered after %s failed", model.key, chain[0][0].key)
        run.used.append(model.key)
        return result

    raise LLMUnavailable("; ".join(failures))


def complete_json(run: LLMRun, *, system: str, user: str, schema: dict, effort: str = "low") -> dict:
    """Call an LLM and get back a dict matching `schema`."""
    return _call(run, "complete_json", system=system, user=user, schema=schema, effort=effort)


def complete_text(run: LLMRun, *, system: str, user: str, effort: str = "medium") -> str:
    """Call an LLM for a plain-text answer."""
    return _call(run, "complete_text", system=system, user=user, effort=effort)


def select_tool(
    run: LLMRun, *, system: str, user: str, tools: list[dict], effort: str = "medium"
) -> tuple[str | None, dict, str]:
    """Ask an LLM to pick one tool.

    Returns (tool_name, tool_input, assistant_text). tool_name is None when the model
    answered with text instead of choosing a tool.
    """
    return _call(run, "select_tool", system=system, user=user, tools=tools, effort=effort)
