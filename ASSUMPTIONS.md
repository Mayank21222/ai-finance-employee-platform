# Assumptions

1. **Single-tenant, local-first.** One business, one machine, local files. No
   authentication, no multi-user roles.
2. **The "browser" is a deterministic HTTP form client** (httpx + stdlib
   `HTMLParser`), not a real Chromium session. It is enough to navigate, read,
   fill and submit the sandbox payables app; Playwright is optional and not
   required.
3. **Only the model provider may be remote.** Everything else (sandbox app,
   storage, verifier) stays on the local machine.
4. **The sandbox payables app is the source of truth** for verification. The
   verifier queries `/api/invoices` (or `/api/payments` when the verify action
   sets `kind: "payment"`) and compares vendor/amount/due-date to what the agent
   attempted to write.
5. **The default provider for this build is Groq `openai/gpt-oss-120b`**
   (`MODEL_PROVIDER=groq`, needs `GROQ_API_KEY`). The OpenAI-compatible local
   fallback is `llama3.1:8b` on Ollama (`http://localhost:11434/v1`). A
   deterministic stub (`MODEL_PROVIDER=stub` / `EMPLOYEE_MODEL=stub`) mirrors the
   demo decisions so the system is fully testable offline. Every model call,
   tool, approval and verifier result is logged to `.data/logs/` (see README).
6. **Amounts are INR** unless the employee/flow says otherwise. Guardrail tiers
   follow `Improvements.md` §34 (`<10,000` auto, `10,000–50,000` manager,
   `>=50,000` finance), which supersedes the older ₹42,500 "no approval"
   example in `initial_task_prompt.md`.
7. **"Latest invoice"** means the invoice file with the greatest `Due date`.
8. **The four human actions** are: approve an irreversible/over-limit action,
   reject it, answer a clarification, or pick a seed document. Everything else
   is autonomous.
9. **The flow config is the single source of truth** in the UI; the runtime is
   compiled from it rather than duplicating logic.
10. **One process, in-memory run registry** for the dashboard. Runs are
    persisted to SQLite on completion; live SSE state is process-local.
11. **HTMX is intentionally not a dependency.** The UI uses native `EventSource`
    (SSE) + `fetch`, so the repo runs entirely offline with no CDN.
12. **Finance actions are deterministic tools, not model reasoning.** Lookups,
    math, currency conversion and writes happen in `ai_operator/tools.py`; the
    model only chooses when to call them. Common argument-name variants are
    normalized so a near-miss from a small model does not crash a run.
13. **The Run screen is a conversation.** Each message starts a run over the same
    runtime; the transcript is client-side for the session, while every run and
    its trace are persisted to History.
