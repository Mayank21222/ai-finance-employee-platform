# improvement.md

---

## Phase 1 — Core agent runtime (baseline)

The core agent runtime, mock payables app, tools, verifier, approval system, tracing and test
suite are all working and green.

## Phase 2 — Low-code platform layer

### Known issues to fix first

**Stale server problem.** Add a build hash or a `/health` endpoint that returns a short hash of
`mock_app/app.py`, and make `run.py` compare that hash before reusing a process. If they differ,
kill the old process and start a fresh one.

**Single browser worker bottleneck.** Change the browser tool to use a small pool of two workers
instead of one. Keep the same queue interface so no other code has to change.

**Approval system hardcoded.** Move the approval threshold and the conditions that trigger it into
the session context (the immutable part).

**History window truncation in the decide node.** All history lines are kept, each compressed to a
single line, so the context stays bounded without losing the record of what was done. Document this
decision explicitly in a comment.

### The flow configuration system

Create `platform/flow/` with three files.

`models.py` defines Pydantic models for a flow: agent node, message node, verify node, end node.
An agent node has a node id, system prompt (file path or inline), step-by-step instructions,
document paths, tool names, allowed next-node ids, fallback next-node id, and an optional
`save_as` variable name. A message node has a node id, a template string with
`{{vars.variable_name}}` placeholders, and next-node ids. A verify node has a node id, check type,
and two next-node ids. The full flow has a list of nodes, a start node id, and a session context
block (tenant, currency, approval threshold, user role, enabled tool set).

`validator.py` checks that every connection points to an existing node, there is exactly one start
node and at least one end node, every agent node has at least one connection, every template
variable referenced in a message node exists as a session variable key or as a `save_as` target of
a prior agent node, and no tool is enabled for an agent that does not exist in the tool registry.
Validation errors are returned as a list of plain-English messages, not exceptions.

`compiler.py` takes a validated flow and compiles it into a LangGraph `StateGraph`. The existing
node implementations in `ai_operator/nodes/` become the building blocks. The existing hardcoded
`graph.py` is replaced by a thin wrapper that loads `configs/finance_employee.json` and calls the
compiler.

### Variables

Session context is set at the start of a run and frozen. Wrap it in a Pydantic model with
`model_config = ConfigDict(frozen=True)` and never pass it as a writable field in LangGraph state.
Session variables are mutable working memory. Every write to a session variable must be logged to
the trace with the node, old value, and new value. Message node template rendering reads from this
block. If a template references a variable that is not yet set, render a visible error message and
route to the fallback node rather than crashing.

### Self-connecting nodes and visit limits

Add a `node_visits` counter to the run state. Before entering any node, check its count against
the configured limit. The platform default is 3 visits per node and 40 total node visits per run.
When a limit is hit, end the run with a `needs_human` status and a report explaining which node
hit the limit and what the last few steps were.

### The dashboard

Build as a FastAPI app in `platform/dashboard/` with server-rendered HTML and HTMX.

- **Session setup page:** tenant name, currency, approval threshold, user role. Stored in SQLite,
  linked to a run by session id.
- **Agents page:** name, system prompt textarea, instructions textarea, tool toggles from the tool
  registry. Clicking a badge toggles the tool and saves to the flow config immediately.
- **Documents page:** upload text or PDF files and attach them to specific agents. Files go into
  `company_data/<tenant>/`.
- **Message nodes page:** template string, dropdown of session variables (inserts
  `{{vars.variable_name}}` at cursor), and routing target.
- **Flow editor page:** form to pick source and destination nodes and add the connection. Show the
  current flow as a Mermaid diagram. A button runs the validator and shows errors inline.
- **Run console page:** type a request, pick a session, start a run. Trace streams live via
  server-sent events. When an approval or clarification interrupt fires, show a prompt on the page
  with an input and a submit button. The run console is the most important page for the demo.

### Build order

Flow models, validator and compiler first. Replace the hardcoded `graph.py`. Run the full test
suite to prove nothing broke. Then: variable system, node visit limits, dashboard (run console
first, then agents and tool toggles, then flow editor with Mermaid, then the rest). Commit after
each finished piece.

### What not to change

Do not touch the tool implementations, the verifier, the approval routing, the tracing module, or
the test suite for the existing scenarios.

---

## Phase 3 additions — build after Phase 2 is green

> **Actual state going in:** 63 tests green (Phase 1 + 2 baseline).

Run the full existing test suite before starting. Confirm the count. Then begin.

### Add the Classifier agent and make the flow genuinely multi-agent

Add a second agent node to `configs/finance_employee.json`: a Classifier that sits before
`ap_agent`. Give it its own system prompt in `prompts/agent_classifier.md`. Its job is to read the
request, identify the task type (invoice entry, payment reminder, expense review, or unknown), and
route to the right specialist. It saves its classification to a variable called `task_type`. The
existing `ap_agent` becomes the invoice entry specialist. Add a second specialist, a reminder
agent, that reads the invoice list and identifies which ones are overdue based on due dates. It
should not write to the payables system, only read, and should output a list to a message node.

Add an `AgentNode.routes` dict (value → node_id) and wire `_agent_router` to return
`route:<value>` on a terminal action when the saved `save_as` value matches.

Add one test that sends a reminder request and asserts the Classifier routes to the reminder
agent, not `ap_agent`.

### Replace the stub with a real model

The stub provider was the right call for building. Wire `claude-sonnet-4-6` as the provider by
adding a real client class in `llm.py` that calls the Anthropic API. The stub stays as a fallback
when `STUB_MODEL=1` is set, because the tests need it. The real client reads `LLM_API_KEY` from
the environment. Use `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY` as the canonical env var names.
Increase `max_tokens` to 2048 for the decision JSON.

The e2e tests must keep using the stub so CI stays fast and offline. Pin `STUB_MODEL=1` in
`tests/conftest.py` so a developer `.env` can never make the suite go online.

The real model demo must reach `verified_complete`. If the real model makes a different choice than
the stub at any step, fix the prompt rather than the test.

### Proper run state machine and retry from the dashboard

Add a `state` column to the `runs` table: `queued`, `running`, `waiting_approval`, `completed`,
`failed`, `interrupted`. The runner sets the state at each transition and the run console reflects
it live.

When the approval interrupt fires, the state becomes `waiting_approval`. If the runner process
dies while a run is `waiting_approval`, the state stays and the run console shows a resume button.
The resume button re-enters the graph from the checkpoint using the LangGraph SQLite checkpointer.

`reconcile()` at startup flips `queued`/`running` rows with no live handle to `interrupted`, but
deliberately does NOT touch `waiting_approval` rows — that is the resume path.

Add a runs history page listing past runs with state, task, session, when it ran, and links to
trace and evidence.

### Per-agent browser isolation

Each agent node gets a dedicated browser worker pinned for the duration of its execution. When the
agent's decide/execute/ask loop runs, it always uses the same worker. When the agent exits, the
worker is released back to the pool.

Implement `pin_agent(node_id)` and `release_agent(node_id)` in `browser.py`. Track
`_agent_pins`, `_active_agent`, and `_default_worker`. The compiler calls `pin_agent` at agent
entry and `release_agent` on every leave. `decide`/`execute`/`ask` re-pin idempotently for resume
safety. Released workers are kept as `_default_worker` so `verify_node`'s `capture_evidence` still
lands on the correct page after the agent leaves. `stop()` resets all pin state so each run starts
clean.

### PDF parsing for uploaded documents

When a PDF is uploaded, extract its text using `pdfminer.six` and store the extracted text as a
`.txt` file next to the original in `company_data/<tenant>/`. When the agent reads a document, it
reads the `.txt` version if one exists. If extraction fails, store an empty `.txt` and log a
warning so the failure is visible.

Add a test that uploads a minimal valid PDF and confirms the extracted text file appears and
contains readable content.

### Approval as a prominent dashboard card, not a text prompt

Change the approval interrupt in the run console to look like a decision item. Show: the action
being requested (tool + data), the policy rule that triggered the approval (from the trace event),
a summary of what the agent has done so far, and two buttons — Approve and Reject. The card must
stay visible after the decision, showing what was decided and who approved it (from
`session.user_role`). Do not use JavaScript for this beyond what HTMX already provides. `hx-post`
on the approve/reject buttons is enough.

When the request carries `HX-Request`, the `/answer` endpoint returns the refreshed card body so
the verdict swaps in place; otherwise it still 303-redirects.

### External API connector for the tool registry

Add a `ConnectorDef` model with: name, description, HTTP method, URL template with
`{{vars.variable_name}}` / `{{session.<key>}}` / `{{app.base_url}}` placeholders, headers, JSON
body template, and permission level. The registry loads these from a `connectors` section in the
flow config, exposed as tools alongside built-ins. The `execute` node calls them with `httpx`.

Add `get_invoice_list` (GET `/api/invoices`) to `configs/finance_employee.json` and prove it works
in a test using `httpx.MockTransport`.

Validate: duplicate names rejected, reserved builtin-name collision rejected, invalid method/level
rejected, unterminated template rejected. The tools-enabled existence check must accept connector
names before registration (validate-before-register).

### Run once, confirm all existing tests still pass

After each addition, run the full test suite before moving to the next.

---

> **Actual Phase 3 outcome (from trace log):**
> - 7 commits: `38732f6` (real model), `fb833b4` (state machine), `c21cb31` (PDF),
>   `0c56091` (classifier), `dc20586` (browser isolation), `bea193f` (approval card),
>   `37aaa1b` (connector)
> - Test count: 63 → 111 (+48)
> - **Real model: NOT tested with a live API key.** No key was present in the workspace. Stub
>   only. The client is wired correctly; a developer who exports `LLM_API_KEY` can run the demo.
> - Browser pool: isolation tested via monkeypatched `pin_agent`/`release_agent`; no multi-agent
>   Playwright run against the live mock app.
> - Approval card: HTMX only, no email or external notification.

---

## Phase 4 additions — NocoBase-inspired platform UX

> **Actual state going in:** 111 tests green.

Start Phase 4 only after Phase 3 is green and committed. Run the full test suite before touching
anything.

### Usage mode and configuration mode

Add a mode toggle to every page header. A button labeled "Configure" switches to configuration
mode and shows all editing surfaces. A button labeled "Use" switches to usage mode and shows only
the run console, run history, and current variables. Store the mode in a cookie (`app_mode`). Add
`data-mode` to the `<body>` element and use CSS to show/hide sections:
`[data-mode=use] .mode-edit { display:none }` and `[data-mode=configure] .mode-run { display:none }`.
Add `GET /mode/{configure|use}` which sets the cookie and 303-redirects back via `Referer`.

This is a CSS and routing change, not an architecture change.

### Data model as a separate configurable concept

Add a data models page. A data model has a name and a list of fields. Each field has a name, type
(text, number, date, boolean), required flag, and a short description for the agent's context.

The Invoice data model defines: `vendor_name` (text, required), `invoice_number` (text, required),
`amount` (number, required), `due_date` (date, required), `purchase_order_ref` (text, optional).

Store data models in `configs/data_models.json` (not a DB table — the compiler and CLI both need
it without a DB dependency). Add `ai_operator/datamodel.py` with `FieldSpec`, `DataModel`,
`load_models`, `save_models`, `get_model`, `prompt_block`, and `write_permission`.

When an agent node is compiled, append the data model's field list below the persona. The verifier
reads the data model for type checks: amount must parse as a number (strip ₹ and commas); due_date
must parse as a valid date. Add `VerificationResult.model_check`.

Add an `AgentNode.data_model` field. The shipped `ap_agent` gets `"data_model": "Invoice"`.

### Field-level permissions per agent

Each field in the data model has `write_agents` and `read_agents` lists. In
`ai_operator/variables.py`, `write_variable` checks `write_permission(node_id, name)` before
writing. If the writer is not in `write_agents`, the write is rejected: log a `permission_denied`
trace event with old/new values, return a `PERMISSION DENIED` observation, and leave the variable
unchanged. This is enforced in code, not in the prompt.

The mandated test: an agent with `policy_agent` (not in `write_agents`) tries to write `amount`;
variable remains absent, trace contains `permission_denied`; control run as `ap_agent` writes it
correctly; non-model vars (`task_type`) are unrestricted.

Add a field-level audit section to the data models page showing every `variable_written` and
`permission_denied` event from `trace.jsonl` filtered to model fields.

### Visual flow editor: clickable nodes, not just a read-only diagram

Replace the static Mermaid diagram with a server-generated SVG. Each node shape is a `<g>` with
`hx-get="/flow/nodes/{id}"` and `hx-target="#node-panel"`. Clicking a node swaps the node edit
form into a panel beside the diagram.

Layout: fixed top-to-bottom, nodes in config order, `x=80`, `w=240`, `h=52`, pitch `98`.
Edges: solid for next, dashed for fallback, labeled match/mismatch. Shape by kind: `rect/rx8`
(agent), `rx16` (message), hexagon (verify), stadium (end). Start badge on the configured start
node.

Routes: `GET /flow/diagram` (fragment), `GET /flow/nodes/{id}` (404 for unknown),
`POST /flow/nodes/{id}` → validate-before-write, 400+panel-with-error on invalid,
`HX-Trigger: refresh-diagram` on success. This is not a drag-and-drop canvas. Connections are
still added via the form.

### Plugin-style tool connectors with a connector registry page

Add a `connectors` table to the dashboard DB: id, name, base_url, description, endpoints (JSON),
default headers (JSON). `flowcfg.publish_connectors()` mirrors DB rows into the flow config's
`connectors` section and calls `sync()`. Every page mutation calls `publish_connectors()` with
rollback on error (validate-before-write).

The shipped example "Mock Payables System" has one endpoint: "Get invoice list" (GET
`/api/invoices`, reads vendor and amount fields, permission level: read).

### Audit trail page

Add `GET /audit`. Read every `trace.jsonl` across all runs. Show events in reverse-chronological
order: run id, node, event type, timestamp, short summary. Typed labels:
- `variable_written`: old → new values
- `tool_disabled`: tool name + reason (scope: agent or session)
- `visit_limit_hit`: node + limit + total
- `approval_requested`: action + reason + decision (from the paired `human_input` event)
- `verification_result`: matched / MISMATCH with details

Click-to-expand shows the full event JSON. Filter by run id, node, or event type via query
parameters (linkable). Cap at 500 rows. The mandated test writes a synthetic `trace.jsonl` and
asserts all five typed summaries, the expand `<details>/<pre>`, ordering, filters, and empty-state.

### What not to change in Phase 4

Do not change the agent runtime, the LangGraph graph, the tool implementations, the verifier
logic, or the test suite for existing agent behavior.

After each addition, run the full test suite. Add at least one test per new page.

---

> **Actual Phase 4 outcome (from trace log):**
> - Commits: `415710a` (mode toggle), + 5 further commits for P4-2 through P4-6
> - Test count: 111 → 118 (+7: 4 mandated + 3 supplementary)
> - 4 mandated tests all present: data-models CRUD cycle, field-permission write rejection,
>   connector registry load into tool registry, audit trail from synthetic trace.
> - All NAV links resolve. No dead links remain after P4-6.
> - Real model still untested (no API key in workspace).
> - SVG editor clickable nodes, mode toggle cookie, connectors DB table all working.

---

## Phase 5 additions — Agenta-inspired reliability and observability

> **Actual state going in:** 118 tests green.
> **Phase 5 not yet started.** Run the full suite first, confirm 118, then begin.

### Agent configuration version history

Add an `agent_versions` table: agent node id, full configuration snapshot as JSON, version number
(increments per agent), timestamp, and optional label. Every save through the node edit panel
writes the old configuration as a new version row before applying the change. Versions are never
deleted.

Add a versions tab to the agent edit panel in the SVG flow editor. It shows version history with
timestamp, label, and a diff summary (which fields changed). Clicking a version shows the full
snapshot beside the current one. A restore button writes the old snapshot as a new version (not an
overwrite), so the restore itself is in the history.

Add `agent_versions_used` (JSON dict mapping agent node id → version number) to the `runs` table.
Populate it in `runner.py` when the run starts. The run history page shows which version was used.

Tests: create an agent, save it twice with different instructions, confirm two version rows with
correct diffs, restore v1, confirm v3 is the restore record and the current config matches v1. A
second test confirms that starting a run records the agent version numbers in the run row.

### Token usage and cost tracking per run

Add token tracking to `llm.py`. The Anthropic API returns `usage.input_tokens` and
`usage.output_tokens`. Capture these after every `complete()` call and return them alongside the
response. The decide node passes them to the tracing module as a `model_call` event with token
counts, model name, and agent node id.

Add `estimated_cost_usd()` to `llm.py` with a hard-coded price table for known models
(`claude-sonnet-4-6` default; stub returns zero). Never call this an exact price; label it
"estimated cost" everywhere. Accumulate token counts and cost in run state. The finish node writes
`total_input_tokens`, `total_output_tokens`, `total_estimated_cost_usd` to the final report. The
run history page shows these three numbers. The run console shows a running total updating via SSE.

The stub client returns 250 input and 180 output tokens per call so the suite can exercise
accumulation without hitting the real API.

Test: a full stub run produces non-zero token counts in the report and they appear in the history
row.

### Scheduled background runs

Add a `schedules` table: id, session id, task string, cron expression (daily at HH:MM, weekly on
day at HH:MM), timezone offset in hours, enabled flag, last run time, last run id.

Add a schedules page. A scheduler thread starts with the dashboard app and checks every 60 seconds
whether any enabled schedule is due. A schedule is due when the current time matches the configured
time within the last 60 seconds and it has not already run in that window (compare last run time
to now minus 70 seconds). When due, call the same `start_run` path as the console, with
`source=schedule` and the schedule id recorded.

The scheduler thread must catch all exceptions and log them without dying. If a scheduled run fires
while a manual run is active, log a skipped event and try again at the next check. Add
`skipped_reason` to the `runs` table.

Tests: a schedule that is due fires a run (mock the current time and the runner, confirm a run row
is created with `source=schedule`); a schedule that would fire while another run is active is
skipped with a reason recorded.

### Reusable agent skills

Add a `skills` table: id, name, description, body (plain text instruction content). Skills are
global to the platform, not per-session.

Add a skills page. Show which agents currently use each skill.

Add a `skills` field to `AgentNode` in `platform/flow/models.py` as a list of skill names. The
compiler reads skills from the database and appends their bodies below the agent's own instructions
and above the data model block, each labelled with the skill name. If a skill name cannot be found
at compile time, raise a clear validation error (same model as the tools-enabled check). The
validator checks skill references before running.

Add a skill attachment section to the agent edit panel in the SVG flow editor: currently attached
skills with a remove button, and a dropdown of available skills to add. Attaching or detaching a
skill saves the config immediately and creates a new agent version row.

Tests: attaching a skill compiles it into the correct position in the system prompt. Referencing a
skill that does not exist produces a named validation error, not a silent omission.

### Prompt testing panel per agent

Add a test panel to each agent's edit view in the SVG flow editor, visible in configuration mode.
It shows a textarea for a sample task input, a run button, and a response area. When clicked, it
sends `POST /flow/nodes/{id}/test`. That endpoint builds a minimal prompt using the agent's current
saved configuration (system prompt, instructions, attached skills, data model block — no tools, no
browser) and calls the LLM with a simplified user message asking the agent to reason about the
input. It does not execute any tools. It returns the raw model response as plain text.

With `STUB_MODEL=1`, return a canned string so tests can hit the endpoint without an API key.

Stream the response back using the same SSE pattern as the run console.

Test: the endpoint returns 200 with a non-empty body when the stub is active, and the request
included the agent's system prompt and instructions in the expected format.

### What not to change in Phase 5

Do not touch the agent runtime, the LangGraph graph, the verifier, tool implementations, or the
existing agent-behavior tests. Phase 5 adds the version history and skills to the data layer,
token tracking to the LLM client, the scheduler as a separate thread, and the test panel as a new
dashboard endpoint. None of these modify how an existing run executes.

After each addition, run the full test suite before moving on. Commit each piece separately. At
the end, report the test count before and after Phase 5, list every new commit, and note clearly
whether the scheduled runs and the real LLM test panel have been verified with actual API calls or
only with the stub.

---

## Phase 6 additions — NocoBase open-interface and collaboration patterns

> Referenced: https://github.com/nocobase/nocobase (read 2026-10-08)
>
> NocoBase's current pitch: **"AI and people build together on production-proven infrastructure."**
> Its repo now ships `AGENTS.md`, `CLAUDE.md`, and `CONTEXT.md` so coding agents can set up and
> extend the system end-to-end. It has a full CLI (`nb init`, `nb --version`), open interfaces for
> external agents (MCP, HTTP API, CLI), AI employees in both front-end (analysis, Q&A, form
> filling) and back-end (document recognition, risk monitoring, task routing), and a microkernel
> plugin architecture where everything is a plugin with shared conventions.
>
> Phases 4 and 5 already implemented NocoBase's WYSIWYG mode toggle, data models decoupled from
> UI, field-level permissions, connectors UI, and audit trail. Phase 6 adds the parts that are
> new or sharper in the current repo: open interfaces for external agents, a CLI for coding agents,
> AI-assisted flow drafting with a human gate, event triggers beyond scheduling, notification
> channels, guardrail lint, a plugin registration point, tenant export, and config schema
> versioning.
>
> **Start Phase 6 only after Phase 5 is green and committed. Run the full suite first, confirm the
> count, record it below, then begin.**

### 0. Carry-over check (do this before anything else)

Write `STATUS.md` at the repo root from a fresh run, not from memory:

1. Exact test count and pass/fail.
2. For each Phase 3 and 5 feature that depends on a real API (real Claude model, prompt test
   panel, scheduled runs, token cost), state whether it has been verified with a real call or only
   with the stub. Write "stub only" where that is true. The Phase 3 real model has never been
   tested with a live `LLM_API_KEY`; say so explicitly.
3. Every item currently in `KNOWN_LIMITATIONS`.

Commit `STATUS.md` on its own. Every subsequent phase report must be consistent with it.

### 1. Roles shared by humans and agents

NocoBase states: "Every AI action follows the same fine-grained permissions as human users. Each
AI employee has its own role, with field-level read and write permissions."

- Add a `roles` table: id, name, description. Seed `finance_manager`, `ap_clerk`, `auditor`.
- Add a `role_permissions` table: role id, resource (a data model field, a tool name, a connector
  endpoint, or a named action such as `approve_payment`), and access level (`none`, `read`,
  `write`, `approve`). An optional `approve_limit_amount` applies to the `approve` level.
- Add a `role` field to `AgentNode` in the flow config. Phase 4's per-field `write_agents` /
  `read_agents` lists become a derived view of this table, not a second source of truth. Migrate
  existing permissions into roles and keep all Phase 4 tests passing.
- The session context's `user_role` must reference a real role in the table. The approval card
  checks that the approver's role has `approve` on the relevant action AND that the amount is
  within the role's limit. If not, the card shows "requires a higher role" and the decision is
  rejected in code, with an `approval_denied_role` trace event.
- A permissions matrix page in configuration mode: roles as rows, resources as columns, inline
  toggle per cell. Every change writes to the audit trail.

Tests: an agent with a read-only role cannot call a write tool — the call is blocked and traced.
An `ap_clerk` cannot approve above their limit; a `finance_manager` can. A wrong role on the
approval card returns the denial message, not a 200.

### 2. Open interface: HTTP API and MCP server

NocoBase exposes "MCP, HTTP APIs, CLI and rich skills" so external agents (n8n, Dify, Coze and
others) can connect under the same permission boundaries as internal ones.

- Add a versioned JSON API under `/api/v1/`: `POST /runs` (session id and task),
  `GET /runs/{id}` (state, report, variables, token totals), `GET /runs/{id}/events` (SSE),
  `POST /runs/{id}/answer`, `GET /approvals?state=pending`. Reuse the existing `start_run` and
  `/answer` code paths. Do not duplicate logic.
- Authentication: an `api_keys` table (key hash, label, role id, created, revoked). Every API
  call runs as the key's role, subject to the same role checks as section 1. Store only hashed
  keys; show a key once at creation.
- Add a minimal MCP server (stdio or HTTP, whichever the existing stack supports with the least new
  code) exposing four tools: `run_task`, `get_run_status`, `list_pending_approvals`,
  `answer_approval`. It calls the HTTP API internally so the permission path is identical.
- An API keys page in configuration mode: create, label, assign a role, revoke.

Tests: calling the API without a key returns 401. A key with `auditor` role cannot start a run. A
valid key starts a stub run and `GET /runs/{id}` shows the same report as the console.

Report whether the MCP server was tested with a real MCP client or only with a scripted one.

### 3. A CLI and agent-facing repo docs

NocoBase ships an `nb` CLI and skills so coding agents can handle setup, development, migration
and release end to end. Its repo root has `AGENTS.md`, `CLAUDE.md`, and `CONTEXT.md` for those
agents.

- Add `fin` as a console script (Typer or argparse). Commands: `fin init` (create DB, seed example
  flow, roles and sessions), `fin flow validate <file>`, `fin flow export` / `fin flow import
  <file>`, `fin run --session <id> "<task>"`, `fin runs list`, `fin approvals list`.
- `fin flow import` must run the validator and refuse invalid configs with the plain-English error
  list. It must create an agent version row for every changed agent.
- Add `AGENTS.md` and `CONTEXT.md` at the repo root. `AGENTS.md` covers build/test commands,
  the "do not change" list from each phase, commit discipline, and the stub-versus-real-model rule.
  `CONTEXT.md` covers the architecture in one page: flow config, compiler, variables vs session
  context, roles, tools and connectors. Keep both short and accurate. Wrong statements are worse
  than missing ones.

Tests: `fin flow validate` returns non-zero on the broken fixture and zero on
`finance_employee.json`. A `fin flow import` round trip produces version rows.

### 4. AI-assisted flow drafting with a human gate

NocoBase's collaboration model is that "AI can quickly create data models, pages, and workflows;
people can quickly refine the UI and interactions."

- A "Draft with AI" box in configuration mode. The company types a plain-English description of
  the employee they want.
- The server calls the LLM with the flow JSON schema, the list of registered tools and connectors,
  the list of skills and data models, and the description. It requests a flow config JSON only.
- The result is run through the validator. If it fails, retry once with the error list. If it
  still fails, show the errors and apply nothing.
- A valid draft is shown as a side-by-side diff against the current flow and previewed in the SVG
  editor. Nothing is saved until the human clicks Apply. Applying creates agent version rows
  labelled "AI draft".
- The draft may not enable a tool or field permission that the current user's role does not hold.
  Validation catches this.
- With `STUB_MODEL=1`, return a canned valid two-agent draft so tests run offline.

Tests: a stub draft validates and renders a diff. An invalid LLM output is rejected and the
current flow is untouched. Apply writes version rows labelled "AI draft".

### 5. Triggers: from "someone types a task" to "something happens"

NocoBase's back-end AI employees act on events such as document arrival and risk signals. Phase 5
adds time-based schedules. Add event triggers using the same `start_run` path and the same
one-run-at-a-time rule.

- A `triggers` table: id, session id, type (`inbox_folder`, `webhook`), config JSON, task
  template, enabled, last fired, last run id.
- `inbox_folder`: the scheduler thread polls `company_data/<tenant>/inbox/` every 60 seconds. A
  new file starts a run with the task template (placeholders: `{{file.name}}`, `{{file.path}}`).
  Processed files move to `inbox/processed/`. Failed-run files move to `inbox/failed/`.
- `webhook`: `POST /api/v1/triggers/{id}/fire` with the trigger's own secret in a header. The
  body can supply template values.
- A triggers page next to schedules, with the same enable/disable toggle and last-run link.
- If a run is already active, record `skipped_reason` and leave the file in the inbox.

Tests: a file dropped into the inbox fires exactly one run and is moved to processed. A webhook
with a wrong secret returns 403 and fires nothing.

### 6. Approval and alert channels

NocoBase connects to Slack, Telegram, WhatsApp and Gmail so people can act outside the app.

- A `channels` table: id, type (`webhook` for Slack/Teams-style incoming webhooks, `email_smtp`),
  config JSON, enabled.
- When a run enters `waiting_approval`, send a notification with the action summary, the policy
  rule that fired, and a signed one-time link to the approval card. The link opens the card but
  cannot bypass the role check from section 1.
- Also notify on `failed` and `interrupted`.
- A channels page with a "send test message" button.
- Do not claim real delivery in the report unless tested against a real Slack webhook or SMTP
  server. A local capture server counts as "stub".

Tests: entering `waiting_approval` queues exactly one notification. An expired or reused link is
rejected.

### 7. Guardrail lint on flow configs

NocoBase says "built-in guardrails keep AI output aligned with the system architecture." Extend
the validator with warnings. Warnings are shown separately from blocking errors and never throw.

Warnings to implement:

- An agent has a write-level tool but no approval rule or threshold applies to it.
- An agent has the browser tool and no visit limit override below the platform default.
- A message node template references a variable that only some paths set (not all paths leading
  to this node write it).
- An agent's role has `write` on a field that no tool or instruction mentions.
- A connector endpoint with write permission is enabled for an agent whose role is read-only.
- An agent has no data model attached but its instructions mention extracting fields.

Show warnings in the flow editor beside the validate button and in `fin flow validate`. Add a
"guardrail score" count (number of warnings) to the flow header. Do not make warnings blocking.

Tests: one fixture per warning, asserting its exact message text.

### 8. Microkernel-style plugin registration (small and bounded)

NocoBase uses a microkernel where "everything is a plugin and the system can grow without losing
control." Take the smallest useful slice: new tools, connectors, and node types register through
one interface instead of edits to core files.

- Define `PluginManifest` (name, version, description, provides: list of tools / node types /
  dashboard pages) and a `plugins/` directory scanned at startup.
- Move one existing built-in group (the connector tool loader from Phase 3/4) behind the plugin
  interface as proof. Do not move the rest.
- A plugins page in configuration mode lists installed plugins and what each provides, read-only.
- A plugin that fails to load must be logged and skipped. It must not stop the dashboard from
  starting.

Test: a fixture plugin that adds one tool appears in the tool registry and in the agent tool
toggles. A plugin that raises on import is skipped and the app still starts.

### 9. Data sources and no-lock-in export

NocoBase says "your data always stays in your own database, without platform lock-in" and treats
"external databases and third-party APIs as data sources."

- **Tenant export:** `fin export --tenant <name>` and a dashboard button produce a zip with flow
  config, agent versions, skills, data models, roles, connectors (secrets redacted), sessions,
  runs, trace files and evidence. `fin import` restores into an empty database. This is the
  concrete answer to "can a company leave?"
- **Read-only data source connector type:** alongside HTTP connectors, support a CSV or SQLite
  file source with named queries. Expose each saved query as a read tool with the same role checks.
  No arbitrary SQL from the agent — queries are defined on the connectors page and parameters come
  from `{{vars.*}}`.

Tests: an export → import round trip yields identical flow config and run counts. An agent cannot
execute SQL that is not a saved query.

### 10. Config schema versioning and changelog

NocoBase publishes regular release notes and a maintained `CHANGELOG.md`.

- Add `schema_version` to the flow config. The loader runs ordered migrations when it sees an
  older version and writes the migrated config as a new agent version row labelled "schema
  migration".
- Add `CHANGELOG.md` with one entry per phase and per commit group from Phase 3 on.
- Add a `/about` page showing the platform version and the last ten changelog entries.

Tests: a v1 config fixture loads, migrates and validates. An unknown newer version is refused with
a clear message.

### Build order for Phase 6

1. `STATUS.md` carry-over check (must be first).
2. Roles (section 1) — sections 2, 4 and 6 depend on it.
3. Guardrail lint (section 7) — small and improves everything after it.
4. HTTP API and MCP server (section 2).
5. CLI and `AGENTS.md` / `CONTEXT.md` (section 3).
6. Triggers (section 5), then channels (section 6).
7. AI flow drafting (section 4).
8. Data sources and export (section 9), plugin slice (section 8), schema versioning (section 10).

**Demo-worthy trio if time is short:** roles with the approval limit check, the HTTP API / MCP
interface (an external agent triggering the employee), and AI drafting with the human Apply gate.

### Commit discipline and reporting

Each numbered section is its own commit (or more), with a message stating exactly what changed.
Run the full suite after each. At the end, report:

- Test count before and after Phase 6, and how many were added.
- Every new commit.
- What was verified with real services (real Claude model, real MCP client, real Slack/SMTP, real
  trigger firing) versus stub only. Be direct about each item.
- Updated `KNOWN_LIMITATIONS`.
- Exact commands to start the dashboard, mock app, scheduler and MCP server, and the URL of each
  new page.

### What not to change in Phase 6

Do not change the agent runtime loop, the compiled graph semantics, tool implementations, the
verifier logic, or the existing agent-behavior tests. Roles, API, CLI, triggers, channels, lint,
plugins and export all wrap around the existing system. If a section appears to require a change to
one of these, stop and report why instead of editing it.