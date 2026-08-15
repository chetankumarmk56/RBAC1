"""The data catalogue — which data exists, and how finely a role may see it.

Data access is decided at three levels, all enforced server-side:

  1. **Dataset** — may the role read this data at all? Backed by the same
     permission the dataset's tool requires, so ticking a dataset in the console and
     granting the tool in chat are the same write to `role_permissions`.
  2. **Field** — which columns of that dataset come back. Stored in
     `role_field_access`; enforced centrally in `tools.base.execute_tool`, after the
     handler and before the agent ever sees the rows. Withholding a column also
     withholds anything that reconstructs it — the aggregates computed from it
     (`FieldSpec.derived`) and the sibling columns that together determine it
     (`FieldSpec.reconstructed_by`) — so a redacted figure cannot be recovered by
     arithmetic from what is left.
  3. **Row** — which employees' rows are in scope at all. Stored in
     `role_data_scope`; enforced by `tools.base.visible_employee_ids`.

Field keys below are the keys the tools actually emit. A field that is not in this
catalogue is never redacted, so adding a key to a tool's output means adding it here
too if it should be controllable.
"""

from dataclasses import dataclass, field

from rbac.permissions import (
    ATTENDANCE_READ,
    EMPLOYEE_READ,
    LEAVE_READ,
    PAYROLL_READ,
    PERFORMANCE_READ,
    ROLE_ADMIN,
    ROLE_ANALYST,
    ROLE_HR,
    ROLE_SUPER_ADMIN,
    ROLE_SUPERVISOR,
)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    # Identity columns that keep a row readable. They cannot be switched off, or
    # every row would come back anonymous and the answer would be worthless.
    locked: bool = False
    # Aggregates computed from this field elsewhere in the payload. Withholding the
    # field withholds these too, so a redacted column cannot be read back off a total.
    derived: tuple[str, ...] = ()
    # Sibling columns that *together* determine this one exactly. A role holding all
    # of them can compute this field with arithmetic, so granting them is the same as
    # granting this field — see `DatasetSpec.effective_withheld`. Listing a partial
    # set would be wrong: the relationship is "all of these", not "any of these".
    reconstructed_by: tuple[str, ...] = ()


@dataclass(frozen=True)
class DatasetSpec:
    key: str
    label: str
    permission: str  # the dataset-level gate, shared with the tool
    tool: str  # the tool that reads it
    blurb: str
    fields: list[FieldSpec] = field(default_factory=list)

    @property
    def field_keys(self) -> set[str]:
        return {spec.key for spec in self.fields}

    @property
    def locked_keys(self) -> set[str]:
        return {spec.key for spec in self.fields if spec.locked}

    @property
    def configurable_fields(self) -> list[FieldSpec]:
        return [spec for spec in self.fields if not spec.locked]

    def effective_withheld(self, granted: set[str]) -> set[str]:
        """Field keys a role holding `granted` does not get from this dataset.

        Starts from the fields simply not granted, then closes over reconstruction:
        when every column that determines a withheld field is visible, the withheld
        figure is one subtraction away, so those columns are withheld too. Payroll is
        the case that matters — `net_pay + deductions - bonus` is `base_salary`
        exactly, so withholding base salary while leaving the other three in place
        withholds nothing at all.

        Locked columns are never withheld; they identify the row.
        """
        withheld = {spec.key for spec in self.fields if not spec.locked and spec.key not in granted}
        by_key = {spec.key: spec for spec in self.fields}

        changed = True
        while changed:
            changed = False
            for key in list(withheld):
                spec = by_key.get(key)
                if spec is None or not spec.reconstructed_by:
                    continue
                siblings = [by_key[name] for name in spec.reconstructed_by if name in by_key]
                # Only a *complete* set reconstructs the field. If one of them is
                # already withheld the sum cannot be solved, and the rest stay visible.
                if not siblings or any(sibling.key in withheld for sibling in siblings):
                    continue
                for sibling in siblings:
                    if not sibling.locked:
                        withheld.add(sibling.key)
                        changed = True
        return withheld


PAYROLL_DATA = DatasetSpec(
    key="payroll",
    label="Payroll",
    permission=PAYROLL_READ,
    tool="get_payroll",
    blurb="Salary, bonus, deductions and net pay per employee per period.",
    fields=[
        FieldSpec("name", "Employee name", locked=True),
        FieldSpec("department", "Department"),
        FieldSpec("title", "Job title"),
        FieldSpec("period", "Pay period"),
        # The four money columns satisfy net_pay = base_salary + bonus - deductions,
        # so any three of them give the fourth exactly. Each therefore names the other
        # three: withholding one of the four withholds all four, and there is no
        # arrangement that hands a role three of them and calls the fourth private.
        # Withholding *two* is still allowed — that leaves only their difference
        # derivable, which is how HR sees base pay without the bonus/deduction split.
        FieldSpec("base_salary", "Base salary", reconstructed_by=("bonus", "deductions", "net_pay")),
        FieldSpec("bonus", "Bonus", reconstructed_by=("base_salary", "deductions", "net_pay")),
        FieldSpec("deductions", "Deductions", reconstructed_by=("base_salary", "bonus", "net_pay")),
        FieldSpec(
            "net_pay",
            "Net pay",
            derived=("total_net_pay",),
            reconstructed_by=("base_salary", "bonus", "deductions"),
        ),
        FieldSpec("currency", "Currency"),
    ],
)

EMPLOYEE_DATA = DatasetSpec(
    key="employees",
    label="Employee directory",
    permission=EMPLOYEE_READ,
    tool="get_employee",
    blurb="Directory records: contact details, department, title, manager, status.",
    fields=[
        FieldSpec("name", "Employee name", locked=True),
        FieldSpec("email", "Email address"),
        FieldSpec("department", "Department"),
        FieldSpec("title", "Job title"),
        FieldSpec("manager", "Manager"),
        FieldSpec("hire_date", "Hire date"),
        FieldSpec("status", "Employment status"),
    ],
)

ATTENDANCE_DATA = DatasetSpec(
    key="attendance",
    label="Attendance",
    permission=ATTENDANCE_READ,
    tool="get_attendance",
    blurb="Days present, remote, late and absent, hours worked and attendance rate.",
    fields=[
        FieldSpec("name", "Employee name", locked=True),
        FieldSpec("department", "Department"),
        # The day counts sum: total_days = present + remote + late + absent, so any
        # four of the five give the fifth (tools/hr_tools.py builds them that way).
        FieldSpec("present", "Days present", reconstructed_by=("remote", "late", "absent", "total_days")),
        FieldSpec("remote", "Days remote", reconstructed_by=("present", "late", "absent", "total_days")),
        FieldSpec("late", "Days late", reconstructed_by=("present", "remote", "absent", "total_days")),
        FieldSpec("absent", "Days absent", reconstructed_by=("present", "remote", "late", "total_days")),
        FieldSpec("total_days", "Total days", reconstructed_by=("present", "remote", "late", "absent")),
        FieldSpec("hours_worked", "Hours worked"),
        # rate = 100 * (present + remote + late) / total_days — the inputs give it exactly.
        FieldSpec(
            "attendance_rate_pct",
            "Attendance rate",
            derived=("average_rate_pct",),
            reconstructed_by=("present", "remote", "late", "total_days"),
        ),
    ],
)

PERFORMANCE_DATA = DatasetSpec(
    key="performance",
    label="Performance",
    permission=PERFORMANCE_READ,
    tool="get_performance",
    blurb="Review ratings, periods, reviewers and written feedback.",
    fields=[
        FieldSpec("name", "Employee name", locked=True),
        FieldSpec("department", "Department"),
        FieldSpec("review_period", "Review period"),
        FieldSpec("rating", "Rating", derived=("average_rating",)),
        FieldSpec("reviewer", "Reviewer"),
        FieldSpec("comments", "Review comments"),
    ],
)

LEAVE_DATA = DatasetSpec(
    key="leave",
    label="Leave",
    permission=LEAVE_READ,
    tool="get_leave",
    blurb="Leave type, dates, duration, approval status and stated reason.",
    fields=[
        FieldSpec("name", "Employee name", locked=True),
        FieldSpec("department", "Department"),
        FieldSpec("leave_type", "Leave type"),
        # start, end and duration are a triangle: any two give the third. `days` is
        # end - start + 1 in tools/hr_tools.py.
        # `currently_on_leave` is computed from the same two dates but is not a
        # catalogue field of its own, so it is named here as derived — otherwise it
        # would survive every redaction, and since the row keeps a locked name, a bare
        # "on leave today" flag still identifies the person.
        FieldSpec(
            "start_date",
            "Start date",
            derived=("currently_on_leave",),
            reconstructed_by=("end_date", "days"),
        ),
        FieldSpec(
            "end_date",
            "End date",
            derived=("currently_on_leave",),
            reconstructed_by=("start_date", "days"),
        ),
        FieldSpec("days", "Days", reconstructed_by=("start_date", "end_date")),
        FieldSpec("status", "Approval status"),
        FieldSpec("reason", "Stated reason"),
    ],
)

DATASET_CATALOGUE: list[DatasetSpec] = [
    PAYROLL_DATA,
    EMPLOYEE_DATA,
    ATTENDANCE_DATA,
    PERFORMANCE_DATA,
    LEAVE_DATA,
]

DATASETS_BY_KEY: dict[str, DatasetSpec] = {data.key: data for data in DATASET_CATALOGUE}


def get_dataset(key: str) -> DatasetSpec | None:
    return DATASETS_BY_KEY.get(key)


# --------------------------------------------------------------------------- #
# Row scope
# --------------------------------------------------------------------------- #

SCOPE_ALL = "all"
SCOPE_DEPARTMENT = "department"
SCOPE_TEAM = "team"
SCOPE_SELF = "self"

DATA_SCOPES: dict[str, str] = {
    SCOPE_ALL: "Every employee in the company.",
    SCOPE_DEPARTMENT: "Employees in the caller's own department.",
    SCOPE_TEAM: "The caller plus their direct reports.",
    SCOPE_SELF: "The caller's own record only.",
}

SCOPE_KEYS: list[str] = list(DATA_SCOPES)


def normalise_scope(value: str | None) -> str:
    scope = (value or "").strip().lower()
    return scope if scope in DATA_SCOPES else SCOPE_ALL


# --------------------------------------------------------------------------- #
# Seeded baseline — read by seed.py only. At request time everything below is
# read from PostgreSQL.
# --------------------------------------------------------------------------- #

ROLE_SCOPES: dict[str, str] = {
    ROLE_SUPERVISOR: SCOPE_TEAM,
    ROLE_ANALYST: SCOPE_ALL,
    ROLE_HR: SCOPE_ALL,
    ROLE_ADMIN: SCOPE_ALL,
    ROLE_SUPER_ADMIN: SCOPE_ALL,
}

# Fields a role does *not* get. Everything not listed here is granted, so a new
# field is visible by default and has to be taken away deliberately.
ROLE_FIELD_DENIALS: dict[str, dict[str, list[str]]] = {
    ROLE_SUPERVISOR: {
        # A manager sees that someone is on leave, not why.
        "leave": ["reason"],
    },
    ROLE_ANALYST: {
        # Analysts work in aggregate: no direct contact details, no compensation
        # figures even if the payroll dataset is granted to them at runtime, and no
        # named feedback.
        # `manager` goes with `reviewer`: seed.py sets a review's reviewer to the
        # employee's manager, so leaving the directory's manager column visible would
        # hand back the reviewer name this baseline just denied. The reconstruction
        # closure in DatasetSpec.effective_withheld is per-dataset and cannot see a
        # relationship that spans two, so this one is closed here in the baseline.
        "employees": ["email", "manager"],
        "payroll": ["base_salary", "bonus", "deductions", "net_pay"],
        "performance": ["reviewer", "comments"],
        "leave": ["reason"],
    },
    ROLE_HR: {
        # HR operations sees base pay, not the bonus and deduction breakdown.
        "payroll": ["bonus", "deductions"],
    },
    ROLE_ADMIN: {},
    ROLE_SUPER_ADMIN: {},
}


def seeded_fields_for(role: str) -> list[tuple[str, str]]:
    """(dataset_key, field_key) pairs granted to `role` in the seeded baseline."""
    denied = ROLE_FIELD_DENIALS.get(role, {})
    return [
        (dataset.key, spec.key)
        for dataset in DATASET_CATALOGUE
        for spec in dataset.fields
        if spec.key not in denied.get(dataset.key, ())
    ]
