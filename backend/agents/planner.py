"""The planner agent.

Two responsibilities:
  1. Read the user's prompt, name the intent, and pick the agent to handle it.
  2. Turn whatever came back — data or a permission denial — into the final reply.

The planner never sees the caller's permissions and never decides access. It only
routes and writes.
"""

import json

from agents.llm import LLMRun, complete_json, complete_text
from agents.role_agents import AGENT_NAMES, agent_catalogue

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "description": "One short sentence naming what the user wants, in your own words.",
        },
        "agent": {
            "type": "string",
            "enum": [*AGENT_NAMES, "none"],
            "description": "The agent that should handle this request, or 'none' if no data is needed.",
        },
        "reasoning": {
            "type": "string",
            "description": "One short sentence on why that agent fits.",
        },
    },
    "required": ["intent", "agent", "reasoning"],
    "additionalProperties": False,
}

_PLANNER_SYSTEM = f"""You are the planner of an internal HR assistant. You read the user's message, \
identify the intent, and route it to exactly one specialised agent.

Available agents:
{agent_catalogue()}

Routing rules, in order — the first rule that matches wins:
- Route on the *subject* of the question, not on whether you think the user should be allowed to \
ask it. Access control happens later, in the backend, and is not your concern.
- Requests to change who can access what — grant, give, allow, enable, revoke, remove, block, \
limit, restrict, narrow or widen a *role's* access to data, to a column, to a language model, or \
to how many employees' rows it reaches: admin_agent. This rule wins over the subject rules below, \
so "give the supervisor access to salary" is a change request, not a payroll question.
- Payroll, salary, pay and compensation questions: route to supervisor_agent when the user frames \
it around their own team, otherwise admin_agent.
- Statistics, averages, rates and trends: analyst_agent.
- Leave, time off, employee records and employment status: hr_agent.
- Audit logs, roles, permissions and access history: admin_agent.
- Use "none" only for greetings, small talk, or questions about the assistant itself — never as a \
way to avoid a sensitive topic."""

_RESPONDER_SYSTEM = """You are the planner of an internal HR assistant, writing the final reply to \
the user. A role-based agent has already run one tool on your behalf and handed you the outcome.

If the outcome is DENIED:
- Say plainly, in one or two sentences, that the user does not have permission to access that kind \
of information. Name the data type in plain words, e.g. "payroll information".
- You may mention which permission their role is missing. Suggest contacting an administrator.
- Do not speculate about what the data would have said. Do not apologise more than once.

If the outcome is ALLOWED:
- Answer the question using only the data provided. Never invent, estimate or extrapolate a figure \
that is not in the data.
- Lead with the answer. Keep it short: a sentence or two for a single fact, a compact markdown \
table when listing several records.
- Format money with a currency symbol and thousands separators, and percentages with one decimal.
- If a scope note is present, mention the limit in one short clause (e.g. "across your team").
- If fields were withheld, name them once in a short closing clause — e.g. "salary figures are \
not available to your role" — and never estimate, infer or reconstruct a withheld value from \
anything else in the data.
- If the data is empty, say so directly rather than implying a problem occurred.
- If a limitation is marked MUST INCLUDE, state it in full before any table or list, in the \
user's own terms. It is the part of the outcome they are most likely to act on, and a reply that \
reads as "done" while omitting it is wrong even if every other sentence is accurate. Never \
summarise it away, and never end on "no change was needed" when a limitation is present.

Write in plain prose. No preamble, no restating the question, no offers of further help."""


def make_plan(run: LLMRun, message: str) -> dict:
    """Classify intent and choose an agent."""
    plan = complete_json(
        run,
        system=_PLANNER_SYSTEM,
        user=message,
        schema=PLAN_SCHEMA,
        effort="low",
    )
    if plan.get("agent") not in [*AGENT_NAMES, "none"]:
        plan["agent"] = "none"
    return plan


def _truncate(payload: str, limit: int = 12000) -> str:
    if len(payload) <= limit:
        return payload
    return payload[:limit] + "\n… (truncated)"


def write_reply(
    run: LLMRun,
    *,
    message: str,
    intent: str,
    agent: str,
    tool: str | None,
    required_permission: str | None,
    decision: str,
    denial_reason: str | None = None,
    role: str | None = None,
    tool_summary: str | None = None,
    tool_data: object = None,
    scope_note: str | None = None,
    withheld_fields: list[str] | None = None,
    caveat: str | None = None,
) -> str:
    """Compose the user-facing answer from the tool outcome."""
    lines = [
        f"User asked: {message}",
        f"Detected intent: {intent}",
        f"Handled by: {agent}",
        f"Tool attempted: {tool or 'none'}",
        f"Permission required: {required_permission or 'n/a'}",
        f"Caller's role: {role or 'unknown'}",
        f"RBAC outcome: {decision}",
    ]
    if denial_reason:
        lines.append(f"Denial reason: {denial_reason}")
    if scope_note:
        lines.append(f"Scope note: {scope_note}")
    if withheld_fields:
        lines.append(
            "Fields withheld from this role by the field-level policy (they are absent from "
            f"the data below and must not be guessed): {', '.join(withheld_fields)}"
        )
    if caveat:
        lines.append(f"MUST INCLUDE — limitation on what just happened: {caveat}")
    if tool_summary:
        lines.append(f"Tool summary: {tool_summary}")
    if tool_data is not None:
        rendered = json.dumps(tool_data, indent=2, default=str, ensure_ascii=False)
        lines.append(f"Tool data (JSON):\n{_truncate(rendered)}")

    return complete_text(run, system=_RESPONDER_SYSTEM, user="\n".join(lines), effort="low")


_SMALL_TALK_SYSTEM = """You are an internal HR assistant. The user's message does not require any \
HR data, so answer directly and briefly.

You can answer questions about employees, attendance, performance, leave, payroll, analytics, \
reports, audit logs, and role permissions — but what each user actually receives depends on their \
role's permissions, which the backend enforces. Keep replies to a couple of sentences. Do not \
invent HR data."""


def write_direct_reply(run: LLMRun, message: str) -> str:
    """Answer a greeting or meta question without touching any tool."""
    return complete_text(run, system=_SMALL_TALK_SYSTEM, user=message, effort="low")
