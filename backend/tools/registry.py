"""The tool catalogue."""

from tools.admin_tools import (
    AUDIT_TOOL,
    GRANT_TOOL_ACCESS_TOOL,
    REVOKE_TOOL_ACCESS_TOOL,
    ROLE_PERMISSIONS_TOOL,
    TOOL_PERMISSIONS_TOOL,
)
from tools.analytics_tools import ANALYTICS_TOOL, REPORTS_TOOL
from tools.base import Tool
from tools.hr_tools import ATTENDANCE_TOOL, EMPLOYEE_TOOL, LEAVE_TOOL, PERFORMANCE_TOOL
from tools.payroll_tools import PAYROLL_TOOL

ALL_TOOLS: list[Tool] = [
    PAYROLL_TOOL,
    EMPLOYEE_TOOL,
    ATTENDANCE_TOOL,
    PERFORMANCE_TOOL,
    LEAVE_TOOL,
    ANALYTICS_TOOL,
    REPORTS_TOOL,
    AUDIT_TOOL,
    ROLE_PERMISSIONS_TOOL,
    TOOL_PERMISSIONS_TOOL,
    GRANT_TOOL_ACCESS_TOOL,
    REVOKE_TOOL_ACCESS_TOOL,
]

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}


def get_tool(name: str) -> Tool | None:
    return TOOLS_BY_NAME.get(name)


def claude_schemas(names: list[str]) -> list[dict]:
    """Claude-facing tool definitions for the given tool names, in catalogue order."""
    wanted = set(names)
    return [tool.to_claude_schema() for tool in ALL_TOOLS if tool.name in wanted]
