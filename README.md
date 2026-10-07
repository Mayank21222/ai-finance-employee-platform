# ai-operator

A single-agent autonomous operator prototype built on LangGraph. It takes a
short business request, plans and executes with tools, pauses for human
approval when policy requires it, and is marked complete only by a separate
deterministic verifier reading real app state.

## Layout

- `company_data/` — vendor invoices, `policies.md`, `procedures.md`, memory
- `mock_app/` — FastAPI payables app (form + list + read-only API) with failure switches
- `ai_operator/` — graph, state, nodes, tools, permissions, verifier, tracing, model client
- `prompts/` — six-part runtime prompts (role, task, constraints, output format, examples, fallback)
- `runs/` — per-run `trace.jsonl`, evidence screenshots, final `report.json`
- `tests/` — permissions, registry, verifier, and end-to-end tests (all failure modes)

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

## Tests

```bash
python -m pytest tests -q
```

27 unit tests run in milliseconds; the 3 end-to-end tests start their own
mock app on port 8011 and drive the full graph (happy path, `renamed_field`,
`popup,validation`).

## How completion is decided

The model never declares success. After the agent claims the task is done, the
verifier independently re-derives the expected record (latest Acme invoice on
disk), reads `GET /api/invoices`, and matches amount (±0.01) and due date.
Only `verify() -> matched` plus an evidence screenshot yields
`verified_complete`; everything else exits 1.
