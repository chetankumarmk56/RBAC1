# RBAC — AI + RBAC Agentic Orchestration POC

A chat interface over an HR database. Users only ever type natural language. Behind
the chat box, a planner agent routes each prompt to a role-based agent, the agent
picks a tool, and **the tool checks role-based permissions before it touches
PostgreSQL**.

The app has two pages behind a sidebar: **Chat**, where the whole data demonstration
happens, and **Access control**, a console visible only to a super admin for editing
what each role may reach — which **tools**, which **data** (datasets, columns and how
many employees' rows), and which **language models**.

The point of the POC is the boundary: the LLM decides *what to try*, the backend
decides *what is allowed*.

```
User  (picks a model, or lets their role's best one answer)
  ↓
Chat interface
  ↓
Model gate               ← RBAC: may this role run that model? refused before any LLM call
  ↓
Planner agent            (identifies intent, selects an agent)
  ↓
Role-based agent         (selects a tool)
  ↓
Tool
  ↓
RBAC permission check    ← plain Python, permissions read from PostgreSQL
  ↓
PostgreSQL               ← only reached when the check passes; rows narrowed by the role's scope
  ↓
Field policy             ← columns the role may not see are stripped from the result
  ↓
Tool result → agent → planner
  ↓
Final response → user
```

Every request that is gated — a tool call, or a model choice — is written to
`audit_logs`, allowed or denied.

---

## Stack

| Layer     | Choice                                                                   |
| --------- | ------------------------------------------------------------------------ |
| Backend   | FastAPI + Python 3.11                                                    |
| Database  | PostgreSQL 16 (Docker), SQLAlchemy 2.0, Alembic                          |
| LLM       | Claude Opus / Sonnet / Haiku and Gemini — per-role, granted like a permission |
| Frontend  | React 18 + Vite + TypeScript                                             |
| Auth      | JWT bearer tokens, bcrypt password hashing                               |

---

## Setup

Prerequisites: Python 3.11+, Node 18+, Docker, and an Anthropic API key.

### 1. Start PostgreSQL

```bash
docker compose up -d
```

### 2. Backend

```bash
cd backend
python -m venv ../.venv

# Windows (Git Bash)
source ../.venv/Scripts/activate
# macOS / Linux
# source ../.venv/bin/activate

pip install -r requirements.txt
cp .env.example .env          # then edit .env and set ANTHROPIC_API_KEY

alembic upgrade head          # create the schema
python seed.py                # roles, permissions, model + data access, users, HR data

uvicorn main:app --reload --port 8010
```

The API is on `http://localhost:8010`, with interactive docs at
`http://localhost:8010/docs`.

> Port 8010 rather than the usual 8000, which is often already in use. If you
> change it, create `frontend/.env` with `VITE_API_TARGET=http://localhost:<port>`
> — Vite proxies `/api` to that target in development.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Two standalone pages are served alongside the app, both
without signing in:

- [`/demo`](frontend/public/demo/index.html) — five worked scenarios, each with the
  code that decides it, and a diagram of the request pipeline behind a toggle.
- [`/walkthrough.html`](frontend/public/walkthrough.html) — the reviewer's guide: the
  roles, the four gates and a worked scenario.

---

## Test users

All five share the password `password123`. `SEED_PASSWORD` in `backend/.env` changes
what `seed.py` hashes — but the login page's demo buttons prefill `password123`
literally, so change both if you set it.

| Email                      | Role        | Permissions                                                              |
| -------------------------- | ----------- | ------------------------------------------------------------------------ |
| `supervisor@example.com`   | supervisor  | `payroll:read` `employee:read` `attendance:read` `performance:read` `reports:read` |
| `analyst@example.com`      | analyst     | `employee:read` `attendance:read` `performance:read` `analytics:read` `reports:read` |
| `hr@example.com`           | hr          | `employee:read` `attendance:read` `leave:read` `reports:read`             |
| `admin@example.com`        | admin       | the nine read permissions, including `audit:read` and `permissions:manage` |
| `superadmin@example.com`   | super_admin | all ten — the only role with `permissions:write`                         |

Each role also holds a set of **models**, a **row scope** and a set of **visible
columns** — all editable from the Access control page, all enforced server-side:

| Role        | Models (best first)                   | Rows | Columns withheld                                                       |
| ----------- | ------------------------------------- | ---- | ---------------------------------------------------------------------- |
| supervisor  | Sonnet · Haiku · Gemini               | team | `leave.reason`                                                          |
| analyst     | Haiku · Gemini                        | all  | `employees.email`, `employees.manager`, all four payroll figures, `performance.reviewer/comments`, `leave.reason` |
| hr          | Sonnet · Haiku · Gemini               | all  | `payroll.bonus`, `payroll.deductions`                                   |
| admin       | Opus · Sonnet · Haiku · Gemini        | all  | none                                                                    |
| super_admin | Opus · Sonnet · Haiku · Gemini        | all  | none                                                                    |

The supervisor login is linked to an employee row, so with row scope `team` their
data is themselves plus their four direct reports — 5 of the 12 employees.

Column rules apply even to data the role cannot currently reach, so they are already
in force the moment a dataset is granted: give the analyst payroll and they get the
table with the salary columns missing.

`employees.manager` is withheld from the analyst for a reason that spans two datasets:
`seed.py` sets each review's reviewer to the employee's manager, so leaving the
directory's manager column visible would hand back the `performance.reviewer` name the
same baseline denies. The reconstruction closure in
[datasets.py](backend/rbac/datasets.py) works within one dataset, so this cross-dataset
case is closed in the seeded baseline instead.

**admin vs super_admin.** An admin can *read* everything, including the RBAC
configuration. Only a super admin can *change* it. That split is the whole difference
between the two roles, and it is enforced the same way as every other permission —
`grant_tool_access` requires `permissions:write`, and admin does not hold it.

---

## Example prompts

| Signed in as | Prompt                                  | Expected                       |
| ------------ | --------------------------------------- | ------------------------------ |
| supervisor   | "Show me my team's payroll."             | ALLOWED — 5 employees          |
| analyst      | "Show me employee salaries."             | **DENIED** — no `payroll:read` |
| analyst      | "What is the average attendance?"        | ALLOWED                        |
| hr           | "Show me employees currently on leave."  | ALLOWED                        |
| hr           | "Show me payroll information."           | **DENIED** — no `payroll:read` |
| admin        | "Show me payroll information."           | ALLOWED — all 12 employees     |
| admin        | "Show me the current role permissions."  | ALLOWED                        |
| admin        | "Show me denied access attempts."        | ALLOWED — the audit trail      |
| admin        | "Give the analyst role access to payroll."| **DENIED** — no `permissions:write` |
| super_admin  | "Show me the tool access matrix."        | ALLOWED                        |
| super_admin  | "Give the analyst role access to payroll."| ALLOWED — written to the database |
| analyst      | *picks Claude Opus in the composer*      | **DENIED** — no model call made |
| super_admin  | "Which models can the analyst role use?" | ALLOWED — `get_model_access`   |
| super_admin  | "Let the analyst use Claude Opus."       | ALLOWED — `set_model_access`   |
| super_admin  | "Hide the bonus column from HR."         | ALLOWED — `set_field_access`   |
| super_admin  | "Limit the analyst to their own department." | ALLOWED — `set_data_scope` |

Each reply carries a trace strip showing the agent, the tool, the permission that was
required, the RBAC decision, the row count, how many columns the field policy withheld,
and which model answered.

### Watching the pipeline run

The chat streams over Server-Sent Events (`POST /api/chat/stream`), emitting one frame
per stage. The interface shows **one status line at a time**, each replacing the last,
so the pipeline reads as a sequence rather than a growing list:

```
● Claude Sonnet allowed for supervisor
        ↓  (replaced in place)
● Planner agent is thinking
        ↓
● Supervisor agent selected
        ↓
● get_payroll tool selected
        ↓
● Permission granted — payroll:read
        ↓
● Queried PostgreSQL — 5 row(s)
        ↓
● Writing the answer
```

On a denied request the line turns red and reads *"Permission denied — payroll:read.
Database not queried."* A model the role does not hold stops the run one step
earlier: *"Model access denied — Claude Opus. No model was called."* When columns are
withheld, a *"Field policy withheld base_salary, bonus…"* line appears between the
query and the answer. When the answer arrives the status line disappears and the
trace chips take its place.

The narration comes from the pipeline itself, not a timer: `run_chat_events()` in
[executor.py](backend/agents/executor.py) yields a `Progress` at each stage and a
final `ChatOutcome`. `run_chat()` drains the same generator, so the non-streaming
endpoint and `verify_pipeline.py` share one code path with the streaming one.
(`verify_rbac.py` sits below the orchestrator entirely — it calls `execute_tool`
directly, with no planner, agent or LLM in the picture.)

### Chat history

The sidebar keeps a **New chat** button and a list of past conversations. Both turns
of every exchange are written to `conversations` / `chat_messages`, along with the
trace, so reopening a conversation replays its Allowed/Denied chips exactly as they
were.

History is per-user and enforced server-side: every query filters on the caller's
`user_id`, and a request for someone else's conversation returns **404, not 403** — so
the API never confirms that it exists.

```
hr    → GET /api/conversations/1   200   (owner)
analyst → GET /api/conversations/1   404
analyst → DELETE /api/conversations/1 404
```

A new chat has no row until you send the first message; the stream's opening
`conversation` frame tells the client the id it created, and the title is taken from
that first message. Note that closing the tab mid-answer leaves the user turn saved
without a reply — the pipeline stops when the client disconnects rather than burning
tokens for nobody.

Each message is routed independently — the planner does not yet receive earlier turns
as context, so a bare follow-up like *"what about last month?"* won't resolve against
the previous question.

### Four openers per role

Every new chat offers four starters. The first three land; **the fourth is
deliberately outside that role's permissions**, so the RBAC boundary is one click away
without having to think up a denial:

| Role | Blocked opener | Missing |
| --- | --- | --- |
| supervisor | "Who on my team is on leave right now?" | `leave:read` |
| analyst | "Show me employee salaries." | `payroll:read` |
| hr | "Show me payroll information." | `payroll:read` |
| admin | "Give the analyst role access to payroll." | `permissions:write` |
| super_admin | "Revoke payroll access from the super_admin role." | *protected role* |

Super admin holds every permission, so its fourth exercises the lockout guard instead
— the one action even a super admin is refused. Blocked starters render with a dashed
border and the missing permission beside them.

### Changing access at runtime

Sign in as `superadmin@example.com` and run this sequence to watch a permission
boundary move while the app is running:

1. **super_admin** — "Give the analyst role access to payroll."
   → `grant_tool_access` writes `payroll:read` onto the analyst role.
2. **analyst** (sign in again) — "Show me employee salaries."
   → now **ALLOWED**, where it was denied a moment ago.
3. **super_admin** — "Revoke payroll access from the analyst role."
4. **analyst** — "Show me employee salaries." → **DENIED** again.

The same sequence works for a model and for a single column:

1. **analyst** — pick **Claude Opus** in the composer and send anything.
   → **DENIED** at the model gate; no provider was called.
2. **super_admin** — "Let the analyst role use Claude Opus."
   → `set_model_access` writes it to `role_models`.
3. **analyst** — pick Claude Opus again. → it answers, and the trace chip says
   `claude-opus`.
4. **super_admin** — "Give the analyst role access to payroll."
   → the analyst now gets the payroll table with **4 fields withheld** — no salary,
   bonus, deductions or net pay, and no total either.
5. **super_admin** — "Let the analyst see base salary."
   → `set_field_access` adds the one column; the next answer includes it.

No restart, no re-seed. `Principal` is rebuilt from the database on every message, so
a change lands on that role's next turn. Every one of these writes is recorded in
`audit_logs` with a description of what changed.

The same changes can be made from the **Access control** page — see below.

Two guardrails, both enforced in the tool rather than the prompt:

- The `super_admin` role itself cannot be edited — permissions, models, columns or
  scope — so a super admin cannot cut off their own access and lock everyone out
  (`PROTECTED_ROLES`).
- The `permissions:write` tools cannot be granted to anyone, so a second super admin
  can only be created in `seed.py`.

---

## Choosing a model

The composer has a model picker. **Auto** runs the most capable model the role holds;
naming one runs that one. Models the role does *not* hold are still listed, marked
with a 🔒 — picking one and sending is the shortest demonstration of a model denial:

```
analyst picks Claude Opus  →  Model access denied — Claude Opus. No model was called.
                              "Your role (analyst) is not allowed to use Claude Opus.
                               Models you can use: Claude Haiku, Gemini. Choose one of
                               those in the model picker and send your message again."
```

The refusal is decided by `check_model_access()` before the planner runs, so no
provider is contacted, and it is written to `audit_logs` with
`required_permission = model:claude-opus` — a model decision reads like any other RBAC
decision in the trail. The refusal sentence is deterministic Python, because a role
with no model at all has nothing to write it with.

When the model does run, the rest of the role's models sit behind it as fallbacks: a
supervisor on Sonnet drops to Haiku, then Gemini, if a call fails. The trace chip names
the model that actually answered, so a fallback is visible rather than silent.

---

## The Access control page

Sign in as the super admin and pick **Access control** in the sidebar. Three tabs.

Everything reads in plain language — *Payroll*, *Read payroll*, *Super admin* — with
the identifier it has in the database (`get_payroll`, `payroll:read`) printed underneath,
because that identifier is what the audit log records. The mapping lives in
[frontend/src/labels.ts](frontend/src/labels.ts) and falls back to a tidied version of
the identifier, so a tool or permission added on the backend still reads sensibly
before it is named there. The same labels are used for the trace chips under each chat
reply, which carry the raw identifier in a tooltip.

**Tools** — the original tool × role matrix. Tick to grant, untick to revoke. Locked
cells carry a tooltip: the `super_admin` column is the protected role, and the
`permissions:write` rows are the tools that manage RBAC itself.

**Data** — access to data at three levels:

- *Row access*: each role's scope — `all`, `department`, `team` or `self`. It decides
  which employees' rows any dataset returns, and is the setting that used to be a
  hardcoded "supervisors see their own team" rule.
- *Dataset access*: one checkbox per dataset per role. It is the same grant as the
  tool row on the Tools tab — one permission, one row in `role_permissions`.
- *Columns*: one checkbox per field per role. A withheld column is stripped from the
  tool result before the agent sees it, together with any total computed from it
  (withhold `net_pay` and `total_net_pay` goes too) and any aggregate of it in
  `get_analytics` / `get_reports`, so the model can neither quote nor reconstruct it.
  Identity columns are marked `identity` and cannot be withheld — removing the name
  from every row would make the answer meaningless; remove the dataset instead.

**Models** — a model × role matrix. A model whose provider has no API key on this
server is tagged `no key`, so you can tell "not granted" from "not configured".

The page is not a parallel implementation. Each control posts to `/api/admin/*`, which
runs the **same** tools the chat agent uses — `grant_tool_access` /
`revoke_tool_access`, `set_model_access`, `set_field_access`, `set_data_scope` — so
console and chat share one RBAC check and one audit trail. Console changes are tagged
`admin_console` in `audit_logs`, chat changes carry the agent name, so you can tell
them apart.

**Hiding the nav item is presentation, not security.** Every console endpoint is gated
on `permissions:write` through the `require_permission` dependency, which calls the
same `check_permission` the tool layer calls:

| Caller       | `GET /api/admin/access-matrix` | `POST /api/admin/access`, `/model-access`, `/field-access`, `/data-scope` |
| ------------ | ------------------------------ | ------------------------ |
| super_admin  | 200                            | 200                      |
| admin        | 403                            | 403                      |
| analyst      | 403                            | 403                      |
| no token     | 401                            | 401                      |

An admin can still *read* the same information through chat — `get_role_permissions`,
`get_tool_permissions`, `get_model_access` and `get_data_access` only need
`permissions:manage`. What they cannot do is change it, from either surface.

---

## How RBAC is enforced

Four rules keep authorization out of the model's hands, and three further rules decide
how much of the allowed data comes back.

**1. The role comes from the database, never the client or the LLM.**
The JWT carries only a user id and email. On every request `load_principal()`
([backend/rbac/service.py](backend/rbac/service.py)) reads that user's permissions,
models, row scope and visible columns out of PostgreSQL and builds a `Principal`. A
client cannot send a role, a permission or a tool name — the chat request body is just
`{"message": "…", "model": "claude-haiku"}`, and the model is a *request*, checked
against the role before anything runs.

**2. The check happens inside the tool layer, before any query.**
`execute_tool()` ([backend/tools/base.py](backend/tools/base.py)) calls
`check_permission()` first and raises `PermissionDenied` on failure, so a denied
request never issues SQL:

```python
def execute_tool(tool: Tool, ctx: ToolContext, tool_input: dict) -> ToolResult:
    check_permission(ctx.principal, tool.required_permission)   # raises on failure
    # only reached if allowed; the result is then stripped of columns this role
    # may not see, before the agent or the responder ever sees them
    return apply_field_policy(tool, ctx, tool.handler(ctx, tool_input))
```

**3. Agents are not gatekeepers — deliberately.**
Every agent may *attempt* every tool, and their system prompts explicitly tell them
not to reason about permissions. Ask the analyst agent for salaries and it *will*
reach for `get_payroll`; the RBAC check is what stops it. If each agent filtered its
own tool list, the LLM would be the access-control layer and the boundary would be
untestable. What differs between agents is specialisation, not privilege.

**4. Both outcomes are audited.**
`log_tool_request()` ([backend/rbac/audit.py](backend/rbac/audit.py)) writes the user,
role, agent, tool, required permission, decision and prompt to `audit_logs` for
allowed and denied requests alike — model denials included, keyed as `model:<key>`.

### The three narrowing rules

Permissions decide *whether* a tool runs. Three further rules decide *how much* comes
back, and none of them is visible to the model:

| Rule       | Where                                          | Effect                                              |
| ---------- | ---------------------------------------------- | --------------------------------------------------- |
| Model      | `check_model_access()`, before the planner      | Refuses a model the role does not hold — no LLM call |
| Row scope  | `visible_employee_ids()`, inside each handler   | `all` / `department` / `team` / `self`               |
| Field policy | `apply_field_policy()`, inside `execute_tool` | Strips withheld columns and their derived totals     |

Row scope and the field policy are *scoping*, not authorization — a supervisor still
needs `payroll:read` to reach payroll at all. Redaction happens centrally in
`execute_tool`, so a new tool that declares a dataset inherits it without writing any
redaction code; the two handlers that fold a figure into their prose summary ask the
policy directly (`shows(ctx, "payroll", "net_pay")`) rather than emitting a total the
caller may not see.

---

## Verifying it without an API key

Two scripts exercise the backend with no Claude calls at all.

```bash
cd backend
python verify_rbac.py       # every tool × every role, plus leakage and scope checks
python verify_pipeline.py   # the documented prompts, with a stubbed LLM
```

`verify_rbac.py` prints the full 17-tool × 5-role decision matrix and checks five
things: every outcome matches the caller's permissions **as stored in PostgreSQL**;
every denied call issues **zero SQL statements** (counted with a SQLAlchemy event
listener, so "the database is never queried" is measured, not asserted); each role's
row scope really is as narrow as it claims; a withheld column appears **nowhere** in
that role's results, at any depth of the payload, including in derived totals; and
`check_model_access` allows exactly the models the role holds. It also reports any
drift from the seeded baseline, which is expected once a super admin has changed
something.

`verify_pipeline.py` replaces only the LLM calls with deterministic keyword routing
and runs the real orchestrator, RBAC gate, permission writes and audit logging. Its
super-admin sequence grants payroll to the analyst, proves the analyst can then read
it, revokes it, proves the denial is back — and finally asserts the analyst's
permissions match the seeded baseline again, so the run leaves no residue. A second
pass drives the model gate: a role asking for a model it does not hold is refused
without a tool ever being chosen, and one asking for a model it holds passes through.

Both exit non-zero on any mismatch.

---

## Deploying

The app runs on a managed host without code changes, but a few settings stop being
optional once it leaves localhost.

**Database.** `DATABASE_URL` takes a hosting provider's connection string as-is —
`postgres://…` and `postgresql://…` are both rewritten to `postgresql+psycopg://` in
[config.py](backend/config.py), since the project installs psycopg3 only. Paste it
straight from the dashboard.

**Secrets.** Set `JWT_SECRET` to a long random string. It has a working default, so
the app boots happily without one and signs tokens with a value published in this
repo — set it. Set `SEED_PASSWORD` too if anyone else can reach the deployment.

**CORS.** `CORS_ORIGINS` is a comma-separated list defaulting to localhost only, so a
frontend served from another origin needs its URL added. Don't reach for `*`; the API
sends `Access-Control-Allow-Credentials`.

**Seeding.** Use the `--if-empty` flag in a deploy hook:

```bash
alembic upgrade head
python seed.py --if-empty
```

`--if-empty` seeds a fresh database once and then does nothing, so redeploying never
wipes saved conversations or runtime access grants. Plain `python seed.py` always
resets the demo data.

**Split origins.** When the frontend is served from a different origin than the API,
build it with `VITE_API_BASE_URL=https://your-api-host`. Vite inlines it at build
time, so it must be set *before* `npm run build`, not at runtime. Leave it unset in
development, where the proxy handles `/api`.

**Port.** `backend/main.py` has no `PORT` handling and the repo carries no Procfile or
Dockerfile, so a platform that injects `$PORT` needs the start command to pass it:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

---

## Project layout

```
backend/
  main.py             FastAPI app, CORS, router registration, GET /api/health
  config.py           Settings from backend/.env — DB, API keys, JWT, CORS
  schemas.py          Pydantic request/response models
  seed.py             Roles, permissions, model + data access, users, dummy HR data
  requirements.txt
  .env.example        Copy to .env and set ANTHROPIC_API_KEY
  agents/
    llm.py            LLMRun: the models a message may use, tried in order
    provider_base.py  LLMUnavailable and the Provider protocol
    provider_claude.py / provider_gemini.py
    planner.py        Intent + agent routing, and the final response writer
    role_agents.py    The four role agents and their system prompts
    executor.py       The pipeline: model gate → plan → tool → RBAC → DB → respond
  tools/
    base.py           Tool contract, execute_tool (RBAC gate), row scope, field policy
    registry.py       Tool catalogue and Claude-facing schemas
    payroll_tools.py  get_payroll
    hr_tools.py       get_employee, get_attendance, get_performance, get_leave
    analytics_tools.py get_analytics, get_reports
    admin_tools.py    Audit trail, RBAC matrix, and the five access-changing tools
  rbac/
    permissions.py    Permission vocabulary, role → permission matrix, protected roles
    model_catalog.py  The model catalogue and the seeded role → model baseline
    datasets.py       Datasets, their columns, row scopes, and the seeded baseline
    service.py        Principal loading, check_permission, check_model_access
    audit.py          Audit-log writes
  auth/
    security.py       bcrypt hashing, JWT encode/decode
    dependencies.py   Bearer token → DB-resolved Principal, require_permission
    router.py         POST /api/auth/login, GET /api/auth/me
  api/
    chat.py           GET /api/models, POST /api/chat, POST /api/chat/stream
    admin.py          Access-control console API — super admin only
    conversations.py  Chat history, scoped to the caller
  models/             SQLAlchemy models: rbac.py, hr.py, chat.py, audit.py
  db/                 base.py (declarative base), session.py (engine, get_db)
  alembic/            0001 schema · 0002 chat history · 0003 model + data access
  alembic.ini
  verify_rbac.py      Every tool × every role, no LLM — enforcement, leakage, scope, fields
  verify_pipeline.py  The documented prompts through the real orchestrator, stubbed LLM

frontend/
  index.html
  vite.config.ts      Dev server on 5173, proxies /api, serves the static pages below
  tsconfig.json · package.json
  public/
    demo/index.html   Scenarios and pipeline diagram, served at /demo
    walkthrough.html  Standalone reviewer's guide, served at /walkthrough.html
  src/
    main.tsx          React entry point
    App.tsx           Session restore, sidebar shell, page switching
    index.css         Design tokens and every style in the app
    components/       Sidebar, ChatMessage, ChatInput, LiveStatus, Markdown
    pages/            LoginPage, ChatPage, AccessControlPage
    services/api.ts   fetch wrapper, token storage, SSE reader
    labels.ts         Display names for tools, permissions, roles and models
    types/index.ts    Shared types

docker-compose.yml    PostgreSQL 16 for local development
```

---

## Tools and their permissions

| Tool                   | Permission           | Returns                                            |
| ---------------------- | -------------------- | -------------------------------------------------- |
| `get_payroll`          | `payroll:read`       | Salary, bonus, deductions, net pay per period      |
| `get_employee`         | `employee:read`      | Directory: name, department, title, manager, status |
| `get_attendance`       | `attendance:read`    | Per-employee attendance summary and rate           |
| `get_performance`      | `performance:read`   | Review ratings, reviewers and comments             |
| `get_leave`            | `leave:read`         | Leave records, including who is on leave today     |
| `get_analytics`        | `analytics:read`     | Aggregate statistics — never compensation          |
| `get_reports`          | `reports:read`       | Rolled-up organisational summary                   |
| `get_audit_logs`       | `audit:read`         | The tool-access audit trail                        |
| `get_role_permissions` | `permissions:manage` | Roles and the permissions granted to each          |
| `get_tool_permissions` | `permissions:manage` | Tool-by-tool matrix of which roles can run what    |
| `get_model_access`     | `permissions:manage` | Which models each role may run                     |
| `get_data_access`      | `permissions:manage` | Each role's row scope and visible columns          |
| `grant_tool_access`    | `permissions:write`  | **Writes** — gives a role access to a tool         |
| `revoke_tool_access`   | `permissions:write`  | **Writes** — removes a role's access to a tool     |
| `set_model_access`     | `permissions:write`  | **Writes** — allows or stops a role using a model  |
| `set_field_access`     | `permissions:write`  | **Writes** — shows or withholds one column         |
| `set_data_scope`       | `permissions:write`  | **Writes** — sets a role's row reach               |

`get_analytics` and `get_reports` deliberately exclude compensation, so
`analytics:read` cannot become a back door to salary data. Each block of statistics is
also tied to the column it aggregates — hide `performance.rating` from a role and its
rating averages disappear from analytics too, so an aggregate cannot be a back door
around the field policy either.

The last five are the only tools that write. They edit `role_permissions`,
`role_models`, `role_field_access` and `role_data_scope` — the same tables
`load_principal()` reads on every request — so the RBAC system configures itself
through its own enforcement path rather than a side channel.

---

## Database schema

`users`, `roles`, `permissions`, `role_permissions`, `role_models`,
`role_field_access`, `role_data_scope`, `employees`, `payroll`, `attendance`,
`performance`, `leave_records`, `audit_logs`, `conversations`, `chat_messages`.

The three access tables are keyed by role: `role_models(role_id, model_key)`,
`role_field_access(role_id, dataset_key, field_key)` and
`role_data_scope(role_id, scope)`. Migration `0003` creates them and back-fills every
existing role from the seeded baseline, so an upgrade leaves the app configured rather
than locked out — and row scope back-fills to exactly what the code did before it
existed: `team` for supervisor, `all` for everyone else.

Seed data: 12 employees across 4 departments with reporting lines, 3 months of
payroll, 60 days of attendance, 2 review cycles, and 8 leave records. Dates are
generated relative to today, so "who is on leave right now" always returns rows.
`seed.py` is idempotent — re-running it resets the demo data.

---

## API

| Method | Path                       | Requires            | Notes                                       |
| ------ | -------------------------- | ------------------- | ------------------------------------------- |
| `POST` | `/api/auth/login`          | —                   | `{email, password}` → token + user info     |
| `GET`  | `/api/auth/me`             | any token           | Current user, role, permissions, models, scope |
| `GET`  | `/api/models`              | any token           | The picker's options — locked ones included |
| `POST` | `/api/chat`                | any token           | `{message, model?}` → `{reply, trace}`      |
| `POST` | `/api/chat/stream`         | any token           | The same work as SSE, one frame per stage   |
| `GET`  | `/api/conversations`       | any token           | The caller's own chat history               |
| `GET`  | `/api/conversations/{id}`  | ownership           | One conversation with its messages          |
| `DELETE` | `/api/conversations/{id}`| ownership           | Delete a conversation                       |
| `GET`  | `/api/admin/access-matrix` | `permissions:write` | Roles, tools, models, datasets and scopes   |
| `POST` | `/api/admin/access`        | `permissions:write` | `{role_name, tool_name, granted}` → matrix  |
| `POST` | `/api/admin/model-access`  | `permissions:write` | `{role_name, model_key, granted}` → matrix  |
| `POST` | `/api/admin/field-access`  | `permissions:write` | `{role_name, dataset, field, granted}` → matrix |
| `POST` | `/api/admin/data-scope`    | `permissions:write` | `{role_name, scope}` → matrix               |
| `GET`  | `/api/health`              | —                   | Status and whether an API key is configured |

---

## Notes on the LLM integration

- Four models are on offer — `claude-opus-5`, `claude-sonnet-5`,
  `claude-haiku-4-5-20251001` and `gemini-3.1-flash-lite`. The ids come from
  `backend/.env` (`CLAUDE_OPUS_MODEL`, `CLAUDE_SONNET_MODEL`, `CLAUDE_HAIKU_MODEL`,
  `GEMINI_MODEL`); *who may run them* comes from PostgreSQL.
- The set a request may use is passed explicitly as an `LLMRun`, not held in a
  ContextVar: the streaming endpoint resumes the pipeline generator from a thread
  pool, and each resumption gets its own copy of the context, so per-request state
  stashed between two `yield`s would be lost.
- The planner uses **structured outputs** (`output_config.format`) so its routing
  decision always parses — no regex extraction, no retry loop.
- Agents use tool-use with `disable_parallel_tool_use`, keeping one tool per turn so
  the pipeline stays single-path and legible.
- `effort` is set per call: `low` for routing and response writing, `medium` for tool
  selection.
- Denials fall back to a deterministic sentence, so a permission refusal never depends
  on any model being available.

### Failover across a role's models

A role's models are also its failover chain, most capable first. If a call fails, the
*same* call is retried on the next model the role holds — Sonnet → Haiku → Gemini for
a supervisor — and the user gets an answer anyway. Set `GEMINI_API_KEY` in
`backend/.env` to keep Gemini in the chain; leave it blank and the Claude tiers carry
it alone.

Failover triggers on any operational failure: missing or invalid key, network error,
timeout, rate limit, 5xx, a refusal, or malformed output. It is not a retry of the
same model — each gets one attempt per call, then the next one runs.

```
agents/
  llm.py               LLMRun: walks the role's models in order, records which answered
  provider_base.py     LLMUnavailable + the three-method Provider protocol
  provider_claude.py   the three Claude tiers
  provider_gemini.py   Gemini
```

Both providers implement the same three calls — JSON-schema output, plain text, and
tool selection. Tool schemas need no translation: Gemini's `parameters_json_schema`
and `response_json_schema` take raw JSON Schema, which is exactly what the tool
registry already produces for Claude. Gemini has no `effort` parameter, so that
argument is accepted and ignored.

`GET /api/health` reports the catalogue and which models this server could actually
run — `usable: false` means the provider behind that model has no API key:

```json
{ "providers": ["claude", "gemini"],
  "models": [{ "key": "claude-opus", "model_id": "claude-opus-5", "usable": true }, …] }
```

**The fallback is visible, not silent.** Every chat reply carries a model chip in its
trace naming the model that actually answered — amber when a fallback ran — so a
supervisor who asked for Sonnet and got Gemini can see it. If every model in the chain
fails, the API still returns a readable message naming each failure rather than a 500:

```
The assistant is unavailable right now.
claude-sonnet: Claude request failed: Error code: 401 …; gemini: Gemini request failed: 400 …
```

Config: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.1-flash-lite`), and
`LLM_FALLBACK_ENABLED=false` to keep Gemini as a last resort rather than a fallback —
with it off, Gemini runs only when no Claude tier in the role's set is usable.
