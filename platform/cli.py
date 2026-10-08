"""`fin` - the coding-agent-facing CLI (Phase 6 section 3).

Commands (argparse, no new dependencies):

    fin init                          create/seed the dashboard DB, check assets
    fin flow validate <file>          blocking errors -> non-zero exit; warnings shown
    fin flow export [-o FILE]         print the shipped flow config as JSON
    fin flow import <file>            validate, refuse invalid, version changed agents
    fin run --session <id> "<task>"   start a run and wait for its report
    fin runs list                     run history (state, status, task)
    fin approvals list                pending approvals

Run it as `./fin ...` or `python -m platform.cli ...`.
Exit codes: 0 = success/valid/verified_complete, 1 = errors/failed run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Importing the tool package registers every built-in tool, so validation in
# a fresh process sees the same registry the dashboard has.
import ai_operator.tools  # noqa: F401
from ai_operator.graph import DEFAULT_FLOW_PATH
from platform.flow.models import AgentNode, Flow, load_flow
from platform.flow.validator import validate


def _dashboard():
    from platform.dashboard import db

    db.init_db()  # idempotent: makes every command hermetic on a fresh box
    return db


def cmd_init(_args: argparse.Namespace) -> int:
    db = _dashboard()
    if not DEFAULT_FLOW_PATH.exists():
        print(f"ERROR: shipped flow config missing: {DEFAULT_FLOW_PATH}")
        return 1
    flow = load_flow(DEFAULT_FLOW_PATH)
    result = validate(flow)
    roles = db.list_roles()
    sessions = db.list_sessions()
    print(f"database:       {db.db_path()}")
    print(f"roles:          {', '.join(r['name'] for r in roles) or 'none'}")
    print(f"sessions:       {len(sessions)} "
          f"(default tenant {sessions[0]['tenant'] if sessions else '-'})")
    print(f"flow config:    {DEFAULT_FLOW_PATH.name} "
          f"({len(flow.nodes)} nodes, start={flow.start_node_id})")
    print(f"validation:     {'ok' if not result.errors else str(len(result.errors)) + ' blocking error(s)'}"
          f", guardrail score {result.guardrail_score}")
    return 0 if not result.errors else 1


def cmd_flow_validate(args: argparse.Namespace) -> int:
    path = Path(args.file)
    if not path.is_file():
        print(f"ERROR: no such file: {path}")
        return 1
    try:
        flow = Flow.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001 - malformed JSON/shape is a CLI error
        print(f"ERROR: cannot read flow: {exc}")
        return 1
    result = validate(flow)
    for err in result.errors:
        print(f"ERROR: {err}")
    for warn in result.warnings:
        print(f"WARNING: {warn}")
    print(f"guardrail score: {len(result.warnings)} warning(s)")
    if result.errors:
        print(f"INVALID: {len(result.errors)} blocking error(s)")
        return 1
    print("VALID")
    return 0


def cmd_flow_export(args: argparse.Namespace) -> int:
    if not DEFAULT_FLOW_PATH.exists():
        print(f"ERROR: shipped flow config missing: {DEFAULT_FLOW_PATH}")
        return 1
    text = DEFAULT_FLOW_PATH.read_text(encoding="utf-8")
    json.loads(text)  # sanity: must be valid JSON before echoing
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text, end="" if text.endswith("\n") else "\n")
    return 0


def cmd_flow_import(args: argparse.Namespace) -> int:
    """Validate the file, refuse it when invalid, version every changed agent."""
    db = _dashboard()
    path = Path(args.file)
    if not path.is_file():
        print(f"ERROR: no such file: {path}")
        return 1
    try:
        new_flow = Flow.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot read flow: {exc}")
        return 1
    result = validate(new_flow)
    if result.errors:  # refuse invalid configs with the plain-English list
        print(f"REFUSED: {len(result.errors)} validation error(s); nothing written.")
        for err in result.errors:
            print(f"  - {err}")
        return 1
    target = Path(args.output) if args.output else DEFAULT_FLOW_PATH
    old_cfg: dict = {}
    if target.exists():
        try:
            old_cfg = json.loads(target.read_text(encoding="utf-8"))
        except ValueError:
            old_cfg = {}
    old_agents = {str(n.get("node_id")): n for n in old_cfg.get("nodes", [])
                  if n.get("type") == "agent"}
    new_agents = {n.node_id: n for n in new_flow.nodes
                  if isinstance(n, AgentNode)}
    changed: list[str] = []
    for node_id, node in new_agents.items():
        new_dump = node.model_dump(mode="json")
        old_raw = old_agents.get(node_id)
        if old_raw is None:
            changed.append(node_id)          # brand-new agent: record initial state
            db.add_version(node_id, new_dump, label="flow import")
            continue
        try:
            old_dump = AgentNode.model_validate(old_raw).model_dump(mode="json")
        except Exception:                     # unparseable old node: treat changed
            old_dump = {}
        if old_dump != new_dump:
            changed.append(node_id)           # version what the import replaces
            db.add_version(node_id, old_dump, label="flow import")
    target.write_text(
        json.dumps(new_flow.model_dump(mode="json"), indent=2) + "\n",
        encoding="utf-8")
    if changed:
        print(f"imported {path} -> {target}")
        print(f"agent version rows created: {', '.join(changed)}")
    else:
        print(f"imported {path} -> {target} (no agent changed)")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    db = _dashboard()
    from platform.dashboard import runner

    try:
        handle = runner.start_run(args.task, int(args.session))
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(f"run_id={handle.run_id}; waiting for completion...")
    deadline = time.time() + 600
    while not handle.terminal and time.time() < deadline:
        time.sleep(0.5)
    if not handle.terminal:
        print(f"ERROR: run {handle.run_id} still active after 600s")
        return 1
    report_path = Path(REPO_ROOT) / "runs" / handle.run_id / "report.json"
    report = (json.loads(report_path.read_text(encoding="utf-8"))
              if report_path.is_file() else None)
    print(json.dumps(report, indent=2, default=str))
    if handle.error:
        print(f"error: {handle.error}")
        return 1
    return 0 if (report or {}).get("status") == "verified_complete" else 1


def cmd_runs_list(_args: argparse.Namespace) -> int:
    db = _dashboard()
    rows = db.list_runs(50)
    if not rows:
        print("no runs yet")
        return 0
    print(f"{'run_id':<34} {'state':<17} {'status':<18} task")
    for r in rows:
        task = str(r.get("task") or "").replace("\n", " ")[:50]
        print(f"{str(r['run_id']):<34} {str(r.get('state')):<17} "
              f"{str(r.get('status')):<18} {task}")
    return 0


def cmd_approvals_list(_args: argparse.Namespace) -> int:
    db = _dashboard()
    from ai_operator.tracing import RUNS_ROOT

    pending = []
    for row in db.list_runs(200):
        if str(row.get("state") or "") != "waiting_approval":
            continue
        rid = str(row["run_id"])
        req: dict = {}
        trace = RUNS_ROOT / rid / "trace.jsonl"
        if trace.is_file():
            for line in trace.read_text(errors="replace").splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if (ev.get("event") == "approval_requested"
                        and ev.get("kind") == "approval"):
                    req = ev
        pending.append((rid, req))
    if not pending:
        print("no pending approvals")
        return 0
    for rid, req in pending:
        args_str = json.dumps(req.get("tool_args") or {}, default=str)
        print(f"{rid}\n  {req.get('tool_name')}({args_str})\n"
              f"  reason: {req.get('reason')}\n"
              f"  question: {req.get('question')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fin", description="Comp Ops finance-employee control CLI.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create/seed the database and check assets")

    flow = sub.add_parser("flow", help="flow config operations")
    flow_sub = flow.add_subparsers(dest="flow_command", required=True)
    p = flow_sub.add_parser("validate", help="validate a flow file")
    p.add_argument("file")
    p = flow_sub.add_parser("export", help="print the shipped flow config")
    p.add_argument("-o", "--output", default=None)
    p = flow_sub.add_parser("import",
                            help="validate and install a flow file "
                                 "(refuses invalid configs)")
    p.add_argument("file")
    p.add_argument("-o", "--output", default=None,
                   help="target path (default: the shipped flow config)")

    p = sub.add_parser("run", help="start a run and wait for its report")
    p.add_argument("--session", type=int, required=True)
    p.add_argument("task")

    runs = sub.add_parser("runs", help="run history")
    runs_sub = runs.add_subparsers(dest="runs_command", required=True)
    runs_sub.add_parser("list", help="list recent runs")

    approvals = sub.add_parser("approvals", help="approvals")
    approvals_sub = approvals.add_subparsers(dest="approvals_command",
                                             required=True)
    approvals_sub.add_parser("list", help="list pending approvals")

    return parser


_HANDLERS = {
    "init": cmd_init,
    "run": cmd_run,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "flow":
        return {"validate": cmd_flow_validate,
                "export": cmd_flow_export,
                "import": cmd_flow_import}[args.flow_command](args)
    if args.command == "runs":
        return cmd_runs_list(args)
    if args.command == "approvals":
        return cmd_approvals_list(args)
    return _HANDLERS[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
