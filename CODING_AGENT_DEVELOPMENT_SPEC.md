# Verified Coding Agent — Development Specification

> Status: Draft / V1  
> Purpose: This document is the source of truth for Codex implementation.  
> Core idea: **Project Initialization + Executable Plan + Task Graph + Execution Trace + Verification + Evidence + Deterministic Workflow**

---

# 1. Project Overview

## 1.1 Project Goal

Build a reliable software engineering runtime for coding agents.

This project is **not another AI coding chat/CLI**.

The goal is to transform:

```text
User Requirement
```

into:

```text
Requirement
    ↓
Executable Plan
    ↓
Task Graph
    ↓
Coding
    ↓
Verification
    ↓
Review
    ↓
Evidence
    ↓
Quality Gate
    ↓
Commit / PR
```

The system must ensure that a task is considered complete only when there is sufficient engineering evidence.

Core rule:

```text
Done != LLM says done

Done =
Requirement satisfied
+ Tests passed
+ Static checks passed
+ Acceptance criteria satisfied
+ Review passed
+ Evidence recorded
```

---

# 2. Project Positioning

The project should be positioned as:

> **Verified Coding Agent Runtime**

or:

> **Software Engineering Harness for Coding Agents**

User-facing positioning:

> **A coding agent runtime for existing repositories that follows project rules and delivers changes with versioned acceptance evidence.**

The intended user benefits are inspectable rule adherence, explicit progress and remaining work, and evidence that applies to the delivered code. These are design commitments until implemented and evaluated. `VERIFIED` means the required acceptance checks have valid supporting evidence under the recorded policy; it is not a general proof of software correctness.

The system focuses on:

- reliable task execution;
- explicit requirements;
- executable planning;
- dependency-aware task orchestration;
- isolated workspace execution;
- automated verification;
- evidence-driven completion;
- deterministic outer workflow;
- LLM-driven intelligent inner nodes.

The system should not depend on a single LLM provider.

---

# 3. Core Design Principle

The fundamental architectural rule is:

```text
Deterministic Engineering
          ×
       LLM Agent
```

The LLM must **not control the entire software development lifecycle**.

Instead:

```text
Workflow Engine
    controls lifecycle

LLM
    handles reasoning-intensive nodes
```

Example:

```text
┌─────────────────────────────┐
│ Deterministic Workflow      │
│                             │
│ Plan → Execute → Verify     │
│   ↑               │         │
│   └──── Replan ←──┘         │
└─────────────┬───────────────┘
              │
           LLM Agent
```

The outer loop must be controlled by code.

The inner reasoning can be controlled by the LLM.

---

# 4. Design Principles

## 4.1 Task Graph is the Source of Truth

Conversation history must not be the source of truth.

Bad:

```text
User
 ↓
LLM Conversation
 ↓
Tools
 ↓
More Conversation
 ↓
Done
```

Correct:

```text
Requirement
 ↓
Task Graph
 ↓
State Machine
 ↓
Agent Execution
 ↓
Evidence
```

Agents may restart.

Models may change.

Context may be compressed.

Sessions may resume.

The task state must remain consistent.

---

## 4.2 Plan Must Be Executable

A plan must not only be Markdown.

Bad:

```text
1. Modify auth.py
2. Add API
3. Add tests
4. Run tests
```

Instead, planning should compile requirements into structured tasks.

Conceptually:

```text
Requirement
     ↓
Plan Compiler
     ↓
Executable Task DAG
```

---

## 4.3 Agents Are Workers, Not the System

Do not design the system around many persistent role-playing agents.

Avoid making the core architecture:

```text
PM Agent
Architect Agent
Backend Agent
Frontend Agent
QA Agent
DevOps Agent
Security Agent
```

V1 should have only a few explicit intelligent components:

```text
Explorer
Planner
Coder
Reviewer
Debugger
```

Agents should be created around tasks.

Example:

```text
Task
 ↓
Context Pack
 ↓
Coder
 ↓
Dispose Context
```

A new task may start with a new clean Agent context.

---

## 4.4 Verification Determines Completion

Coder cannot mark a task successful.

Coder may only report:

```text
implementation_finished
```

Only Workflow Engine, after verification and the applicable QualityGate, may transition a task to:

```text
VERIFIED
```

---

## 4.5 Every Completion Must Have Evidence

Every acceptance criterion should have evidence.

Readable result summary (the canonical AcceptanceSpec and Evidence fields are in Sections 9.1 and 24):

```yaml
acceptance:
  token_rotation:
    status: passed
    evidence:
      test: tests/auth/test_refresh.py::test_rotation

  replay_rejected:
    status: passed
    evidence:
      test: tests/auth/test_refresh.py::test_replay

  regression:
    status: passed
    evidence:
      command: pytest tests/auth/
      result: 121 passed
```

---

## 4.6 Initialize and Maintain Project Knowledge

Before planning the first coding task, establish a reusable project knowledge baseline through `agent init` or the equivalent first-run flow.

Initialization combines repository exploration, existing documentation, and user-supplied development rules. It must distinguish observed code facts, explicit project rules, and unconfirmed assumptions.

Agents must reference relevant project knowledge during planning, implementation, and review. Initialization does not replace task-specific source inspection. See Section 16 for its lifecycle.

---

## 4.7 Record Plans, Actions, and Outcomes Separately

Every coding session must preserve:

- the versioned plan describing intended work;
- runtime-generated events describing actual execution;
- verification evidence and a final outcome describing what was established.

An Agent summary is not a substitute for tool execution records. Execution history must remain available after failure, cancellation, or replanning. See Sections 24, 30, and 31.

---

# 5. High-Level Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│                        Interface                            │
│                                                             │
│              CLI / API / Web / IDE / ACP                   │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                     Task Control Plane                      │
│                                                             │
│ Requirement Analyzer                                       │
│        │                                                    │
│ Requirement Contract                                       │
│        │                                                    │
│ Plan Compiler                                               │
│        │                                                    │
│ Executable Task Graph                                       │
│        │                                                    │
│ Scheduler                                                   │
│                                                             │
│ Policy / State / Checkpoint                                 │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Agent Intelligence                       │
│                                                             │
│ Explorer                                                    │
│ Planner                                                     │
│ Coder                                                       │
│ Debugger                                                    │
│ Reviewer                                                    │
│                                                             │
│ Model Router                                                │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                      Context Engine                         │
│                                                             │
│ Repo Map                                                    │
│ AST                                                         │
│ LSP                                                         │
│ Code Search                                                 │
│ Git History                                                 │
│ Symbol Graph                                                │
│ Impact Analysis                                             │
│                                                             │
│ Context Builder → TaskContextPack                           │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Execution Runtime                        │
│                                                             │
│ Workspace Manager                                           │
│ Git Worktree                                                │
│ Sandbox                                                     │
│ Tool Runtime                                                │
│                                                             │
│ filesystem                                                  │
│ search                                                      │
│ patch                                                       │
│ shell                                                       │
│ git                                                         │
│ test                                                        │
│ MCP                                                         │
└────────────────────────────┬────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────┐
│                    Verification Engine                      │
│                                                             │
│ Build                                                       │
│ Type Check                                                  │
│ Lint                                                        │
│ Unit Test                                                   │
│ Integration Test                                            │
│ Acceptance Checker                                          │
│ Reviewer                                                    │
│ Evidence Ledger                                             │
│ Quality Gate                                                │
└────────────────────────────┬────────────────────────────────┘
                             │
                   FAIL      │      PASS
                    │        │
                    ▼        ▼
                 Fix /      Delivery
                 Replan        │
                               ▼
                        Commit / PR
```

---

# 6. Main Execution Flow

Project setup, once and then refreshed as needed:

```text
Repository + Existing Documentation + User Development Rules
        ↓
agent init
        ↓
Versioned Project Knowledge
```

The standard workflow should be:

```text
User Requirement
       │
       ▼
Load Project Knowledge / Check Relevant Sources for Changes
       │
       ▼
Repository Exploration
       │
       ▼
Requirement Contract
       │
       ▼
Plan Compiler
       │
       ▼
Executable Task Graph
       │
       ▼
Human Approval
       │
       ▼
Task Scheduler
       │
       ▼
Context Builder
       │
       ▼
Workspace / Worktree
       │
       ▼
Coder Agent
       │
       ▼
Patch
       │
       ▼
Verification
       │
       ├── FAIL → Debug / Fix
       │
       └── PASS
              │
              ▼
         Review Agent
              │
       ┌──────┴──────┐
       │             │
    Issues         Clean
       │             │
       ▼             ▼
      Fix       Quality Gate
                     │
                     ▼
                Task VERIFIED
                     │
                     ▼
                 Next Task
                     │
                     ▼
             Integration Test
                     │
                     ▼
                  Commit
```

Record plan versions, tool events, state transitions, and evidence throughout this flow. Each session retains the project knowledge revisions it used. Final delivery includes a concise result report with links to the plan, diff, and evidence.

Assess task complexity after task-specific exploration and before compiling the executable plan. For staged refactors, prepare and run baseline checks before restructuring the implementation; preparation may add characterization tests. Verification is also used at this preparation point. See Section 29.

---

# 7. Requirement Contract

The user's natural-language request must first be converted into a structured `RequirementContract`.

Example:

```yaml
id: req-001

title: Refresh token rotation

goal:
  Implement single-use refresh token rotation.

functional_requirements:
  - refresh token can be exchanged for a new token
  - old refresh token becomes invalid
  - token replay must return 401
  - existing access-token behavior must remain unchanged

constraints:
  - do not change public login API
  - no new external dependency unless necessary

non_goals:
  - OAuth support
  - session management redesign

risk:
  level: high
  security: high
  public_api: medium
  database: medium

suggested_validation:
  - unit tests
  - auth integration tests
  - static checks
```

Recommended model:

```python
class RequirementContract(BaseModel):
    id: str
    title: str
    goal: str

    functional_requirements: tuple[str, ...]
    constraints: tuple[str, ...] = ()
    non_goals: tuple[str, ...] = ()

    risk: RiskProfile

    suggested_validation: tuple[str, ...] = ()
```

---

# 8. Plan Compiler

The Planner should not directly control execution.

Planner output should be validated and compiled into an executable graph.

Architecture:

```text
RequirementContract
        ↓
Planner LLM
        ↓
PlanDraft
        ↓
Plan Validator
        ↓
Plan Compiler
        ↓
TaskGraph
```

Plan validation must check:

- duplicate task IDs;
- circular dependencies;
- missing dependencies;
- missing acceptance criteria;
- overly broad tasks;
- conflicting file scopes;
- impossible task ordering.

Each plan version must also record:

- its requirement, project knowledge revision, and starting workspace revision;
- stable task and acceptance criterion IDs;
- relevant development rule and documentation references for each task;
- intended changes, permitted scope, and required validation;
- known assumptions and unresolved questions that affect execution.

Record complexity and its reasons, unknowns, and execution strategy separately from risk. Large tasks may use a versioned milestone roadmap with a fully specified current batch. Later milestones remain explicit pending scope until refined into executable tasks; do not schedule placeholders with unknown acceptance criteria. Completing the current batch does not complete the requirement while required scope remains pending. See Sections 29.2–29.5.

Show users a readable summary of scope, validation, and unresolved decisions. Users must not need to interpret the serialized DAG to understand the plan.

Persist the plan before executing its tasks. Apply the existing approval policy to the concrete plan version. Changes during execution follow Section 27; never overwrite the previous plan silently.

---

# 9. TaskSpec

Every task is an executable engineering contract.

Example:

```yaml
id: auth-04

title: Implement refresh token rotation

goal:
  Implement single-use refresh tokens.

depends_on:
  - auth-02
  - auth-03

scope:
  allowed:
    - src/auth/**
    - tests/auth/**

  forbidden:
    - src/payment/**

requirements:
  - refresh token is single-use
  - old token is invalidated
  - replay returns 401

acceptance:
  criteria:
    - id: refresh-succeeds
      description: refresh succeeds
      required_check_ids: [refresh-tests, auth-regression, auth-lint, auth-review]
    - id: replay-rejected
      description: replay fails
      required_check_ids: [refresh-tests, auth-regression, auth-review]
    - id: expired-token-rejected
      description: expired token fails
      required_check_ids: [refresh-tests, auth-regression]
    - id: login-compatible
      description: existing login flow still passes
      required_check_ids: [auth-regression, auth-review]

  checks:
    - id: refresh-tests
      description: refresh behavior tests
      evidence_type: test
      command: [python, -m, pytest, tests/auth/test_refresh.py]
    - id: auth-regression
      description: authentication regression tests
      evidence_type: test
      command: [python, -m, pytest, tests/auth/]
    - id: auth-lint
      description: authentication static style checks
      evidence_type: lint
      command: [python, -m, ruff, check, src/auth]
    - id: auth-review
      description: independent review of behavior and project rules
      evidence_type: review

risk:
  level: high
  security: high
  public_api: medium
  database: medium

permissions:
  network: false
  database: test_only
  shell: restricted

max_attempts: 3
```

Recommended core object:

```python
class TaskSpec(BaseModel):
    id: str
    title: str
    goal: str

    depends_on: tuple[str, ...] = ()

    scope: ScopePolicy

    requirements: tuple[str, ...]
    acceptance: AcceptanceSpec

    risk: RiskProfile
    permissions: PermissionPolicy

    max_attempts: int = 3
```

The serialized plan must associate each TaskSpec with its relevant project rules and source references. Acceptance criteria need stable IDs within that plan version so results can be traced back to the intended behavior.

## 9.1 P01 Contract Decisions (D02)

`RequirementContract.risk` and `TaskSpec.risk` both use RiskProfile from Section 29.1.
The implemented models live in `src/coding_agent/core/models.py`; Python snippets
summarize fields. Models are frozen, reject unknown fields, and use tuples for
collections (JSON arrays). Load external data through validated construction,
not `model_construct` or unvalidated `model_copy(update=...)`.

`AcceptanceSpec` contains nonempty `criteria` and `checks`. Each criterion has a
stable `id`, `description`, and nonempty `required_check_ids`. Each check has an
`id`, `description`, `evidence_type`, and optional `command` argument array.
Executable check types require commands; review/diff checks can refer to non-command
records. P01 does not execute commands. Arguments preserve spaces and empty strings.

Criterion IDs and check IDs are separately unique within a task. Duplicate or missing
references and unreferenced checks are invalid. Every listed criterion and linked
check is required. A check may support several criteria, with separate evidence for
each criterion/check pair; a test exit code alone does not establish behavioral
coverage. P09 records actual coverage. Optional observations cannot replace required checks.

IDs use ASCII letters/digits followed by letters/digits or `._:-`. Stable identity
includes the task ID and criterion/check ID; P07 preserves identities across plan
versions. `AcceptanceSpec.validate_evidence` checks the criterion, check, description,
type and declared command association. It does not authenticate sources, check
freshness/task ownership, or decide success. P02 QualityGate checks the active task,
plan/context/snapshot and required outcomes; P03/P09 supply runtime-owned records.

`ScopePolicy` declares repository-relative glob patterns with `/` separators.
Absolute paths, drive prefixes, empty/dot/parent segments, backslashes and colons are
invalid. Empty `allowed` grants no write scope; `forbidden` takes precedence in P03.
Lexical validation does not resolve symlinks or enforce a sandbox.

`PermissionPolicy` defaults to `network: false`, `shell: deny`, `database: deny`.
P01 also accepts `shell: restricted` and `database: test_only`; network must be an
actual boolean. These declare requested capabilities only. P03 must reject execution
if its backend cannot enforce requested restrictions (D05 remains open).

---

# 10. Task Graph

Task Graph is one of the most important domain objects.

Example:

```text
T1 Shared contracts + local tests/review
 │
 ├────────────────────────┐
 ▼                        ▼
T2 DB behavior          T3 API behavior
+ local tests/review    + local tests/review
 │                        │
 └───────────┬────────────┘
             ▼
    T4 Service + local tests/review
             │
             ▼
    Final integration verification + delivery gate
```

Each business task includes implementation, required local verification, and applicable
review before becoming VERIFIED. Repository exploration prepares the plan. Tests and
review needed to verify a task are not downstream business tasks. Final integration
verification checks the combined result before delivery and cannot replace task-local
evidence. This resolves D01 without allowing unverified dependencies to run.

P01 API (implemented in `src/coding_agent/core/graph.py`):

```python
class TaskGraph:
    tasks: tuple[TaskSpec, ...]

    def add_task(self, task: TaskSpec) -> TaskGraph: ...
    def add_dependency(self, task_id: str, dependency_id: str) -> TaskGraph: ...
    def ready_tasks(self, states: Mapping[str, TaskState]) -> tuple[TaskSpec, ...]: ...
    def descendants(self, task_id: str) -> frozenset[str]: ...
    def ancestors(self, task_id: str) -> frozenset[str]: ...
    def validate_graph(self) -> TaskGraph: ...
```

Construction validates duplicate IDs, missing dependencies and cycles; an empty graph
is valid. Additions return a newly validated graph and preserve the old graph on
success or failure. For forward references, construct the complete graph at once.
Queries never mutate states. `ready_tasks` requires exactly one typed state per task,
rejects missing/unknown entries, and returns PENDING/READY candidates in plan order
only when every dependency is VERIFIED. Ancestor/descendant queries return transitive
task IDs, excluding the starting task.

The earlier proposed `mark_running/mark_verified/mark_failed` graph methods are removed.
Workflow Engine owns the sole writable state store in P02; the graph describes dependencies.

Scheduler must only return tasks whose dependencies are:

```text
VERIFIED
```

---

# 11. Task State Machine

Minimum states:

```text
PENDING
   │
   ▼
READY
   │
   ▼
RUNNING
   │
   ▼
IMPLEMENTATION_FINISHED
   │
   ▼
VERIFYING
   │
   ├──── FAIL ───→ DEBUGGING
   │                 │
   │                 ▼
   │              RUNNING
   │
   ▼
REVIEWING
   │
   ├──── ISSUE ───→ FIXING
   │                 │
   │                 ▼
   │              VERIFYING
   │
   ▼
VERIFIED
```

Additional states:

```text
BLOCKED
REPLAN_REQUIRED
FAILED
CANCELLED
```

State transitions must be controlled by code.

Agents must not directly mutate task state.

P01 implements `validate_transition(current, target)` as a pure check. No self-transitions
are legal. Passing this check does not establish dependency readiness, approval, retry
budget or QualityGate success; those preconditions belong to P02. D03 will reconcile
execution modes in P02; P01 has no direct VERIFYING → VERIFIED bypass.

| Current state | Allowed next states |
| --- | --- |
| PENDING | READY, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| READY | RUNNING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| RUNNING | IMPLEMENTATION_FINISHED, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| IMPLEMENTATION_FINISHED | VERIFYING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| VERIFYING | REVIEWING, DEBUGGING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| REVIEWING | VERIFIED, FIXING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| DEBUGGING | RUNNING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| FIXING | VERIFYING, BLOCKED, REPLAN_REQUIRED, FAILED, CANCELLED |
| BLOCKED | READY, REPLAN_REQUIRED, FAILED, CANCELLED |
| REPLAN_REQUIRED | FAILED, CANCELLED |
| VERIFIED / FAILED / CANCELLED | None |

Unblocking requires runtime reconciliation before returning to READY. REPLAN_REQUIRED
does not restart implementation in the same plan; P07/P11 introduce versioned replanning.
Terminal states cannot be reopened through P01 validation. A historical VERIFIED state
does not make stale evidence valid; freshness remains a separate gate check.

---

# 12. Scheduler

Responsibilities:

- find ready tasks;
- respect dependencies;
- enforce concurrency limits;
- respect risk level;
- choose sequential vs parallel execution;
- assign isolated workspace;
- handle task completion;
- trigger downstream tasks.

Example:

```python
class TaskScheduler:

    async def next_tasks(
        self,
        graph: TaskGraph,
        max_concurrency: int
    ) -> list[TaskSpec]:
        ...
```

V1 concurrency may be:

```text
1
```

but architecture must support future parallelism.

---

# 13. Agent Architecture

## Explorer

Responsibilities:

- inspect repository structure;
- identify language/framework;
- locate relevant modules;
- identify tests;
- locate project instructions;
- generate repository summary.

Explorer should primarily use:

```text
read
search
git
AST/LSP
```

Explorer must be read-only.

---

## Planner

Input:

```text
RequirementContract
+
RepoSummary
+
Relevant Project Knowledge
```

Output:

```text
PlanDraft
```

Planner must never edit files.

---

## Coder

Input:

```text
TaskSpec
+
TaskContextPack
```

Capabilities:

```text
read
search
patch
shell
test
git diff
```

Coder output:

```text
ImplementationResult
```

Example:

```python
class ImplementationResult(BaseModel):
    task_id: str
    modified_files: list[str]
    summary: str
    commands_executed: list[str]
```

Coder cannot declare success.

Coder executes the active task under the persisted plan and its project rules. If new findings require a scope or acceptance change, return a replan request instead of silently changing the contract. `commands_executed` and `modified_files` in the Agent response are claims; the runtime records the actual commands and diff.

---

## Debugger

Debugger handles failed verification.

Input:

```text
TaskSpec
Patch
VerificationFailure
Relevant Context
```

Debugger should focus on:

```text
root cause
→ minimal fix
→ rerun affected checks
```

---

## Reviewer

Reviewer should receive a fresh context.

Input:

```text
Requirement
+
TaskSpec
+
Git Diff
+
Relevant Code
+
Verification Evidence
```

Reviewer should NOT receive the full Coder conversation.

Output:

```yaml
blocking:
  - ...

major:
  - ...

minor:
  - ...

result:
  PASS | FAIL
```

---

# 14. Context Engine

Context Engine must be independent from the Agent implementation.

Architecture:

```text
Repository
   │
   ├── File Index
   ├── AST
   ├── LSP
   ├── Git History
   ├── Tests
   └── Config
       │
       ▼
   Repo Intelligence
       │
       ▼
   Context Builder
       │
       ▼
   TaskContextPack
```

---

# 15. TaskContextPack

Example:

```yaml
task_id: auth-04

repository:
  language: python
  framework: fastapi

architecture:
  - auth module handles JWT lifecycle

symbols:
  - AuthService.refresh
  - TokenRepository
  - RefreshToken

files:
  - src/auth/service.py
  - src/auth/repository.py
  - tests/auth/test_refresh.py

related_tests:
  - tests/auth/test_login.py

instructions:
  - AGENTS.md
  - CONTRIBUTING.md

git_history:
  - commit: abc123
    reason: auth token changes

constraints:
  - public login API cannot change

project_knowledge:
  revision: project-003
  references:
    - AGENTS.md
    - docs/architecture/auth.md
  rules:
    - id: auth-storage-boundary
      source: docs/architecture/auth.md
      instruction: AuthService accesses token storage through TokenRepository
  open_questions: []
```

Different Agents should receive different context packs.

```text
PlannerContext
!=
CoderContext
!=
ReviewerContext
```

Every task execution/review context pack must reference the active plan and a recorded project knowledge revision. Before a plan exists, Planner receives the requirement and the knowledge revision used to prepare its draft. Include relevant rules, source locations, and unresolved assumptions rather than loading all project documentation into every Agent context. Planner, Coder, and Reviewer must receive the applicable constraints even when their other context differs.

---

# 16. Repository Intelligence

V1:

```text
File tree
Text search
Git diff
Git history
Basic language detection
Test detection
```

Later:

```text
AST
LSP
Symbol graph
Call graph
Dependency graph
Impact analysis
```

Do not implement sophisticated RAG in V1.

Repository intelligence should remain deterministic whenever possible.

## 16.1 Project Initialization (`agent init`)

Initialization creates a reusable project knowledge baseline with LLM-assisted exploration and user input. It is a V1 capability.

```text
Read Existing Project Instructions and Documentation
        ↓
Inspect Repository Structure and Important Code Paths
        ↓
Draft Project Guide with Sources and Open Questions
        ↓
Incorporate User Development Rules and Corrections
        ↓
Save Project Knowledge Revision
```

Start with repository-wide orientation, then inspect representative modules and important call paths. Completion does not require reading every file or constructing a complete call graph. Record exploration gaps and deepen the analysis when a task needs them.

Reuse existing `AGENTS.md`, `CONTRIBUTING.md`, architecture documents, and API documentation as sources. Prefer references and targeted additions over copying their contents into competing documents. Explorer remains read-only; the initialization controller persists generated artifacts through Tool Runtime.

## 16.2 Project Guide Contents and Authority

The project guide must cover, where available:

| Area | Expected content |
| --- | --- |
| Project overview | Purpose, technology stack, directory structure, important entry points |
| Module relationships | Module responsibilities, key callers/callees, interface boundaries, representative data flows |
| Development rules | Naming and style, error handling, allowed dependency directions, compatibility requirements |
| Validation | Build, test, lint, and type-check commands; working directories; environment prerequisites |
| Change boundaries | Public APIs, migrations, generated files, modules with special restrictions |
| Open questions | Missing information, conflicting documents, unverified assumptions, exploration gaps |

Every entry must distinguish its authority:

- **Observed fact:** supported by a file, symbol, configuration, or other repository source. Existing code patterns are not automatically development rules.
- **Explicit rule:** supplied by the user or an applicable project instruction, with its source recorded. Give rules stable reference IDs.
- **Unconfirmed assumption:** an inference or unresolved question that must not silently become a requirement.

For example, observing that OrderService writes inventory tables directly does not establish that new code may do so. The user may specify that new code must call InventoryService, while the existing direct access remains a documented legacy exception.

Invite the user to supplement architecture intent, module interaction rules, and development conventions. Ask focused questions about decisions that affect work; do not require users to rewrite information already present in the repository. Existing explicit instructions remain effective without repeated confirmation.

Preserve unresolved items. Only questions that prevent safe or correct execution of the current task need to block that task. Explicit user instructions take precedence over generated guidance; surface material conflicts with existing project rules rather than silently rewriting them.

## 16.3 Minimal Initialization Artifacts

V1 may use:

```text
.agent/
├── project.md       # Human-readable guide and index of existing documentation
└── project.json     # Revision, source references/fingerprints, and exploration coverage
```

`project.md` stores user additions when there is no suitable existing document. `project.json` tracks provenance and freshness; it must not become a second editable copy of the rules. Together they form one project knowledge revision. Referenced source content used by a session must be retained or reproducibly addressable.

Initialization discovers validation commands; it must not describe them as passing until the verification runtime actually executes them. Discovery alone must not install dependencies or mutate application code.

Running `agent init` again reuses the existing baseline and preserves user-authored content. `agent init --refresh` refreshes changed sources and affected generated entries. It must not overwrite explicit rules with new model inferences.

On first `agent plan` or `agent run`, missing initialization invokes the same setup flow. Non-interactive execution may proceed with available instructions and recorded assumptions; if a required user decision is missing, stop with an actionable explanation rather than guessing or waiting indefinitely.

## 16.4 Referencing and Updating Project Knowledge

Before planning, load relevant rules and compare recorded sources with the current repository, including uncommitted changes. Refresh affected observations and inspect task-specific code. A source fingerprint detects change; it does not establish that the old explanation is still semantically correct.

During execution:

1. Associate tasks with the rules and documentation they rely on.
2. Keep the active context revision explicit. If relevant source or user guidance changes, reconcile it before continuing dependent work and record any plan revision.
3. Surface documentation/code conflicts as findings with source references.
4. Include necessary documentation maintenance in the plan when interfaces, module responsibilities, call relationships, or validation commands change.

At completion, Reviewer checks whether the implementation followed the applicable rules and whether relevant documentation needs updating. Record documentation updates or the conclusion that none were required. Proposals to change explicit rules must be distinguished from factual documentation maintenance and follow the applicable approval policy.

Reuse unaffected knowledge. Do not rescan the entire repository after every task or treat an old initialization summary as a substitute for current source evidence.

---

# 17. Execution Runtime

Runtime architecture:

```text
Agent
 ↓
Tool Request
 ↓
Policy Engine
 ↓
Tool Runtime
 ↓
Sandbox
 ↓
Workspace
```

Never:

```text
LLM
 ↓
Direct OS access
```

---

# 18. Workspace Manager

Workspace must be a first-class abstraction.

Interface:

```python
class Workspace:
    path: Path
    branch: str | None

    async def prepare(...)
    async def snapshot(...)
    async def diff(...)
    async def reset(...)
    async def cleanup(...)
```

V1:

```text
local repository workspace
```

V2:

```text
Git worktree per task
```

Future:

```text
Container / Remote Sandbox
```

---

# 19. Git Worktree Strategy

Parallel execution should use isolated Git worktrees.

Example:

```text
repository
│
├── main
│
├── .agent/worktrees/task-001
├── .agent/worktrees/task-002
└── .agent/worktrees/task-003
```

Execution:

```text
Task
 ↓
Create Worktree
 ↓
Agent Coding
 ↓
Verify
 ↓
Generate Patch
 ↓
Merge
 ↓
Delete Worktree
```

Failure:

```text
discard worktree
```

---

# 20. Tool Runtime

Keep the number of tool categories small.

Recommended tools:

```text
filesystem
search
patch
shell
git
test
mcp
```

Avoid creating dozens of tiny tools.

Example interface:

```python
class Tool(Protocol):

    name: str

    async def execute(
        self,
        request: ToolRequest,
        context: ToolContext
    ) -> ToolResult:
        ...
```

---

# 21. Policy Engine

Every tool invocation must pass through Policy Engine.

Project metadata, plan versions, execution events, checkpoints, and verification evidence are controller-owned records. Agent tools may read the context they need but must not directly rewrite these records, including through shell commands. Initialization and user guidance updates go through the controller; runtime and verifier components write their respective records.

Example:

```text
Agent
 ↓
run("rm -rf ...")
 ↓
Policy Engine
 ↓
DENY
```

Policies:

```text
ALLOW
ASK
DENY
```

Permission dimensions:

```text
filesystem
shell
network
git
database
external directory
MCP
```

Example:

```yaml
shell:
  pytest*: allow
  npm test*: allow
  git status: allow
  git diff: allow
  git push*: deny
  rm -rf*: deny
```

---

# 22. Verification Engine

Verification is a separate subsystem.

Pipeline:

```text
Patch
 ↓
Build
 ↓
Static Analysis
 ↓
Lint
 ↓
Type Check
 ↓
Unit Tests
 ↓
Integration Tests
 ↓
Acceptance Checker
```

Not every repository will have every stage.

The verification plan should be dynamically generated from:

```text
repository capabilities
+
Task acceptance criteria
```

---

# 23. Acceptance Checker

Acceptance criteria are stronger than generic tests.

Example requirement: old refresh tokens cannot be reused.

Map `AcceptanceSpec.criteria[].required_check_ids` (Section 9.1) to
`Evidence.criterion_id` / `Evidence.check_id` (Section 24). For example,
`replay-rejected` maps to `refresh-tests`. Only actual, applicable evidence with
`status: passed` can satisfy that pair. Free-text summaries are not evidence records.

---

# 24. Evidence Ledger

Evidence should be stored as structured data.

Recommended model:

```python
class Evidence(BaseModel):
    id: str
    task_id: str

    plan_version: int
    context_revision: str
    workspace_revision: str
    criterion_id: str
    criterion: str
    check_id: str

    evidence_type: Literal["test", "build", "lint", "static_analysis", "review", "command", "diff"]

    command: tuple[str, ...] | None
    result: str

    status: Literal["passed", "failed", "skipped", "unavailable", "inconclusive"]
    artifacts: tuple[str, ...] = ()

    source: str
    timestamp: AwareDatetime
```

`criterion_id` identifies an acceptance criterion and `check_id` its required check
in the recorded task and plan version (Section 9.1). `source` identifies the runtime
request/result or review record that produced the evidence. IDs, result, source and
context/workspace revisions must be nonempty. `plan_version` is a strict positive
integer; `timestamp` must include a timezone. Provenance, outcome, type, command
(explicitly nullable), and time fields are required inputs with no generated defaults.
P01 validates structure only; it cannot prove that a supplied source actually ran.
The derived `passed` property means only `status == "passed"`, not freshness or gate
success. Missing evidence is absence of a record. Stale evidence retains its historical status.

Evidence must bind the result to the exact workspace snapshot, plan version, and project knowledge revision used for verification. A Git HEAD alone is insufficient when there are uncommitted changes: identify the relevant tracked and untracked inputs as part of the snapshot. Record command arguments, working directory, exit code, and relevant environment/tool versions in the referenced verification artifact; omit secrets.

Exclude execution logs and generated verification output from the source snapshot identity so recording an event does not invalidate its own evidence. Delivery must confirm that committed source content still matches the verified snapshot; a source change introduced during delivery requires revalidation.

The verification runtime writes the execution facts. LLM review is recorded as review evidence and must not be presented as a test execution result. Missing, skipped, unavailable, and inconclusive required checks do not count as passing.

Preserve historical evidence after changes. It remains evidence about the earlier snapshot, not automatic proof about the current result. V1 may conservatively rerun required checks for the final delivery snapshot instead of implementing impact-based evidence reuse. If relevant project rules or acceptance criteria change, reevaluate the verification plan as well.

The ledger must allow querying:

```text
Why is this task VERIFIED?
```

and return actual evidence.

---

# 25. Quality Gate

Quality Gate determines whether a task may transition to:

```text
VERIFIED
```

Pseudo logic:

```python
def evaluate(task, evidence, review):
    if acceptance_criteria_missing:
        return FAIL

    if required_evidence_missing_or_not_passed:
        return FAIL

    if required_evidence_not_valid_for_current_snapshot_and_plan:
        return FAIL

    if required_review_missing_or_blocking_issue:
        return FAIL

    return PASS
```

This logic must remain deterministic.

The final result must distinguish completed implementation from completed verification and identify any unverified requirements. A readable success summary cannot override a failed or incomplete gate.

For staged work, the requirement-level completion gate must cover all required scope and milestone outcomes, including scope not yet compiled into the active TaskGraph. A verified current batch is insufficient while required later work remains pending.

---

# 26. Failure Handling

Never allow infinite Agent retry loops.

Recommended policy:

```text
Attempt 1
  ↓ fail
Fix

Attempt 2
  ↓ fail
Diagnose

Attempt 3
  ↓ fail
REPLAN_REQUIRED
```

Configuration:

```yaml
max_implementation_attempts: 3
max_review_fix_attempts: 2
```

---

# 27. Replanning

Replanning occurs when:

- architecture assumption is wrong;
- required dependency is missing;
- task scope is too large;
- repeated verification failure;
- downstream dependency invalidated;
- repository changed unexpectedly.

New cross-module dependencies, broader scope, or unexpected verification difficulty must also trigger complexity reassessment. Do not wait for repeated failures when these findings already invalidate the active execution strategy. Reuse this replanning flow to refine the next batch of a staged task.

Flow:

```text
Task failed repeatedly
       ↓
Diagnosis
       ↓
Replan Request
       ↓
Planner
       ↓
New Task Graph Version
       ↓
Graph Validator
       ↓
Continue Execution
```

Task graph versions must be preserved.

Example:

```text
plan/v1
plan/v2
plan/v3
```

Each revision must record its trigger, a concise reason, changed tasks or criteria, applicable context revision, and the disposition of previous work and evidence. Retain executed task IDs in history even when the new graph replaces their tasks.

Replanning must not silently remove a failing acceptance criterion, relax an explicit project rule, or expand granted permissions. Apply existing authorization and approval rules to material scope changes, and record the plan version covered by approval. Purely operational adjustments within existing authorization need no new blanket approval gate.

Complexity changes must record the previous/current classification, evidence for the change, and its effects on pending scope, validation, and budget in the plan revision. Existing session time, usage, and retry limits carry across new tasks, milestones, and classifications; reassessment cannot reset consumed budget.

---

# 28. Risk-Adaptive Workflow

Not every task should use the full pipeline.

Define three execution modes.

Risk and complexity are separate inputs. Risk determines applicable permission, review, approval, and verification requirements. Complexity determines exploration depth, task decomposition, staging, and budget allocation. A Small task with high risk still receives the required high-risk safeguards; changing complexity cannot silently downgrade risk or bypass an applicable gate. See Section 29.

## FAST

Suitable for:

```text
documentation
typo
small isolated change
```

Workflow:

```text
Coder
 ↓
Verification
 ↓
Done
```

---

## STANDARD

Suitable for:

```text
normal bug fix
normal feature
small refactor
```

Workflow:

```text
Plan
 ↓
Coder
 ↓
Verification
 ↓
Review
```

---

## STRICT

Suitable for:

```text
authentication
database migration
public API change
security
refactor with significant compatibility or architectural impact
architecture change
```

Workflow:

```text
Explore
 ↓
Plan
 ↓
Human Approval
 ↓
Task Graph
 ↓
Implementation
 ↓
Full Verification
 ↓
Independent Review
 ↓
Integration Verification
 ↓
Human Approval
```

---

# 29. Risk and Complexity Assessment

## 29.1 Risk Profile

Risk factors:

```text
number of affected files
database changes
public API changes
authentication/security
dependency changes
repository size
cross-module changes
migration
historical failure
```

Example:

```python
class RiskProfile(BaseModel):
    level: RiskLevel

    security: RiskLevel | None = None
    database: RiskLevel | None = None
    public_api: RiskLevel | None = None
    architecture: RiskLevel | None = None
```

`RiskLevel` is `low`, `medium`, or `high` in every contract. The overall level is
required and must cover the highest explicitly assessed dimension. `None` means
unassessed/not supplied, not implicitly low risk. The overall level may be higher due
to other consequences. This resolves D02: Section 7 uses `public_api` (not `api`),
Section 9 uses RiskProfile, and booleans are not valid dimension levels. Assessors must
expose relevant unknowns; P01 validates structure, not automatic risk assessment.

Risk concerns the consequences of an incorrect change. File counts and repository size are supporting signals, not sufficient risk or complexity classifications on their own. For example, one changed authorization condition can have low implementation complexity and high risk.

## 29.2 Complexity Dimensions

Complexity concerns how difficult the requested work is to understand, organize, and validate. Assess it for the current requirement and task scope, not as a permanent label for the repository.

| Dimension | Questions to establish from exploration |
| --- | --- |
| Scope | Is the work confined to a function/module, or does it cross modules, repositories, or services? |
| Coupling | Which callers, public interfaces, data schemas, and shared state must change together? |
| Uncertainty | Are current behavior, dependencies, and the implementation approach understood? Which unanswered questions could change the plan? |
| Verification difficulty | Can existing checks establish acceptance? Are new behavior tests, integration environments, or manual judgments needed? |

Explorer supplies source references and observations; Planner produces a reasoned classification. Counts may support the judgment, but neither a large diff nor a model-generated numeric score proves that a task is complex. Unknown dependencies must remain visible instead of defaulting the task to Small.

## 29.3 V1 Levels and Execution Strategy

| Level | Typical characteristics | Planning behavior |
| --- | --- | --- |
| Small | Localized scope, understood behavior, straightforward validation | One independently verifiable business task can be sufficient; retain the applicable risk policy. |
| Medium | Several related changes with understood dependencies and validation | Compile a bounded TaskGraph with explicit acceptance for each business task. |
| Large | Cross-module restructuring, staged migration, significant coupling, or uncertainty requiring discovery | Establish milestones, resolve critical unknowns, and compile successive bounded batches with integration checkpoints. |

Use simple structured metadata in the versioned plan; no separate classifier Agent or scoring service is required in V1:

```yaml
assessment:
  complexity: large
  reasons:
    - OrderService and InventoryService share a boundary used by multiple callers
    - Callers must migrate in stages while preserving the existing public API
  unknowns:
    - Whether callers outside OrderService access inventory tables directly
  execution_strategy: staged
```

The allowed complexity values are `small`, `medium`, and `large`; execution strategies are `single_task`, `task_graph`, and `staged`. Risk remains recorded separately. Include source references with the assessment's supporting context and make materially different child-task assessments explicit when they affect execution.

Planner proposes the strategy and a bounded budget allocation within the session's limits; Workflow Engine enforces the recorded scope, limits, and applicable policy. For unresolved questions that can change correctness or scope, perform bounded exploration before enabling dependent implementation. A Large label does not itself authorize additional resources, network access, or broader changes.

## 29.4 Assessment Timing and Updates

1. After task-specific exploration, assess complexity before finalizing the plan.
2. During execution, Coder/Verifier/Reviewer report new dependencies, scope changes, or validation obstacles; Planner reassesses through Section 27.
3. Before starting a later milestone, refresh the relevant sources, refine the next batch, and reevaluate its assumptions and budget.
4. Record any changed classification and strategy with the plan version. Apply the existing approval policy only where the revised work changes the applicable authorization.

Task-local review and verification remain part of each business task. Milestone integration checks establish behavior across completed tasks; they do not replace task-local evidence. Preserve the requirement-wide acceptance conditions and pending milestones across batches. A successful batch cannot mark the whole requirement complete or silently abandon later work.

## 29.5 Large Refactors and Behavior Preservation

The architecture is intended to support staged refactors. V1 must validate this with a bounded, single-repository, multi-module refactor with regression tests before claiming support for large restructuring work. Multi-repository and distributed-service migrations remain outside the initial validation scope.

For refactoring, RequirementContract must state the behavior that must remain unchanged: relevant public API signatures/results, error behavior, data formats, side effects, and any explicitly required performance characteristics. Turn these invariants into stable acceptance criteria. File movement or extraction alone does not establish acceptance.

Example: extract inventory responsibilities from an order service while preserving its public behavior.

1. Inspect callers, shared state, interface boundaries, and applicable project rules; record unknowns.
2. Run existing checks on the starting snapshot and add necessary behavior characterization checks. Record pre-existing failures and unavailable checks explicitly; they do not become passes or blanket exemptions.
3. Define milestones for extracting an interface, migrating caller groups, and retiring the old path. Specify detailed tasks for the current batch and retain later work as pending scope in the plan.
4. Implement and verify each business task with its applicable review. Keep intermediate boundaries usable for subsequent tasks; use small compatibility steps when needed.
5. Run milestone and final integration checks against the preserved behavior and final snapshot. Review the diff and update affected module/call documentation.

Compare baseline and final outcomes against the original invariants. Preserve failure and recovery artifacts; when baseline gaps prevent establishing required behavior, resolve them or report the affected acceptance as incomplete. Do not declare the refactor complete solely because newly generated tests pass.

---

# 30. Session and Checkpoint

Long tasks must be resumable.

Session stores:

```text
RequirementContract
Project knowledge revisions used
Plan versions
Task Graph
Task states
Tool calls
Modified files
Verification results
Evidence
Review results
Token usage
Model usage
Workspace state
```

Example:

```text
.agent/
├── project.md
├── project.json
└── sessions/
    └── session-001/
        ├── requirement.json
        ├── context/          # Knowledge snapshots and referenced rule/source versions
        ├── plans/            # Immutable plan versions
        ├── graph.json
        ├── state.json
        ├── evidence.jsonl
        ├── events.jsonl      # Includes actual tool requests and results
        ├── artifacts/        # Diffs and sanitized verification output
        ├── result.md
        └── checkpoints/
```

The Workflow Engine owns the current graph and state. Versioned plans preserve intent; events preserve execution history; evidence preserves verification results. Do not maintain a second independently writable tool-call log containing the same facts.

Checkpoints must identify the plan version, context revision, workspace revision, and last incorporated event sequence. Write checkpoint/state snapshots atomically. Execution recording begins before real Agent tool use; Phase 12 adds reconciliation and resume behavior rather than introducing logging for the first time.

On resume, compare the checkpoint with recorded events and actual workspace state. A tool request with no recorded outcome is unresolved, not successful or definitely unexecuted. Inspect its side effects before retrying; if the outcome cannot be established, stop dependent execution with an actionable recovery report. Do not promise exactly-once shell execution.

Retain partial diffs, completed steps, and failure evidence after interruption or cancellation. Recovery must preserve user changes and must not reset the repository merely to match an old checkpoint. Relevant manual edits require reconciliation of the plan, context, and evidence before continuing.

---

# 31. Execution Trace and Observability

Every Agent run must generate a persistent execution trace in V1.

Trace structure:

```text
Session
 ├── Plan
 ├── Task
 │    ├── Agent Run
 │    │    ├── Model Call
 │    │    ├── Tool Call
 │    │    └── Tool Result
 │    ├── Verification
 │    └── Review
 │
 └── Integration
```

Track:

```text
tokens
latency
cost
tool calls
retry count
verification failures
context size
task duration
```

V1 may use a single append-only `events.jsonl` per session, written by the controller. No distributed tracing service or event-sourcing framework is required.

OpenTelemetry can be added later.

## 31.1 Event Contract and Writers

Every event must carry:

- event ID, monotonically increasing sequence within the session, and timestamp;
- session ID and task ID when applicable;
- event type and the active plan version when one exists;
- context/workspace revision when the event depends on that snapshot;
- structured event details, correlation IDs, and artifact references as needed.

V1 event types must cover session start/end/interruption, plan creation/revision, task state changes, tool requests/results, verification results, review results, and delivery results.

Workflow Engine records lifecycle and plan events. Tool Runtime records actual command arguments, working directory, permission decisions, start/end, exit code or error, and output references. Verification records criterion results and evidence IDs. Keep explanations short: record the reason for an action or plan change when useful, not private model reasoning.

Flush the request record before executing a side-effecting operation and record its actual outcome afterwards. If recording is unavailable, stop further side-effecting work. A crash between these records is handled as an unresolved operation under Section 30, never as an automatic pass. Handle an incomplete final JSONL record without discarding earlier valid history.

Use the runtime diff for actual file changes; do not treat the model's claimed file list as authoritative. Capture a task's before/after workspace references and retain a diff artifact. Sanitize recorded arguments and outputs, exclude credentials and raw environment dumps, and clearly indicate truncated output.

Example completed verification event (after its request event):

```json
{
  "event_id": "evt-018",
  "sequence": 18,
  "timestamp": "2026-09-15T09:00:00Z",
  "session_id": "session-001",
  "task_id": "auth-04",
  "type": "verification_completed",
  "plan_version": 2,
  "context_revision": "project-003",
  "workspace_revision": "snapshot-007",
  "details": {
    "request_id": "check-004",
    "criterion_id": "replay-rejected",
    "check_id": "refresh-tests",
    "command": ["pytest", "tests/auth/test_refresh.py::test_replay"],
    "cwd": ".",
    "exit_code": 0,
    "status": "passed",
    "evidence_id": "evidence-004",
    "artifact": "artifacts/check-004.txt"
  }
}
```

## 31.2 User-Facing Progress and Final Results

Default progress should show the active task/step, completed work, current check, and any blocker or required decision. Detailed events are available on demand through `agent history <session-id>`; users should not have to read raw logs to understand progress.

Every ended or interrupted session produces a result summary from recorded state and evidence containing:

- requested behavior and the active plan version, including material revisions;
- current complexity, execution strategy, and pending milestones for staged work;
- completed, incomplete, and blocked work;
- actual changed files and a diff reference;
- criterion-by-criterion outcomes, including skipped or unavailable checks;
- the verified workspace revision and delivery/commit result, if any;
- relevant project documentation updates or why none were required;
- remaining limitations, required user decisions, and how to resume or inspect artifacts;
- recorded elapsed time and usage/cost when available.

For an abrupt crash, generate the interrupted-session summary during recovery from durable records. If a command's outcome remains unknown, say so. Never equate `IMPLEMENTATION_FINISHED` with `VERIFIED`, and never report a commit as created based only on intent.

---

# 32. Model Provider Abstraction

Do not couple business logic to one LLM vendor.

Interface:

```python
class ModelProvider(Protocol):

    async def generate(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> ModelResponse:
        ...
```

Possible providers later:

```text
OpenAI
Anthropic
Gemini
Qwen
OpenAI-compatible
local model
```

V1 only needs one implementation.

---

# 33. Recommended Repository Structure

```text
coding-agent/
│
├── README.md
├── pyproject.toml
│
├── apps/
│   ├── cli/
│   └── server/
│
├── core/
│   ├── requirement/
│   │   ├── models.py
│   │   └── parser.py
│   │
│   ├── plan/
│   │   ├── models.py
│   │   ├── compiler.py
│   │   └── validator.py
│   │
│   ├── task/
│   │   ├── models.py
│   │   ├── graph.py
│   │   ├── scheduler.py
│   │   └── state_machine.py
│   │
│   ├── workflow/
│   │   ├── engine.py
│   │   ├── policy.py
│   │   └── gate.py
│   │
│   └── evidence/
│       ├── models.py
│       └── store.py
│
├── agents/
│   ├── base.py
│   ├── explorer.py
│   ├── planner.py
│   ├── coder.py
│   ├── debugger.py
│   └── reviewer.py
│
├── context/
│   ├── repository.py
│   ├── repo_map.py
│   ├── search.py
│   ├── git_context.py
│   └── builder.py
│
├── runtime/
│   ├── workspace.py
│   ├── worktree.py
│   ├── sandbox.py
│   └── process.py
│
├── tools/
│   ├── base.py
│   ├── filesystem.py
│   ├── search.py
│   ├── patch.py
│   ├── shell.py
│   ├── git.py
│   └── test.py
│
├── verification/
│   ├── engine.py
│   ├── build.py
│   ├── lint.py
│   ├── test.py
│   ├── acceptance.py
│   └── review.py
│
├── models/
│   ├── base.py
│   └── providers/
│
├── session/
│   ├── models.py
│   ├── store.py
│   └── checkpoint.py
│
├── telemetry/
│   ├── events.py
│   ├── logger.py
│   └── metrics.py
│
└── tests/
```

---

# 34. Recommended V1 Technology Stack

Unless repository constraints indicate otherwise:

```text
Python 3.12+
Pydantic v2
asyncio
Typer
Git CLI
subprocess
SQLite
pytest
ruff
mypy
```

Avoid heavy frameworks in V1.

Do not introduce:

```text
LangChain
LangGraph
Celery
Kafka
Kubernetes
Redis
vector database
```

unless required by an actual V1 use case.

The workflow engine should initially be implemented directly.

---

# 35. V1 Scope

V1 should test one hypothesis:

> A structured engineering runtime can execute coding tasks more reliably than a direct autonomous coding loop.

V1 includes project initialization and persistent execution records:

- `agent init` creates a sourced project guide and incorporates user development rules and module interaction constraints;
- task planning and execution reference a recorded project knowledge revision;
- versioned plans, actual execution events, and snapshot-bound evidence make progress and outcomes inspectable;
- changed interfaces and module relationships trigger relevant documentation maintenance.

Complexity assessment and bounded staged execution are V1 requirements. Validate them first with a single-repository refactor using known regression checks, as specified in Section 29.5; do not infer general large-task support from a successful small edit.

V1 workflow:

```text
Requirement
 ↓
Initialize or Reuse Project Knowledge
 ↓
Repository Explore
 ↓
Plan
 ↓
Task Graph
 ↓
Human Confirm
 ↓
Coder
 ↓
Git Diff
 ↓
Test
 ↓
Reviewer
 ↓
Fix
 ↓
Final Verification
 ↓
Commit
 ↓
Result Report with Plan / Diff / Evidence References
```

Initialization is reused across tasks, with relevant stale information refreshed. Execution recording applies to FAST, STANDARD, and STRICT modes even when their verification requirements differ. Known project guidance must be loaded in every mode; a small task does not require an exhaustive repository scan.

---

# 36. Explicit V1 Non-Goals

Do NOT implement in V1:

```text
Web UI
Multi-user support
Kubernetes runtime
Remote workers
Agent marketplace
Complex MCP ecosystem
Vector database
Complex RAG
Multi-agent debate
Agent teams
Automatic GitHub PR
Browser automation
Distributed scheduler
Dozens of model providers
```

Keep the project focused.

---

# 37. V1 Development Phases

## Phase 1 — Domain Foundation

Implement:

```text
RequirementContract
TaskSpec
TaskState
TaskGraph
Evidence
RiskProfile
```

Tests must cover:

```text
graph dependency validation
cycle detection
state transition validation
ready-task calculation
evidence outcome validation and required provenance fields
```

No LLM required yet. Evidence domain models must support the outcome and revision fields in Section 24. Project initialization orchestration is implemented in Phase 6, not in this phase.

---

## Phase 2 — Workflow Engine

Implement:

```text
TaskScheduler
StateMachine
WorkflowEngine
QualityGate
```

Build a fake Agent runner.

Example:

```text
FakeCoder
FakeVerifier
FakeReviewer
```

Use them to test the complete lifecycle.

Define lifecycle event emission and test plan/task event ordering with a fake event writer. QualityGate must reject missing, non-passing, or stale required evidence. Do not build a separate event-sourcing subsystem.

---

## Phase 3 — Tool Runtime

Implement:

```text
filesystem
search
patch
shell
git
```

Add:

```text
ToolRequest
ToolResult
PolicyEngine
```

Ensure commands can be allowed or denied.

Add durable JSONL event recording before any real coding loop is enabled. Record actual requests, permission decisions, correlated results/errors, and artifact references. Verify that recording failure blocks new mutations and that Agent tools cannot rewrite controller-owned session records.

---

## Phase 4 — Workspace

Implement:

```text
Workspace
Git status
Git diff
Snapshot
Reset
```

Then add:

```text
Git Worktree
```

---

## Phase 5 — Model Provider

Implement provider abstraction.

Add one real provider.

Require structured output for:

```text
RequirementContract
PlanDraft
ReviewResult
```

---

## Phase 6 — Explorer and Project Initialization

Implement repository exploration and `agent init` using Sections 16.1–16.4.

Output:

```text
RepoSummary
Project knowledge guide and revision metadata
```

Include:

```text
language
framework
important directories
test commands
lint commands
build commands
project instructions
module responsibilities and important call paths
user development rules and module interaction constraints
source references and unresolved questions
```

During task-specific exploration, also provide the scope, coupling, unknowns, and validation conditions needed for complexity assessment. Initialization's repository overview is input to this work, not a substitute for evaluating the actual task.

Support reusing existing documents, focused user clarification, repeated initialization, and `agent init --refresh`. Persist generated artifacts through the controller while keeping Explorer read-only.

Verify that refresh preserves user-authored rules, assumptions remain visibly unconfirmed, and relevant source changes are detected. A complete call graph, AST/LSP integration, and a documentation database are not required.

---

## Phase 7 — Planner

Implement:

```text
RequirementContract
+
RepoSummary
+
Relevant Project Knowledge
→
PlanDraft
→
TaskGraph
```

Validate every plan before execution.

Persist numbered plan versions with context/workspace references and stable criterion IDs. Tasks must reference applicable project rules. Render a readable plan summary and record plan revisions instead of overwriting earlier intent.

Add the complexity metadata from Section 29 to PlanDraft/plan serialization in this phase. Reuse Explorer and Planner; do not add an independent classifier Agent. For Large tasks, preserve a milestone roadmap and pending scope, compile the current batch, and prevent requirement completion while required milestones remain unresolved. Verify that low complexity cannot bypass a high-risk policy and that unknown future tasks are not schedulable.

---

## Phase 8 — Coder

Implement tool-calling coding loop.

Initial loop:

```text
TaskContext
 ↓
Model
 ↓
Tool
 ↓
Observation
 ↓
Model
 ↓
...
 ↓
ImplementationFinished
```

Limit maximum steps.

Example:

```yaml
max_agent_steps: 30
```

Associate execution with the active task and plan version. Build context from relevant project knowledge and current source. Record actual tool activity through the Phase 3 runtime; an Agent summary must not manufacture execution events. Return explicit replan requests when discovered work exceeds the approved contract.

---

## Phase 9 — Verification

Implement automatic detection and execution of:

```text
tests
lint
type check
build
```

Record every result as Evidence.

Bind results to criterion IDs, plan/context revisions, and the verified workspace snapshot. Distinguish failed, skipped, unavailable, and inconclusive results. Retain command/output artifacts and emit verification events. Verify that old results cannot satisfy a changed snapshot's completion gate without valid revalidation.

---

## Phase 10 — Reviewer

Implement independent Reviewer.

Reviewer receives:

```text
Requirement
Task
Diff
Evidence
Relevant Context
```

Reviewer must return structured output.

Include relevant project rules and documentation references. Review conformance, requirement coverage, and whether changed interfaces/module relationships require documentation updates. Persist review results with their plan and workspace references.

---

## Phase 11 — Fix Loop

Implement:

```text
Review issue
 ↓
Fix Agent
 ↓
Verification
 ↓
Review
```

Set maximum retries.

Record each repair attempt and its trigger. New code changes require fresh applicable verification evidence. Preserve earlier failures and plan versions; review fixes must not erase execution history or silently relax acceptance criteria.

Reassess complexity when actual dependencies, scope, or validation difficulty exceed the plan. Refine staged work through the same versioned replanning path; preserve behavior invariants and session-wide budget consumption across batches.

---

## Phase 12 — Checkpoint / Resume

Persist:

```text
graph
states
evidence
workspace
model runs
context revisions and plan versions
last incorporated event sequence
```

Reuse the event and evidence persistence already introduced in earlier phases. Implement reconciliation of interrupted operations and manual workspace changes as specified in Section 30, then generate result reports from recorded facts. Test interruption after a side effect but before its result is saved, preservation of user changes, and recovery of an incomplete final event record.

CLI:

```bash
agent run "implement xxx"

agent sessions

agent resume <session-id>
```

Handle user interruption by stopping new scheduling and recording the known task/process state. Keep partial work and expose history, evidence, and recovery guidance.

---

# 38. Suggested CLI

Initialize a project and incorporate development rules:

```bash
agent init
```

Refresh affected project knowledge while preserving user-authored rules:

```bash
agent init --refresh
```

Existing project instructions are reused. On the first plan/run without a baseline, invoke this same initialization flow; unresolved questions only block work that depends on their answers.

Initial CLI:

```bash
agent run "Add refresh token rotation"
```

Planning only:

```bash
agent plan "Add refresh token rotation"
```

Execute approved plan:

```bash
agent run --plan .agent/plan.json
```

When importing a saved plan, validate its structure, applicable authorization, and recorded source/context revisions against the current project before execution. Preserve the imported plan in the session's version history.

Resume:

```bash
agent resume <session-id>
```

Inspect:

```bash
agent status
```

Show graph:

```bash
agent graph
```

Show evidence:

```bash
agent evidence
```

Show diff:

```bash
agent diff
```

Inspect the execution history and recorded plan changes:

```bash
agent history <session-id>
```

Show the final or interrupted-session result report:

```bash
agent result <session-id>
```

`agent status` presents current progress and blockers; `agent history` exposes actual events; `agent evidence` explains criterion outcomes and their code revisions. Read-only inspection commands do not trigger initialization or new Agent work. Resume reconciles recorded context with the current workspace before starting new work.

---

# 39. Human Approval Gates

Human approval should exist around dangerous transitions.

Examples:

```text
Plan accepted
Database migration
Dependency changes
Large refactor
External network access
git push
```

V1 minimum:

```text
Requirement
 ↓
Plan
 ↓
[Human Confirm]
 ↓
Execution
```

and:

```text
Verified Diff
 ↓
[Human Confirm]
 ↓
Commit
```

---

# 40. Difference From Existing Open-Source Coding Agents

## 40.1 Comparison Scope and Evidence

Official repository/documentation review date: **2026-09-15**. This comparison records documented capabilities, not results from running the same tasks across products. P01 domain foundation is implemented; the end-to-end runtime, reliability, and performance advantages remain unproven.

Initialization, project rules, planning, task dependencies, subagents, worktrees, sandboxing, execution logs, automated tests, and resumable workflows must not be advertised as unique features. A deterministic outer workflow is an architectural choice, not sufficient evidence of differentiation by itself.

Absence of a behavior from the documentation reviewed here does not establish that another project lacks it. Before publishing benchmark claims, record exact repositories, versions/commits, configuration, and observed behavior. Refresh this comparison when the cited capabilities change.

## 40.2 Existing Capabilities and Relevant Comparisons

| Project | Capabilities documented by the project | Implication for this runtime |
| --- | --- | --- |
| OpenCode | `/init` inspects important files, asks targeted questions where needed, and creates or updates `AGENTS.md` with architecture, conventions, and validation guidance. [Official rules documentation](https://opencode.ai/docs/rules/) | Project initialization and reusable development guidance already have substantial overlap. Compare rule provenance, freshness, and consistent application to tasks. |
| Cline | Plan/Act modes, controller-managed persistent task state, and Git-based checkpoints. [Official architecture overview](https://github.com/cline/cline/blob/main/.clinerules/cline-overview.md) | Planning, persisted state, and rollback alone do not distinguish the project. Compare completion semantics and recovery under changed workspace state. |
| Aider | Automatic linting/testing after edits and attempts to repair reported failures. [Official lint/test documentation](https://aider.chat/docs/usage/lint-test.html) | Running checks after coding is an existing capability. Compare criterion coverage and whether evidence remains valid for the final delivered snapshot. |
| OpenHands | SDK workspace abstractions and immutable, serializable execution events; the automation service also provides scheduling, dispatch, and run history. [SDK architecture](https://docs.openhands.dev/sdk/arch/sdk), [Automation repository](https://github.com/OpenHands/automation) | Treat OpenHands as more than a sandbox backend. Events, runtime separation, and orchestration already overlap with this design. |
| GitHub Spec Kit | Specification-driven development plus workflows combining commands, shell steps, human gates, conditions, loops, fan-out/fan-in, persisted state/logs, and resume. [Project documentation](https://github.github.com/spec-kit/), [Workflow reference](https://github.github.com/spec-kit/reference/workflows.html) | A close workflow comparison. Evaluate evidence validity and completion guarantees instead of claiming that executable outer workflows are new. |
| GSD (Get Shit Done) | Structured plans, dependency-based execution waves, requirement coverage gates, requirement-to-test mapping, post-execution verification, and separation of sourced claims from assumptions. [Official feature documentation](https://github.com/gsd-build/get-shit-done/blob/main/docs/FEATURES.md) | A close end-to-end comparison. Evaluate the enforcement, provenance, and failure behavior of both systems under equivalent tasks and settings. |
| mini-SWE-agent | A deliberately small agent loop using shell actions and linear interaction history. [Official project documentation](https://github.com/SWE-agent/mini-swe-agent/blob/main/docs/index.md) | A useful minimal baseline for measuring whether additional runtime mechanisms justify their cost and complexity. |

## 40.3 Differentiation Hypothesis

The product hypothesis is that a consistent combination of project-rule references, controlled completion transitions, version-bound evidence, and side-effect-aware recovery reduces incorrect completion claims and the user's effort to verify delivery.

Spec Kit and GSD should be priority workflow baselines because the documented overlap is substantial. Interactive coding agents and a minimal loop remain useful baselines for usability, cost, and incremental value. These projects operate at different layers; comparisons must describe the configured end-to-end system rather than assume every repository is a standalone coding agent.

Existing agents or SDKs may become execution backends later if they can satisfy the runtime's recording and permission contracts. Such integration is an optional extension, not a new V1 implementation requirement.

---

# 41. Core Differentiators

The following are behavior commitments to implement and measure. They define the intended focus of the product; they are not claims that every other system lacks these mechanisms.

## 41.1 Runtime-Enforced Completion

Only Workflow Engine may transition task state. Coder returns implementation results; QualityGate evaluates required evidence and review under the recorded policy. Missing, failed, unavailable, or stale required evidence prevents `VERIFIED`.

Acceptance criterion IDs, required checks, and plan revisions remain explicit. Replanning must preserve history and must not silently weaken the requirement or expand authorization. See Sections 8, 24, 25, and 27.

**User outcome:** implementation progress, verification status, and delivery status can be inspected separately.

## 41.2 Evidence Bound to the Delivered Version

The runtime must preserve the relationship:

```text
Requirement / Acceptance Criterion
        ↓
Plan Version + Project Knowledge Revision
        ↓
Code Snapshot
        ↓
Actual Check / Review Result
        ↓
Delivery Decision
```

A check that passed on snapshot A remains historical evidence after the source changes to snapshot B. Final delivery requires evidence valid for the delivered snapshot and applicable plan; old logs cannot automatically satisfy that gate. See Section 24.

**User outcome:** the user can determine whether the reported checks cover the code they actually receive.

## 41.3 Project Rules with Sources and Revision History

Initialization distinguishes observed code facts, explicit user/project rules, and unconfirmed assumptions. Planning, implementation, and review reference the applicable rule sources and knowledge revision. Relevant source changes trigger reconciliation and documentation maintenance. See Section 16.

For example, existing direct database access by OrderService can be recorded as legacy behavior while an explicit rule requires new code to use InventoryService. The task must carry the rule, and review must assess whether the change follows it. Semantic adherence may require review; the presence of a rule reference alone does not prove compliance.

**User outcome:** project guidance is inspectable, corrections persist, and outdated observations do not silently become development rules.

## 41.4 Recovery Based on Actual Execution State

Persisted plans, events, workspace references, and evidence support reconciliation after interruption. When a tool request has no recorded result, inspect its effects before retrying or declaring success. Preserve partial work and user edits. See Sections 30 and 31.

For example, if a Git commit was created before the process crashed, recovery must inspect the repository and identify the outcome before attempting delivery again. Any unresolved result remains explicit and blocks dependent execution where required.

**User outcome:** an interrupted task can be inspected and safely continued or handed back with its known results and remaining uncertainty.

---

# 42. Evaluation

The project must include evaluation early.

Do not judge quality only by demos.

## 42.1 Baselines and Experimental Controls

Compare the runtime with:

- a direct coding agent or minimal loop;
- a configured Spec Kit workflow;
- a configured GSD workflow.

Record the underlying coding agent for workflow-based baselines. Use the same model/version where supported, repository starting snapshot, user requirement and rules, validation environment, and comparable permissions and budget limits. Report unavoidable differences in model access or tool capabilities rather than treating the systems as identical.

Pin project versions/commits, prompts or instruction artifacts, verification flags, retry policies, approval modes, and cost assumptions. Repeat model-driven tasks and report sample sizes and variation. Measure initialization separately from subsequent tasks, and record user clarification/review time in addition to Agent execution time.

Determine acceptance using predefined independent tests and/or human assessment against the original requirement. Generated tests, the Agent's final summary, and its own `VERIFIED` state must not be the sole evaluator. Give each system a fair completion interpretation, including explicit success claims by baselines that do not use the `VERIFIED` label.

Use small ablation runs to identify the contribution of project initialization, review, task orchestration, and evidence gating. Keep the remaining settings comparable; a higher overall success rate with more model calls does not by itself attribute the improvement to the Task Graph.

## 42.2 Behavioral Acceptance Scenarios

These scenarios specify this runtime's required behavior. They do not presume that a comparison project will fail them.

| Scenario | Controlled trigger | Required behavior |
| --- | --- | --- |
| Missing verification | A required test is skipped, collects no applicable cases, or cannot run because its environment is unavailable. | Report the actual outcome and incomplete verification; do not mark the task VERIFIED. |
| Stale evidence | Checks pass on snapshot A, then source code changes before delivery. | Retain A's historical evidence, identify the changed snapshot, and require applicable revalidation before delivery. |
| Replanning under failure | A failing task is split or replaced by a revised plan. | Preserve the original requirement, criterion history, applicable authorization, and prior events; expose material scope changes. |
| Crash after a side effect | A commit is created but the process stops before its result event is saved. | Inspect actual repository state before retrying; preserve user edits and report any outcome that remains unresolved. |
| Project-rule conflict or staleness | A user rule conflicts with a legacy code pattern, or a referenced source changes after initialization. | Retain the explicit rule, identify the conflict/stale observation, and reconcile relevant context and plan before dependent work continues. |
| Low complexity with high risk | A localized change affects an authorization or compatibility rule. | Keep the applicable risk policy and required checks; a Small assessment cannot bypass them. |
| Complexity escalation | A seemingly local task reveals shared state or additional dependent modules. | Reassess and version the plan before dependent work; preserve authorization and consumed budget. |
| Staged refactor | A multi-module refactor has behavior invariants and later milestones beyond the active batch. | Establish baseline evidence, validate each batch and final integration, and prevent whole-requirement completion while required scope remains pending. |

Record actual behavior for each configured baseline under the same trigger, including successful handling, failures, unsupported scenarios, and any human recovery effort.

## 42.3 Metrics and Reporting

Metrics:

```text
task success rate
test pass rate
requirement coverage
regression rate
retry count
review issues
token usage
cost
latency
tool calls
human intervention count
incorrectly VERIFIED outcomes under independent acceptance checks
project-rule violations and stale-documentation incidents
initialization time and user clarification effort
complexity reassessments and outcomes by initial/final complexity and risk
```

Evaluate initialization overhead separately from subsequent task execution. Include cases where user rules override legacy code patterns, documentation becomes stale, verification is unavailable, or execution is interrupted. Verify both task outcomes and whether recorded plans/events/evidence accurately explain them.

Define the incorrect-completion rate as the number of runs reported complete that fail independent acceptance, divided by the number of runs reported complete. If no runs are reported complete, report this metric as N/A. Also report accepted-task rate over all attempted tasks so a system cannot appear reliable merely by refusing to complete work.

Report template; **no comparative results have been measured yet**. A dash means unmeasured, never zero.

| Metric | Direct agent | Spec Kit workflow | GSD workflow | This runtime |
| --- | --- | --- | --- | --- |
| Independently accepted tasks / all attempted tasks | — | — | — | — |
| Incorrectly completed / reported-complete runs | — | — | — | — |
| Regressions and project-rule violations | — | — | — | — |
| User review and recovery time | — | — | — | — |
| Initialization and task execution time, separately | — | — | — | — |
| Tokens and cost per accepted task | — | — | — | — |
| Interrupted-run recovery outcomes | — | — | — | — |

Summarize user benefit as fewer incorrect completion claims and less effort to validate or recover a delivery, alongside cost and latency. Architecture diagrams, additional modules, or richer logs alone do not establish those benefits.

---

# 43. Regression Dataset

Every successfully completed task should optionally be converted into a regression case.

Also retain failed, interrupted, and incorrectly reported-complete cases from evaluation. A dataset containing only successful runs cannot expose the failure behavior central to this product's differentiation.

Example:

```text
Task
+
Requirement
+
Repository commit
+
Expected tests
+
Expected behavior
```

Store under:

```text
evals/
```

Future system changes must be tested against historical cases.

---

# 44. Important Implementation Constraints for Codex

Codex MUST follow these rules.

### Architecture

Do not collapse modules into one giant Agent class.

Keep separation between:

```text
core
agent
runtime
tool
verification
context
session
```

---

### State

Never use conversation history as persistent workflow state.

Use structured state objects.

---

### Structured Output

Planner and Reviewer outputs must use schemas.

Avoid parsing free-form Markdown where possible.

---

### Project Knowledge

Reuse initialization output and existing project documentation when building task contexts. Preserve the distinction between code facts, explicit rules, and assumptions. Record the knowledge revision used by each plan and refresh relevant stale sources. Generated guidance must never silently replace explicit user rules.

---

### Execution Records

Persist plans before task execution. Runtime components record actual actions and verification outcomes; Agents may provide concise explanations but cannot author authoritative success records. Preserve earlier plans, failed attempts, and evidence after replanning. Produce an inspectable result report for success, failure, cancellation, and recovered interruption.

---

### Tool Calls

All shell and file mutation must go through Tool Runtime.

Never allow arbitrary direct OS access from Agent business logic.

---

### Task State

Only Workflow Engine may change Task state.

Agents return results.

Agents do not mutate workflow state.

---

### Verification

Coder cannot mark its own task successful.

Completion belongs to Verification + Quality Gate.

---

### Testing

Every domain module must have unit tests.

Critical components requiring tests before integration:

```text
TaskGraph
StateMachine
PlanValidator
Scheduler
PolicyEngine
QualityGate
EvidenceStore
```

---

### Dependencies

Prefer Python standard library and lightweight dependencies.

Do not introduce large orchestration frameworks unless justified.

---

### Scope

Do not implement future features while building V1.

If a future requirement appears, design an interface but do not implement the whole subsystem.

---

# 45. Recommended Codex Working Method

Codex should NOT attempt to implement the entire project in one pass.

For each development stage:

```text
1. Read this specification
2. Inspect current repository
3. Propose implementation plan
4. Implement one phase
5. Add tests
6. Run tests
7. Run lint/type checks
8. Review git diff
9. Commit logical checkpoint
10. Continue next phase
```

Do not combine unrelated phases in one commit.

---

# 46. First Codex Task

Start with Phase 1 only.

Prompt:

```text
Read CODING_AGENT_DEVELOPMENT_SPEC.md.

Implement only Phase 1: Domain Foundation.

Create the core domain models for:

- RequirementContract
- RiskProfile
- TaskSpec
- TaskState
- TaskGraph
- Evidence
- AcceptanceSpec
- ScopePolicy
- PermissionPolicy

Requirements:

1. Use Python 3.12+.
2. Use Pydantic v2 for structured domain models where appropriate.
3. Keep domain logic independent from LLM providers.
4. Do not implement agents yet.
5. Do not implement CLI yet.
6. Do not implement workflow execution yet.
7. Implement dependency graph validation.
8. Detect dependency cycles.
9. Implement ready-task calculation.
10. Implement explicit state transition validation.
11. Add comprehensive pytest tests.
12. Add ruff configuration.
13. Add mypy configuration.
14. Run all tests and static checks.
15. Review the final git diff and summarize architectural decisions.
16. Include Evidence outcome states and plan/context/workspace provenance from Section 24.

Do not proceed to Phase 2 until Phase 1 is clean and all tests pass.
```

---

# 47. Definition of Done for Phase 1

Phase 1 is complete only when:

```text
RequirementContract implemented
TaskSpec implemented
TaskGraph implemented
TaskState implemented
Evidence implemented

Graph validation works
Cycle detection works
Ready-task calculation works
State validation works
Evidence outcomes and required provenance validate

pytest passes
ruff passes
mypy passes
```

No LLM code should exist yet.

---

# 48. V1 Definition of Done

The complete V1 is successful when the following scenario works end-to-end:

```text
User enters requirement
        ↓
Initializes or reuses sourced project knowledge and user rules
        ↓
System explores repository
        ↓
Generates structured requirement
        ↓
Generates Task Graph
        ↓
Persists plan version with project knowledge references
        ↓
User approves
        ↓
Coder modifies repository
        ↓
Tests execute
        ↓
Evidence recorded
        ↓
Reviewer reviews diff
        ↓
Issues are fixed
        ↓
Final verification passes
        ↓
Quality Gate passes
        ↓
Git commit created
        ↓
Result report links plan, actual changes, and verification evidence
```

Execution events must be recorded throughout this scenario. In addition to the successful path, V1 acceptance must demonstrate:

- initialization identifies important modules, interaction boundaries, validation commands, sources, and open questions;
- user development rules are retained and referenced by planning, implementation, and review;
- repeated initialization/refresh preserves user additions and detects relevant stale source material;
- material plan changes have preserved versions and concise reasons;
- tool execution, verification, and commit claims agree with actual runtime records;
- every required criterion has a recorded outcome bound to the delivered code snapshot;
- missing, unavailable, or stale required evidence prevents a VERIFIED result;
- documentation affected by the change is updated or explicitly identified as outstanding;
- failure and interruption retain partial work and generate actionable reports;
- resume reconciles ambiguous operations and user workspace edits before continuing;
- complexity is recorded separately from risk and is updated when new findings change the plan;
- a bounded multi-module refactor preserves specified behavior, retains pending scope between batches, and completes final integration validation.

V1 behavioral acceptance includes the scenarios in Section 42.2. Passing these checks establishes the runtime's own behavior; publish a measured comparison under Section 42.3 before claiming an advantage over another project.

The system must be able to answer:

```text
Why does the system believe this task is complete?
```

with structured evidence.

Example:

```text
Task auth-04 VERIFIED

Requirement:
Refresh token replay must be rejected.

Evidence:
✓ test_refresh_success PASSED
✓ test_token_replay PASSED
✓ auth regression suite: 121 PASSED
✓ ruff PASSED
✓ reviewer: no blocking issue

Modified:
src/auth/service.py
src/auth/repository.py
tests/auth/test_refresh.py
```

That capability is the core value of the project.

---

# 49. Long-Term Architecture Direction

After V1 is stable, future development can gradually add:

```text
parallel Task execution
Git Worktree isolation
Docker sandbox
remote runtime
LSP
AST / Symbol Graph
impact analysis
MCP
ACP
multi-model routing
automatic PR
CI integration
GitHub Issues
Jira
distributed workers
OpenTelemetry
historical task retrieval
automatic regression generation
```

These must remain extensions around the core:

```text
Requirement
    ↓
Executable Plan
    ↓
Task Graph
    ↓
Controlled Execution
    ↓
Verification
    ↓
Evidence
    ↓
Quality Gate
```

Do not allow future features to replace this core architecture.

---

# 50. Final Architectural Rule

Whenever an architectural decision is unclear, use this rule:

> **Prefer deterministic software engineering mechanisms for things that can be verified deterministically. Use LLM reasoning only for problems that actually require reasoning.**

Examples:

```text
Dependency ordering      → deterministic
Task state transition    → deterministic
Test result              → deterministic
Permissions              → deterministic
Quality gate             → deterministic

Requirement understanding → LLM
Repository exploration    → LLM + deterministic search
Planning                  → LLM
Coding                    → LLM
Debugging                 → LLM
Semantic review           → LLM
```

The resulting architecture should therefore look like:

```text
                     Verified Coding Runtime

                          Requirement
                              │
                              ▼
                     Requirement Contract
                              │
                              ▼
                        Plan Compiler
                              │
                              ▼
                          Task DAG
                              │
                       Workflow Engine
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
          Agent             Agent             Agent
            │                 │                 │
            └─────────────────┼─────────────────┘
                              ▼
                         Verification
                              │
                              ▼
                           Evidence
                              │
                              ▼
                         Quality Gate
                              │
                              ▼
                           Delivery
```

The project is not trying to make an LLM more autonomous.

The project is trying to make **AI software engineering more reliable**.
