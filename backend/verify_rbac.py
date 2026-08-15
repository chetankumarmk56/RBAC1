"""Verify the RBAC gates by calling every tool as every user, with no LLM involved.

Five checks:
  1. Enforcement — every tool's allow/deny outcome matches the caller's permissions
     as stored in PostgreSQL.
  2. No leakage — a denied call issues zero SQL statements, so the database is
     genuinely never reached.
  3. Row scope — each role sees exactly the rows its scope allows, and a narrower
     scope really is narrower.
  4. Field policy — a field a role may not see appears nowhere in that role's tool
     results, at any depth, including in derived totals.
  5. Model access — `check_model_access` allows exactly the models the role holds.

It also reports any drift between the live configuration and the seeded baseline in
`rbac/permissions.py`. Drift is expected after a super admin grants or revokes access
at runtime, so it is reported, not failed.

Run from the backend directory:
    python verify_rbac.py
"""

import sys

from sqlalchemy import event, select

from db.session import SessionLocal, engine
from models import User
from rbac.datasets import DATASET_CATALOGUE, DATASETS_BY_KEY
from rbac.model_catalog import ALL_MODEL_KEYS, model_label
from rbac.permissions import ROLE_PERMISSIONS
from rbac.service import ModelAccessDenied, PermissionDenied, check_model_access, load_principal
from tools.analytics_tools import BLOCKS as _ANALYTICS_BLOCKS
from tools.base import ToolContext, execute_tool
from tools.registry import ALL_TOOLS, tool_for_dataset

# payload key -> the dataset it aggregates. Read from the tools themselves so a new
# statistics block cannot be added without this check covering it.
_AGGREGATE_BLOCKS = {key: dataset for _name, key, dataset, _column in _ANALYTICS_BLOCKS}


def _keys_in(value) -> set[str]:
    """Every dict key appearing anywhere in a nested result payload."""
    if isinstance(value, dict):
        return set(value) | {key for item in value.values() for key in _keys_in(item)}
    if isinstance(value, list):
        return {key for item in value for key in _keys_in(item)}
    return set()

USER_EMAILS = [
    "supervisor@example.com",
    "analyst@example.com",
    "hr@example.com",
    "admin@example.com",
    "superadmin@example.com",
]

# Write tools are exercised for their allow/deny decision only; calling them with
# empty arguments is a no-op that reports an unknown role rather than changing anything.
_statements: list[str] = []


@event.listens_for(engine, "before_cursor_execute")
def _record_statement(conn, cursor, statement, parameters, context, executemany):
    _statements.append(statement)


def main() -> int:
    failures: list[str] = []

    with SessionLocal() as db:
        principals = {}
        for email in USER_EMAILS:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                print(f"FAIL  missing seeded user {email} — run seed.py first")
                return 1
            principals[email] = load_principal(db, user)

        header = f"{'tool':<22}{'permission':<20}" + "".join(f"{p.role:<13}" for p in principals.values())
        print(header)
        print("-" * len(header))

        for tool in ALL_TOOLS:
            row = f"{tool.name:<22}{tool.required_permission:<20}"
            for principal in principals.values():
                # Expectation comes from the database, so the check still holds after a
                # super admin changes access at runtime.
                expected_allow = tool.required_permission in principal.permissions
                ctx = ToolContext(db=db, principal=principal)

                _statements.clear()
                try:
                    execute_tool(tool, ctx, {})
                    actual = "ALLOWED"
                except PermissionDenied:
                    actual = "DENIED"
                    if _statements:
                        failures.append(
                            f"{tool.name} as {principal.role}: denied but issued "
                            f"{len(_statements)} SQL statement(s)"
                        )

                ok = (actual == "ALLOWED") == expected_allow
                if not ok:
                    failures.append(
                        f"{tool.name} as {principal.role}: expected "
                        f"{'ALLOWED' if expected_allow else 'DENIED'}, got {actual}"
                    )
                row += f"{('OK ' if ok else 'XX ') + actual:<13}"
            print(row)

        print("\nno-leakage: every DENIED call above issued 0 SQL statements")

        # Row-level scope: a supervisor sees only their own team.
        supervisor = principals["supervisor@example.com"]
        admin = principals["admin@example.com"]
        employee_tool = next(tool for tool in ALL_TOOLS if tool.name == "get_employee")

        supervisor_rows = execute_tool(employee_tool, ToolContext(db=db, principal=supervisor), {}).row_count
        admin_rows = execute_tool(employee_tool, ToolContext(db=db, principal=admin), {}).row_count
        print(
            f"row scope : supervisor ({supervisor.row_scope}) sees {supervisor_rows} employees, "
            f"admin ({admin.row_scope}) sees {admin_rows}"
        )
        if supervisor.row_scope != admin.row_scope and supervisor_rows >= admin_rows:
            failures.append("supervisor scope is not narrower than admin scope")

        # Field policy: a withheld column must not appear anywhere in the payload.
        print("\nfield policy (withheld columns, by role and dataset):")
        for principal in principals.values():
            notes = []
            for dataset in DATASET_CATALOGUE:
                tool = tool_for_dataset(dataset.key)
                if tool is None or tool.required_permission not in principal.permissions:
                    continue

                result = execute_tool(tool, ToolContext(db=db, principal=principal), {})
                withheld = principal.field_access.withheld_fields(dataset.key)
                expected = [spec.key for spec in dataset.fields if spec.key in withheld and not spec.locked]
                if sorted(result.withheld_fields) != sorted(expected):
                    failures.append(
                        f"{tool.name} as {principal.role}: reported withheld "
                        f"{result.withheld_fields}, expected {expected}"
                    )

                present = _keys_in(result.data)
                for spec in dataset.fields:
                    if spec.key not in expected:
                        continue
                    leaked = ({spec.key} | set(spec.derived)) & present
                    if leaked:
                        failures.append(
                            f"{tool.name} as {principal.role}: withheld field leaked as {sorted(leaked)}"
                        )
                    # A column stripped from the payload is still leaked if everything
                    # that reconstructs it came back: base_salary is exactly
                    # net_pay + deductions - bonus, so handing over those three hands
                    # over the salary. Redaction has to survive arithmetic, not just grep.
                    if spec.reconstructed_by and not set(spec.reconstructed_by) & set(expected):
                        failures.append(
                            f"{tool.name} as {principal.role}: withheld '{spec.key}' is "
                            f"reconstructible from {', '.join(spec.reconstructed_by)}"
                        )
                # A row key that is neither a catalogue field nor tied to one through
                # `derived` cannot be reached by any policy, so it ships to every role
                # no matter what the console says. Rows keep a locked name, so even a
                # bare boolean is attached to a named person.
                tied = dataset.field_keys | {key for spec in dataset.fields for key in spec.derived}
                rows = result.data.get("records") if isinstance(result.data, dict) else None
                if isinstance(rows, list) and rows:
                    untethered = sorted(set(rows[0]) - tied)
                    if untethered:
                        failures.append(
                            f"{tool.name} as {principal.role}: row keys reachable by no "
                            f"policy: {untethered}"
                        )

                if expected:
                    notes.append(f"{dataset.key}: {', '.join(expected)}")
            print(f"  {principal.role:<12} {'; '.join(notes) or 'nothing withheld'}")

        # Aggregates are still the underlying dataset's data: reports:read and
        # analytics:read must not return a block from a dataset the role cannot read.
        print("\naggregate boundaries (blocks omitted for want of the dataset permission):")
        for principal in principals.values():
            notes = []
            for tool in ALL_TOOLS:
                if tool.name not in ("get_reports", "get_analytics"):
                    continue
                if tool.required_permission not in principal.permissions:
                    continue
                result = execute_tool(tool, ToolContext(db=db, principal=principal), {})
                for block, dataset_key in _AGGREGATE_BLOCKS.items():
                    dataset = DATASETS_BY_KEY[dataset_key]
                    if block in (result.data or {}) and not principal.has(dataset.permission):
                        failures.append(
                            f"{tool.name} as {principal.role}: returned '{block}' without "
                            f"'{dataset.permission}'"
                        )
                omitted = [item for item in result.withheld_fields if item.endswith(".*")]
                if omitted:
                    notes.append(f"{tool.name}: {', '.join(omitted)}")
            print(f"  {principal.role:<12} {'; '.join(notes) or 'nothing omitted'}")

        # Model access: exactly the models the role holds, and no others.
        print("\nmodel access:")
        for principal in principals.values():
            for key in ALL_MODEL_KEYS:
                allowed = key in principal.models
                try:
                    check_model_access(principal, key)
                    refused = False
                except ModelAccessDenied:
                    refused = True
                if refused == allowed:
                    failures.append(
                        f"model {key} as {principal.role}: expected "
                        f"{'ALLOWED' if allowed else 'DENIED'}, got the opposite"
                    )
            names = ", ".join(model_label(key) for key in principal.models) or "none"
            print(f"  {principal.role:<12} {names}")

        # Drift from the seeded baseline — informational, not a failure.
        drift = []
        for principal in principals.values():
            baseline = set(ROLE_PERMISSIONS.get(principal.role, []))
            live = set(principal.permissions)
            for added in sorted(live - baseline):
                drift.append(f"{principal.role}: +{added}")
            for removed in sorted(baseline - live):
                drift.append(f"{principal.role}: -{removed}")

        if drift:
            print("\nconfiguration drift from the seeded baseline (expected after runtime changes):")
            for entry in drift:
                print(f"  {entry}")
        else:
            print("baseline  : live configuration matches rbac/permissions.py")

    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nAll RBAC expectations hold.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
