"""Claude provider — the primary."""

import json
from functools import lru_cache
from typing import Any

import anthropic

from agents.provider_base import MAX_TOKENS, LLMUnavailable
from config import settings


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _text_of(response: Any) -> str:
    """Concatenate the text blocks of a response, ignoring thinking blocks."""
    return "".join(block.text for block in response.content if block.type == "text").strip()


def _guard(response: Any) -> Any:
    if response.stop_reason == "refusal":
        raise LLMUnavailable("Claude declined to answer this request.")
    return response


class ClaudeProvider:
    name = "claude"

    def configured(self) -> bool:
        return bool(settings.anthropic_api_key)

    def complete_json(self, *, system: str, user: str, schema: dict, effort: str) -> dict:
        """Structured outputs, so the response always parses — no regex, no retry loop."""
        try:
            response = _client().messages.create(
                model=settings.claude_model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={
                    "effort": effort,
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except anthropic.APIError as exc:
            raise LLMUnavailable(f"Claude request failed: {exc}") from exc

        try:
            return json.loads(_text_of(_guard(response)))
        except json.JSONDecodeError as exc:  # pragma: no cover — structured outputs prevent this
            raise LLMUnavailable("Claude returned malformed JSON.") from exc

    def complete_text(self, *, system: str, user: str, effort: str) -> str:
        try:
            response = _client().messages.create(
                model=settings.claude_model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": effort},
            )
        except anthropic.APIError as exc:
            raise LLMUnavailable(f"Claude request failed: {exc}") from exc

        return _text_of(_guard(response))

    def select_tool(
        self, *, system: str, user: str, tools: list[dict], effort: str
    ) -> tuple[str | None, dict, str]:
        try:
            response = _client().messages.create(
                model=settings.claude_model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=tools,
                # One tool per turn keeps the demo pipeline single-path and legible.
                tool_choice={"type": "auto", "disable_parallel_tool_use": True},
                output_config={"effort": effort},
            )
        except anthropic.APIError as exc:
            raise LLMUnavailable(f"Claude request failed: {exc}") from exc

        _guard(response)
        for block in response.content:
            if block.type == "tool_use":
                return block.name, dict(block.input or {}), _text_of(response)

        return None, {}, _text_of(response)
