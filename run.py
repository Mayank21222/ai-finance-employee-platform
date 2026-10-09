"""CLI runner: streams the trace, asks for approvals/clarification, prints the final report.

Usage:
    python run.py [task text] [--approve-all]
Example:
    python run.py
"""

from __future__ import annotations

import argparse
import json

from ai_operator.observability import configure_logging, log_event
from ai_operator.runtime import Operator

DEMO_TASK = (
    "Process the latest invoice from Acme Corp: enter the amount and due date "
    "into the payables system and tell me when it's done."
)


def stream_new(op: Operator, run_id: str, seen: int) -> int:
    state = op.graph.get_state(op.config(run_id))
    records = state.values.get("records", [])
    for rec in records[seen:]:
        print(f"  [{rec['step']}] {rec.get('action_type')} {rec.get('tool')}: {rec['summary'][:240]}")
    return len(records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the AI finance employee demo.")
    parser.add_argument("task", nargs="?", default=DEMO_TASK)
    parser.add_argument("--approve-all", action="store_true", help="auto-approve everything")
    args = parser.parse_args()

    trace_log = configure_logging()
    log_event("cli_run_invoked", task=args.task, approve_all=args.approve_all)

    op = Operator()
    result = op.start(args.task)
    run_id = result["run_id"]
    print(f"-> run {run_id}\n")
    seen = 0

    while True:
        seen = stream_new(op, run_id, seen)
        pending = Operator.suspended(result)
        if not pending:
            break
        payload = pending[0]
        if payload.get("kind") == "clarification":
            print(f"\n[CLARIFY] {payload['question']}")
            answer = "ok" if args.approve_all else input("answer> ").strip()
        else:
            print(f"\n[APPROVAL] {payload.get('description')}")
            print(f"  policy: {payload.get('policy')}  amount: {payload.get('amount')} {payload.get('currency')}")
            answer = "yes" if args.approve_all else "yes" if input("approve? [Y/n] ").strip().lower().startswith("y") else "no"
        result = op.resume(run_id, answer)

    seen = stream_new(op, run_id, seen)
    print("\n" + "=" * 64)
    print(json.dumps(result["report"], indent=2))
    print("trace.jsonl:", result["trace_path"])
    print("combined log:", trace_log)
    op.close()


if __name__ == "__main__":
    main()