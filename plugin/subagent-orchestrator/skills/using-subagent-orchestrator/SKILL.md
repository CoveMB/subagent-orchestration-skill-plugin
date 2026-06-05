---
name: using-subagent-orchestrator
description: Optional legacy compatibility gate for deciding whether the subagent-orchestrator execution-shape helper should be applied. Use only when explicitly invoked, when a quiet hook hint selects it, or for clearly complex work not already covered by existing orchestration, routing, bootstrap, skill-selection, or agent-management frameworks. Skip for child subagent tasks, explicit opt-outs, simple/default work, and any task already governed by another orchestration system.
---

# Using Subagent Orchestrator

This skill is a lightweight compatibility gate. Its job is to decide whether to load and follow `subagent-orchestrator` only when doing so complements existing workflow systems.

It is not a global bootstrap skill. It must not force evaluation or parallel agents by default.

## Host project boundary

- This skill is an execution-shape helper only.
- It must not override user instructions, repository AGENTS.md, source-of-truth rules, citation rules, manuscript rules, test/check rules, safety rules, privacy/release rules, vendor rules, approval rules, local scripts, or audit requirements.
- When host repository rules are stricter than plugin guidance, host repository rules win.
- Subagent output is a work product, not evidence by itself.
- If a host project requires evidence, citations, tests, approvals, locators, or audit notes, subagent output is not a substitute for those requirements.

## Priority

User instructions are highest priority. If the user explicitly says not to use subagents, not to orchestrate, or to work linearly, obey that.

If this session is already a bounded dispatched subagent task, do not recursively orchestrate unless the parent explicitly asked for it.

Existing orchestration, routing, bootstrap, skill-selection, and agent-management frameworks take priority. Use `subagent-orchestrator` only as a complement or fallback.

When `parallel-subagents` is selected, do not ask a separate question solely for bounded read-only delegation. Dirty repo state is not a blocker for bounded read-only agents; tell them to report whether findings depend on uncommitted changes. Still stop or ask when repository rules, user instructions, safety policy, privacy/context-sharing, vendor/tool policy, approval rules, cost/budget limits, destructive actions, external side effects, workspace-write scope, workspace-write dirty-state isolation, or unclear boundaries require it; define clear boundaries instead.

## Boundary check

Before invoking `subagent-orchestrator`:

- check whether the active repository has AGENTS.md or other project instructions,
- keep those instructions above this skill,
- remember that host repository rules win,
- respect explicit user opt-outs,
- avoid recursive orchestration inside dispatched subagents,
- use this bootstrap only to decide whether orchestration should be evaluated.

## First step

If this skill is explicitly invoked or selected by a quiet hook hint, classify the prompt internally:

```text
Orchestration gate: skip | check | use-subagent-orchestrator
Reason: <one sentence>
```

Use:

- `skip` for tiny edits, simple Q&A, direct one-file tasks, or explicit user opt-out.
- `check` for moderate uncertainty where a short local evaluation is enough.
- `use-subagent-orchestrator` for complex debugging, multi-file work, PR review, refactors, migrations, architecture exploration, performance/security work, broad tests, unfamiliar APIs, or tasks with separable research/review/testing tracks.

Do not ask the user whether orchestration is preferable. Decide internally.
Do not print this gate for simple/default prompts.

## Hook result mapping

Hook classification is metadata plus optional non-binding action hints; it does not spawn agents by itself or inject binding execution instructions.

| Hook result | Compatibility-gate action | Execution-shape action |
| --- | --- | --- |
| `single-thread-default` | `skip` | Proceed normally; do not load orchestration by default. |
| `single-thread-likely` | `check` | Proceed normally after a short local gate check if useful; do not load orchestration by default. |
| `orchestration-check` | `check` | Do a short local gate check; load `subagent-orchestrator` only if independent tracks are clear. |
| `use-subagent-orchestrator` | `use-subagent-orchestrator` | Load `subagent-orchestrator` before broad work; then choose `single-thread`, `sequential-plan`, or `parallel-subagents`. |
| `orchestration-opt-out` | `skip` | Do not load orchestration or spawn agents. |
| `recursion-guard` | `skip` | Do not recursively orchestrate unless the parent explicitly provided bounded permission. |

When loaded, `subagent-orchestrator` chooses only `single-thread`, `sequential-plan`, or `parallel-subagents`.

## If `use-subagent-orchestrator`

Load and follow the `subagent-orchestrator` skill before any code changes or broad tool work.

The desired result is one of:

- `single-thread`
- `sequential-plan`
- `parallel-subagents`

Only spawn subagents when the evaluation shows real value.
When the evaluation selects `parallel-subagents`, actual spawning is required: call `spawn_agent` or the available subagent-spawning tool after giving the compact why-parallel proof and defining bounded roles. Do not stop at a plan, recommendation, or statement that subagents would be useful. If no subagent-spawning tool is available, the proof fails, or a higher-priority instruction blocks spawning, state that blocker and proceed with the closest sequential fallback.

## If `check`

Do a short inline evaluation using the same categories. Do not load extra ceremony unless the task decomposes cleanly.

## Red flags

Stop and evaluate before continuing when you notice:

- multiple possible root causes,
- multiple independent files/subsystems,
- separate research, testing, review, or implementation tracks,
- noisy logs or broad searches,
- security/performance/regression risks,
- external API or version-specific behavior,
- unclear requirements likely to benefit from separate design/review.

## Avoidance rules

Do not use orchestration when:

- coordination overhead would exceed value,
- conflicting writes would edit the same files without worktree isolation,
- workspace-write agents would run in a dirty repo without clear isolation,
- the task is strictly sequential,
- the user wants a quick direct answer,
- the jobs would be unbounded broad agent tasks,
- you cannot define bounded subagent jobs with clear outputs.
