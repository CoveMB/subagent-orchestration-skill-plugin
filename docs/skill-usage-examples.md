# Skill usage examples

Use this file as a practical companion to the skill bodies. It shows when each skill fits, what context matters, and what a prompt can look like.

The source of truth still lives under `plugin/subagent-orchestrator/skills/`. If these examples drift from the skill files or tests, check the drift before relying on the examples.

## `using-subagent-orchestrator`

Use this skill when you need to decide whether orchestration belongs in the task at all. It is a compatibility gate, not a required first step for every prompt.

It fits when the user invokes it directly, a hook contract selects it, or the task is broad enough that a short orchestration check may help. It should stay out of simple default work. Repository instructions, user opt-outs, child-agent boundaries, and existing workflow systems still come first.

It returns `skip`, `check`, or `use-subagent-orchestrator`. It does not spawn agents by itself; strong/check hook results carry a production orchestration contract that still depends on tool availability and concrete blockers. Standing user authorization for bounded read-only delegation applies when `parallel-subagents` is selected.

### Example: explicit gate request

```text
$using-subagent-orchestrator
Evaluate whether this task should use the subagent orchestration helper, then proceed:

Investigate the failing API and web tests across modules and identify the smallest fix path.
```

Expected result:

- Classify as `use-subagent-orchestrator` if the work has separable investigation, testing, or review tracks.
- Load `subagent-orchestrator` before broad work.
- Define bounded roles before any spawn attempt.

### Example: explicit opt-out

```text
No subagents. Review this small patch linearly and report only material issues.
```

Expected result:

- Return `skip`.
- Respect the explicit opt-out.
- Do not load `subagent-orchestrator` or spawn agents.

### Example: moderate uncertainty

```text
Check whether the checkout test failures need parallel investigation or a short sequential plan.
```

Expected result:

- Return `check`.
- Do a short local evaluation.
- Use a sequential plan unless independent tracks are clear.

## `subagent-orchestrator`

Use this skill when orchestration is explicitly requested or when `using-subagent-orchestrator` decides the task needs the full execution-shape helper. Its job is to pick the smallest useful shape: `single-thread`, `sequential-plan`, or `parallel-subagents`.

It is most useful for complex debugging, branch review, refactors, migrations, performance work, security-sensitive work, or multi-module work. Parallel subagents make sense only when bounded independent tracks can improve correctness, evidence quality, speed, or context hygiene.

Subagent output is work product. It does not replace required tests, citations, approvals, or direct verification. If the skill selects `parallel-subagents` and a spawning tool is available, briefly state why parallel work is useful, name at least two independent tracks with clear outputs, check blockers, then state the role, mode, scope, expected output, and no recursive fan-out requirement before spawning bounded agents in the same turn. Do not ask a separate question solely for bounded read-only delegation.

### Example: single-thread

```text
$subagent-orchestrator
Summarize what this repository does.
```

Expected result:

- Choose `single-thread`.
- Read and summarize locally.
- Do not spawn agents because parallelism adds overhead.

### Example: sequential plan

```text
$subagent-orchestrator
Apply this migration checklist in order. Stop if any step fails.
```

Expected result:

- Choose `sequential-plan`.
- Run each step only after the previous step succeeds.
- Do not spawn agents because the work has strict ordering.

### Example: parallel subagents for debugging

```text
$subagent-orchestrator
Debug a flaky multi-file auth regression and propose tests.
```

Expected result:

Phase 1 read-only subagents:

```text
Why parallel:
- independent track 1: map auth flow files and likely failure boundaries
- independent track 2: identify targeted tests and coverage gaps
Blockers checked: explicit user opt-out, child-agent recursion, strict sequential dependencies, conflicting writes, workspace-write dirty-state isolation, external side effects, privacy/tool limits, unbounded broad agent tasks.
Subagents:
- name: so_mapper
  mode: read-only
  scope: map auth flow files, call sites, and likely failure boundaries
  expected output: file paths, execution path facts, uncertainty, whether findings depend on uncommitted changes
  constraints: no recursive fan-out
- name: so_tester
  mode: read-only
  scope: identify targeted tests and reproduction commands
  expected output: commands, expected failures, coverage gaps, whether findings depend on uncommitted changes
  constraints: no recursive fan-out
```

After Phase 1, synthesize the observed failure mode, evidence, likely root cause, minimal fix path, tests to run, and remaining uncertainty before editing.

Phase 2 only if safe and useful:

```text
Subagents:
- name: so_reproducer
  mode: workspace-write
  scope: reproduce the narrowed failure and collect logs
  expected output: commands, exit codes, relevant logs, scratch artifacts created/removed, uncertainty
  constraints: no product code edits unless explicitly assigned; no recursive fan-out
```

Use `so_reproducer` only after mapper/tester narrow the scope. It may use workspace-write for temporary scratch artifacts, but it must report artifacts created or removed.

### Example: parallel subagents for branch review

```text
$subagent-orchestrator
Review this branch for correctness, security, behavior regressions, and missing tests. Do not change files unless I ask.
```

Expected result:

```text
Why parallel:
- independent track 1: map changed files, execution paths, and dependencies
- independent track 2: review correctness, security, regressions, and missing-test risk
Blockers checked: explicit user opt-out, child-agent recursion, strict sequential dependencies, conflicting writes, workspace-write dirty-state isolation, external side effects, privacy/tool limits, unbounded broad agent tasks.
Subagents:
- name: so_mapper
  mode: read-only
  scope: map changed files, execution paths, and dependencies
  expected output: touched surfaces, call sites, risk areas, whether findings depend on uncommitted changes
  constraints: no recursive fan-out
- name: so_reviewer
  mode: read-only
  scope: inspect correctness, security, regressions, and maintainability risks
  expected output: material findings only, with file paths, evidence, and whether findings depend on uncommitted changes
  constraints: no recursive fan-out
- name: so_tester
  mode: read-only
  scope: identify relevant tests and missing coverage
  expected output: test commands, expected coverage, gaps, whether findings depend on uncommitted changes
  constraints: no recursive fan-out
```

The final response should lead with material findings. If there are no material issues, say so and list only meaningful residual risk or test gaps.

## Quick selection guide

- Use `using-subagent-orchestrator` when deciding whether the orchestration helper should run.
- Use `subagent-orchestrator` when the task already clearly needs execution-shape selection.
- Stay single-threaded for tiny edits, simple questions, explicit opt-outs, child-agent tasks, and strictly linear work.
- Prefer read-only agents first for broad investigation, review, or testing questions.
- Do not treat dirty repo state as a blocker for bounded read-only agents; tell them to report whether findings depend on uncommitted changes.
- Treat the plugin as read-only-first: default/simple prompts do not write, spawn, or activate a global bootstrap automatically.
- Use bounded workspace-write roles only when scoped and isolated: `so_reproducer` may collect scratch/log work after narrowing, while `so_implementer` needs explicit task scope or prior synthesis before code edits. Dirty repo state blocks workspace-write agents when isolation is unclear.
- Keep destructive/external actions under host/user/approval rules.
- Keep host repository rules, user instructions, safety, privacy, tests, and approval requirements above plugin guidance.
