# Improvement Prompt

The core agent runtime, mock payables app, tools, verifier, approval system, tracing and test suite are all working and green. Good. Now the next phase is to build the low-code platform layer on top of this foundation, which is what turns this from a single hardcoded agent into something a company can actually configure and own.

Before touching any existing code, read the current state of the repo carefully. Do not rewrite working modules. Extend them.

## What to build

The idea is a low-code platform where a company assembles its Finance AI Employee by wiring agents, tools and message nodes together on a dashboard, without writing code. The flow they create is stored as a JSON config, and the runtime compiles it into a LangGraph graph at run time. The core agent loop underneath stays exactly as it is. What changes is that the graph is now generated from config instead of hardcoded in graph.py.

The shipped example on this platform is the accounts payable employee already built, but now assembled from config visible in the dashboard.

## Known issues to fix first

The trace log exposed several real limitations that need cleaning up before the platform layer goes on top.

The stale server problem is the most dangerous. Right now run.py reuses any process already listening on APP_URL without checking whether it is serving the current code. Add a build hash or a /health endpoint that returns a short hash of the mock app's own source (a hash of mock_app/app.py is enough), and make run.py compare that against the expected hash before reusing the server. If they differ, kill the old process and start a fresh one. This burned multiple runs in the trace log and will keep burning them.

The single browser worker is a bottleneck. Right now one thread handles all Playwright calls and callers time out if it is mid-operation. For the platform version multiple agents may need the browser in sequence. Change the browser tool to use a small pool of two workers instead of one. Keep the same queue interface so no other code has to change.

The approval system currently only gates on amount. The platform version needs approvals to be configurable per agent in the flow config, not hardcoded. Move the approval threshold and the conditions that trigger it into the session context (the immutable part), so a company can set their own rules without touching code.

The history window truncation in the decide node caused the agent to forget completed actions and restart cycles. The fix was applied during the session but check that it is committed correctly and document the design decision explicitly in a comment: all history lines are kept, each compressed to a single line, so the context stays bounded without losing the record of what was done.

## The flow configuration system

Create a new module platform/flow/ with three files. The first is models.py, which defines Pydantic models for a flow: a node can be an agent node, a message node, a verify node, or an end node. An agent node has a node id, a system prompt (as a file path or inline text), step-by-step instructions, a list of document paths attached to it, a list of tool names enabled for it, a list of allowed next-node ids, a fallback next-node id, and an optional variable name to save the agent's answer into. A message node has a node id, a template string that can contain {{vars.variable_name}} placeholders, a list of next-node ids, and a self-loop is allowed. A verify node has a node id, a check type, and two next-node ids (one for match, one for mismatch). The full flow has a list of nodes, a start node id, and a session context block (the immutable values: tenant, currency, approval threshold, user role, enabled tool set for the session). The second file is validator.py, which checks that every connection points to an existing node, that there is exactly one start node and at least one end node, that every agent node has at least one connection, that every template variable referenced in a message node exists as a session variable key or as a save_as target of a prior agent node, and that no tool is enabled for an agent that does not exist in the tool registry. Validation errors should be returned as a list of plain-English messages, not exceptions, so the dashboard can display them. The third file is compiler.py, which takes a validated flow and compiles it into a LangGraph StateGraph. The existing node implementations in ai_operator/nodes/ become the building blocks; compiler.py wires them together based on the config. The existing hardcoded graph.py should be replaced by a thin wrapper that loads configs/finance_employee.json and calls the compiler.

## Variables

There are two kinds and the runtime must enforce the difference in code, not in prompts. Session context is set at the start of a run and frozen. Wrap it in a Pydantic model with model_config = ConfigDict(frozen=True) and never pass it as a writable field in LangGraph state. Session variables are mutable working memory (extracted invoice fields, check results, pending questions, agent answers saved via save_as). Every write to a session variable must be logged to the trace with the node that made it and the old and new values. Add a variables block to the run state that accumulates these writes. Message node template rendering reads from this block. If a template references a variable that is not yet set, render a visible error message and route to the fallback node rather than crashing or sending a blank.

## Self-connecting nodes and visit limits

Message nodes can connect to themselves or back to earlier nodes, which is what makes clarification loops possible. Add a node_visits counter to the run state (a dict keyed by node id). Before entering any node, check its count against the configured limit. The platform default is 3 visits per node and 40 total node visits per run. When a limit is hit, end the run with a needs_human status and a report explaining which node hit the limit and what the last few steps were.

## The dashboard

Build this as a FastAPI app in platform/dashboard/ with server-rendered HTML. It does not need a heavy frontend. HTMX is fine. It should support these things in a sensible order.

A session setup page where a company enters the immutable session context values: tenant name, currency, approval threshold, user role. These get stored in a sessions table in SQLite and linked to a run by session id.

An agents page where a company can create or edit an agent node. They give it a name, write its system prompt in a textarea, write step-by-step instructions in a second textarea, and see a list of all available tools as toggles they can switch on or off. Switching a tool on adds it to the agent's tools_enabled list in the config. Switching it off removes it. The tool list comes from the tool registry so it is always accurate.

A documents page where a company uploads text or pdf files and attaches them to specific agents. Uploaded files go into company_data/ under a tenant subfolder.

A message nodes page where a company creates message nodes. They write a template string, pick from a dropdown of the session variables that exist so far in the flow, and the dropdown inserts the {{vars.variable_name}} placeholder at the cursor. They also pick which node the message routes to after rendering, including itself.

A flow editor page where a company connects nodes together using a simple form: pick a source node, pick a destination node, add the connection. Show the current flow as a Mermaid diagram rendered on the same page so the company can see the structure as they build it. A button runs the validator and shows any errors inline.

A run console page where a company types a request, picks a session, and starts a run. The trace streams live to the page using server-sent events. Each node's entry and exit, each tool call and its result, each decision with the agent's reasoning, each variable write, and each approval or clarification prompt should appear as it happens. When an approval or clarification interrupt fires, show a prompt on the page with an input and a submit button so the company can respond without leaving the console.

The run console is the most important page for the demo video. The flow editor and tool toggles are what make this feel like a low-code platform. Prioritize these three over the others if time is short.

## What the agents page should show on the dashboard

For each agent in the flow, the dashboard shows its name and its status (idle, running, complete, failed). When running, it shows the agent's current reasoning in real time as it streams from the trace. After it finishes, it shows the answer it produced and, if it saved a variable, the variable name and value. Below each agent, show the tools it has enabled as small badges. Clicking a badge toggles the tool on or off and saves the change to the flow config immediately.

## Build order

Do the flow models, validator and compiler first, because they are the foundation. Replace the hardcoded graph.py with a compiled version of configs/finance_employee.json and run the full test suite to prove nothing broke. Then add the variable system. Then the node visit limits. Then the dashboard in the order: run console first, then agents and tool toggles, then flow editor with Mermaid, then the rest. At each stage, run the existing tests to catch regressions. Add new tests for the validator, the compiler producing a runnable graph, template rendering with missing variables, and the visit limit logic.

## What not to change

Do not touch the tool implementations, the verifier, the approval routing, the tracing module, or the test suite for the existing scenarios. They are working and tested. Extend them only if the platform genuinely needs a new capability from them.

## Commit discipline

Commit after each finished piece with a message that says exactly what changed. The flow models, the compiler, the hardcoded graph replacement, the variable system, the dashboard pages, and the visit limits should each be their own commit. Do not bundle everything into one commit at the end.

At the end, tell me the exact commands to start the platform dashboard and the mock app, give me the URL for the run console, and be straight about what is working and what is stubbed.


---

## Phase 3 additions (build after Phase 2 is green)

The trace log confirms the dashboard, flow compiler, variables, visit limits and the 63-test suite are all working. The following additions come from two sources: gaps that appeared during Phase 2, and concepts from OpenDots that translate directly to this platform.

Do not start any of this until the full existing test suite is green on a fresh run. Run it first, confirm the count, then begin.

### Add the Classifier agent and make the flow genuinely multi-agent

The original design called for a Classifier agent that reads the request and routes it to a specialist. Right now there is one agent, ap_agent, doing everything. That means a different request, such as "remind all vendors with overdue invoices" or "check last month's expenses for anything unusual", would require a new flow from scratch instead of just routing differently.

Add a second agent node to configs/finance_employee.json: a Classifier that sits before ap_agent. Give it its own system prompt in prompts/agent_classifier.md. Its job is to read the request, identify the task type (invoice entry, payment reminder, expense review, or unknown), and route to the right specialist. It saves its classification to a variable called task_type. The existing ap_agent becomes the invoice entry specialist. Add a second specialist, a reminder agent, that reads the invoice list and identifies which ones are overdue based on due dates. It should not write to the payables system, only read, and should output a list to a message node.

The point is not to build all specialists fully. The point is to show that the Classifier can route between two different agents, and that both agents run through the same compiled graph without any code change. That is what proves the platform is actually general.

The Classifier prompt should be short and focused. It gets the request and the list of available specialists from its instructions (not from the system prompt, which stays generic), and it returns which specialist to call and a short reason. Add one test that sends a reminder request and asserts the Classifier routes to the reminder agent, not ap_agent.

### Replace the stub with a real model

The stub provider was the right call for building and testing the runtime. But a submission with only a stub model is not an AI employee, it is a state machine. Before the demo video, swap the decision node to use a real model call.

The architecture already has an LLM_PROVIDER and LLM_MODEL env var and a thin llm.py interface. Wire claude-sonnet-4-6 as the provider by adding a real client class in llm.py that calls the Anthropic API with the structured decision schema. The stub stays as a fallback when STUB_MODEL=1 is set in the environment, because the tests need it. The real client reads LLM_API_KEY from the environment.

The decide node sends the system prompt, the history, and the tool results, and expects back the six-key decision JSON. The real model should do this reliably given the prompt structure already written. If it returns invalid JSON, the existing retry-once-with-error logic handles it. Do not remove the schema validation or the retry.

Run the demo once with the real model and confirm verified_complete. If the real model makes a different choice than the stub at any step (different tool, different selector), fix the prompt rather than fixing the test to match. The e2e tests should keep using the stub so CI stays fast and offline.

### Proper run state machine and retry from the dashboard

Right now a run is either in progress or done. OpenDots calls this interrupted state, meaning a run that was cut short and needs a human decision to continue. Add that concept here.

The runs table in the dashboard database should have a state column with these values: queued, running, waiting_approval, completed, failed, interrupted. The runner sets the state at each transition and the run console page reflects it live. When a run finishes, the state is either completed or failed depending on the report status. When the approval interrupt fires, the state becomes waiting_approval. If the runner process dies while a run is waiting_approval, the state stays waiting_approval and the run console shows a resume button when you open that run again. The resume button re-enters the graph from the checkpoint using the LangGraph checkpointer that is already there. This makes the SQLite checkpointer actually useful instead of just present.

Add a runs history page that lists past runs with their state, the task, the session, when it ran, and a link to the trace and evidence. The run console already writes trace.jsonl and evidence screenshots. The history page just links to them.

This does not require multi-user support or job queues. It requires the state machine, the database column, and the history page. One run at a time is still fine.

### Per-agent browser isolation

Right now the browser pool is shared across all agents in a run. That means if the Classifier and the invoice agent are both configured with browser tools, they could end up on different pool workers and their page state could mix. The stickiness fix from Phase 2 helps but does not fully prevent this.

The right model, borrowed directly from how OpenDots handles per-Dot computers, is that each agent node gets a dedicated browser worker pinned for the duration of its execution. When the agent's decide/execute/ask loop runs, it always uses the same worker. When the agent exits, the worker is released back to the pool.

Implement this by tracking which worker is assigned to which agent node in the pool, and assigning at agent entry instead of at individual tool call time. This replaces the thread-id stickiness with node-id stickiness, which is cleaner and survives the case where an agent makes tool calls from different internal threads.

### PDF parsing for uploaded documents

KNOWN_LIMITATIONS item 12 says PDFs are stored but not parsed, so they are useless to the agent. Fix this. When a PDF is uploaded, extract its text at upload time using pdfminer.six (it is pure Python and does not need a separate binary). Store the extracted text as a .txt file next to the original in company_data/<tenant>/. When the agent reads a document, it reads the .txt version if one exists. If extraction fails, store an empty .txt and log a warning so the failure is visible instead of silent.

Add a test that uploads a minimal valid PDF (a one-page PDF can be constructed in four lines of Python without any library) and confirms the extracted text file appears and contains readable content.

### Approval as a prominent dashboard card, not a text prompt

The current approval interrupt renders as a plain text message in the SSE stream with an input box. That works but it looks like a debug output, not something a finance manager would trust.

Change the approval card in the run console to look like a decision item. It should show the action being requested (what tool, what data), the policy rule that triggered the approval (which comes from the trace event that already has this), a summary of what the agent has done so far, and two buttons: Approve and Reject. Approving sends the existing /answer endpoint. The card should stay visible in the trace even after the decision is made, showing what was decided and who approved it (use the session's user_role from the session context). This is the exact pattern OpenDots uses for its human-in-the-loop cards.

Do not use JavaScript for this beyond what HTMX already provides. An hx-post on the approve/reject buttons is enough.

### External API connector for the tool registry

Right now tools are Python functions hardcoded in ai_operator/tools/. A company cannot add their own API endpoint without writing Python code, which defeats the low-code purpose.

Add a connector type to the tool registry: a tool defined entirely in config with a name, description, an HTTP method, a URL template that can include {{vars.variable_name}} placeholders, headers, a JSON body template, and a permission level. The registry loads these from a connectors section in the flow config and exposes them as tools the same way as the built-in ones. The execute node calls them with httpx.

An example connector for the mock app's own API would be enough to demonstrate: a tool called get_invoice_list that calls GET /api/invoices and returns the JSON. Add it to configs/finance_employee.json and show it working in a test. This is what makes the platform capable of reaching a company's actual ERP or accounting system without code changes.

### Run once, confirm all existing tests still pass

After each of these additions, run the full test suite before moving to the next. If something breaks, fix it before continuing. The additions above are ordered by how much they affect the existing architecture. Real model, state machine and PDF parsing are isolated. Classifier and per-agent browser isolation touch the compiler and the pool. Approval card and connector are UI and registry additions. Tackle them in that order.

At the end, confirm the exact test count and tell me how many new tests were added versus how many existed before.


---

## Phase 4 additions — NocoBase-inspired platform UX

These come from studying how NocoBase separates configuration from usage, defines data models independently from UI, and makes every permission and action traceable. None of these require rewriting what exists. They are additions to the dashboard and the data layer.

Start Phase 4 only after Phase 3 is green and committed. Run the full test suite before touching anything.

### Usage mode and configuration mode

NocoBase's most useful UX idea is a single toggle that switches the dashboard between two completely different purposes. In configuration mode, a finance operations manager can see agents, edit their instructions, toggle tools, and wire connections. In usage mode, that same person just runs the employee: types a task, watches it work, answers approval prompts, and reads the report. The configuration scaffolding is invisible.

Add a mode toggle to every page header. A button labeled "Configure" switches to configuration mode and shows all the editing surfaces (agent edit forms, tool toggles, flow editor, connection forms). A button labeled "Use" switches to usage mode, which hides all editing surfaces and shows only the run console, the run history, and the current variables. The active mode is stored in the session (a cookie or a query parameter is fine) and persists across page loads. This is a CSS and routing change, not an architecture change. Add a data attribute to the body element reflecting the current mode, and use CSS to show and hide the relevant sections.

The reason this matters for the demo: you can show configuration mode first (here is how a company would set this up) and then switch to usage mode to run the actual task. That transition makes the low-code story visible without needing a separate demo script.

### Data model as a separate configurable concept

Right now the agent's understanding of what an invoice contains is embedded in its system prompt. That means changing a field name or adding a new required field requires editing the prompt directly, which defeats the low-code purpose.

Add a data models page to the dashboard. A data model has a name (Invoice, Vendor, Expense) and a list of fields. Each field has a name, a type (text, number, date, boolean), whether it is required, and a short description that goes into the agent's context. The description is how the agent knows what to look for: "amount: the total invoice value in the session currency, as a number without currency symbols."

The Invoice data model for the shipped example should define these fields: vendor_name (text, required), invoice_number (text, required), amount (number, required), due_date (date, required), purchase_order_ref (text, optional).

When an agent node is compiled, the compiler reads the data model attached to it (a field in the agent node config) and appends a structured field list to the agent's system prompt, below the instructions. The agent is expected to extract these fields from documents and save them as session variables using the field names as variable names. This replaces the current situation where field names live in multiple places (the prompt, the verifier, the variable writes) and go out of sync.

The verifier also reads the data model: it knows the expected field names and types without them being hardcoded. A type check on amount (must parse as a number) and a format check on due_date (must be a valid date) happen at the verifier level, not inside the agent prompt.

Add a test that attaches an Invoice data model to the ap_agent, runs the demo, and confirms that the variables written during the run match the field names in the data model exactly.

### Field-level permissions per agent

NocoBase assigns each AI employee its own role with field-level read and write permissions. For the finance employee, this translates directly to which fields each agent can write to versus only read.

Add a permissions block to the data model definition. Each field has a list of agent node IDs that can write it and a list that can only read it. The Policy and Approval agent should be read-only on all Invoice fields. The Invoice Entry agent can write all invoice fields. The Classifier can write only the task_type session variable.

The variable write path in ai_operator/variables.py should check this before writing. If an agent tries to write a field it does not have write permission on, the write is rejected, the attempt is logged to the trace with a permission_denied event, and the observation goes back to the agent. This is enforced in code, not in the prompt.

Add an audit log section to the data models page that shows every write to a model's fields: which agent wrote it, what the old and new values were, and what time. This is already logged in trace.jsonl but presenting it as a field-level audit trail is what makes it feel like a real business system.

### Visual flow editor: clickable nodes, not just a read-only diagram

The current flow editor renders a Mermaid diagram beside a form where you pick source and destination nodes to add connections. This works but it feels like two separate things. NocoBase's WYSIWYG principle is that you interact directly with the thing you are configuring.

Make the Mermaid diagram clickable. When you click a node in the diagram, the right side of the page replaces with an edit form for that node, showing its system prompt, instructions, attached documents, and enabled tools. Saving the form updates the config and re-renders the diagram. This requires replacing the static Mermaid render with an SVG that the server generates from the flow config and annotates with hx-get attributes for each node shape. When clicked, HTMX swaps in the node edit form to a panel beside the diagram.

This is not a drag-and-drop canvas. You still add connections through the existing form. But clicking a node to edit it in place is a significant UX improvement over navigating to a separate agents page.

The server-side SVG generator can be simple: a fixed layout algorithm (top-to-bottom, nodes in the order they appear in the flow config, edges as straight lines) is enough. The visual does not need to be beautiful. It needs to be interactive.

### Plugin-style tool connectors with a connector registry page

The external API connector added in Phase 3 allows HTTP-based tools to be defined in config. The missing piece is a UI for managing them, and a design that makes the pattern extensible without code changes.

Add a connectors page to the dashboard. Each connector has a name, a base URL, a list of endpoints (each with a name, HTTP method, path, description, expected response fields, and permission level), and optional default headers. A company configures their ERP system's base URL once and then adds individual endpoints as tools. Each endpoint appears as a toggleable tool in the agent tool list.

The connector config is stored in a connectors table in the dashboard database and loaded into the tool registry at startup. Adding a new connector from the dashboard takes effect on the next run (the existing "config edits apply next run" limitation already covers this).

The shipped example connector is the mock app's own read API, already suggested in Phase 3. On the connectors page, it should appear as "Mock Payables System" with one endpoint: "Get invoice list" (GET /api/invoices, reads vendor and amount fields, permission level: read). Show it as active in the demo.

This pattern directly matches NocoBase's "use the main database, external databases, and third-party APIs as data sources" principle, translated to the agent tool layer.

### Audit trail page

NocoBase's audit logs make every data change and workflow trigger traceable. The trace.jsonl file already captures everything. The missing piece is a readable UI for it.

Add an audit trail page to the dashboard. It shows every event across all runs in reverse chronological order: run id, node, event type, timestamp, and a short summary. Event types should be rendered with distinct labels: variable_written shows old and new values, tool_disabled shows which tool was blocked and why, visit_limit_hit shows the node and the last steps, approval_requested shows the action and the decision, verification_result shows matched or mismatched with the expected and actual values.

Clicking any row expands to show the full event from trace.jsonl. Filtering by run id, node name, or event type should work via query parameters so a link can point to a specific filtered view.

This page does not need any new data. Everything it shows is already in trace.jsonl. It is a reading and rendering concern.

### What not to change in Phase 4

Do not change the agent runtime, the LangGraph graph, the tool implementations, the verifier logic, or the test suite for existing agent behavior. Phase 4 is entirely additive: new dashboard pages, new data layer concepts, and a UX improvement to the flow editor. The risk of regressions from this phase is low if the existing tests stay green throughout.

After each addition, run the full test suite. Add at least one test per new page: the data models CRUD cycle, the field-level permission rejection, the connector registry loading into the tool registry, and the audit trail rendering from a synthetic trace.jsonl file. Report the final test count alongside the before/after comparison.