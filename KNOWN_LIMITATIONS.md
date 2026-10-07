# Known Limitations

1. **The default model is a stub.** `MODEL_PROVIDER=stub` (the default) runs a
   deterministic rule-based script, not an LLM. It exists so the demo and the
   e2e tests work offline with no API key. The graph, approval interrupts,
   tool layer, tracing, and verifier are all real; only the *decision-making
   brain* is scripted. Setting `MODEL_PROVIDER=openai` or `anthropic` selects
   real httpx-based clients, but they have not been exercised against live APIs
   in this session (they need a key and network).

2. **The verifier only understands invoice-entry tasks.** It derives ground
   truth (vendor from the task text, latest `Invoice Date` on disk) and compares
   it against `GET /api/invoices`. Any other task kind is reported honestly as
   unverified (`matched: false`, "no deterministic checker") — the run will end
   `failed` rather than pretend success.

3. **The runner reuses whatever server is already on `APP_URL`.** If you start
   `uvicorn` manually and later edit `mock_app/`, the running process keeps the
   old code (uvicorn is started without `--reload`). Restart it after edits —
   this exact stale-server scenario caused a full debugging session.

4. **`renamed_field` relies on a backend alias.** The switch renames the due
   date input's `id` *and* `name` to `due-date-field`; `POST /invoices` accepts
   either `due_date` or `due-date-field` so the renamed form can actually save.
   The agent must still discover the new selector by reading the page.

5. **Browser pool of 2, sticky per thread.** All browser tools are serialised
   through a pool of at most 2 pages; each calling thread keeps a sticky page
   for the life of a run (claims of dead threads are purged). A tool call's
   timeout stops the *caller* from waiting, but does not cancel work already
   running in the worker; internal waits are capped (≤4 s each) so a single op
   stays under `TOOL_TIMEOUT_SECONDS`.

6. **`--answer` is a single blanket reply.** In non-interactive mode every
   approval/clarification prompt receives the same answer (`y`, `n`, or a
   sentence), so a run cannot approve one prompt and deny another.

7. **Approval threshold is amount-only.** Any tool arg named `amount` above
   INR 50,000 triggers approval regardless of tool, and every
   `irreversible_write` tool (currently `submit_form`) always requires
   approval. There is no per-user or per-tool override list.

8. **Checkpoints accumulate; `run_id` reuse resumes state.** Every run writes
   to `checkpoints.db` keyed by `run_id`. Re-running with an existing `run_id`
   resumes that thread's old state (LangGraph) instead of starting fresh —
   pick a new `run_id` per run (the default timestamped id is safe). The file
   is never pruned.

9. **Platform.** Developed and tested on macOS with Python 3.12; the e2e tests
   bind port 8011. Windows is untested.

10. **Dashboard runs one at a time.** The run console serialises runs on
    purpose: concurrent runs would race on the mock app's payables records and
    on flow-level state. There is also no authentication — it is a local
    single-user tool (bind it to localhost yourself if that matters).

11. **Dashboard config edits apply from the next run.** Tool toggles, agent
    prompts, message templates, connections, and document attachments rewrite
    `configs/finance_employee.json` through a validate-before-save helper, but
    a currently running graph was already compiled from the old config.

12. **Agent status/reasoning on the run page is a heuristic.** The compiler
    traces node entry/exit only for flow-level nodes, so live "current
    reasoning" is attributed to the most recently entered agent that has not
    saved its answer yet, and a finished run marks an entered-but-unsaved
    agent `failed`. The saved answer itself (`variable_written`) is exact.

13. **Documents: PDFs are stored but not parsed.** Uploads land in
    `company_data/<tenant>/` and attach per agent, but prompt injection reads
    utf-8 text (PDF bytes render as "(unreadable document...)"). Session
    variables are strings only; message templates cannot compute values.
