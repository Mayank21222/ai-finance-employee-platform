# ai-operator

A single-agent autonomous operator prototype built on LangGraph. It takes a short
business request, plans and executes with tools, pauses for human approval when
policy requires it, and is marked complete only by a separate deterministic
verifier reading real app state.

## Layout

- `company_data/` — vendor invoices, `policies.md`, `procedures.md`, persistent memory
- `mock_app/` — FastAPI payables app (form + list + read-only API) with failure switches
- `operator/` — graph, state, nodes, tools, permissions, verifier, tracing, model client
- `prompts/` — six-part prompt files (role, task, constraints, output format, examples, fallback)
- `runs/` — per-run `trace.jsonl`, evidence screenshots, final `report.json`
- `tests/` — verifier, permissions, registry, and a stubbed-model end-to-end test

## Quickstart

See the final section of the repository history / delivery message for exact commands.

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
uvicorn mock_app.app:app --port 8000   # terminal 1
python -m operator.run "Process the latest invoice from Acme Corp ..."  # terminal 2
```
