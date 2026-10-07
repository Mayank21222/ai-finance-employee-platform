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

5. **One browser, one worker thread.** All browser tools share a single page
   and are serialised through a queue. A tool call's timeout stops the *caller*
   from waiting, but does not cancel work already running in the worker;
   internal waits are capped (≤4 s each) so a single op stays under
   `TOOL_TIMEOUT_SECONDS`.

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
