# Known Limitations

- **The default model is Groq `openai/gpt-oss-120b`** (fast, hosted, needs
  `GROQ_API_KEY`). The fallback local 8B (`llama3.1:8b`) is capable but slow
  (roughly 10–25s per decision) and sometimes repeats a successful action; a
  deterministic loop guard forces a stop after repeats. Set `MODEL_PROVIDER=stub`
  for a fast, deterministic demo that only "knows" the invoice workflow. Other
  cloud providers (OpenAI/Anthropic/Gemini) are optional and need a key.
- **Logs are file-based, not a log service.** Events stream to
  `STORAGE_DIR/logs/{app.log,traces.jsonl}` and are read back into the Logs
  screen; there is no external aggregation, retention policy, or log redaction
  beyond truncating very long fields.
- **No real browser.** The form client handles static HTML forms; it will not
  execute JavaScript, handle logins, CAPTCHAs, or multi-page SPA flows.
- **The sandbox app is a stand-in.** It is a small FastAPI app, not SAP/NetSuite/
  QuickBooks. Real connectors are out of scope.
- **Dashboards persist run metadata, not live state.** If the server restarts,
  in-flight runs are lost (completed runs and their traces remain in SQLite).
- **SQLite concurrency is basic.** Multiple simultaneous runs in the dashboard
  share files under `.data` and are fine for a demo, not for production load.
- **Approval routing is role-tiered, not person-based.** There is no user
  directory, delegation, or escalation chain.
- **The verifier checks one known outcome at a time** — an invoice record or a
  prepared payment, selected by `kind` on the verify action. Richer
  multi-document reconciliation is not implemented.
- **Retry limit is per-tool and fixed** (`MAX_RETRIES = 2`); step limit is
  `MAX_STEPS = 25`. Neither is configurable from the UI yet.
- **No auth, no audit of who approved** beyond a `by = "human"` label.
- **HTMX/SSE design note:** the brief mentions HTMX; to keep the repo
  dependency-free and fully offline this build uses a tiny vanilla-JS SSE/fetch
  layer instead. Dropping in HTMX later is straightforward.
