# Assumptions

1. **Repo root is the workspace root.** The requested tree's `ai-operator/` is the
   repository name itself, so files live directly at the repository root rather
   than inside a nested `ai-operator/` folder.
1a. **Package renamed `operator` → `ai_operator`.** This Python preloads the
   stdlib `operator` module into `sys.modules` during interpreter startup, so a
   top-level package named `operator/` is unreachable (verified: `import
   operator` resolves to the stdlib file; `python -m operator.run` cannot work).
   The folder is therefore `ai_operator/` — layout otherwise matches the
   requested tree; this is noted per the fallback rule because it is an
   import-plumbing decision, not an architecture change.
2. **No meta-prompt files.** The session instruction forbids creating new
   meta-prompt/instruction files. The `prompts/` directory is still created
   because the task requires the *runtime agent's* prompts to live as files
   there; these are program assets loaded by `operator/nodes`, not new
   instruction files for the session.
3. **Model provider via environment.** `MODEL_PROVIDER` selects `openai`,
   `anthropic`, or `stub`. The `stub` provider is a deterministic scripted model
   so the demo and e2e test run with no network and no API key; it is clearly
   labelled as stubbed in KNOWN_LIMITATIONS.md.
4. **"Latest invoice" = latest `Invoice Date` field** in the invoice text files,
   not file mtime.
5. **Read-only verification path** = `GET /api/invoices` on the mock app via
   httpx from plain code; the verifier never uses the browser or the model.
6. **Approval threshold** is evaluated on the invoice amount parsed as a number:
   `amount > 50000` (INR) requires human approval, per `company_data/policies.md`.
7. **Failure switches** are toggled per run with `POST /debug/failures` (or the
   `--failures` flag of `operator.run`), not by editing app code.
8. **Ask-human** and **policy approval** both use LangGraph `interrupt()`; the CLI
   supplies the resume value. Clarification questions and approvals are separate
   trace event types.
9. **Memory** is a small JSON document at `company_data/memory.json`, read/written
   through tools; writes are `reversible_write` permission level.
10. **Step budget** counts graph decision steps (max 25); each tool action has at
    most 2 retries and a timeout from `TOOL_TIMEOUT_SECONDS`.
