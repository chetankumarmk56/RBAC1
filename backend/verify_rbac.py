"""Verify the RBAC gate by calling every tool as every user, with no LLM involved.

Three checks:
  1. Enforcement — every tool's allow/deny outcome matches the caller's permissions
     as stored in PostgreSQL.
  2. No leakage — a denied call issues zero SQL statements, so the database is
     genuinely never reached.
  3. Scope — a supervisor sees fewer employee rows than an admin.

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
from rbac.permissions import ROLE_PERMISSIONS
from rbac.service import PermissionDenied, load_principal
from tools.base import ToolContext, execute_tool
from tools.registry import ALL_TOOLS

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
        print(f"scope     : supervisor sees {supervisor_rows} employees, admin sees {admin_rows}")
        if supervisor_rows >= admin_rows:
            failures.append("supervisor scope is not narrower than admin scope")

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
