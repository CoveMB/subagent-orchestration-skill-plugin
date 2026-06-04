---
name: subagent-orchestrator
description: Optional execution-shape helper for deciding whether a Codex task should use single-thread execution, a sequential plan, or bounded parallel subagents. Use only for explicit subagent/orchestration requests or clearly complex work after existing orchestration, routing, bootstrap, skill-selection, or agent-management frameworks have priority. Do not use for tiny edits, simple Q&A, one obvious single-file fixes, strictly sequential tasks, or child subagent tasks.
---

# Subagent Orchestrator

Use this skill only when the prompt explicitly requests subagents/orchestration or when clearly complex work may benefit from parallel delegation. It can be invoked directly or reached through a quiet UserPromptSubmit hint.

This is not a global bootstrap skill and not a replacement for any other process-skill, bootstrap, routing, skill-selection, or agent-management flow. Existing frameworks take priority. Use this skill only as a complement or fallback.

The goal is not to spawn agents by default. The goal is to choose the smallest execution shape that is likely to improve correctness, evidence quality, speed, or context hygiene.

## Host project boundary

- This skill is an execution-shape helper only.
- It may organize work, separate investigation tracks, reduce context noise, and improve review coverage.
- It must not decide research truth, source validity, citation validity, manuscript truth, test sufficiency, safety approval, or vendor trust.
- It must not override repository AGENTS.md, source-of-truth rules, or other host project instructions.
- When host repository rules are stricter than plugin guidance, host repository rules win.
- If a host project requires evidence, citations, tests, approvals, locators, or audit notes, subagent output is not a substitute for those requirements.
- Subagent output is a work product, not evidence by itself.

Subagents may:

- inspect,
- compare,
- summarize,
- challenge,
- identify gaps,
- propose next actions,
- propose tests,
- propose audit notes,
- report uncertainty.

Subagents may not:

- create unsupported facts,
- invent sources,
- invent citekeys,
- invent page numbers,
- invent quotations,
- invent studies,
- invent bibliographic metadata,
- treat their own output as evidence,
- bypass required checks,
- silently resolve conflicts,
- authorize edits forbidden by the host project,
- weaken uncertainty or evidence status.

## Authorization and boundaries

When `parallel-subagents` is selected, do not ask a separate question solely for bounded read-only delegation. Still stop or ask when repository rules, user instructions, safety policy, privacy/context-sharing, vendor/tool policy, approval rules, cost/budget limits, destructive actions, external side effects, workspace-write scope, or unclear boundaries require it.

Clear boundaries are required first: role, mode, scope, expected output, and no recursive fan-out. Ask the user only when boundaries cannot be defined, the user opted out, or the action itself needs approval such as destructive or externally visible work.

## Quiet first decision

Decide internally whether the task fits exactly one of:

1. `single-thread`
   - The task is small, direct, low-uncertainty, or one-file/one-command obvious.
   - Parallelism would add more overhead than value.

2. `sequential-plan`
   - The task has multiple steps, but the steps depend on each other.
   - Make a concise plan and proceed linearly.
   - Do not spawn subagents yet.

3. `parallel-subagents`
   - The task can be decomposed into at least two bounded, independent exploration, reproduction, test review, documentation verification, architecture mapping, implementation alternative, or risk review tracks.
   - Each track has a clear expected output and can make useful progress without waiting on the others.
   - Subagents are likely to reduce context pollution, improve evidence, or save wall-clock time.

Complexity alone is not enough. Select `parallel-subagents` only when real parallelizable tracks exist; otherwise use `sequential-plan` or `single-thread`.

## Subagent Prompt Compiler

Use this compiler when the hook result is `use-subagent-orchestrator`, when the local check promotes a task to `parallel-subagents`, or when the user explicitly requests bounded orchestration and no higher-priority rule blocks it. When `parallel-subagents` is selected, compile the user task into the smallest useful read-only agent set before spawning. Do not merely say that subagents would be useful: produce concrete bounded prompts and call `spawn_agent` or the available subagent-spawning tool. The compiler does not override the quiet first decision.

### Scope primer before spawning

A minimal local read-only pass is allowed before spawning. Use it only to identify the active repository rules, current branch/status when relevant, obvious files or docs to inspect, existing agent templates, and the smallest safe scope for each agent. Do not use the primer as a broad investigation substitute. If the primer shows only one useful track, a strict sequence, unclear boundaries, an opt-out, child-agent recursion, unsafe context sharing, or write conflicts, choose `sequential-plan` or `single-thread`.

### Compilation order

1. Extract task type: review/audit, debugging/root-cause, refactor/migration, docs/API/version-dependent task or version verification, comparison/options or comparison/design, or implementation after investigation.
2. Extract scope: files explicitly named by the user, changed files from diff/status if available, subsystems named by the user, repository/branch/diff boundaries, unknown scope if no reliable boundary is known, non-goals, host rules, privacy/tool limits, and explicit read/write boundaries.
3. Identify independent tracks: require at least two independent tracks that can return useful evidence without waiting on each other, such as code-path mapping, risk/security/correctness review, test discovery/verification, reproduction/log collection, docs/API/version verification, or design alternatives.
4. Select the smallest read-only roster: choose the smallest useful read-only roster, start with two agents when enough, and add a third only when it covers a distinct risk or evidence gap. Do not include `so_implementer` before synthesis.
5. Emit bounded prompts: one self-contained prompt per agent with context, scope, non-goals, task, constraints, structured expected output, confidence, and evidence versus inference requirements.
6. Spawn read-only agents first: call `spawn_agent` or the available subagent-spawning tool after the compact proof passes.
7. Wait and synthesize: wait for every spawned agent that affects the next decision, then merge facts, conflicts, uncertainty, risks, file targets, and tests.
8. Decide whether workspace-write is justified: use a workspace-write agent only after synthesis, with exact write scope and verification needs.

### Minimal initial rosters

Use the smallest roster that covers independent tracks.

- Review/audit: `so_mapper` for changed files, call paths, and boundaries; `so_reviewer` for correctness, security, and regression risks; `so_tester` for missing tests and verification commands. Add `so_docs_researcher` only when external API or version behavior matters.
- Debugging/root-cause: `so_mapper` for execution path and likely failure region; `so_tester` for reproduction commands and targeted tests; `so_reviewer` only when fix risk, security, or hidden coupling is likely. Use `so_reproducer` only after mapper/tester scope narrowing.
- Refactor/migration: `so_mapper` for affected APIs, call sites, and dependencies; `so_designer` for phased options, reversibility, and migration risk; `so_reviewer` for compatibility and hidden coupling; `so_tester` for regression strategy.
- Docs/API/version-dependent task: `so_docs_researcher` for authoritative behavior and version constraints; `so_mapper` for where documented behavior affects code; add `so_tester` or `so_reviewer` depending on whether verification or risk is central.
- Comparison/options: `so_designer` for options and tradeoffs; `so_reviewer` for risks and failure modes; add `so_mapper` only when repository structure is unclear.
- Implementation after investigation: do not start with `so_implementer` unless the user explicitly requested edits and the write scope is clear. Prefer read-only mapping, review, and test planning first. Use one `so_implementer` only after synthesis, with exact workspace-write scope.

### Required compact proof

Before spawning, emit a compact why-parallel proof:

```text
Why parallel:
- independent track 1: <track and expected output>
- independent track 2: <track and expected output>
Blockers checked: opt-out, child-agent recursion, strict sequence, write conflict, dirty repo/isolation, external side effects, privacy/tool limits.
Initial roster: <agent names and read-only/workspace-write modes>
```

The proof must show at least two independent tracks, blockers checked, and the initial roster. If any part is weak, do not spawn.

## Spawn subagents when at least two are true

- Multi-file, multi-module, or multi-service change.
- Unknown code path or unfamiliar repository.
- Failure reproduction is separable from source inspection.
- Test discovery and result interpretation can happen independently.
- Documentation/API/version behavior may affect correctness.
- Security, correctness, performance, and implementation concerns can be reviewed separately.
- The task is likely to produce noisy logs or broad search output.
- There are multiple plausible approaches worth comparing.
- The user asks for review, audit, migration, refactor, architecture, performance, debugging, or root-cause analysis.

## Avoid subagents when any are true

- The user asks a simple direct question.
- The edit is tiny and obvious.
- The task needs one linear chain of work.
- Agents would compete to mutate the same files.
- The user asked for a quick answer.
- The repo state is dirty and isolation is unclear.
- You cannot define bounded jobs with clear outputs.

## User-facing output

Do not ask the user whether orchestration is preferable. Decide internally.

Do not print a standard orchestration banner for simple/default work. For complex work, mention orchestration only when it materially changes the approach. If a user-facing note is useful, keep it brief:

```text
Orchestration: single-thread | sequential-plan | parallel-subagents
Reason: <1-3 sentences>
Plan: <short plan>
```

If using subagents, include:

```text
Why parallel:
- independent track 1: <mapping/reproduction/testing/docs/etc. and expected output>
- independent track 2: <review/testing/design/etc. and expected output>
Blockers checked: opt-out, child-agent recursion, strict sequence, write conflict, dirty repo/isolation, external side effects, privacy/tool limits.
Subagents:
- name: <agent name>
  role: <bounded role>
  mode: read-only | workspace-write
  task: <specific bounded task>
  expected output: <evidence format>
Initial roster: <agent names and read-only/workspace-write modes>
```

Keep this proof to one or two lines plus the compact blocker checklist. It is a pre-spawn check, not a long planning ritual; if the proof fails, choose `sequential-plan` or `single-thread`.

Then spawn the agents, wait for all results, and synthesize before acting.

Actual spawning is part of the contract. When the execution shape is `parallel-subagents`, call `spawn_agent` or the available subagent-spawning tool in the same turn after giving the compact proof and defining bounded roles. Do not stop at a plan, recommendation, or statement that subagents would be useful. If no subagent-spawning tool is available, the proof fails, or a higher-priority instruction blocks spawning, state that blocker and proceed with the closest sequential fallback.

When the available subagent-spawning tool does not expose a dedicated `agent_type` parameter, begin the spawned task prompt with `agent_type: <agent-name>` so the role remains auditable in live traces. Use the exact names below, such as `so_mapper`, `so_tester`, and `so_reviewer`. When using a custom `agent_type`, keep `fork_context` unset and include the required context in the spawned task prompt instead.

## Execution Runbook

Use this order when the decision is `parallel-subagents`:

1. State the orchestration decision, reason, compact why-parallel proof, blocker checklist, and bounded subagents.
2. Call `spawn_agent` or the available subagent-spawning tool for the smallest useful set of read-only agents first.
3. Give each agent one self-contained task, explicit mode, expected output, and no permission to fan out.
4. Keep implementation agents for later unless the user already requested code changes and write scopes are disjoint.
5. Continue local work only on non-overlapping context while agents run.
6. Wait for all agents that affect the next decision.
7. Synthesize agreed facts, conflicts, files, risks, and tests before any edits.
8. Ask before code changes only if implementation was not clearly requested, boundaries are unclear, or the action is destructive or externally visible.

### Spawn Template

For read-only tasks, the default constraint remains: do not edit files; do not spawn more agents; report uncertainty.

```text
Spawn <agent-name> prompt:
agent_type: <agent-name>
mode: read-only | workspace-write
context: <relevant facts, host rules, current branch/status if relevant, and why this track matters>
scope: <files, subsystem, or question>
non-goals: <what not to inspect, decide, or change>
task: <specific bounded task>
constraints: no edits unless workspace-write scope is explicit; do not spawn more agents; no recursive fan-out; report uncertainty
structured expected output:
- facts with file paths, symbols, commands, or source locations
- evidence versus inference
- risks, tests, and recommended next action
- confidence: high | medium | low, with the reason
```

For workspace-write tasks, add:

```text
- write scope: <exact files/modules>
- coordination: other agents may be working; do not revert unrelated edits
- verification: <targeted commands or manual checks>
```

### Agent Task Templates

- `so_mapper`: Map execution paths, affected files, call sites, dependencies, and likely change boundaries. Return evidence with file paths and uncertainty.
- `so_reviewer`: Review correctness, security, regressions, hidden coupling, and missing tests. Return only real findings with severity and recommended next action.
- `so_tester`: Identify targeted tests, expected failures, verification commands, and remaining coverage gaps. Run commands only when safe for a read-only workspace.
- `so_reproducer`: Phase-2 workspace-write agent for reproducing failures, collecting logs, and managing temporary scratch artifacts after read-only mapper/tester planning narrows scope. Do not edit product code unless explicitly assigned.
- `so_docs_researcher`: Verify external API, framework, or version-specific behavior from authoritative sources. Separate documented facts from inference.
- `so_designer`: Compare implementation options, tradeoffs, migration risks, and testability. Recommend the smallest reversible plan.
- `so_implementer`: Apply one bounded patch after mapping/review narrows the change. Use only with explicit write scope and verification requirements.

### Fallback When Custom Agents Are Unavailable

If a named custom agent cannot be spawned, use the closest available read-only agent or perform that subtask locally. Preserve the same boundaries: one task, no recursive fan-out, explicit evidence, and synthesis before action. Do not fabricate agent results.

## Default patterns

### Debugging

Default to read-only scope narrowing first. Do not spawn workspace-write reproducers before mapper/tester scope is narrowed.

Phase 1 - read-only:

- `so_mapper`: map relevant code paths and likely failure location.
- `so_tester`: identify targeted tests, reproduction commands, and missing coverage.
- `so_reviewer`: inspect likely fix risks.

Phase 2 - only if safe and useful:

- `so_reproducer`: reproduce the failure and collect logs after mapper/tester narrow scope. Use workspace-write only for temporary scratch artifacts, report artifacts created/removed, and do not edit product code unless explicitly assigned.

Synthesis must include:

- observed failure mode,
- evidence,
- likely root cause,
- minimal fix path,
- tests to run,
- uncertainty or conflicts.

### PR/branch review

Spawn:

- `so_mapper`: map changed files and execution paths.
- `so_reviewer`: correctness, security, regression, and missing-test risks.
- `so_tester`: test coverage and likely failing cases.
- `so_docs_researcher`: only if external API/framework/version behavior matters.

Synthesis must include real issues only, with severity, file paths, symbols, reproduction/evidence, and recommended next action.

### Refactor or migration

Spawn:

- `so_mapper`: affected APIs, call sites, dependencies, and boundaries.
- `so_reviewer`: hidden coupling, backwards compatibility, and behavior risks.
- `so_tester`: regression test plan and safety checks.
- `so_implementer`: only after mapping/risk review are summarized, unless the user explicitly requested parallel implementation attempts in isolated worktrees.

Synthesis must include phased implementation, files to change, migration risks, rollback plan, and tests.

### Feature implementation

Use subagents only when the feature is large or ambiguous:

- `so_mapper`: current architecture and extension points.
- `so_designer`: implementation options and tradeoffs.
- `so_tester`: acceptance and regression tests.
- `so_reviewer`: correctness and maintenance risks.

Then implement the smallest safe plan.

## Hard rules

- Existing orchestration, routing, bootstrap, skill-selection, and agent-management frameworks take priority.
- Use this skill as a complement or fallback, not a competing workflow.
- Prefer `single-thread` or `sequential-plan` unless bounded independent tracks clearly add value.
- Prefer read-only subagents before edit-capable subagents.
- Keep each subagent bounded and independently useful.
- Give each subagent a clear return format.
- Before spawning, name at least two independent tracks and check blockers briefly; if the proof fails, use `sequential-plan` or `single-thread`.
- If the decision is `parallel-subagents`, do not stop at a plan; spawn immediately or state the concrete blocker.
- Do not recursively spawn subagents unless the user explicitly asks.
- Wait for all subagents before final synthesis.
- Do not silently merge conflicting findings.
- Do not make code changes until the orchestration decision is complete.
- Ask before code changes only if implementation was not clearly requested, boundaries are unclear, or the action is destructive or externally visible.
- When subagents are not worth it, say so briefly and continue single-threaded.

## Synthesis format after subagents

```text
Synthesis:
- Agreed facts:
- Conflicts or uncertainty:
- Recommended path:
- Files/symbols involved:
- Risks:
- Tests/verification:
- Next action:
```
