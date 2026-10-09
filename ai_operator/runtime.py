"""Single-agent LangGraph runtime.

flow: START -> decide -> (tool_call: execute | ask_human: ask | verify: verify | finish: finalize)
execute/ask use LangGraph `interrupt` for human-in-the-loop approval/clarification.
Only the deterministic verifier result can mark a run as verified_complete.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from typing import Any, TypedDict

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from ai_operator.llm import DEFAULT_STUB_SCRIPT, StubModel, get_model
from ai_operator.models import Decision, FinalReport, Verification
from ai_operator.observability import log_event, set_run
from ai_operator.policy import build_approval, default_policy, should_approve
from ai_operator.tools import (APP_BASE_URL, DATA_DIR, STORAGE_DIR, ToolContext, ToolError, get_tool, run_tool)
from ai_operator.verifier import Verifier
from ai_operator.workflow import bump_stage, current_stage, skip_decorative, sop_prompt

MAX_STEPS = 25
MAX_RETRIES = 2

# LangGraph re-executes a node after `interrupt()` resumes it, so anything
# logged BEFORE the interrupt would appear twice (the Logs screen showed
# duplicate approval/clarification requests). Keys are process-local and bounded.
_REPLAY_LOGGED: dict[str, None] = {}


def _log_once(key: str, event: str, **fields) -> None:
    if key in _REPLAY_LOGGED:
        return
    if len(_REPLAY_LOGGED) > 1000:
        _REPLAY_LOGGED.clear()
    _REPLAY_LOGGED[key] = None
    log_event(event, **fields)


def _similar(a: str, b: str) -> bool:
    """True when two questions share most of their words (same question asked
    again in slightly different words)."""
    ta = set(re.findall(r"[a-z0-9]+", (a or "").lower()))
    tb = set(re.findall(r"[a-z0-9]+", (b or "").lower()))
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= 0.6


class OperatorState(TypedDict):
    run_id: str
    request: str
    step: int
    plan: str
    observation: str
    variables: dict[str, Any]
    decision: dict
    records: list[dict]
    approvals: list[dict]
    verification: dict | None
    forced: bool
    report: dict | None
    trace_path: str
    cost: float
    started: float


def _latest_due(paths: list[str]) -> str | None:
    """Pick the file whose 'Due date: YYYY-MM-DD' line is latest."""
    best, date = None, None
    for p in paths:
        try:
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    if "due date" in line.lower() and "-" in line:
                        d = line.split(":", 1)[-1].strip()
                        if d and (date is None or d > date):
                            date, best = d, p
        except OSError:
            continue
    return best


class Operator:

    def __init__(
        self,
        model=None,
        policy=None,
        verifier=None,
        checkpointer=None,
        base_url: str = APP_BASE_URL,
        data_dir: str = DATA_DIR,
        storage_dir: str = STORAGE_DIR,
        max_steps: int = MAX_STEPS,
        allowed_tools: list[str] | None = None,
        instructions: str = "",
        role: str = "",
        employee_name: str = "",
        workflow: list | None = None,
        knowledge: str = "",
    ) -> None:
        self.storage_dir = storage_dir
        self.ctx = ToolContext(base_url=base_url, data_dir=data_dir, storage_dir=storage_dir)
        self.verifier = verifier or Verifier(base_url)
        self.model = model or self._default_model()
        self.policy = policy or default_policy()
        self.max_steps = max_steps
        # Employee tool allow-list from the platform config. None = all tools
        # (CLI default); an explicit list (even empty) is enforced.
        self.allowed_tools = set(allowed_tools) if allowed_tools is not None else None
        self.instructions = instructions
        self.role = role
        self.employee_name = employee_name
        self.workflow = list(workflow or [])
        self.knowledge = knowledge
        self._checkpoint_conn = None
        if checkpointer is None:
            os.makedirs(storage_dir, exist_ok=True)
            self._checkpoint_conn = sqlite3.connect(
                os.path.join(storage_dir, "checkpoints.db"), check_same_thread=False)
            checkpointer = SqliteSaver(self._checkpoint_conn)
        self.graph = self._build_graph().compile(checkpointer=checkpointer)

    @staticmethod
    def _default_model():
        provider = os.getenv("MODEL_PROVIDER", "stub")
        if provider == "stub":
            return StubModel(DEFAULT_STUB_SCRIPT)
        return get_model(provider)

    # ------------------------------------------------------------------ #
    # nodes
    # ------------------------------------------------------------------ #

    def _apply_ask_guard(self, state: OperatorState, d: Decision, step: int,
                         vars_: dict) -> tuple[Decision, dict, str | None]:
        """Deterministic clarification-loop guard.

        After the human has answered, a model that keeps re-asking (the
        "approve? … yes … approve?" loop) is stopped instead of burning steps.
        Returns (decision, variables, blocked_observation_or_None).
        """
        if d.action_type != "ask_human":
            if vars_.get("_ask_streak"):
                vars_["_ask_streak"] = 0          # real progress happened
            return d, vars_, None
        streak = int(vars_.get("_ask_streak", 0))
        last_q = str(vars_.get("_last_question", "") or "")
        new_q = str((d.tool_args or {}).get("question", "") or d.tool_args or "")
        reason = None
        if streak >= 2:
            reason = (f"three clarifications in a row without any action in between "
                      f"(last answer: '{vars_.get('_last_answer', '')}')")
        elif streak >= 1 and _similar(last_q, new_q):
            reason = "the same question was already answered"
        if reason is None:
            return d, vars_, None
        answer = str(vars_.get("_last_answer", ""))
        log_event("ask_loop_blocked", run_id=state["run_id"], step=step, level="warning",
                  reason=reason, question=new_q[:200], answer=answer)
        obs = (f'The human already answered ("{answer}"), but the agent tried to ask '
               f"again instead of acting ({reason}) — stopping to avoid a loop. "
               f"Nothing further was changed.")
        d = Decision(thought=f"Human already answered ('{answer}'); acting instead of re-asking.",
                     action_type="finish")
        return d, vars_, obs

    def _decide_node(self, state: OperatorState) -> dict:
        step = state.get("step", 0) + 1
        vars_ = state.get("variables", {})
        if vars_.get("_force_finish"):
            return {
                "step": step,
                "decision": Decision(thought="No progress on repeated actions; stopping.",
                                     action_type="finish").model_dump(),
                "forced": False,
            }
        if step > self.max_steps:
            return {
                "step": step,
                "decision": Decision(thought="step limit reached", action_type="finish").model_dump(),
                "forced": True,
                "observation": f"Step limit of {self.max_steps} reached",
            }
        vars_ = dict(vars_)
        vars_["_stage"] = skip_decorative(self.workflow, int(vars_.get("_stage", 0) or 0))
        stage = current_stage(self.workflow, int(vars_["_stage"]))
        if stage and stage.get("kind") == "human" and not vars_.get(f"_human_done_{vars_['_stage']}"):
            question = (stage.get("instructions") or "").strip() or (
                f"Workflow requires human review at “{stage.get('name')}” before continuing. "
                "Reply yes to proceed, or no to stop."
            )
            d = Decision(
                thought=f"SOP human gate: {stage.get('name')}",
                action_type="ask_human",
                tool_args={"question": question},
                expected_outcome="Human approves or rejects this workflow stage.",
            )
            log_event("workflow_human_gate", run_id=state["run_id"], step=step,
                      stage=stage.get("name"), node=stage.get("id"))
            return {"step": step, "decision": d.model_dump(), "forced": False, "variables": vars_}
        prompt = self._build_prompt({**state, "variables": vars_, "step": state.get("step", 0)})
        try:
            d = self.model.decide(prompt)
        except (ValidationError, ValueError) as exc:
            # Retry once with the validation error attached; if it still fails,
            # fall back to asking the human (never loop on bad output).
            retry_prompt = (prompt + "\n\n# INVALID PREVIOUS OUTPUT\nYour last reply did not match the "
                            f"required JSON schema: {exc}\nReturn ONLY one valid JSON object now.")
            try:
                d = self.model.decide(retry_prompt)
            except (ValidationError, ValueError) as exc2:
                d = Decision(
                    thought="Model output failed schema validation twice.",
                    action_type="ask_human",
                    tool_args={"question": f"I couldn't produce a valid action ({exc2}). How should I proceed?"},
                )
                log_event("invalid_model_output", run_id=state["run_id"], step=step, level="warning",
                          error=str(exc2))
                d, vars_, blocked = self._apply_ask_guard(state, d, step, vars_)
                return {"step": step, "decision": d.model_dump(), "forced": False,
                        "variables": vars_,
                        "observation": blocked or f"Invalid model output: {exc2}"}
        d, vars_, blocked = self._apply_ask_guard(state, d, step, vars_)
        log_event("decision", run_id=state["run_id"], step=step,
                  action_type=d.action_type, tool=d.tool_name, args=d.tool_args,
                  expected_outcome=d.expected_outcome)
        ret = {"step": step, "decision": d.model_dump(), "forced": False, "variables": vars_}
        if blocked:
            ret["observation"] = blocked
        return ret


    def _tool_allowed(self, name: str | None) -> bool:
        return self.allowed_tools is None or (name or "") in self.allowed_tools

    def _execute_node(self, state: OperatorState) -> dict:
        d = Decision(**state["decision"])
        tool = get_tool(d.tool_name or "")
        if tool is None:
            return {"observation": f"Unknown tool '{d.tool_name}'"}
        if not self._tool_allowed(tool.spec.name):
            return {"observation": f"Tool '{tool.spec.name}' is not enabled for this employee. "
                                   f"Only use tools from the Available tools list in the prompt."}
        args = dict(d.tool_args)
        if tool.spec.name == "read_file" and not args.get("path"):
            args["path"] = state.get("variables", {}).get("last_file", "")

        needs, reason = should_approve(tool, args, self.policy)
        approvals = [dict(a) for a in state.get("approvals", [])]
        vars_ = dict(state.get("variables", {}))
        reuse_key = None
        if needs:
            payload = build_approval(tool, args, self.policy).model_dump()
            reuse_key = f"_approved_retry::{tool.spec.name}::{payload.get('amount')}"
            if vars_.get(reuse_key):
                # The human already granted this exact approval and the action
                # failed afterwards — retry silently instead of showing the
                # same approval card again.
                log_event("approval_reused", run_id=state["run_id"], tool=tool.spec.name,
                          amount=payload.get("amount"))
            else:
                _log_once(f"{state['run_id']}:appr:{state.get('step')}:{tool.spec.name}:{payload.get('amount')}",
                          "approval_requested", run_id=state["run_id"], tool=tool.spec.name,
                          args=args, policy=reason, amount=payload.get("amount"))
                answer = interrupt(payload)
                approved = answer in (True, 1, "y", "yes", "approve", "approve_all")
                approvals.append({**payload, "decision": approved})
                log_event("approval_decision", run_id=state["run_id"], tool=tool.spec.name,
                          approved=approved, answer=str(answer))
                self._record(state, d, f"Approval requested: {reason}. Decision: {answer if approved else 'rejected'}")
                if not approved:
                    return {"observation": f"Human rejected the action: {tool.spec.name}",
                            "records": state["records"], "approvals": approvals}

        try:
            result = run_tool(tool.spec.name, self.ctx, args)
            ok = True
        except ToolError as exc:
            result = f"{exc}"
            ok = False
        except (OSError, ValueError, TypeError, httpx.HTTPError) as exc:
            # httpx errors (sandbox unreachable, 5xx, timeout) must degrade to a
            # failed step, never crash the whole run.
            result = f"{type(exc).__name__}: {exc}"
            ok = False
        if reuse_key:
            if ok:
                vars_.pop(reuse_key, None)      # succeeded — grant consumed
            else:
                vars_[reuse_key] = True         # keep the grant for the retry
        log_event("tool_call", run_id=state["run_id"], step=state.get("step"),
                  tool=tool.spec.name, args=args, ok=ok, result=result)

        if ok and tool.spec.permission == "irreversible_write":
            vars_["_wrote"] = True
        if ok and tool.spec.name == "search_files":
            hits = [line for line in result.splitlines() if os.path.isfile(line)]
            vars_["last_file"] = _latest_due(hits) or (hits[0] if hits else None)
        if ok and tool.spec.name == "browser_fill":
            vars_[args.get("field", "")] = args.get("value", "")

        # Loop guard: a small model will happily repeat a successful call forever.
        if ok:
            sig = tool.spec.name + "|" + json.dumps(args, sort_keys=True, default=str)
            history = vars_.get("_call_sigs", [])
            history.append(sig)
            vars_["_call_sigs"] = history[-10:]
            count = vars_["_call_sigs"].count(sig)

            # "No new information" guard: a repeat can alternate tools (A,B,A,B…)
            # yet still return the exact same output. If a call adds nothing new,
            # there is nothing left to learn — force a stop on the second repeat.
            res_sig = tool.spec.name + "::" + str(result)[:200]
            seen_res = vars_.get("_seen_results", [])
            if res_sig in seen_res:
                vars_["_no_progress"] = vars_.get("_no_progress", 0) + 1
            else:
                vars_["_no_progress"] = 0
            seen_res.append(res_sig)
            vars_["_seen_results"] = seen_res[-12:]

            if count >= 3 or vars_.get("_no_progress", 0) >= 1:
                vars_["_force_finish"] = True
                result += "\n\n[system] This action added nothing new; stopping to avoid a loop."
            elif count == 2:
                result += ("\n\n[system] You already ran this exact action and have its result. "
                           "Do not repeat it: take a different action, call verify, or finish.")
        if not ok:
            key = tool.spec.name
            vars_[f"_retry_{key}"] = vars_.get(f"_retry_{key}", 0) + 1
            if vars_[f"_retry_{key}"] >= MAX_RETRIES:
                vars_.pop(f"_retry_{key}", None)
                self._record(state, d, f"Failed after {MAX_RETRIES} attempts; giving up on this action")
                return {"observation": f"Failed after {MAX_RETRIES} attempts: {result}", "variables": vars_,
                        "records": state["records"], "approvals": approvals}

        if ok and self.workflow:
            before = skip_decorative(self.workflow, int(vars_.get("_stage", 0) or 0))
            after = bump_stage(self.workflow, before, tool.spec.name)
            vars_["_stage"] = after
            if after != before:
                nxt = current_stage(self.workflow, after)
                label = nxt["name"] if nxt else "end"
                self._record(state, d, f"Workflow advanced to: {label}")
                log_event("workflow_stage", run_id=state["run_id"], stage=label,
                          from_idx=before, to_idx=after)
        self._record(state, d, f"{tool.spec.name} -> {result[:1200]}")
        return {"observation": result, "variables": vars_, "records": state["records"], "approvals": approvals}

    def _ask_node(self, state: OperatorState) -> dict:
        d = Decision(**state["decision"])
        q = d.tool_args.get("question") or d.tool_args or "Need clarification"
        _log_once(f"{state['run_id']}:clar:{state.get('step')}",
                  "clarification_requested", run_id=state["run_id"], question=q)
        answer = interrupt({"kind": "clarification", "question": q})
        vars_ = dict(state.get("variables", {}))
        vars_["clarification"] = str(answer)
        # Clarification tracking for the loop guard (after interrupt, so a
        # re-executed superstep counts each answered question exactly once).
        vars_["_ask_streak"] = int(vars_.get("_ask_streak", 0)) + 1
        vars_["_last_question"] = str(q)
        vars_["_last_answer"] = str(answer)
        qa = list(vars_.get("_qa_log", []))
        qa.append({"question": str(q)[:300], "answer": str(answer)[:200]})
        vars_["_qa_log"] = qa[-6:]
        log_event("clarification_answered", run_id=state["run_id"], question=q, answer=str(answer))
        self._record(state, d, f"Human said: {answer}")
        idx = skip_decorative(self.workflow, int(vars_.get("_stage", 0) or 0))
        stage = current_stage(self.workflow, idx)
        if stage and stage.get("kind") == "human":
            vars_[f"_human_done_{idx}"] = True
            ans = str(answer).lower().strip()
            if ans in ("no", "n", "reject", "stop", "cancel"):
                vars_["_force_finish"] = True
                return {"observation": f"Human rejected workflow stage “{stage.get('name')}”: {answer}",
                        "variables": vars_, "records": state["records"]}
            vars_["_stage"] = skip_decorative(self.workflow, idx + 1)
            nxt = current_stage(self.workflow, int(vars_["_stage"]))
            self._record(state, d, f"Workflow advanced to: {nxt['name'] if nxt else 'end'}")
        return {"observation": f"Human: {answer}", "variables": vars_, "records": state["records"]}

    def _verify_node(self, state: OperatorState) -> dict:
        d = Decision(**state["decision"])
        vars_ = state.get("variables", {})
        kind = d.tool_args.get("kind") or vars_.get("kind") or "invoice"
        vendor = d.tool_args.get("vendor") or vars_.get("vendor")
        amount = d.tool_args.get("amount") or vars_.get("amount")
        due = d.tool_args.get("due_date") or vars_.get("due_date")
        if not (vendor and amount and due):
            v = Verification(checked=f"{kind} record", expected="vendor, amount, due_date recorded",
                             found="not recorded by agent", matched=False)
        elif kind == "payment":
            v = self.verifier.verify_payment(str(vendor), float(amount), str(due))
        else:
            v = self.verifier.verify_invoice(str(vendor), float(amount), str(due))
        log_event("verification", run_id=state["run_id"], kind=kind, matched=v.matched,
                  expected=v.expected, found=v.found)
        self._record(state, d, f"verify {kind} -> {v.matched}")
        return {"verification": v.model_dump(), "observation": f"Verification {'matched' if v.matched else 'mismatch: ' + v.found}", "records": state["records"]}

    def _finalize_node(self, state: OperatorState) -> dict:
        v: Verification | None = Verification(**state["verification"]) if state.get("verification") else None
        wrote = bool(state.get("variables", {}).get("_wrote"))
        if v and v.matched and not state.get("forced"):
            status: str = "verified_complete"
            summary = f"Verified: {v.checked} matches expected."
        elif not wrote and not state.get("forced"):
            # Read-only task: nothing was written, so a verify step (if any) has
            # no record to match against and must not fail the run.
            status = "completed"
            last = state.get("observation", "") or ""
            base = last.split("[system]")[0].strip() or "Task finished (no verifiable write was requested)."
            if state.get("variables", {}).get("_force_finish"):
                summary = ("Stopped early: the agent repeated actions without new "
                           f"information. Last result: {base}")
            else:
                summary = base
        elif state.get("forced"):
            status = "failed"
            summary = state.get("observation", "Run stopped (limit reached or invalid model output).")
        elif v and not v.matched:
            status = "failed"
            summary = f"Mismatch: expected {v.expected}; found {v.found}."
        else:
            status = "failed"
            summary = "A write was performed but never verified."

        actions = [r.get("summary", r.get("tool", "")) for r in state["records"]]
        evidence = self._prepare_evidence(state)
        report = FinalReport(status=status, summary=summary, actions=actions,
                             verification=v, evidence=evidence,
                             approvals=state.get("approvals", []))
        trace_path = self._write_trace(state)
        log_event("final_report", run_id=state["run_id"], status=status, summary=summary,
                  steps=state.get("step"), trace=trace_path)
        return {"report": report.model_dump(), "trace_path": trace_path}

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def _record(self, state: OperatorState, d: Decision, summary: str) -> None:
        step = state.get("step", 0)
        rec = {"step": step, "action_type": d.action_type, "tool": d.tool_name,
               "args": d.tool_args, "summary": summary,
               "expected_outcome": d.expected_outcome, "ts": time.time()}
        try:
            state.setdefault("records", []).append(rec)
        except TypeError:
            state["records"] = [rec]

    def _prepare_evidence(self, state: OperatorState) -> list[str]:
        run_id = state["run_id"]
        ev_dir = os.path.join(self.storage_dir, "runs", run_id)
        os.makedirs(ev_dir, exist_ok=True)
        if self.ctx.browser.body:
            path = os.path.join(ev_dir, "final_page.html")
            try:
                self.ctx.browser.screenshot(path)
                return [path]
            except Exception:
                return []
        return []

    def _write_trace(self, state: OperatorState) -> str:
        run_id = state["run_id"]
        path = os.path.join(self.storage_dir, "runs", run_id, "trace.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            for rec in state["records"]:
                fh.write(json.dumps(rec) + "\n")
            fh.write(json.dumps({"run_id": run_id, "request": state["request"],
                                 "verification": state.get("verification"),
                                 "report": state.get("report")}) + "\n")
        return path

    def _build_prompt(self, state: OperatorState) -> str:
        from ai_operator.tools import TOOLS
        tools = "\n".join(f"- {t.spec.name}: {t.spec.description}"
                         for t in sorted(TOOLS.values(), key=lambda t: t.spec.name)
                         if self._tool_allowed(t.spec.name))
        vars_ = state.get("variables", {})

        # Human status: what has been approved and what was asked/answered.
        # Without this the model re-asks for approvals it already has.
        approvals = state.get("approvals", []) or []
        if approvals:
            appr = "\n".join(
                f"- {a.get('description', 'action')}: {a.get('policy', '')} -> "
                f"{'APPROVED' if a.get('decision') else 'REJECTED'}"
                for a in approvals)
        else:
            appr = "- none yet"
        qa = vars_.get("_qa_log", []) or []
        qa_text = "\n".join(f"- Q: {x.get('question', '')}\n  A: {x.get('answer', '')}" for x in qa) or "- none"
        human = (f"\n# HUMAN STATUS\nApprovals so far:\n{appr}\n"
                 f"Recent questions and your human's answers:\n{qa_text}\n"
                 "A granted approval or a human answer is FINAL: act on it in your very "
                 "next decision (run the tool, verify, or finish). Never ask the same "
                 "thing again and never re-request an approval you already have.\n")
        streak = int(vars_.get("_ask_streak", 0) or 0)
        if streak >= 1:
            human += (f"\n# HUMAN JUST ANSWERED\nYour last question was already answered: "
                      f"'{vars_.get('_last_answer', '')}'. Do NOT ask another question now — "
                      f"act on that answer immediately. Asking again will stop the run.\n")

        # Compact history of recent step results. Without it, a fact retrieved
        # two steps ago (invoice number, amount, due date) disappears from the
        # prompt as soon as another tool runs — and the model re-fetches it,
        # which the loop guard then punishes with a forced stop.
        recs = [r for r in (state.get("records") or []) if r.get("summary")][-5:]
        recent = "\n".join(f"- {str(r.get('summary', ''))[:400]}" for r in recs) or "- none yet"
        history = (f"\n# RECENT STEPS (results you already have — never redo these)\n"
                   f"{recent}\n"
                   "Facts already present above (invoice number, amount, due date, "
                   "verdicts) must not be fetched again.\n")

        persona = ""
        who = self.employee_name or self.role
        if who or self.instructions:
            persona = (f"\n# EMPLOYEE\nYou are {who or 'the finance employee'}."
                       f"{' Role: ' + self.role if self.role else ''}\n")
            if self.instructions:
                persona += f"Standing instructions:\n{self.instructions.strip()}\n"
        sop = sop_prompt(self.workflow, int(vars_.get("_stage", 0) or 0))
        know = ""
        if self.knowledge:
            know = "\n# COMPANY KNOWLEDGE (authoritative; do not contradict)\n" + self.knowledge[:3500] + "\n"
        context = (
            f"{persona}{sop}{know}"
            f"\n# CURRENT CONTEXT\nRequest: {state['request']}\n"
            f"Plan: {state.get('plan', '')}\n"
            f"Last observation: {state.get('observation', '')}\n"
            f"Variables: {json.dumps({k: v for k, v in vars_.items() if not k.startswith('_')})}\n"
            f"Steps used: {state.get('step', 0)} / {self.max_steps}\n"
            f"{human}"
            f"{history}"
            f"Available tools:\n{tools}"
        )
        if self.allowed_tools is not None:
            context += ("\nOnly the tools listed under 'Available tools' are enabled for "
                        "this employee. If a tool is not in that list, it does not exist "
                        "for you — use an enabled one instead.\n")
        template = os.path.join("prompts", "decide.md")
        if os.path.isfile(template):
            with open(template, encoding="utf-8") as fh:
                return fh.read() + context
        return context + "\nReturn JSON exactly: {\"thought\":str,\"action_type\":\"tool_call\"|\"ask_human\"|\"verify\"|\"finish\",\"tool_name\":str|null,\"tool_args\":{},\"plan_update\":str|null,\"expected_outcome\":str|null}"

    # ------------------------------------------------------------------ #
    # graph
    # ------------------------------------------------------------------ #

    def _build_graph(self) -> StateGraph:
        g = StateGraph(OperatorState)
        g.add_node("decide", self._decide_node)
        g.add_node("execute", self._execute_node)
        g.add_node("ask", self._ask_node)
        g.add_node("verify", self._verify_node)
        g.add_node("finalize", self._finalize_node)
        g.add_edge(START, "decide")
        g.add_conditional_edges("decide", lambda s: {
            "tool_call": "execute", "ask_human": "ask", "verify": "verify", "finish": "finalize",
        }.get(s["decision"]["action_type"], "finalize"))
        for n in ("execute", "ask", "verify"):
            g.add_edge(n, "decide")
        g.add_edge("finalize", END)
        return g

    # ------------------------------------------------------------------ #
    # public API
    # ------------------------------------------------------------------ #

    def config(self, run_id: str) -> dict:
        return {"configurable": {"thread_id": run_id}}

    def start(self, request: str, run_id: str | None = None, prompt: str = "") -> dict:
        run_id = run_id or uuid.uuid4().hex[:12]
        set_run(run_id)
        log_event("run_started", run_id=run_id, request=request, model=getattr(self.model, "name", "?"))
        initial: dict[str, Any] = {
            "run_id": run_id, "request": request, "step": 0, "plan": "",
            "observation": prompt or "", "variables": {"task": request},
            "decision": {}, "records": [], "approvals": [], "verification": None,
            "forced": False, "report": None, "trace_path": "", "cost": 0.0, "started": time.time(),
        }
        return self.graph.invoke(initial, self.config(run_id))

    def resume(self, run_id: str, value: Any) -> dict:
        set_run(run_id)
        log_event("run_resumed", run_id=run_id, value=str(value))
        return self.graph.invoke(Command(resume=value), self.config(run_id))

    @staticmethod
    def suspended(result: dict) -> list[dict]:
        """Return list of pending interrupt payloads if the run is paused."""
        return [i.value for i in result.get("__interrupt__", [])]

    def close(self) -> None:
        self.verifier.close()
        self.ctx.browser.close()
        if self._checkpoint_conn is not None:
            self._checkpoint_conn.close()
            self._checkpoint_conn = None