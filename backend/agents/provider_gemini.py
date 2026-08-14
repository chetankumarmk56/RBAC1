"""Gemini provider — the fallback used when Claude cannot answer.

Gemini has no `effort` parameter, so the argument is accepted and ignored; the two
providers otherwise expose the same three call shapes. Tool schemas need no
conversion: `parameters_json_schema` and `response_json_schema` both take raw JSON
Schema, which is exactly what the tool registry already produces for Claude.
"""

import json
from functools import lru_cache

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from agents.provider_base import MAX_TOKENS, LLMUnavailable
from config import settings


@lru_cache(maxsize=1)
def _client() -> genai.Client:
    return genai.Client(api_key=settings.gemini_api_key)


def _generate(*, system: str, user: str, config: types.GenerateContentConfig):
    try:
        return _client().models.generate_content(
            model=settings.gemini_model,
            contents=user,
            config=config,
        )
    except genai_errors.APIError as exc:
        raise LLMUnavailable(f"Gemini request failed: {exc}") from exc


class GeminiProvider:
    name = "gemini"

    def configured(self) -> bool:
        return bool(settings.gemini_api_key)

    def complete_json(self, *, system: str, user: str, schema: dict, effort: str) -> dict:
        del effort  # Gemini has no effort parameter
        response = _generate(
            system=system,
            user=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_TOKENS,
                response_mime_type="application/json",
                response_json_schema=schema,
            ),
        )

        text = (response.text or "").strip()
        if not text:
            raise LLMUnavailable("Gemini returned an empty response.")
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMUnavailable("Gemini returned malformed JSON.") from exc

    def complete_text(self, *, system: str, user: str, effort: str) -> str:
        del effort
        response = _generate(
            system=system,
            user=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_TOKENS,
            ),
        )

        text = (response.text or "").strip()
        if not text:
            raise LLMUnavailable("Gemini returned an empty response.")
        return text

    def select_tool(
        self, *, system: str, user: str, tools: list[dict], effort: str
    ) -> tuple[str | None, dict, str]:
        del effort
        declarations = [
            types.FunctionDeclaration(
                name=tool["name"],
                description=tool["description"],
                parameters_json_schema=tool["input_schema"],
            )
            for tool in tools
        ]

        response = _generate(
            system=system,
            user=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=MAX_TOKENS,
                tools=[types.Tool(function_declarations=declarations)],
                tool_config=types.ToolConfig(
                    function_calling_config=types.FunctionCallingConfig(
                        mode=types.FunctionCallingConfigMode.AUTO
                    )
                ),
                # These are declarations, not Python callables — the SDK must not try
                # to execute them. The tool layer runs the RBAC check and the query.
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            ),
        )

        text = (response.text or "").strip()
        calls = response.function_calls or []
        if calls:
            call = calls[0]
            return call.name, dict(call.args or {}), text

        return None, {}, text
