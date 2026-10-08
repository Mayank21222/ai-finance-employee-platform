# AGENTS.md — working with this repository

Short, accurate notes for coding agents. Wrong statements are worse than
missing ones: if something here disagrees with the code, trust the code and
fix this file.

## What this is

A single-agent autonomous operator (LangGraph) plus a low-code platform:

- `ai_operator/` — runtime: graph assembly, typed state, nodes
  (understand → decide ⇄ execute/ask → verify → finish), permission layer,
  role gates, deterministic verifier, tracing, model client, CLI runner.
- `platform/flow/` — flow config models, validator (errors + guardrail
  warnings), compiler (flow JSON → LangGraph StateGraph).
- `platform/dashboard/` — FastAPI + HTMX control plane (run console, roles
  matrix, API keys, editors), SQLite storage, scheduler, background runner.
- `platform/cli.py` — the `fin` CLI; `platform/mcp_server.py` — stdio MCP.
- `configs/` — `finance_employee.json` (the shipped flow),
  `data_models.json` (extraction contracts).
- `mock_app/` — the payables app the demo task drives (plus failure switches).
- `tests/` — the whole safety net.

## Build / test / run commands

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env

.venv/bin/python -m pytest tests -q          # full suite (must stay green)

python -m uvicorn mock_app.app:app --port 8000        # mock payables app
python -m ai_operator.run --fresh --answer y          # CLI demo run
python -m uvicorn platform.dashboard.app:app --port 8001   # dashboard

./fin init                                    # or: python -m platform.cli init
./fin flow validate configs/finance_employee.json
./fin runs list
FIN_API_KEY=... FIN_DASHBOARD_URL=http://127.0.0.1:8001 \
    python -m platform.mcp_server             # MCP over stdio
```

Start uvicorn as `python -m uvicorn ...` from the repo root: the local
`platform/` package must win over the stdlib module of the same name.

## Stub vs real model (never misreport this)

- Tests and CI pin `STUB_MODEL=1` (see `tests/conftest.py`). The default
  provider is the deterministic **stub**, not an LLM.
- Real models: `LLM_PROVIDER=anthropic|openai` + `LLM_API_KEY` in `.env`.
- Never claim a feature was verified against a real service (model API, MCP
  host, Slack webhook, SMTP) unless it actually ran against one. A scripted
  or local-capture test is "stub" — say so.

## Do-not-change list (carried from the phase specs)

- **Agent runtime loop, compiled graph semantics, tool implementations,
  verifier logic, and existing agent-behavior tests are off limits** unless
  the task explicitly says otherwise. Phase 6 features wrap around them.
- If a change seems to require touching one of these, stop and report why
  instead of editing.
- `prompts/` files are program assets; do not edit prompt files unless the
  task explicitly asks.

## Commit discipline

- One phase section per commit; the message states exactly what changed.
- Run the full suite after each section before committing.
- Keep `STATUS.md` / `KNOWN_LIMITATIONS.md` honest: update them when
  behaviour or verification status changes; never inflate test counts or
  real-service claims.
