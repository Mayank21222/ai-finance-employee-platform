# improvement.md — AI Finance Employee Builder

## 0. MAJOR GOAL

The **primary goal of this project is to build a clean, dynamic, low-code/no-code platform for creating AI finance employee agents.**

This is NOT primarily a dashboard redesign.

It is NOT primarily:

- a LangGraph demo
- an AI chatbot
- a workflow viewer
- a finance analytics dashboard
- a developer console

Those are implementation pieces.

The actual product is:

> **A visual platform where a non-technical finance user can create, configure, test, deploy, and monitor AI finance employees without writing code.**

A user should be able to create employees such as:

- Accounts Payable Employee
- Accounts Receivable Employee
- FP&A Analyst
- Expense Auditor
- Reconciliation Employee
- Collections Employee
- Financial Reporting Employee
- Procurement Finance Employee
- Cash Flow Analyst
- CFO Assistant

The entire repository should be optimized around this goal.

---

# 1. PRODUCT VISION

The user experience should feel like:

> **"Create and manage an AI finance employee."**

Not:

> "Configure a LangGraph workflow."

The primary user journey should be:

```text
Create AI Employee
        ↓
Choose Finance Role
        ↓
Choose Template
        ↓
Configure Responsibilities
        ↓
Connect Knowledge
        ↓
Select Tools
        ↓
Build / Modify Workflow Visually
        ↓
Configure Approval Rules
        ↓
Select AI Model
        ↓
Test
        ↓
Deploy
        ↓
Employee Performs Work
        ↓
Monitor / Approve / Review
```

A non-programmer should be able to complete this flow.

No Python should be required.

No LangGraph knowledge should be required.

No JSON should be required.

No paid API key should be required.

---

# 2. LOW-CODE / NO-CODE PRINCIPLE

The platform should have two levels.

## Normal Mode

For finance users.

Users interact through:

- forms
- visual workflow nodes
- dropdowns
- toggles
- templates
- drag/click interactions
- approval settings
- test buttons

Users should not need to understand:

- Python
- LangGraph
- internal runtime events
- JSON
- APIs
- model internals

## Advanced Mode

For technical users.

Allow optional access to:

- custom prompts
- expressions
- variables
- JSON schemas
- API configuration
- custom tools
- advanced model configuration
- debugging
- raw execution information

Use **progressive disclosure**.

Do not expose technical complexity to beginners.

---

# 3. FREE / LOCAL AI MODELS

This is a core requirement.

The platform must work without requiring paid API keys.

The default development and demo experience should use free/local models.

Prioritize:

```text
Ollama
vLLM
Local OpenAI-compatible endpoints
Other locally hosted/open-weight models
```

The platform should be useful even when the user has:

```text
OPENAI_API_KEY = unavailable
ANTHROPIC_API_KEY = unavailable
GEMINI_API_KEY = unavailable
```

Do NOT make paid cloud APIs a prerequisite for running the application.

---

# 4. MODEL ABSTRACTION

The application should have a clean model abstraction.

Conceptually:

```text
AI Employee
      ↓
Model Interface
      ↓
Local / Free
├── Ollama
├── vLLM
└── OpenAI-compatible local endpoint

Optional Cloud
├── OpenAI
├── Anthropic
├── Gemini
└── Groq
```

Cloud providers are optional.

Do not hard-code provider-specific logic throughout the application.

There should be one clean interface for model execution.

---

# 5. FIRST-RUN EXPERIENCE

Do not make the first screen:

```text
Enter OpenAI API Key
```

Instead:

```text
Welcome

Build your first AI Finance Employee.

AI Model

● Local / Free
○ External Provider

Local models allow you to use the platform
without paid API keys.

[Continue]
```

If no local model is available:

```text
No local model detected.

[Configure Local Model]
```

The application should clearly explain what the user needs to do.

---

# 6. DEMO / STUB MODE

The application should support a demo/stub mode so that the UI and workflow experience can be demonstrated without requiring a real model.

Demo mode should be capable of simulating:

```text
Employee started
Tool executed
Data retrieved
Decision made
Approval requested
Approval received
Employee completed
```

This allows UI/product development independently of model availability.

Do not make a cloud API key necessary just to demonstrate the platform.

---

# 7. EXISTING WORKING FOUNDATION

The following components are known to be working and must be protected.

## LangGraph Runtime

```text
ai_operator/
```

Current state:

- working
- 65 tests passing

Do NOT replace LangGraph.

Do NOT introduce another agent orchestration framework.

Do NOT rewrite the runtime simply because another architecture may look cleaner.

Only modify it if a concrete product requirement genuinely requires it.

---

## Flow Configuration + Compiler

```text
platform/flow/
```

This is the existing flow representation/compiler.

Reuse it.

Do NOT create another workflow engine.

The desired architecture is:

```text
Visual Builder
      ↓
Existing Flow Configuration
      ↓
Existing Compiler
      ↓
LangGraph Runtime
      ↓
Tools / Knowledge / Models / Data
```

The visual builder should be a user-friendly interface over the existing flow system.

---

## Existing Tooling

The following are known working foundations:

```text
Tool Registry
Verifier
Tracer
Approvals
```

Do not replace them unnecessarily.

Simplify duplicated code around them when justified.

---

## Tests

The existing test suite contains:

```text
118 tests
```

All existing tests must remain passing.

Never:

- delete tests to make the suite pass
- weaken assertions merely to avoid failures
- skip tests
- remove functionality only because it makes testing difficult

---

# 8. CRITICAL ENGINEERING PRINCIPLE — MINIMAL CODEBASE

The goal is NOT to produce a large software architecture.

The goal is:

> **The smallest clean, understandable, maintainable codebase capable of delivering the AI Finance Employee Builder.**

An unnecessarily large codebase will make:

- debugging harder
- development slower
- AI coding agents less effective
- testing harder
- future modifications riskier
- duplicated behavior more likely
- architectural understanding harder

Therefore:

> **Prefer fewer, well-organized files over many tiny files.**

---

# 9. REPOSITORY AUDIT BEFORE IMPLEMENTATION

Before making major changes, inspect the entire repository.

Understand:

```text
directories
imports
routes
configuration
runtime
compiler
tools
dashboard
tests
dependencies
```

Identify:

- dead files
- unused files
- duplicate modules
- duplicate functions
- duplicate abstractions
- obsolete routes
- abandoned prototypes
- unused dependencies
- debug files
- temporary files
- redundant configuration
- over-engineered modules
- duplicated UI implementations
- unused CSS
- unused JavaScript
- old experimental code

Do not assume every existing file needs to remain.

---

# 10. REMOVE UNNECESSARY / REDUNDANT CODE

The coding agent is explicitly expected to simplify the repository.

If code is genuinely unused:

> Remove it.

If two implementations perform the same job:

> Consolidate them.

If an abstraction is unnecessary:

> Remove it.

If a dependency is unnecessary:

> Remove it.

If an old implementation has been replaced:

> Delete the old implementation after verifying references and tests.

If a module is over-engineered:

> Simplify it.

Do not preserve obsolete code simply because:

> "We may need it later."

Git history provides a backup.

---

# 11. SAFE DELETION PROCESS

Before deleting a file/module/route, check:

```text
Imports
References
Tests
Configuration
Dynamic loading
Templates
Routes
Runtime usage
```

Only delete when it is safe.

After cleanup:

```bash
pytest tests -q
```

must remain green.

---

# 12. DO NOT ARTIFICIALLY SPLIT FILES

Do not create a new file simply because a new function or class exists.

Avoid unnecessary structures such as:

```text
agent_service.py
agent_manager.py
agent_factory.py
agent_provider.py
agent_registry.py
agent_adapter.py
agent_helper.py
agent_utils.py
```

when a small number of clear modules can handle the functionality.

Likewise avoid:

```text
flow_service.py
flow_manager.py
flow_builder.py
flow_factory.py
flow_adapter.py
flow_helper.py
flow_utils.py
```

without demonstrated need.

A file can contain multiple closely related functions/classes.

---

# 13. AVOID ONE-CLASS-PER-FILE ARCHITECTURE

Do not split every class into its own file.

For example, closely related dashboard rendering functions may live together:

```text
dashboard.py
```

rather than:

```text
sidebar.py
card.py
timeline.py
approval_card.py
modal.py
button.py
```

unless those components have genuinely independent complexity.

---

# 14. AVOID PREMATURE ABSTRACTION

Do not build abstractions for hypothetical future requirements.

Avoid:

```text
AbstractModelProvider
ModelProviderFactory
ModelProviderRegistry
ModelProviderAdapter
```

if the system only requires a simple model interface.

Avoid:

```text
UniversalWorkflowManager
WorkflowFactory
WorkflowAdapter
WorkflowProvider
WorkflowRegistry
```

if the existing compiler already provides the required functionality.

Prefer simple, explicit code.

Add abstractions when there are real multiple implementations or consumers.

---

# 15. ONE SOURCE OF TRUTH

Do not duplicate important state.

There should not be separate competing representations of:

```text
Employee
Workflow
Tools
Model
Configuration
```

The existing flow configuration should remain the source of truth for workflow behavior.

Do not create:

```text
JSON flow
+
Python flow
+
frontend flow
+
database flow
```

unless there is a genuine requirement.

---

# 16. REUSE BEFORE CREATING

Before implementing anything new:

> Search the repository.

Ask:

```text
Does this already exist?
```

If yes:

```text
Reuse it.
```

If it exists twice:

```text
Consolidate it.
```

If it is overly complicated:

```text
Simplify it.
```

If it is unused:

```text
Remove it.
```

Only create new code when existing code cannot reasonably satisfy the requirement.

---

# 17. AVOID DUPLICATE FRONTENDS

There should be one primary dashboard implementation.

Do not create:

```text
dashboard/
dashboard_v2/
dashboard_new/
dashboard_redesign/
dashboard_prototype/
```

Do not maintain multiple competing implementations.

Cleanly improve the existing dashboard.

---

# 18. MINIMIZE DEPENDENCIES

For every dependency ask:

```text
Is it actually used?
Is it required at runtime?
Is it required by tests?
Can existing code solve this?
Can the Python standard library solve this?
Does another dependency already solve it?
```

Remove unnecessary dependencies.

Do not add libraries merely because they are popular.

---

# 19. AVOID UNNECESSARY FRAMEWORKS

Prefer the existing stack:

```text
Python
HTML
CSS
HTMX
SSE
Existing backend
```

Do not introduce React, Next.js, Vue, Angular, Redux, Tailwind, another workflow engine, or another agent framework unless there is a concrete technical requirement that the existing stack cannot reasonably handle.

The goal is:

> **Better product, not more technology.**

---

# 20. DEBUGGABILITY

The codebase should make debugging straightforward.

A typical execution should be traceable as:

```text
User Action
    ↓
HTTP / HTMX Request
    ↓
Handler
    ↓
Flow Configuration
    ↓
Compiler
    ↓
LangGraph Runtime
    ↓
Tool / Model
    ↓
Response
```

Avoid unnecessary chains such as:

```text
UI
 ↓
Controller
 ↓
Service
 ↓
Manager
 ↓
Factory
 ↓
Adapter
 ↓
Provider
 ↓
Registry
 ↓
Executor
 ↓
Runtime
```

unless those layers are genuinely necessary.

If debugging a simple feature requires navigating many unrelated files, simplify the architecture.

---

# 21. OPTIMIZE FOR AI CODING AGENTS

This repository will likely be developed using coding agents.

Therefore code organization must be easy for an AI coding agent to understand.

Prefer:

```text
Few files
Clear names
Clear responsibilities
Minimal indirection
Explicit control flow
Minimal duplication
```

Avoid:

```text
Deep directory trees
Tiny fragmented modules
Hidden behavior
Magic abstractions
Duplicated configuration
Implicit dependencies
```

A coding agent should be able to quickly locate:

```text
UI
Employee configuration
Flow compiler
Runtime
Tools
Approvals
Tests
```

---

# 22. DO NOT OVER-ENGINEER FOR SCALE

This is a prototype.

Do not prematurely introduce:

```text
Microservices
Message brokers
Event buses
Service meshes
Distributed caches
Complex queues
Multiple databases
Complex dependency injection
```

unless a concrete requirement demands them.

Build the product first.

Scale the architecture when the product actually requires it.

---

# 23. CODE SIZE IS NOT SUCCESS

Do not optimize for:

```text
Number of files
Number of classes
Number of abstractions
Number of dependencies
Lines of code
```

Optimize for:

```text
Ease of employee creation
Ease of workflow modification
Ease of understanding execution
Safety of finance actions
Ease of debugging
Reliability
```

---

# 24. CORE PRODUCT OBJECT — AI EMPLOYEE

The central UI object should be:

## AI Employee

Each employee contains:

```text
Employee
├── Name
├── Role
├── Responsibilities
├── Instructions
├── Goals
├── Knowledge
├── Tools
├── Data Access
├── Workflow
├── Approval Rules
├── Guardrails
├── Model
├── Triggers
└── Activity
```

The workflow is how the employee performs work.

The employee is what the user manages.

---

# 25. EMPLOYEE CREATION EXPERIENCE

The primary action should be:

```text
+ Create AI Employee
```

The user can enter:

```text
What should this employee do?

[ Process incoming invoices and prepare them for payment ]
```

Then choose:

```text
Role:
[ Accounts Payable ]
```

The system should provide a template.

AI-assisted configuration can optionally help generate the initial configuration.

However:

> AI generation must not be mandatory.

Users should be able to manually configure the employee.

---

# 26. EMPLOYEE TEMPLATES

Provide templates for:

## Accounts Payable

```text
Receive Invoice
↓
Extract Information
↓
Validate
↓
Vendor Lookup
↓
PO Matching
↓
Duplicate Detection
↓
Policy Check
↓
Approval
↓
Payment Preparation
```

## Expense Auditor

```text
Expense
↓
Extract
↓
Categorize
↓
Policy Check
↓
Anomaly Detection
↓
Review / Approval
```

## FP&A Analyst

```text
Fetch Data
↓
Calculate KPIs
↓
Budget vs Actual
↓
Variance Analysis
↓
Driver Analysis
↓
Generate Report
```

## CFO Assistant

```text
Gather Data
↓
Validate
↓
Calculate KPIs
↓
Generate Summary
↓
Human Review
↓
Distribute
```

---

# 27. FOUR PRIMARY SCREENS

Keep the main navigation simple:

```text
Flow
Agents
Run
History
```

Do NOT create separate top-level navigation for:

```text
Sessions
Documents
Message Nodes
Data Models
Connectors
Audit
Permissions
Models
```

Those capabilities should live contextually inside the four main experiences.

---

# 28. CONFIGURE / USE MODES

Provide:

```text
Configure
Use
```

## Configure

Used for:

- employee configuration
- workflow editing
- tools
- knowledge
- prompts
- policies
- testing

## Use

Used for:

- running employees
- monitoring tasks
- approvals
- exceptions
- history

The two modes should feel genuinely different.

---

# 29. FLOW — VISUAL BUILDER

Flow is the primary no-code builder.

The flow should fill the available screen.

Use an interactive visual diagram.

Nodes should be clickable.

Clicking a node opens a right-side configuration panel.

The existing specification calls for a full-area visual flow with a slide-over node panel. Retain that interaction pattern, but keep its implementation simple.

---

# 30. FLOW NODE TYPES

Keep the node library small.

## Triggers

```text
Manual
Schedule
Email
File
Webhook
```

## AI

```text
AI Employee
Classify
Extract
Analyze
Summarize
Generate
```

## Finance

```text
Invoice
Vendor
Expense
Payment
Reconciliation
Budget
Forecast
Financial Metric
```

## Logic

```text
IF / ELSE
Switch
Filter
Loop
```

## Human

```text
Approval
Review
Clarification
Escalation
```

## Output

```text
Email
Report
Database
Webhook
```

Do not build dozens of specialized nodes before the core experience works.

---

# 31. NODE CONFIGURATION

Clicking a node should open a contextual panel.

Example:

```text
Accounts Payable Employee

Role
Accounts Payable Analyst

Instructions
[........................]

Model
[ Local Model ▼ ]

Knowledge
☑ AP Policy
☑ Vendor Policy

Tools
☑ Invoice Parser
☑ Vendor Lookup
☑ PO Search
☐ Payment Creation

Approval
☑ Required

Threshold
₹100,000
```

Changes should immediately update the workflow.

---

# 32. FINANCE TOOLS

Tools should be reusable.

Examples:

```text
Invoice Parser
Vendor Lookup
PO Lookup
Transaction Search
Expense Search
Financial Calculator
Currency Converter
Reconciliation
Report Generator
Email
Spreadsheet
Database
```

Each tool should expose:

```text
Name
Description
Inputs
Outputs
Permissions
Risk Level
Approval Requirement
```

---

# 33. HUMAN-IN-THE-LOOP

This is mandatory for important financial actions.

Example:

```text
AI Employee
      ↓
Prepare Payment
      ↓
Risk / Policy Check
      ↓
Approval Required
      ↓
Finance Manager
      ↓
Approve / Reject
      ↓
Continue
```

High-risk actions should not silently execute.

Examples:

```text
Payments
Refunds
Bank detail changes
Vendor creation
Large transactions
Financial record modifications
```

---

# 34. FINANCE GUARDRAILS

Allow users to configure simple rules.

Example:

```text
Payment amount

< ₹10,000
→ Automatic

₹10,000–₹100,000
→ Manager approval

> ₹100,000
→ Finance approval
```

Rules should be configured through the UI.

Do not require code.

---

# 35. RUN EXPERIENCE

The Run page should be the main operational experience.

Example:

```text
Accounts Payable Employee

● Receiving invoice
✓ Extracted invoice data
✓ Found vendor
✓ Matched PO
✓ Checked duplicate
⚠ Approval required

APPROVAL REQUIRED

Vendor: Acme Supplies
Amount: ₹47,000
Policy: Approval required

[ Approve ] [ Reject ]
```

Updates should happen dynamically without unnecessary page refreshes.

---

# 36. EVENT TIMELINE

Translate internal events into readable language.

For example:

```text
node_entered
→ Agent started

tool_call
→ Searched vendor database

tool_result
→ Vendor found

variable_written
→ Saved invoice amount

verification_result
→ Invoice verified

approval_requested
→ Approval required
```

Do not show raw JSON by default.

Advanced/debug information may be available separately.

---

# 37. EMPLOYEE STATUS

Show useful real-time status:

```text
Idle
Working
Waiting for Approval
Completed
Failed
```

Use dynamic updates.

The user should always know what the employee is currently doing.

---

# 38. EMPLOYEE ACTIVITY

Each employee should expose:

```text
Tasks today
Completed
Running
Awaiting approval
Failed
Success rate
Average duration
Cost
```

Example:

```text
Accounts Payable Employee

342 invoices processed
331 completed
8 awaiting review
3 failed

Success rate: 96.8%
Average duration: 2.4s
```

---

# 39. HISTORY

History should contain:

```text
Task
Employee
Status
Started
Duration
Cost
```

Statuses:

```text
Completed
Failed
Waiting Approval
Interrupted
```

Clicking a run should expose its trace.

Avoid creating unnecessary standalone detail pages.

---

# 40. EXCEPTION HANDLING

Do not display generic errors.

Instead:

```text
Invoice INV-39281

Unable to match purchase order.

Reason:
No matching PO found.

Suggested action:
Ask procurement to confirm the PO.

[Retry]
[Assign]
[Resolve]
[Escalate]
```

Failed or uncertain work should become actionable.

---

# 41. KNOWLEDGE

Users should be able to provide employees with knowledge.

Support:

```text
PDF
DOCX
XLSX
CSV
TXT
```

Examples:

```text
AP Policy
Tax Policy
Accounting Guidelines
Vendor Policy
Company SOP
```

The employee should retrieve relevant knowledge during execution.

Show source information where useful.

---

# 42. DATA CONNECTIONS

Start with:

```text
CSV
Excel
PostgreSQL
MySQL
REST API
Google Sheets
Email
```

Build a clean abstraction so future connectors can be added.

Potential future integrations:

```text
SAP
Oracle
NetSuite
QuickBooks
Xero
Stripe
Snowflake
BigQuery
```

Do not attempt to implement every integration in the prototype.

---

# 43. STRUCTURED OUTPUTS

Finance workflows should use structured outputs whenever possible.

Example:

```json
{
  "invoice_number": "INV-042",
  "vendor": "Acme Supplies",
  "amount": 47000,
  "currency": "INR",
  "duplicate_risk": "low",
  "approval_required": true
}
```

Validate outputs before passing them to critical downstream operations.

Do not rely on arbitrary model text for important financial actions.

---

# 44. AUDITABILITY

Every important action should be traceable.

Record:

```text
Timestamp
Employee
Workflow
User
Action
Tool
Inputs
Outputs
Policy
Approval
Model
Model Version
Latency
Cost
Result
```

The UI should answer:

> What happened?

> Why did it happen?

> What evidence was used?

> Which policy applied?

> Who approved it?

Do not expose private chain-of-thought.

Use structured decision summaries instead.

---

# 45. UI / UX DESIGN PRINCIPLES

The UI should be:

```text
Clean
Dynamic
Modern
Professional
Calm
Trustworthy
Finance-oriented
Responsive
```

Avoid:

```text
Raw JSON
Developer-console styling
Excessive gradients
Excessive animation
Huge configuration pages
Technical jargon
Clutter
```

The interface should feel like a serious finance product.

---

# 46. DESIGN SYSTEM

Create one consistent design system.

Use shared:

```text
Colors
Typography
Spacing
Cards
Buttons
Badges
Panels
Inputs
Toggles
Dialogs
Timelines
Approval cards
```

Avoid scattered inline styling.

Use a small number of reusable CSS classes.

Do not add Tailwind merely for convenience.

---

# 47. DYNAMIC UI

The application should communicate state changes immediately.

Examples:

```text
Saving...
Saved ✓

Starting...
Running...

Waiting for approval

Approved ✓

Failed
```

Long-running operations should provide progress.

The user should never wonder whether an action succeeded.

---

# 48. RESPONSIVE UI

The interface should work on:

```text
Desktop
Laptop
Tablet
```

The workflow canvas should adapt to available space.

Side panels should behave appropriately on smaller screens.

---

# 49. JAVASCRIPT / HTMX

Prefer the existing stack.

Use:

```text
HTML
CSS
HTMX
SSE
Native browser APIs
```

where appropriate.

Use JavaScript only when necessary.

Do not introduce a full frontend framework without a concrete reason.

---

# 50. REMOVE CURRENT DASHBOARD FRAGMENTATION

The current dashboard should be simplified into:

```text
Flow
Agents
Run
History
```

Move or remove separate pages such as:

```text
/sessions
/documents
/message-nodes
/models
/permissions
/connectors
/audit
```

Examples:

```text
Sessions
→ Run modal

Documents
→ Employee/node configuration

Message Nodes
→ Flow nodes

Data Models
→ Node configuration

Connectors
→ Employee configuration

Audit
→ History timeline

Permissions
→ Policy/configuration layer
```

Do not automatically delete routes if existing tests depend on them.

First migrate the functionality.

Then remove genuinely obsolete code.

---

# 51. IMPLEMENTATION ORDER

## Phase 0 — Repository Audit & Cleanup

First:

```text
Inspect repository
Map architecture
Find dead code
Find duplicates
Find unused dependencies
Find obsolete routes
Find over-engineering
Simplify safely
```

Then:

```bash
pytest tests -q
```

---

## Phase 1 — Core Employee Model / Existing Integration

Ensure the UI can cleanly represent:

```text
Employee
Role
Instructions
Tools
Knowledge
Model
Workflow
Approval
```

Reuse the existing backend abstractions.

---

## Phase 2 — Design System + Shell

Implement:

```text
Sidebar
Top bar
Configure / Use toggle
Four primary navigation items
Shared design tokens
```

---

## Phase 3 — Flow Builder

Implement:

```text
Full-area visual workflow
Clickable nodes
Node configuration panel
Tool toggles
Validation
```

---

## Phase 4 — Agents

Implement:

```text
Employee cards
Employee status
Tool configuration
Test Prompt
Model selection
```

---

## Phase 5 — Run

Implement:

```text
Task input
Employee status
Dynamic timeline
SSE updates
Approval cards
Clarification
```

---

## Phase 6 — History

Implement:

```text
Run table
Expandable traces
Status
Duration
Cost
```

---

## Phase 7 — Knowledge / Data / Exceptions

Add:

```text
Knowledge
Data connections
Exception handling
```

Only after the core employee experience works.

---

## Phase 8 — Final Simplification

Inspect the entire repository again.

Look for:

```text
Dead files
Unused imports
Duplicate functions
Duplicate classes
Unused dependencies
Obsolete routes
Old templates
Debug code
Temporary files
Redundant configuration
Unnecessary abstractions
```

Remove what is no longer required.

Run all tests.

---

# 52. TESTING

After every meaningful phase:

```bash
pytest tests -q
```

The existing 65 tests must remain green.

Add tests for:

```text
Employee creation
Employee configuration
Flow rendering
Flow editing
Tool configuration
Model selection
Local model configuration
Run execution
Approval
History
Navigation
```

Never delete tests merely to obtain a green test suite.

---

# 53. WHAT NOT TO BUILD YET

Do not prioritize:

```text
Complex plugin marketplace
Huge connector library
Complex enterprise RBAC UI
Billing
Multi-tenant SaaS infrastructure
Complicated Git-like version control
Multiple workflow engines
Multiple agent runtimes
Complex autonomous-agent systems
Large MCP ecosystem
```

These can come later.

The first objective is an excellent core employee-building experience.

---

# 54. NO FEATURE CREEP

Before adding a feature, ask:

> Does this directly improve the ability to create, configure, test, run, monitor, or safely operate an AI finance employee?

If no:

> Defer it.

---

# 55. FINAL REPOSITORY QUALITY STANDARD

The final repository should be:

```text
Small
Clean
Understandable
Debuggable
Testable
Maintainable
Easy for AI coding agents to navigate
```

It should NOT be:

```text
Over-engineered
Fragmented
Duplicated
Over-abstracted
Dependency-heavy
Full of abandoned experiments
```

The ideal architecture is:

```text
                         UI
                          │
                          ▼
                 Employee / Flow Config
                          │
                          ▼
                  Existing Compiler
                          │
                          ▼
                 Existing LangGraph
                          │
              ┌───────────┼───────────┐
              ▼           ▼           ▼
            Tools      Knowledge     Models
              │           │           │
              └───────────┼───────────┘
                          ▼
                 Approval / Guardrails
                          │
                          ▼
                    Execution
                          │
                          ▼
                    Audit / History
```

Keep this architecture simple.

---

# 56. MINIMAL CODE PRINCIPLE

When choosing between:

### Option A

```text
3 files
100 lines
clear control flow
```

and:

### Option B

```text
12 files
250 lines
5 abstractions
3 interfaces
2 factories
```

choose **Option A** unless Option B provides a concrete, demonstrated benefit.

Prefer:

> **Minimal code. Maximum clarity.**

---

# 57. DEBUGGING PRINCIPLE

A developer should be able to trace a problem quickly:

```text
User Action
    ↓
Request
    ↓
Handler
    ↓
Flow
    ↓
Compiler
    ↓
Runtime
    ↓
Tool / Model
    ↓
Result
```

If a simple bug requires understanding ten abstraction layers:

> The architecture is too complicated.

Simplify it.

---

# 58. OPTIMIZE FOR FUTURE DEVELOPMENT

This repository should be easy for future developers and AI coding agents to modify.

When implementing a feature:

1. Search first.
2. Reuse existing code.
3. Modify the smallest number of files possible.
4. Avoid creating unnecessary abstractions.
5. Keep control flow explicit.
6. Add tests.
7. Run the full test suite.
8. Remove obsolete code created by the change.

Do not leave temporary implementations behind.

---

# 59. DEFINITION OF DONE

The product is successful when a person with no programming knowledge can:

```text
Open platform
      ↓
Create AI Employee
      ↓
Choose Accounts Payable
      ↓
Select template
      ↓
Configure responsibilities
      ↓
Select local/free AI model
      ↓
Connect AP policy
      ↓
Enable invoice/vendor/PO tools
      ↓
Visually inspect workflow
      ↓
Modify workflow
      ↓
Set approval threshold
      ↓
Run test
      ↓
Watch employee execute
      ↓
Approve a financial action
      ↓
See task complete
      ↓
Open History
      ↓
Understand what happened
```

without writing code or entering a paid API key.

---

# 60. FINAL PRODUCT VISION

The final product should feel like:

> **A visual operating system for AI finance employees.**

The platform should allow users to create employees that combine:

```text
AI Models
+
Finance Tools
+
Business Data
+
Knowledge
+
Visual Workflows
+
Human Approvals
+
Guardrails
+
Auditability
```

The central product loop is:

```text
CREATE
  ↓
CONFIGURE
  ↓
TEST
  ↓
RUN
  ↓
APPROVE
  ↓
MONITOR
  ↓
IMPROVE
```

The most important requirement is:

> **Make creating an AI finance employee easy.**

The second most important requirement is:

> **Make the employee safe and understandable.**

The third is:

> **Keep the implementation as small and simple as possible.**

---

# 61. FINAL ENGINEERING RULE

Before writing new code, ask:

> **Does this already exist?**

If yes:

> Reuse it.

If it exists twice:

> Consolidate it.

If it is unused:

> Remove it.

If it is unnecessarily complicated:

> Simplify it.

If it is working and necessary:

> Protect it.

If a new abstraction is proposed:

> Prove that it is necessary.

If a feature does not directly contribute to the AI Finance Employee Builder:

> Defer it.

The goal is not to produce the most code.

The goal is not to produce the most sophisticated architecture.

The goal is:

> **Build the smallest clean, reliable, understandable codebase that delivers a genuinely useful low-code/no-code platform for creating AI finance employee agents.**

**Build less. Reuse more. Remove aggressively. Keep the working foundation. Make the product excellent.**