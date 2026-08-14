# RBAC — AI + RBAC Agentic Orchestration POC

A chat interface over an HR database. Users only ever type natural language. Behind
the chat box, a planner agent routes each prompt to a role-based agent, the agent
picks a tool, and **the tool checks role-based permissions before it touches
PostgreSQL**.

The app has two pages behind a sidebar: **Chat**, where the whole data demonstration
happens, and **Access control**, a console visible only to a super admin for editing
which roles may use which tools.

The point of the POC is the boundary: the LLM decides *what to try*, the backend
decides *what is allowed*.

```
User
  ↓
Chat interface
  ↓
Planner agent            (Claude — identifies intent, selects an agent)
  ↓
Role-based agent         (Claude — selects a tool)
  ↓
Tool
  ↓
RBAC permission check    ← plain Python, permissions read from PostgreSQL
  ↓
PostgreSQL               ← only reached when the check passes
  ↓
Tool result → agent → planner
  ↓
Final response → user
```

Every tool request, allowed or denied, is written to `audit_logs`.

---

## Stack

| Layer     | Choice                                                                   |
| --------- | ------------------------------------------------------------------------ |
| Backend   | FastAPI + Python 3.11                                                    |
| Database  | PostgreSQL 16 (Docker), SQLAlchemy 2.0, Alembic                          |
| LLM       | Claude (`claude-opus-5`), falling back to Gemini (`gemini-3.1-flash-lite`) |
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
python seed.py                # roles, permissions, users, dummy HR data

uvicorn main:app --reload --port 8010
```

The API is on `http://localhost:8010`, with interactive docs at
`http://localhost:8010/docs`.

> Port 8010 rather than the usual 8000, which is often already in use. If you
> change it, set `VITE_API_TARGET` for the frontend (see `frontend/.env.example`).

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`.

---

## Test users

All five share the password `password123` (configurable via `SEED_PASSWORD`).

| Email                      | Role        | Permissions                                                              |
| -------------------------- | ----------- | ------------------------------------------------------------------------ |
| `supervisor@example.com`   | supervisor  | `payroll:read` `employee:read` `attendance:read` `performance:read` `reports:read` |
| `analyst@example.com`      | analyst     | `employee:read` `attendance:read` `performance:read` `analytics:read` `reports:read` |
| `hr@example.com`           | hr          | `employee:read` `attendance:read` `leave:read` `reports:read`             |
| `admin@example.com`        | admin       | the nine read permissions, including `audit:read` and `permissions:manage` |
| `superadmin@example.com`   | super_admin | all ten — the only role with `permissions:write`                         |

The supervisor login is also linked to an employee row, so their data is scoped to
themselves plus their four direct reports — 5 of the 12 employees.

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

Each reply carries a trace strip showing the agent, the tool, the permission that
was required, the RBAC decision, the row count and which model answered.

### Watching the pipeline run

The chat streams over Server-Sent Events (`POST /api/chat/stream`), emitting one frame
per stage. The interface shows **one status line at a time**, each replacing the last,
so the pipeline reads as a sequence rather than a growing list:

```
● Planner agent is thinking
        ↓  (replaced in place)
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
Database not queried."* When the answer arrives the status line disappears and the
trace chips take its place.

The narration comes from the pipeline itself, not a timer: `run_chat_events()` in
[executor.py](backend/agents/executor.py) yields a `Progress` at each stage and a
final `ChatOutcome`. `run_chat()` drains the same generator, so the non-streaming
endpoint and both verify scripts share one code path with the streaming one.

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

No restart, no re-seed. `Principal` is rebuilt from the database on every message, so
a change lands on that role's next turn. Both the grant and the revoke are written to
`audit_logs` with a description of what changed.

The same changes can be made from the **Access control** page — see below.

Two guardrails, both enforced in the tool rather than the prompt:

- The `super_admin` role itself cannot be edited, so a super admin cannot revoke their
  own `permissions:write` and lock everyone out (`PROTECTED_ROLES`).
- `grant_tool_access` / `revoke_tool_access` cannot be granted to anyone, so a second
  super admin can only be created in `seed.py`.

---

## The Access control page

Sign in as the super admin and pick **Access control** in the sidebar. It shows a
tool × role matrix — tick a box to grant a role access to a tool, untick to revoke.
Locked cells carry a tooltip explaining why: the `super_admin` column is the protected
role, and the two `permissions:write` rows are the tools that manage RBAC itself.

The page is not a parallel implementation. It posts to `/api/admin/access`, which runs
the **same** `grant_tool_access` / `revoke_tool_access` tools the chat agent uses — so
console and chat share one RBAC check and one audit trail. Console changes are tagged
`admin_console` in `audit_logs`, chat changes carry the agent name, so you can tell
them apart.

**Hiding the nav item is presentation, not security.** Both console endpoints are
gated on `permissions:write` through the `require_permission` dependency, which calls
the same `check_permission` the tool layer calls:

| Caller       | `GET /api/admin/access-matrix` | `POST /api/admin/access` |
| ------------ | ------------------------------ | ------------------------ |
| super_admin  | 200                            | 200                      |
| admin        | 403                            | 403                      |
| analyst      | 403                            | 403                      |
| no token     | 401                            | 401                      |

An admin can still *read* the same information through chat — `get_role_permissions`
and `get_tool_permissions` only need `permissions:manage`. What they cannot do is
change it, from either surface.

---

## How RBAC is enforced

Four rules keep authorization out of the model's hands.

**1. The role comes from the database, never the client or the LLM.**
The JWT carries only a user id and email. On every request `load_principal()`
([backend/rbac/service.py](backend/rbac/service.py)) joins `users → roles →
role_permissions → permissions` and builds a `Principal`. A client cannot send a
role, a permission or a tool name — the chat request body is just `{"message": "..."}`.

**2. The check happens inside the tool layer, before any query.**
`execute_tool()` ([backend/tools/base.py](backend/tools/base.py)) calls
`check_permission()` first and raises `PermissionDenied` on failure, so a denied
request never issues SQL:

```python
def execute_tool(tool: Tool, ctx: ToolContext, tool_input: dict) -> ToolResult:
    check_permission(ctx.principal, tool.required_permission)   # raises on failure
    return tool.handler(ctx, tool_input)                        # only reached if allowed
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
allowed and denied requests alike.

Beyond permissions there is one row-level rule: a supervisor sees only their own
team (`visible_employee_ids()`). That is data *scoping*, not authorization — a
supervisor still needs `payroll:read` to reach payroll at all.

---

## Verifying it without an API key

Two scripts exercise the backend with no Claude calls at all.

```bash
cd backend
python verify_rbac.py       # every tool × every role, plus leakage and scope checks
python verify_pipeline.py   # the documented prompts, with a stubbed LLM
```

`verify_rbac.py` prints the full 12-tool × 5-role decision matrix and checks three
things: every outcome matches the caller's permissions **as stored in PostgreSQL**;
every denied call issues **zero SQL statements** (counted with a SQLAlchemy event
listener, so "the database is never queried" is measured, not asserted); and a
supervisor sees fewer employee rows than an admin. It also reports any drift from the
seeded baseline, which is expected once a super admin has changed something.

`verify_pipeline.py` replaces only the two Claude calls with deterministic keyword
routing and runs the real orchestrator, RBAC gate, permission writes and audit
logging. Its super-admin sequence grants payroll to the analyst, proves the analyst
can then read it, revokes it, proves the denial is back — and finally asserts the
analyst's permissions match the seeded baseline again, so the run leaves no residue.

Both exit non-zero on any mismatch.

---

## Project layout

```
backend/
  agents/
    llm.py            Provider façade with Claude → Gemini failover
    provider_base.py  LLMUnavailable and the Provider protocol
    provider_claude.py / provider_gemini.py
    planner.py        Intent + agent routing, and the final response writer
    role_agents.py    The four role agents and their system prompts
    executor.py       The pipeline: plan → select tool → RBAC → DB → respond
  tools/
    base.py           Tool contract, ToolContext, execute_tool (the RBAC gate)
    registry.py       Tool catalogue and Claude-facing schemas
    payroll_tools.py  get_payroll
    hr_tools.py       get_employee, get_attendance, get_performance, get_leave
    analytics_tools.py get_analytics, get_reports
    admin_tools.py    Audit trail, RBAC matrix, grant/revoke tool access
  rbac/
    permissions.py    Permission vocabulary, role → permission matrix, protected roles
    service.py        Principal loading and check_permission
    audit.py          Audit-log writes
  auth/
    security.py       bcrypt hashing, JWT encode/decode
    dependencies.py   Bearer token → database-resolved Principal
    router.py         POST /api/auth/login, GET /api/auth/me
  models/             SQLAlchemy models (rbac.py, hr.py, audit.py)
  db/                 Declarative base, engine, session
  api/chat.py         POST /api/chat
  api/admin.py        Access-control console API — super admin only
  api/conversations.py Chat history, scoped to the caller
  alembic/            Migrations
  seed.py             Roles, permissions, users, dummy HR data
  schemas.py          Pydantic request/response models
  main.py             FastAPI app

frontend/
  src/
    components/       Sidebar, ChatMessage, ChatInput, LiveStatus, Markdown
    pages/            LoginPage, ChatPage, AccessControlPage
    services/api.ts   fetch wrapper, token storage
    types/index.ts    Shared types
    App.tsx           Session restore, sidebar shell, page switching
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
| `grant_tool_access`    | `permissions:write`  | **Writes** — gives a role access to a tool         |
| `revoke_tool_access`   | `permissions:write`  | **Writes** — removes a role's access to a tool     |

`get_analytics` and `get_reports` deliberately exclude compensation, so
`analytics:read` cannot become a back door to salary data.

The last two are the only tools that write. They edit `role_permissions`, which is
the same table `load_principal()` reads on every request — so the RBAC system
configures itself through its own enforcement path rather than a side channel.

---

## Database schema

`users`, `roles`, `permissions`, `role_permissions`, `employees`, `payroll`,
`attendance`, `performance`, `leave_records`, `audit_logs`, `conversations`,
`chat_messages`.

Seed data: 12 employees across 4 departments with reporting lines, 3 months of
payroll, 60 days of attendance, 2 review cycles, and 8 leave records. Dates are
generated relative to today, so "who is on leave right now" always returns rows.
`seed.py` is idempotent — re-running it resets the demo data.

---

## API

| Method | Path                       | Requires            | Notes                                       |
| ------ | -------------------------- | ------------------- | ------------------------------------------- |
| `POST` | `/api/auth/login`          | —                   | `{email, password}` → token + user info     |
| `GET`  | `/api/auth/me`             | any token           | Current user, role and permissions          |
| `POST` | `/api/chat`                | any token           | `{message}` → `{reply, trace}`              |
| `POST` | `/api/chat/stream`         | any token           | The same work as SSE, one frame per stage   |
| `GET`  | `/api/conversations`       | any token           | The caller's own chat history               |
| `GET`  | `/api/conversations/{id}`  | ownership           | One conversation with its messages          |
| `DELETE` | `/api/conversations/{id}`| ownership           | Delete a conversation                       |
| `GET`  | `/api/admin/access-matrix` | `permissions:write` | Roles, tools and the current grant matrix   |
| `POST` | `/api/admin/access`        | `permissions:write` | `{role_name, tool_name, granted}` → matrix  |
| `GET`  | `/api/health`              | —                   | Status and whether an API key is configured |

---

## Notes on the LLM integration

- Model: `claude-opus-5`, with adaptive thinking (on by default on this model).
- The planner uses **structured outputs** (`output_config.format`) so its routing
  decision always parses — no regex extraction, no retry loop.
- Agents use tool-use with `disable_parallel_tool_use`, keeping one tool per turn so
  the pipeline stays single-path and legible.
- `effort` is set per call: `low` for routing and response writing, `medium` for tool
  selection.
- Denials fall back to a deterministic sentence, so a permission refusal never depends
  on any model being available.

### Gemini fallback

Claude is the primary. If a Claude call fails, the *same* call is retried on
**`gemini-3.1-flash-lite`** and the user gets an answer anyway. Set `GEMINI_API_KEY`
in `backend/.env` to enable it; leave it blank and the app runs on Claude alone.

Failover triggers on any operational failure: missing or invalid key, network error,
timeout, rate limit, 5xx, a refusal, or malformed output. It is not a retry of the
same provider — Claude gets one attempt per call, then Gemini does.

```
agents/
  llm.py               façade: tries each provider in order, records which answered
  provider_base.py     LLMUnavailable + the three-method Provider protocol
  provider_claude.py   primary
  provider_gemini.py   fallback
```

Both providers implement the same three calls — JSON-schema output, plain text, and
tool selection. Tool schemas need no translation: Gemini's `parameters_json_schema`
and `response_json_schema` take raw JSON Schema, which is exactly what the tool
registry already produces for Claude. Gemini has no `effort` parameter, so that
argument is accepted and ignored.

`GET /api/health` reports the active chain, primary first:

```json
{ "providers": ["claude", "gemini"], "claude_model": "claude-opus-5",
  "gemini_model": "gemini-3.1-flash-lite" }
```

**The fallback is visible, not silent.** Every chat reply carries a provider chip in
its trace — grey for `claude`, amber for `gemini` — so you can see when the fallback
answered. If every provider fails, the API still returns a readable message naming
each failure rather than a 500:

```
The assistant is unavailable right now.
claude: Claude request failed: Error code: 401 …; gemini: Gemini request failed: 400 …
```

Config: `GEMINI_API_KEY`, `GEMINI_MODEL` (default `gemini-3.1-flash-lite`), and
`LLM_FALLBACK_ENABLED=false` to pin to Claude only. If `ANTHROPIC_API_KEY` is blank
but `GEMINI_API_KEY` is set, Gemini becomes the primary.
