# ai-operator

A single-agent autonomous operator prototype built on LangGraph. It takes a
short business request, plans and executes with tools, pauses for human
approval when policy requires it, and is marked complete only by a separate
deterministic verifier reading real app state.

## Layout

- `company_data/` — vendor invoices, `policies.md`, `procedures.md`, memory
- `mock_app/` — FastAPI payables app (form + list + read-only API) with failure switches
- `ai_operator/` — graph, state, nodes, tools, permissions, verifier, tracing, model client
- `platform/` — low-code layer: flow models/validator/compiler + FastAPI dashboard
- `configs/` — `finance_employee.json`, the shipped flow config the graph compiles from
- `prompts/` — six-part runtime prompts (role, task, constraints, output format, examples, fallback)
- `runs/` — per-run `trace.jsonl`, evidence screenshots, final `report.json`
- `tests/` — permissions, registry, verifier, e2e, platform flow, dashboard tests

## Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env        # MODEL_PROVIDER=stub works offline
```

## Run the demo

Terminal 1 — start the mock payables app:

```bash
python -m uvicorn mock_app.app:app --port 8000
```

Terminal 2 — run the operator:

```bash
python -m ai_operator.run --fresh --answer y
```

- `--fresh` clears payables records first; `--answer y` auto-approves the
  submit approval so the run is non-interactive. Drop `--answer` to answer the
  approval prompt (`y`/`n`) yourself.
- Success = exit code 0 and `"status": "verified_complete"` in the final
  report; the evidence screenshot lands in `runs/<run_id>/evidence/`.
- `--app-url` points at a different server; `--no-start-app` fails fast if the
  app is not reachable (otherwise the CLI spawns uvicorn itself).

### Failure switches

```bash
python -m ai_operator.run --fresh --answer y --failures renamed_field
python -m ai_operator.run --fresh --answer y --failures popup,validation
python -m ai_operator.run --fresh --answer y --failures slow
```

| switch | behavior |
|---|---|
| `renamed_field` | due-date input renamed to `#due-date-field` |
| `popup` | overlay popup must be closed before the form is usable |
| `validation` | comma-formatted amounts are rejected until re-sent plain |
| `slow` | responses delayed to exercise tool timeouts |

They map to `POST /debug/failures` / `POST /debug/reset` on the mock app.

### Model provider

`MODEL_PROVIDER` = `stub` (default, offline, deterministic) | `openai` |
`anthropic`. See `.env.example`.

## Dashboard (run console + editors)

Terminal 2 — start the dashboard:

```bash
.venv/bin/python -m uvicorn platform.dashboard.app:app --port 8001
```

Open <http://127.0.0.1:8001/runs>, pick a session, type a request, start a
run. The trace streams live over SSE; approval/clarification prompts appear
inline (answer without leaving the page); agent cards show status, current
reasoning, the saved answer, and clickable tool toggles.

Pages: **Run console** (runs + history + report), **Sessions**
(tenant/currency/threshold/role), **Flow editor** (Mermaid diagram, connect
nodes, inline validator), **Agents** (persona, instructions, save_as,
fallback, tool toggles), **Message nodes** (templates with a
`{{vars.x}}` insert dropdown, next/fallback picks), **Documents** (upload to
`company_data/<tenant>/`, attach per agent).

Notes:

- One run at a time (runs would fight over the payables data); config edits
  apply from the next run.
- Use `python -m uvicorn`, not the `uvicorn` console script: the repo's
  `platform/` package must win over the stdlib module of the same name, which
  needs the working directory on `sys.path`.
- Dashboard state lives in `dashboard.db` (sessions + run history).

## Tests

```bash
python -m pytest tests -q
```

62 tests run in seconds: permissions, registry, verifier, the 3 end-to-end
failure-mode tests (their own mock app on port 8011), the platform flow
system (validator, compiler, template rendering, visit limits), and the
dashboard (sessions, run lifecycle, SSE, agents view, config editors).

## How completion is decided

The model never declares success. After the agent claims the task is done, the
verifier independently re-derives the expected record (latest Acme invoice on
disk), reads `GET /api/invoices`, and matches amount (±0.01) and due date.
Only `verify() -> matched` plus an evidence screenshot yields
`verified_complete`; everything else exits 1.
