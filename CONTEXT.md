# CONTEXT.md — architecture on one page

## The loop

A run is one LangGraph `StateGraph` compiled from **`configs/finance_employee.json`**
(`platform/flow/compiler.py`). The model never decides outcomes: it proposes
(decide node), deterministic code gates (permissions, roles, approvals),
executes (tool registry), and a separate verifier confirms success by reading
real app state. The graph shape per agent node: entry (understand) →
decide ⇄ (execute | ask), then routing on the saved `save_as` value or the
node's `next`/`fallback` connections.

## Flow config → graph

- `platform/flow/models.py` — Pydantic flow: agent / message / verify / end
  nodes, connections inside nodes (`next_node_ids`, `fallback_next`,
  `routes`, verify `match/mismatch`), connectors, visit limits,
  `session_context`.
- `platform/flow/validator.py` — `validate()` returns `ValidationResult`
  with **blocking errors** and non-blocking **guardrail warnings**
  (guardrail score = warning count). `== []` still means "no errors".
- `platform/flow/compiler.py` — validates, registers connectors as tools,
  expands each agent into entry/decide/execute/ask graph nodes, enforces
  per-node and total visit limits, injects skills + data-model field lists
  into the agent's system prompt, pins/releases browser workers per agent.

## Frozen context vs mutable variables

- **Session context** (`ai_operator/session.py`, `SessionContextBlock`) is
  frozen per run: tenant, currency, approval threshold, user role, enabled
  tools, approval trigger levels. It rides in the LangGraph config, never in
  state, so the model cannot tamper with its own rules.
- **Session variables** (`ai_operator/variables.py`) are the writable half
  (state[`variables`]): every write goes through `write_variable`, which
  checks data-model field permissions and role gates, traces
  `variable_written` / `permission_denied`, and never mutates on denial.

## Roles (shared by humans and agents)

`roles` + `role_permissions` tables in `platform/dashboard/db.py` are the
source of truth; `ai_operator/roles.py` is the runtime resolver. Resources
are `<kind>:<name>` (`tool:`, `field:`, `action:`) with `kind:*` fallbacks;
access ranks `none < read < write < approve`, optional approve limit per
action. Enforcement points: execute node (tool gate), variable writes
(field gate), approval card and both `/answer` paths (approve gate), API
run starts (`action:start_run`). Phase 4's `write_agents`/`read_agents`
lists are a derived view migrated into roles at first seed.

## Tools

- Registry (`ai_operator/tools/registry.py`): name, permission level
  (`read` / `reversible_write` / `irreversible_write`), Pydantic args,
  timeout; unknown names are rejected, handler errors become observations.
- Built-ins: sandboxed file search/read, browser pool (Playwright, 2
  workers, pinned per agent), company memory, screenshots.
- **Connectors** (`ai_operator/tools/connectors.py`): HTTP tools declared in
  the flow config (`{{vars.x}}`, `{{args.x}}`, `{{session.*}}`,
  `{{app.base_url}}` templates), fed by the dashboard's connectors registry.
- **Data sources** (Phase 6): CSV/SQLite files with *named queries* saved on
  the connectors page; each query becomes a read tool. No arbitrary SQL ever
  reaches the agent — arguments cannot carry SQL, parameters are values
  substituted into the saved query only.

## Verification and completion

The model cannot declare success. `ai_operator/verifier.py` re-derives the
expected record (invoice files on disk), reads `GET /api/invoices` over
plain httpx, matches amount ±0.01 and due date, and (Phase 4) type-checks
written fields against the attached data model. Only `matched` + an
evidence screenshot yields `verified_complete`; everything else exits
honestly (`failed` / `needs_human`).

## Surfaces

- **CLI**: `python -m ai_operator.run` (demo run) and `./fin` (ops commands:
  init, flow validate/export/import, run, runs list, approvals list).
- **Dashboard** (port 8001): run console with SSE trace and approval card,
  history, audit trail (reads `trace.jsonl` only), roles matrix, API keys,
  flow/agents/messages/models/connectors/skills/schedules/triggers/
  channels/plugins editors, `/about`.
- **HTTP API** (`/api/v1/...`): key-authenticated (`Authorization: Bearer
  fin_...`), runs/report/events/answer/approvals; the key's role applies the
  same gates as the UI.
- **MCP** (`python -m platform.mcp_server`): stdio JSON-RPC exposing four
  tools that call the HTTP API, so permissions are identical.
- **Plugins** (`plugins/`): manifest-scanned extensions (tools/node types/
  pages); load failures are logged and skipped, never fatal.

## Persistence

- `dashboard.db` (gitignored) — sessions, runs + lifecycle state, connectors,
  agent versions, schedules, skills, roles/permissions, api keys, triggers,
  channels (`DASH_DB` overrides the path; tests use temp DBs).
- `checkpoints.db` — LangGraph SQLite checkpointer keyed by `run_id`
  (resume path for `waiting_approval` after restarts).
- `runs/<run_id>/` — `trace.jsonl`, `report.json`, `evidence/*.png`.
- `configs/` + `company_data/` — flow/data-model config and the company's
  documents (the only company-specific knowledge; graph code is generic).
