"""What every LLM provider must offer.

The agents need exactly three call shapes: a JSON-schema-constrained call (the
planner's routing decision), a plain-text call (the final response), and a tool-use
call (an agent picking a tool). A provider implements those three and reports whether
it is configured.
"""

from typing import Protocol

MAX_TOKENS = 8000


class LLMUnavailable(RuntimeError):
    """A provider could not produce a usable response.

    Raised for operational failures — missing credentials, network errors, rate
    limits, server errors, refusals, malformed output. The façade in `llm.py` treats
    it as the signal to try the next provider.
    """


class Provider(Protocol):
    """Structural interface. Implementations live in `provider_claude` / `provider_gemini`."""

    name: str

    def configured(self) -> bool:
        """True when this provider has the credentials it needs."""
        ...

    def complete_json(self, *, system: str, user: str, schema: dict, effort: str) -> dict:
        """Return a dict matching `schema`."""
        ...

    def complete_text(self, *, system: str, user: str, effort: str) -> str:
        """Return a plain-text answer."""
        ...

    def select_tool(
        self, *, system: str, user: str, tools: list[dict], effort: str
    ) -> tuple[str | None, dict, str]:
        """Pick at most one tool.

        Returns (tool_name, tool_input, assistant_text); tool_name is None when the
        model answered with text instead of choosing a tool. `tools` uses the Claude
        shape — `{"name", "description", "input_schema"}` — and a provider adapts it.
        """
        ...
