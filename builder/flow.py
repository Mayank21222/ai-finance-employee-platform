"""Flow configuration, role templates and the compiler that turns an
Employee config into a running LangGraph Operator.

The Employee.flow config is the single source of truth for the workflow;
the compiler maps it onto the existing runtime (no second engine)."""

from __future__ import annotations

import os

from ai_operator.llm import DEFAULT_STUB_SCRIPT, StubModel, get_model
from ai_operator.models import Employee, FlowConfig, FlowEdge, FlowNode
from ai_operator.policy import default_policy
from ai_operator.tools import DATA_DIR

# Employees reason with the local free model by default; set EMPLOYEE_MODEL=stub
# to keep the platform fully deterministic/offline (tests do this).
DEFAULT_EMPLOYEE_MODEL = os.getenv("EMPLOYEE_MODEL", "local")

# Role templates (Improvements.md §26). Names only - they describe the
# employee's workflow and are compiled onto the existing runtime.
TRIGGERS = ["Manual", "Schedule", "Email", "File", "Webhook"]
NODE_LIBRARY = {
    "ai": ["AI Employee", "Classify", "Extract", "Analyze", "Summarize", "Generate"],
    "finance": ["Invoice", "Vendor", "Expense", "Payment", "Reconciliation", "Budget", "Forecast", "Financial Metric"],
    "logic": ["IF / ELSE", "Switch", "Filter", "Loop"],
    "human": ["Approval", "Review", "Clarification", "Escalation"],
    "output": ["Email", "Report", "Database", "Webhook"],
}

ROLE_TEMPLATES: dict[str, list[str]] = {
    "Accounts Payable": ["Receive Invoice", "Extract Information", "Validate", "Vendor Lookup",
                         "PO Matching", "Duplicate Detection", "Policy Check", "Approval", "Payment Preparation"],
    "Accounts Receivable": ["Receive Remittance", "Match Open Invoice", "Policy Check", "Record Receipt", "Escalation"],
    "Expense Auditor": ["Expense", "Extract", "Categorize", "Policy Check", "Anomaly Detection", "Review / Approval"],
    "FP&A Analyst": ["Fetch Data", "Calculate KPIs", "Budget vs Actual", "Variance Analysis",
                     "Driver Analysis", "Generate Report"],
    "CFO Assistant": ["Gather Data", "Validate", "Calculate KPIs", "Generate Summary", "Human Review", "Distribute"],
}

ROLE_TOOLS: dict[str, list[str]] = {
    "Accounts Payable": ["search_files", "read_file", "list_knowledge", "vendor_lookup", "po_lookup",
                         "duplicate_check", "policy_check", "financial_calculator", "currency_convert",
                         "create_payment", "browser_navigate", "browser_fill", "browser_submit",
                         "generate_report"],
    "Accounts Receivable": ["search_files", "read_file", "list_knowledge", "vendor_lookup", "duplicate_check",
                            "policy_check", "financial_calculator", "generate_report", "memory_write"],
    "Expense Auditor": ["search_files", "read_file", "policy_check", "financial_calculator",
                        "currency_convert", "duplicate_check", "memory_write", "generate_report"],
    "FP&A Analyst": ["search_files", "read_file", "financial_calculator", "currency_convert",
                     "generate_report", "memory_write"],
    "CFO Assistant": ["search_files", "read_file", "vendor_lookup", "financial_calculator",
                      "currency_convert", "generate_report", "browser_screenshot"],
}

TOOL_LABELS: dict[str, str] = {
    "search_files": "Search company files",
    "read_file": "Read a document",
    "list_knowledge": "List knowledge files",
    "memory_read": "Read company memory",
    "memory_write": "Save to company memory",
    "browser_navigate": "Open the payables app",
    "browser_read": "Read the current form",
    "browser_fill": "Fill a form field",
    "browser_submit": "Submit a form",
    "browser_screenshot": "Save page evidence",
    "app_get": "Read a sandbox API",
    "vendor_lookup": "Look up a vendor",
    "po_lookup": "Match purchase orders",
    "duplicate_check": "Check for duplicates",
    "policy_check": "Check payment policy",
    "currency_convert": "Convert currency",
    "financial_calculator": "Calculate (exact math)",
    "generate_report": "Write a report",
    "create_payment": "Prepare a payment",
    "update_vendor_bank": "Change vendor bank details",
}

_STAGE_TOOLS: list[tuple[str, list[str]]] = [
    ("vendor", ["vendor_lookup"]),
    ("po match", ["po_lookup"]),
    ("purchase", ["po_lookup"]),
    ("duplicate", ["duplicate_check"]),
    ("policy", ["policy_check"]),
    ("payment", ["create_payment", "browser_submit"]),
    ("receipt", ["generate_report", "memory_write"]),
    ("remittance", ["search_files", "read_file"]),
    ("invoice", ["search_files", "read_file", "browser_navigate", "browser_fill", "browser_submit"]),
    ("extract", ["read_file", "search_files"]),
    ("receive", ["search_files", "list_knowledge", "read_file"]),
    ("validate", ["vendor_lookup", "policy_check", "duplicate_check"]),
    ("expense", ["search_files", "read_file", "policy_check"]),
    ("categor", ["read_file", "policy_check"]),
    ("anomaly", ["duplicate_check", "policy_check"]),
    ("forecast", ["financial_calculator", "generate_report"]),
    ("budget", ["financial_calculator", "generate_report"]),
    ("variance", ["financial_calculator", "generate_report"]),
    ("kpi", ["financial_calculator", "generate_report"]),
    ("metric", ["financial_calculator"]),
    ("calculat", ["financial_calculator"]),
    ("report", ["generate_report"]),
    ("summar", ["generate_report"]),
    ("distribut", ["generate_report"]),
    ("fetch", ["search_files", "read_file", "list_knowledge"]),
    ("gather", ["search_files", "read_file", "list_knowledge"]),
    ("reconcil", ["duplicate_check", "financial_calculator"]),
    ("driver", ["financial_calculator", "read_file"]),
]


def _stage_kind(node: FlowNode) -> str:
    name = node.name.lower()
    if node.type == "human" or any(w in name for w in ("approval", "review", "clarif", "escalat")):
        return "human"
    if node.type in ("trigger", "logic"):
        return "skip"
    return "work"


def _stage_tools(node: FlowNode) -> list[str]:
    blob = f"{node.name} {node.type} {node.config.get('instructions', '')}".lower()
    for needle, tools in _STAGE_TOOLS:
        if needle in blob:
            return list(tools)
    if node.type == "output":
        return ["generate_report"]
    if node.type == "finance":
        return ["vendor_lookup", "policy_check"]
    return []


def compile_workflow(flow: FlowConfig | None) -> list[dict]:
    """Walk the first-edge path from start. Extra branches stay as notes."""
    if flow is None or not flow.nodes:
        return []
    by_id = {n.id: n for n in flow.nodes}
    outs: dict[str, list[FlowEdge]] = {}
    for edge in flow.edges:
        outs.setdefault(edge.source, []).append(edge)
    stages: list[dict] = []
    seen: set[str] = set()
    cur = flow.start
    while cur and cur not in seen and cur in by_id:
        seen.add(cur)
        node = by_id[cur]
        branches = outs.get(cur, [])
        extras = [by_id[e.target].name for e in branches[1:] if e.target in by_id]
        note = str(node.config.get("instructions", "") or "")
        if extras:
            note = (note + " ").strip() + f"Other branches (not auto-followed): {', '.join(extras)}."
        stages.append({
            "id": node.id,
            "name": node.name,
            "type": node.type,
            "kind": _stage_kind(node),
            "tools": _stage_tools(node),
            "instructions": note.strip(),
        })
        cur = branches[0].target if branches else ""
    for node in flow.nodes:
        if node.id in seen:
            continue
        stages.append({
            "id": node.id, "name": node.name, "type": node.type,
            "kind": _stage_kind(node), "tools": _stage_tools(node),
            "instructions": str(node.config.get("instructions", "") or ""),
        })
    return stages


def load_knowledge(names: list[str] | None, data_dir: str = DATA_DIR) -> str:
    files = []
    lowered = " ".join(names or []).lower()
    if not names or "policy" in lowered or "ap" in lowered or "vendor" in lowered:
        files.append("policies.md")
    if not names or "procedure" in lowered or "ap" in lowered:
        files.append("procedures.md")
    chunks: list[str] = []
    for name in files:
        path = os.path.join(data_dir, name)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as fh:
            chunks.append(f"## {name}\n{fh.read().strip()[:1800]}")
    return "\n\n".join(chunks)


def build_flow(role: str) -> FlowConfig:
    steps = ROLE_TEMPLATES.get(role, ROLE_TEMPLATES["Accounts Payable"])
    nodes = [FlowNode(id="start", type="trigger", name="Manual", config={})]
    edges: list[FlowEdge] = []
    prev = "start"
    for i, name in enumerate(steps):
        nid = f"n{i + 1}"
        ntype = "human" if "pproval" in name or "Review" in name else "finance"
        nodes.append(FlowNode(id=nid, type=ntype, name=name, config={}))
        edges.append(FlowEdge(source=prev, target=nid))
        prev = nid
    return FlowConfig(start="start", nodes=nodes, edges=edges)


def _slug(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:32] or "employee"


def new_employee(name: str, role: str, description: str = "") -> Employee:
    return Employee(
        id=_slug(name),
        name=name,
        role=role,
        description=description,
        instructions=f"Act as the {role} and complete the requested finance task.",
        tools=list(ROLE_TOOLS.get(role, ROLE_TOOLS["Accounts Payable"])),
        knowledge=["AP Policy", "Vendor Policy"],
        model=DEFAULT_EMPLOYEE_MODEL,
        flow=build_flow(role),
    )


def compile_employee(employee: Employee, base_url: str, storage_dir: str, checkpointer=None):
    """Compile an Employee config into a running Operator (the existing runtime)."""
    from ai_operator.runtime import Operator

    model_name = employee.model or "stub"
    if model_name == "stub":
        model = StubModel(DEFAULT_STUB_SCRIPT)
    else:
        model = get_model(model_name)
    policy = default_policy(employee.auto_threshold, employee.approval_threshold, employee.currency)
    return Operator(
        model=model, policy=policy, base_url=base_url,
        storage_dir=storage_dir, checkpointer=checkpointer,
        allowed_tools=list(employee.tools) if employee.tools is not None else None,
        instructions=employee.instructions or "",
        role=employee.role or "",
        employee_name=employee.name or "",
        workflow=compile_workflow(employee.flow),
        knowledge=load_knowledge(employee.knowledge),
    )