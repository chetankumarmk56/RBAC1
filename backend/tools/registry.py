"""The tool catalogue."""

from tools.admin_tools import (
    AUDIT_TOOL,
    DATA_ACCESS_TOOL,
    GRANT_TOOL_ACCESS_TOOL,
    MODEL_ACCESS_TOOL,
    REVOKE_TOOL_ACCESS_TOOL,
    ROLE_PERMISSIONS_TOOL,
    SET_DATA_SCOPE_TOOL,
    SET_FIELD_ACCESS_TOOL,
    SET_MODEL_ACCESS_TOOL,
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
    MODEL_ACCESS_TOOL,
    DATA_ACCESS_TOOL,
    GRANT_TOOL_ACCESS_TOOL,
    REVOKE_TOOL_ACCESS_TOOL,
    SET_MODEL_ACCESS_TOOL,
    SET_FIELD_ACCESS_TOOL,
    SET_DATA_SCOPE_TOOL,
]

TOOLS_BY_NAME: dict[str, Tool] = {tool.name: tool for tool in ALL_TOOLS}

DATASET_TOOLS: dict[str, Tool] = {tool.dataset: tool for tool in ALL_TOOLS if tool.dataset}


def get_tool(name: str) -> Tool | None:
    return TOOLS_BY_NAME.get(name)


def tool_for_dataset(dataset_key: str) -> Tool | None:
    """The tool that reads a dataset — the console toggles datasets through it."""
    return DATASET_TOOLS.get(dataset_key)


def claude_schemas(names: list[str]) -> list[dict]:
    """Claude-facing tool definitions for the given tool names, in catalogue order."""
    wanted = set(names)
    return [tool.to_claude_schema() for tool in ALL_TOOLS if tool.name in wanted]
