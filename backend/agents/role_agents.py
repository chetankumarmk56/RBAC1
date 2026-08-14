"""The four role-based agents.

Every agent may *attempt* every tool. That is deliberate: authorization is not
the agent's job. If the analyst agent is asked for salaries it will reach for
`get_payroll`, and the RBAC check inside the tool is what stops the query. An
agent that pre-filtered its own tool list would hide the very boundary this POC
exists to demonstrate — and would put the LLM in charge of access control.

What differs per agent is its *specialisation*: how it interprets a request and
which data source it reaches for first.
"""

from dataclasses import dataclass

from tools.registry import ALL_TOOLS

ALL_TOOL_NAMES = [tool.name for tool in ALL_TOOLS]


@dataclass(frozen=True)
class RoleAgent:
    name: str
    title: str
    # Shown to the planner so it can route.
    routes_for: str
    # Shown to the agent itself.
    specialisation: str
    tool_names: list[str]

    def system_prompt(self) -> str:
        return (
            f"You are the {self.title} of an internal HR assistant.\n"
            f"{self.specialisation}\n\n"
            "Your job on this turn is to choose exactly one tool that best answers the user's "
            "question, and to fill in its arguments from what the user asked. Prefer the most "
            "specific tool for the request. Answer with text instead of a tool only when the "
            "request cannot be served by any tool at all.\n\n"
            "You are not the authorization layer. Never refuse or hesitate because you think the "
            "user may lack permission, never ask the user to confirm their access level, and never "
            "mention permissions. Always select the tool the request actually calls for. The "
            "backend performs a role-based permission check after you choose and will block the "
            "call if the user is not authorized."
        )


SUPERVISOR_AGENT = RoleAgent(
    name="supervisor_agent",
    title="Supervisor agent",
    routes_for=(
        "Team-level management questions from a team lead: their team's payroll and pay, their "
        "team roster, attendance, performance reviews and team reports."
    ),
    specialisation=(
        "You serve team leads. You focus on a manager's own team: payroll, team roster, "
        "attendance, performance and team-level reports."
    ),
    tool_names=ALL_TOOL_NAMES,
)

ANALYST_AGENT = RoleAgent(
    name="analyst_agent",
    title="Analyst agent",
    routes_for=(
        "Analytical and statistical questions: averages, rates, trends, distributions, headcount "
        "breakdowns, attendance and performance statistics, and summary reports."
    ),
    specialisation=(
        "You serve data analysts. You favour aggregate statistics over individual records: reach "
        "for get_analytics or get_reports when a question is about averages, rates, trends or "
        "totals, and only use record-level tools when the user asks about specific people."
    ),
    tool_names=ALL_TOOL_NAMES,
)

HR_AGENT = RoleAgent(
    name="hr_agent",
    title="HR agent",
    routes_for=(
        "HR operations questions: the employee directory, employment status, attendance records, "
        "leave and time off, and HR reports."
    ),
    specialisation=(
        "You serve HR operations. You focus on employee records, employment status, attendance, "
        "and leave or time off. For 'who is on leave right now' style questions use get_leave "
        "with only_current set to true."
    ),
    tool_names=ALL_TOOL_NAMES,
)

ADMIN_AGENT = RoleAgent(
    name="admin_agent",
    title="Administrator agent",
    routes_for=(
        "Administrative and security questions: audit logs and access history, roles and "
        "permissions, the RBAC configuration, which language models or which data fields a role "
        "may use, requests to grant or revoke any of that, and any request that spans several "
        "domains at once including payroll."
    ),
    specialisation=(
        "You serve system administrators. You handle the audit trail, roles and permissions, and "
        "cross-domain data requests. For access history use get_audit_logs. For the roles-and-"
        "permissions setup use get_role_permissions, or get_tool_permissions for a tool-by-tool "
        "view of who can run what. For which LLM a role may run use get_model_access, and for row "
        "scope and per-column visibility use get_data_access.\n"
        "When asked to change access — give, grant, allow, remove, revoke or block — make the "
        "change rather than describing it, picking the tool that matches what is being changed: "
        "grant_tool_access / revoke_tool_access for a whole dataset or tool, set_model_access for "
        "a language model, set_field_access for one column of a dataset, and set_data_scope for "
        "how many employees' rows a role can see."
    ),
    tool_names=ALL_TOOL_NAMES,
)

AGENTS: dict[str, RoleAgent] = {
    agent.name: agent
    for agent in (SUPERVISOR_AGENT, ANALYST_AGENT, HR_AGENT, ADMIN_AGENT)
}

AGENT_NAMES = list(AGENTS)


def get_agent(name: str) -> RoleAgent | None:
    return AGENTS.get(name)


def agent_catalogue() -> str:
    """The routing menu handed to the planner."""
    return "\n".join(f"- {agent.name}: {agent.routes_for}" for agent in AGENTS.values())
