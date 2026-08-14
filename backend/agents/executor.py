"""Orchestration — the pipeline this POC exists to demonstrate.

    user prompt
      -> model gate: may this role run the model it asked for?   <-- RBAC, before any LLM
      -> planner: intent + agent selection
      -> role agent: tool selection
      -> RBAC check inside the tool          <-- the authorization gate on data
      -> PostgreSQL (only if the check passed)
      -> field policy strips columns the role may not see
      -> planner: final response

The planner and the agents are LLM calls, run on whichever model the caller's role
holds. The gates around them are plain Python reading permissions out of PostgreSQL.

`run_chat_events` yields a `Progress` for each stage as it happens and a final
`ChatOutcome`, so the UI can narrate the pipeline live. `run_chat` drains that
generator for callers that only want the result.
"""

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from agents.llm import LLMRun, LLMUnavailable, select_tool
from agents.planner import make_plan, write_direct_reply, write_reply
from agents.role_agents import get_agent
from rbac.audit import ALLOWED, DENIED, log_tool_request
from rbac.model_catalog import MODELS_BY_KEY, model_label, model_permission
from rbac.service import ModelAccessDenied, PermissionDenied, Principal, check_model_access
from tools.base import Tool, ToolContext, execute_tool
from tools.registry import claude_schemas, get_tool

logger = logging.getLogger(__name__)

MODEL_GATE = "model_gate"


@dataclass
class Progress:
    """One pipeline step, streamed to the client while the answer is being built."""

    stage: str  # model | planner | agent | tool | rbac | database | fields | compose | error
    text: str
    detail: dict = field(default_factory=dict)


@dataclass
class ChatOutcome:
    reply: str
    intent: str = ""
    agent: str | None = None
    reasoning: str | None = None
    tool: str | None = None
    required_permission: str | None = None
    decision: str | None = None
    reason: str | None = None
    row_count: int | None = None
    scope_note: str | None = None
    # Which LLM provider(s) answered — 'claude', or 'gemini' after a fallback.
    provider: str | None = None
    # Which model(s) answered — 'claude-sonnet', or a chain after a fallback.
    model: str | None = None
    # Columns the field-level policy removed before the agent saw the data.
    withheld_fields: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)


def _denial_fallback(tool: Tool, role: str) -> str:
    """Deterministic denial text, used when no LLM is reachable.

    A permission refusal must never depend on a model being available.
    """
    if tool.mutates:
        lead = "You don't have permission to change access permissions."
    else:
        lead = f"You don't have permission to access {tool.domain or tool.name} information."
    return (
        f"{lead} Your role ({role}) does not include the '{tool.required_permission}' "
        "permission. Contact an administrator if you need access."
    )


def _stamp(run: LLMRun, outcome: ChatOutcome) -> ChatOutcome:
    """Record which model(s) and provider(s) served this request."""
    used = run.models_answered()
    outcome.model = " -> ".join(used) if used else outcome.model
    providers = run.providers_answered()
    outcome.provider = ", ".join(providers) if providers else None
    return outcome


def _model_refusal(principal: Principal, requested: str | None) -> str:
    """Deterministic refusal text for a model the role may not run.

    Written here rather than by an LLM: the caller has just been refused the model
    they asked for, and a role with no model at all has nothing to write it with.
    """
    allowed = ", ".join(model_label(key) for key in principal.models)
    if not principal.models:
        return (
            f"No language model is enabled for your role ({principal.role}), so the assistant "
            "cannot answer. Ask a super admin to grant your role a model on the Access control page."
        )

    lead = (
        f"Your role ({principal.role}) is not allowed to use {model_label(requested)}."
        if requested in MODELS_BY_KEY
        else f"There is no model called '{requested}'."
    )
    return (
        f"{lead} Models you can use: {allowed}. Choose one of those in the model picker and send "
        "your message again."
    )


def run_chat_events(
    db: Session, principal: Principal, message: str, model: str | None = None
) -> Iterator[Progress | ChatOutcome]:
    """Run one user message through the pipeline, narrating each stage.

    `model` is the model the user picked, or None to use the most capable one their
    role holds. Either way the choice is checked against the role before any LLM runs.
    """
    message = message.strip()
    requested = (model or "").strip() or None
    steps = ["User -> Model gate"]
    # One LLM session for this message, passed explicitly: the streaming endpoint
    # resumes this generator from a thread pool, so per-request state kept in a
    # ContextVar would not survive the yields.
    run = LLMRun()

    # 0. Model gate. A model the role does not hold is refused here — no provider is
    #    called, and the refusal is audited like any other RBAC decision.
    try:
        if requested is not None:
            check_model_access(principal, requested)
        elif not principal.models:
            raise ModelAccessDenied("none", principal.role, [])
    except ModelAccessDenied as exc:
        steps.append(f"Model gate -> DENIED ({exc.model_key})")
        yield Progress(
            "model",
            f"Model access denied — {model_label(exc.model_key)}. No model was called.",
            {"decision": DENIED, "model": exc.model_key},
        )
        log_tool_request(
            db,
            principal,
            agent=MODEL_GATE,
            tool=None,
            required_permission=model_permission(exc.model_key),
            decision=DENIED,
            reason=str(exc),
            request_summary=message,
        )
        yield _stamp(
            run,
            ChatOutcome(
                reply=_model_refusal(principal, requested),
                agent=MODEL_GATE,
                required_permission=model_permission(exc.model_key),
                decision=DENIED,
                reason=str(exc),
                model=exc.model_key,
                steps=steps,
            )
        )
        return

    # The picked model first, then the rest of the role's models as fallbacks.
    others = [key for key in principal.models if key != requested]
    chain = [requested, *others] if requested else others
    run = LLMRun.of(chain)
    steps.append(f"Model gate -> ALLOWED ({chain[0]})")
    steps.append("Model gate -> Planner")
    yield Progress(
        "model",
        f"{model_label(chain[0])} allowed for {principal.role}",
        {"decision": ALLOWED, "model": chain[0]},
    )

    # 1. Planner: intent + routing.
    yield Progress("planner", "Planner agent is thinking")
    try:
        plan = make_plan(run, message)
    except LLMUnavailable as exc:
        logger.warning("planner unavailable: %s", exc)
        yield Progress("error", "No language model is reachable")
        yield _stamp(run, ChatOutcome(reply=f"The assistant is unavailable right now. {exc}", steps=steps))
        return

    intent = plan.get("intent", "")
    agent_name = plan.get("agent", "none")
    reasoning = plan.get("reasoning")

    if agent_name == "none":
        steps.append("Planner -> direct answer (no data needed)")
        yield Progress("compose", "Answering directly")
        try:
            reply = write_direct_reply(run, message)
        except LLMUnavailable as exc:
            reply = f"The assistant is unavailable right now. {exc}"
        yield _stamp(run, ChatOutcome(reply=reply, intent=intent, reasoning=reasoning, steps=steps))
        return

    agent = get_agent(agent_name)
    if agent is None:  # pragma: no cover — the plan schema constrains this
        yield _stamp(
            run,
            ChatOutcome(
                reply="The planner selected an unknown agent. Please rephrase your question.",
                intent=intent,
                steps=steps,
            )
        )
        return
    steps.append(f"Planner -> {agent_name}")

    # 2. Role agent: tool selection. The agent may attempt any tool; RBAC decides.
    yield Progress(
        "agent",
        f"{agent.title} selected",
        {"agent": agent_name, "intent": intent},
    )
    try:
        tool_name, tool_input, agent_text = select_tool(
            run,
            system=agent.system_prompt(),
            user=message,
            tools=claude_schemas(agent.tool_names),
        )
    except LLMUnavailable as exc:
        logger.warning("agent unavailable: %s", exc)
        yield Progress("error", "No language model is reachable")
        yield _stamp(
            run,
            ChatOutcome(
                reply=f"The assistant is unavailable right now. {exc}",
                intent=intent,
                agent=agent_name,
                reasoning=reasoning,
                steps=steps,
            )
        )
        return

    if tool_name is None:
        steps.append(f"{agent_name} -> no tool matched")
        yield Progress("agent", "No tool matched this request")
        yield _stamp(
            run,
            ChatOutcome(
                reply=agent_text or "I couldn't map that to any data I can look up. Could you rephrase?",
                intent=intent,
                agent=agent_name,
                reasoning=reasoning,
                steps=steps,
            )
        )
        return

    tool = get_tool(tool_name)
    if tool is None:  # pragma: no cover — tool names come from the schemas we sent
        yield _stamp(
            run,
            ChatOutcome(
                reply="The agent asked for a tool that doesn't exist. Please rephrase your question.",
                intent=intent,
                agent=agent_name,
                reasoning=reasoning,
                steps=steps,
            )
        )
        return
    steps.append(f"{agent_name} -> {tool.name}")

    yield Progress(
        "tool",
        f"{tool.name} tool selected",
        {"tool": tool.name, "required_permission": tool.required_permission},
    )

    # 3. RBAC check, then PostgreSQL. execute_tool raises before issuing any query.
    ctx = ToolContext(db=db, principal=principal)
    try:
        result = execute_tool(tool, ctx, tool_input)
    except PermissionDenied as exc:
        steps.append(f"RBAC -> DENIED ({tool.required_permission})")
        steps.append("Database -> not queried")
        yield Progress(
            "rbac",
            f"Permission denied — {tool.required_permission}. Database not queried.",
            {"decision": DENIED, "required_permission": tool.required_permission},
        )
        log_tool_request(
            db,
            principal,
            agent=agent_name,
            tool=tool.name,
            required_permission=tool.required_permission,
            decision=DENIED,
            reason=str(exc),
            request_summary=message,
        )
        yield Progress("compose", "Writing the answer")
        try:
            reply = write_reply(
                run,
                message=message,
                intent=intent,
                agent=agent_name,
                tool=tool.name,
                required_permission=tool.required_permission,
                decision=DENIED,
                denial_reason=str(exc),
                role=principal.role,
            )
        except LLMUnavailable:
            reply = _denial_fallback(tool, principal.role)

        yield _stamp(
            run,
            ChatOutcome(
                reply=reply,
                intent=intent,
                agent=agent_name,
                reasoning=reasoning,
                tool=tool.name,
                required_permission=tool.required_permission,
                decision=DENIED,
                reason=str(exc),
                row_count=0,
                steps=steps,
            )
        )
        return

    steps.append(f"RBAC -> ALLOWED ({tool.required_permission})")
    steps.append(f"Database -> {result.row_count} row(s)")
    yield Progress(
        "rbac",
        f"Permission granted — {tool.required_permission}",
        {"decision": ALLOWED, "required_permission": tool.required_permission},
    )
    yield Progress(
        "database",
        f"Queried PostgreSQL — {result.row_count} row(s)",
        {"row_count": result.row_count},
    )
    if result.withheld_fields:
        steps.append(f"Field policy -> {len(result.withheld_fields)} field(s) withheld")
        yield Progress(
            "fields",
            f"Field policy withheld {', '.join(result.withheld_fields)}",
            {"withheld_fields": result.withheld_fields},
        )
    log_tool_request(
        db,
        principal,
        agent=agent_name,
        tool=tool.name,
        required_permission=tool.required_permission,
        decision=ALLOWED,
        reason=result.audit_note or f"{result.row_count} row(s) returned",
        request_summary=message,
    )

    # 4. Planner writes the final response from the tool output.
    steps.append(f"{tool.name} -> {agent_name} -> Planner")
    yield Progress("compose", "Writing the answer")
    try:
        reply = write_reply(
            run,
            message=message,
            intent=intent,
            agent=agent_name,
            tool=tool.name,
            required_permission=tool.required_permission,
            decision=ALLOWED,
            role=principal.role,
            tool_summary=result.summary,
            tool_data=result.data,
            scope_note=result.scope_note,
            withheld_fields=result.withheld_fields,
        )
    except LLMUnavailable as exc:
        logger.warning("responder unavailable: %s", exc)
        reply = result.summary

    yield _stamp(
        run,
        ChatOutcome(
            reply=reply,
            intent=intent,
            agent=agent_name,
            reasoning=reasoning,
            tool=tool.name,
            required_permission=tool.required_permission,
            decision=ALLOWED,
            reason=result.summary,
            row_count=result.row_count,
            scope_note=result.scope_note,
            withheld_fields=result.withheld_fields,
            steps=steps,
        )
    )


def run_chat(
    db: Session, principal: Principal, message: str, model: str | None = None
) -> ChatOutcome:
    """Run the pipeline and return only the final outcome."""
    outcome: ChatOutcome | None = None
    for item in run_chat_events(db, principal, message, model):
        if isinstance(item, ChatOutcome):
            outcome = item

    if outcome is None:  # pragma: no cover — every path yields an outcome
        outcome = _stamp(LLMRun(), ChatOutcome(reply="The assistant produced no response."))
    return outcome
