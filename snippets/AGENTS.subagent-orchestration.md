## Subagent orchestration gate

This gate is optional compatibility guidance, not a global bootstrap workflow.
Existing orchestration, routing, bootstrap, skill-selection, and agent-management frameworks take priority.
Host repository rules win. This plugin is an execution-shape helper only; it does not override local source-of-truth, citation, manuscript, safety, privacy, vendor, approval, testing, script, or audit requirements.

For simple/default prompts, stay silent and proceed single-threaded.

Hook result mapping:

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

Use subagent-orchestrator only for explicit subagent/orchestration requests or clearly complex work where existing frameworks do not already cover the decision. Classify internally as one of:

1. `single-thread`,
2. `sequential-plan`,
3. `parallel-subagents`.

For complex debugging, PR review, refactors, architecture exploration, test failures, migrations, performance work, security-sensitive work, unfamiliar APIs, or multi-file changes, the `subagent-orchestrator` skill may be used as a complement or fallback.

When `parallel-subagents` is selected, do not ask a separate question solely for bounded read-only delegation. Do not treat dirty repo state as a blocker for bounded read-only agents; tell them to report whether findings depend on uncommitted changes. Still stop or ask when repository rules, user instructions, safety policy, privacy/context-sharing, vendor/tool policy, approval rules, cost/budget limits, destructive actions, external side effects, workspace-write scope, workspace-write dirty-state isolation, or unclear boundaries require it. Subagents are read-only by default; give a compact why-parallel proof and define clear boundaries first.

If a parallel workflow is valuable:

- briefly state why at least two bounded tracks can run independently,
- explicitly spawn bounded subagents,
- prefer read-only exploration before edits,
- require explicit write scope or isolated worktrees for parallel mutation,
- block workspace-write agents in dirty repo state when isolation is unclear,
- avoid recursive fan-out,
- wait for all agents,
- treat subagent output as work product, not evidence by itself,
- synthesize conflicts before edits,
- ask before code changes only if implementation was not explicitly requested, boundaries are unclear, or the action is destructive or externally visible.

Do not ask the user whether orchestration is preferable. Decide internally.
Never recursively orchestrate inside a bounded subagent task unless explicitly requested.
Respect user opt-outs such as "no subagents", "work linearly", or "single-thread only".
