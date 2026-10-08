# Status

Generated from a fresh run on 2026-10-08 before Phase 6 began.

## 1. Test suite

```
.venv/bin/python -m pytest tests -q
```

**129 passed, 0 failed** (4 warnings, all FastAPI `on_event` deprecations).

This is the count entering Phase 6. It is the result of Phases 1–5 (118 before
Phase 5 + 11 Phase 5 tests in `tests/test_phase5.py`).

## 2. Real-service verification status

Anything that depends on the real Claude model or an external service. Written
from what the tests actually exercise, not from intent.

| Feature | Status | Notes |
| --- | --- | --- |
| Phase 3 real model provider | **stub only / never tested live** | `MODEL_PROVIDER=stub` is the default and the only provider exercised. The OpenAI/Anthropic clients are built but **have never been tested with a live `LLM_API_KEY`** in this session. |
| Prompt test panel (P5-5) | **stub only** | `/flow/nodes/{id}/test` returns a canned `(stub)` string under `STUB_MODEL=1`. The real `get_client().complete()` branch is assembled but not called by any test. |
| Scheduled runs (P5-3) | **stub only** | The scheduler thread and cron matching are real and tested, but every due run uses the stub model. No schedule has fired against a live model. |
| Token usage / cost (P5-2) | **stub only** | Counts come from the stub client's fixed 250 in / 180 out. Real API `usage` parsing has never run against the API. Cost is labelled "estimated" everywhere and is a hard-coded table. |
| Slack/SMTP channels | does not exist yet | Phase 6 section 6. |
| MCP server | does not exist yet | Phase 6 section 2. |

No Phase 3 or Phase 5 feature has been verified with a live model call.

## 3. KNOWN_LIMITATIONS (verbatim summary)

1. Default model is a stub; real providers never exercised live.
2. Verifier only understands invoice-entry tasks; others honestly fail.
3. Runner reuses whatever server is already on `APP_URL` (no `--reload`).
4. `renamed_field` relies on a backend alias (`due-date-field`).
5. Browser pool of 2, sticky per thread; timeouts don't cancel workers.
6. `--answer` is a single blanket reply for every prompt.
7. Approval threshold is amount-only (INR 50,000) plus all irreversible writes.
8. Checkpoints accumulate; reusing a `run_id` resumes old state.
9. macOS / Python 3.12 only; e2e tests bind port 8011.
10. Dashboard runs one at a time and has no authentication.
11. Dashboard config edits apply from the next run.
12. Run-page agent status/reasoning is a heuristic; saved answers are exact.
13. PDFs are stored but not parsed; templates cannot compute values.

See `KNOWN_LIMITATIONS.md` for the full text.