# AI Finance Employee

A minimal, local-first platform where a small business connects a **visual
workflow** to a **conversational AI employee** that safely performs real finance
work — vendor and PO lookup, duplicate and policy checks, currency conversion,
calculations, payment preparation — with **human approval on risky actions** and
a **deterministic verifier** that decides whether the job is actually done.

You chat with the employee on the **Run** screen: it explains what it is about
to do, streams each action live, and pauses inline for approval or a
clarification. It reasons with a free local model (`llama3.1:8b` via Ollama) by
default.

The safety guarantee: an AI can never mark its own work "complete". Only a
read-only check of the real system (the verifier) can, and only after any
irreversible action was approved by a human.

## Architecture

```
run.py / builder/dashboard.py      the two entry points (CLI + web UI)
        │
        ▼
ai_operator/runtime.py             LangGraph state machine (the one engine)
   decide → execute → ask → verify → finalize
        │            │
        │            └── human-in-the-loop via LangGraph interrupt (approval / clarification)
        ├── ai_operator/llm.py       one model interface (local default / stub / optional cloud)
        ├── ai_operator/tools.py     tool registry (finance actions + file / memory / http-form "browser")
        ├── ai_operator/policy.py    permissions + financial guardrail tiers
        └── ai_operator/verifier.py  deterministic, read-only completion check
        │
        ▼
builder/flow.py, store.py, dashboard.py   low-code platform over the same engine
mock_app.py + company_data/               the sandbox environment
```

- **One engine, no duplication.** The platform's flow config compiles an
  `Employee` onto the same `Operator` the CLI uses.
- **Where execution happens:** inside the sandbox app (`mock_app.py`) — the
  agent's actions are real HTTP form submissions against a local server.
- **Where intelligence happens:** `ai_operator/llm.py`. It defaults to a local
  OpenAI-compatible endpoint (Ollama `llama3.1:8b`) and falls back to a
  deterministic stub — no cloud key required.

### Finance actions the LLM cannot do alone

These deterministic tools live in `ai_operator/tools.py` and act on the sandbox:
`vendor_lookup`, `po_lookup`, `duplicate_check`, `policy_check`,
`currency_convert`, `financial_calculator`, `create_payment`,
`update_vendor_bank`, `generate_report`. The model chooses *when* to call them;
the code does the lookup, the math, and the write.

## Quickstart

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # optional

# 1) start the sandbox payables app (terminal 1)
uvicorn mock_app:app --port 8001

# 2a) run the CLI employee (terminal 2)
python run.py                   # approves interactively
python run.py --approve-all     # approves everything automatically

# 2b) or run the low-code dashboard (terminal 2)
uvicorn builder.dashboard:app --port 8080
# open http://127.0.0.1:8080

# 3) tests
pytest -q
```

## Safety model

| Action | Permission | Behaviour |
| --- | --- | --- |
| lookup / search / read / calculate | read | runs automatically |
| fill a form field | reversible_write | runs automatically up to the amount guardrail |
| prepare a payment / change bank details / submit | irreversible_write | **requires human approval** |
| verify | read | runs automatically |

Financial guardrail tiers (`Improvements.md` §34): `< 10,000` auto,
`10,000 – 50,000` manager approval, `>= 50,000` finance approval. Default
currency is INR. A financial amount is only an explicit `amount` argument, so
typing "42500" into a form field named *amount* does not trip the guardrail.

## Configuration

`.env` (see `.env.example`):

- `MODEL_PROVIDER` = `groq` (this build), `local`, `stub`, `openai`, `anthropic`, `gemini`
- `MODEL_NAME` = `openai/gpt-oss-120b` for Groq (or `llama3.1:8b` for local)
- `MODEL_BASE_URL` = `http://localhost:11434/v1` (only used when provider = `local`)
- `EMPLOYEE_MODEL` = model new employees are created with (`groq`|`local`|`stub`)
- `GROQ_API_KEY` = required when `MODEL_PROVIDER=groq` (paste it in `.env`, never commit)
- `APP_BASE_URL` (sandbox app), `STORAGE_DIR` (`.data`), `LOG_LEVEL` (`INFO`)

Groq is OpenAI-compatible (`https://api.groq.com/openai/v1`) and is the fastest
way to see the agent think. If no key is set the app raises a clear error; set
`MODEL_PROVIDER=stub` (or `EMPLOYEE_MODEL=stub`) to run the whole demo
deterministically and offline.

## Logs (every trace)

Every event — model request/response, decision, tool call, approval, verifier,
final report — is written by `ai_operator/observability.py` to two sinks under
`STORAGE_DIR/logs/`:

- `app.log` — human-readable, rotating (console + file)
- `traces.jsonl` — one JSON event per line, run-correlated

View them in the dashboard **Logs** screen, or tail the JSON API:
`GET /api/logs?run_id=<id>&limit=200`. Each History entry links to its own
filtered log. The same events are also persisted per-run to
`.data/runs/<id>/trace.jsonl`.

## Project layout

```
ai_operator/        the runtime (models, llm, tools, policy, verifier, runtime)
builder/            low-code platform (flow, store, dashboard)
company_data/       sandbox documents (invoices, policies, procedures)
prompts/decide.md   the six-part decision prompt
mock_app.py         sandbox payables web app (with failure switches)
run.py              CLI runner
tests/              pytest suite (verifier, policy, tools, e2e)
```
