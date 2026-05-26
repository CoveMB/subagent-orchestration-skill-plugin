# Subagent Orchestration for Codex

This skill plugin gives Codex a quiet, compatibility-oriented orchestration gate:

- every prompt is classified internally and receives compact result/reason metadata,
- existing orchestration, routing, bootstrap, skill-selection, and agent-management frameworks take priority,
- simple prompts stay single-threaded unless the user asks otherwise,
- complex prompts still receive only result/reason metadata by default,
- default/simple prompts do not write, spawn, or activate a global bootstrap automatically,
- when `parallel-subagents` is selected, do not ask a separate question solely for bounded read-only delegation,
- still stop or ask when repository rules, user instructions, safety policy, privacy/context-sharing, vendor/tool policy, approval rules, cost/budget limits, destructive actions, external side effects, workspace-write scope, or unclear boundaries require it,
- parallel subagents are used only when they add real value.

It packages:

```text
plugin/subagent-orchestrator/
  .codex-plugin/plugin.json
  skills/using-subagent-orchestrator/SKILL.md
  skills/subagent-orchestrator/SKILL.md

docs/skill-usage-examples.md
hooks/subagent_orchestration_gate.py
custom-agents/*.toml
snippets/AGENTS.subagent-orchestration.md
snippets/config.subagents.toml
snippets/config.hooks.*.toml
marketplace/*.json
scripts/install_user.py
scripts/uninstall_user.py
scripts/grade_skill_traces.py
scripts/run_live_skill_evals.py
scripts/check.sh
scripts/file_ops.py
scripts/toml_ops.py
scripts/eval_contract.py
templates/project_hook_wrapper.py
evals/skill_prompts.jsonl
evals/trace_eval.schema.json
evals/trace_fixtures/
tests/*.py
.github/workflows/tests.yml
install.sh / install.ps1
uninstall.sh / uninstall.ps1
```

Examples for both included skills live in
[`docs/skill-usage-examples.md`](docs/skill-usage-examples.md).

## How this coexists with other workflows

Superpowers and other workflow systems should keep priority. This plugin does not replace them and does not ask the user whether orchestration is preferable on every prompt.

For Codex, the quiet behavior comes from three layers together:

1. **Compatibility skill**: `using-subagent-orchestrator` exists for explicit or legacy checks.
2. **Orchestration skill**: `subagent-orchestrator` chooses `single-thread`, `sequential-plan`, or `parallel-subagents`.
3. **UserPromptSubmit hook**: reports a result and reason through valid `additionalContext`.

The hook does not spawn agents by itself or inject execution instructions. After the orchestration skill selects `parallel-subagents`, the assistant must call `spawn_agent` or the available subagent-spawning tool in that same turn after defining bounded roles. It should only fall back to sequential work when no spawning tool is available or higher-priority rules block spawning.

Hook result mapping:

| Hook result | Compatibility-gate action | Execution-shape action |
| --- | --- | --- |
| `single-thread-default` | `skip` | Proceed normally; do not load orchestration by default. |
| `single-thread-likely` | `check` | Proceed normally after a short local gate check if useful; do not load orchestration by default. |
| `orchestration-check` | `check` | Do a short local gate check; load `subagent-orchestrator` only if independent tracks are clear. |
| `use-subagent-orchestrator` | `use-subagent-orchestrator` | Load `subagent-orchestrator` before broad work; then choose `single-thread`, `sequential-plan`, or `parallel-subagents`. |
| `orchestration-opt-out` | `skip` | Do not load orchestration or spawn agents. |
| `recursion-guard` | `skip` | Do not recursively orchestrate unless the parent explicitly provided bounded permission. |

When loaded, `subagent-orchestrator` chooses only `single-thread`, `sequential-plan`, or `parallel-subagents`.

The live harness has an opt-in contract mode for measuring that behavior end to end. Use `--hook-mode contract` to append a bounded spawn contract only for strong `use-subagent-orchestrator` decisions. Normal hook output remains metadata-only.

A plugin can package the skills. The installer supports user/global skill installation and project-scoped activation, but activation is always explicit.

## Boundary model

- **Plugin-level boundary**: this plugin is an execution-shape helper only. It can help choose `single-thread`, `sequential-plan`, or `parallel-subagents`; it does not decide truth, evidence, citations, approvals, vendor trust, or test sufficiency.
- **Write-capability boundary**: the plugin is read-only-first. The manifest keeps `Write` only for bounded workspace-write roles such as `so_implementer` or `so_reproducer` scratch/log work. Code edits require explicit task scope or prior synthesis, and destructive/external actions remain governed by host/user/approval rules.
- **Host-repo boundary**: domain-specific user instructions, repository `AGENTS.md`, local scripts, audit requirements, and source-of-truth rules win over plugin guidance. When host repository rules are stricter, host repository rules win.
- **Subagent-output boundary**: subagent output is work product, not evidence by itself. Required tests, citations, source checks, approvals, and audit notes still need to be performed directly.
- **Hook boundary**: the hook reports classification metadata only. The live harness can add spawn-contract guidance for eval runs, but the hook still does not enforce truth, validate sources, authorize edits, satisfy citations, replace tests, or bypass safety/privacy/vendor/approval rules.
- **Installer boundary**: user scope never writes `CODEX_HOME/config.toml` or `CODEX_HOME/AGENTS.md`. Project scope writes only under the selected repository root and never patches `~/.codex` or `~/.agents`.

## Four install modes

### 1. Manual skill-only use

macOS/Linux:

```bash
./install.sh
```

Windows PowerShell:

```powershell
./install.ps1
```

The default user-scope install copies only direct skills to `~/.agents/skills/`. It does not stage a hook, patch config, append guidance, install custom agents, or register plugin packaging.

Manual copy is also supported:

```bash
mkdir -p ~/.agents/skills
cp -R plugin/subagent-orchestrator/skills/* ~/.agents/skills/
```

Then use:

```text
$using-subagent-orchestrator evaluate this task first, then proceed.
```

or:

```text
$subagent-orchestrator choose single-thread, sequential-plan, or parallel-subagents before work.
```

### 2. User/global activation

Stage the hook in `CODEX_HOME/hooks` only when you want to manually activate it for that user profile:

```bash
./install.sh --with-hook
```

PowerShell:

```powershell
./install.ps1 -WithHook
```

The installer still does **not** patch `CODEX_HOME/config.toml`; merge one of `snippets/config.hooks.*.toml` manually when you want the hook active for that global Codex home.

The installer prints an activation reminder at the end:

```text
config.toml was not modified.
Hook staged but not active.
Activation required: manually merge snippets/config.hooks.posix.toml into CODEX_HOME/config.toml.
```

Preview the install without writing files:

```bash
./install.sh --dry-run
```

PowerShell:

```powershell
./install.ps1 -DryRun
```

Remove owned user-scope files and hook config entries:

```bash
./install.sh --uninstall
```

PowerShell:

```powershell
./install.ps1 -Uninstall
```

### 3. Project-scoped activation

Project scope writes only inside the repository root. It never writes `~/.codex`, never writes `~/.agents`, never patches `~/.codex/config.toml`, never appends `~/.codex/AGENTS.md`, never installs hooks globally, and never enables plugin state globally.

Activate the gate for Codex sessions opened from a trusted project:

```bash
./install.sh --scope project --activate-gate
```

PowerShell:

```powershell
./install.ps1 -Scope project -ActivateGate
```

This creates or updates repo-local files:

- `.codex/config.toml`
- `.codex/hooks/subagent_orchestration_gate.py`
- `.agents/skills/using-subagent-orchestrator`
- `.agents/skills/subagent-orchestrator`
- `.codex/subagent-orchestrator-install.json`

Optional project-only additions:

```bash
./install.sh --scope project --activate-gate --with-project-agents
./install.sh --scope project --with-repo-marketplace
./install.sh --scope project --append-project-agents-md
```

Project scope installs repo-local skills by default. Omit `--activate-gate` to make skills available without activating the prompt gate. Use `--dry-run` to print created, patched, copied, symlinked, and backed-up paths without changing files. Use project uninstall to remove manifest-owned project install files:

```bash
./install.sh --scope project --repo-root /path/to/repo --uninstall
```

Project-scoped activation affects only Codex sessions opened from that trusted project, because it uses project `.codex/config.toml`, project hooks, and repo-local skills.

### 4. Vendored project activation

If the plugin is vendored into a repository, prefer symlinked skills:

```bash
./vendor/subagent-orchestration-skill-plugin/install.sh \
  --scope project \
  --repo-root "$(git rev-parse --show-toplevel)" \
  --from-vendor "$(git rev-parse --show-toplevel)/vendor/subagent-orchestration-skill-plugin" \
  --activate-gate \
  --link-skills
```

This links:

```text
.agents/skills/using-subagent-orchestrator
  -> vendor/subagent-orchestration-skill-plugin/plugin/subagent-orchestrator/skills/using-subagent-orchestrator

.agents/skills/subagent-orchestrator
  -> vendor/subagent-orchestration-skill-plugin/plugin/subagent-orchestrator/skills/subagent-orchestrator
```

If symlinks fail, the installer falls back to copying. Omit `--link-skills` to use copies.

The repo marketplace option adds or updates `.agents/plugins/marketplace.json` with a local plugin path:

```json
{
  "name": "subagent-orchestrator",
  "source": {
    "source": "local",
    "path": "./vendor/subagent-orchestration-skill-plugin/plugin/subagent-orchestrator"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Productivity"
}
```

Caveat: plugin UI enable/disable state is stored in `~/.codex/config.toml`, so strict project-only behavior should use repo-local skills and hooks rather than relying only on plugin installation.

## Project hook config

Project activation patches `.codex/config.toml` idempotently and preserves unrelated config:

```toml
[features]
hooks = true

[agents]
max_threads = 4
max_depth = 1

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = 'python3 "$(git rev-parse --show-toplevel)/.codex/hooks/subagent_orchestration_gate.py"'
timeout = 5
statusMessage = "Evaluating subagent orchestration"
```

Existing project config is backed up before modification. The project hook fails open if a vendored hook target is missing.

## Custom agents

Included custom agents:

- `so_mapper`: read-only code path and dependency mapper.
- `so_reviewer`: read-only correctness/security/test-risk reviewer.
- `so_tester`: read-only test and verification planner.
- `so_reproducer`: workspace-write failure reproducer.
- `so_docs_researcher`: docs/version behavior verifier.
- `so_designer`: compares implementation options and tradeoffs.
- `so_implementer`: bounded implementation agent.

Copy them manually into `~/.codex/agents` or `.codex/agents` only when you want these custom agent profiles available.

## Smoke test the hook

```bash
bash scripts/check.sh
```

Expected:

- debugging prompt => `use-subagent-orchestrator`,
- rename prompt => `single-thread-likely` with only result/reason metadata,
- user opt-out => `orchestration-opt-out`,
- child-agent prompt => `recursion-guard`.

## CI / maintainer checks

GitHub Actions runs on pushes to `main` and pull requests with Python 3.11. The workflow entrypoint is `bash scripts/check.sh`, which runs:

- `tests/test_hook.py` for installer, hook classifier, snippet/config, marketplace, and CI wiring checks,
- `tests/test_skills.py` for skill frontmatter, manifest, decision taxonomy, boundary, and spawn-contract checks,
- `tests/test_evals.py` for prompt corpus, offline grader, realistic trace fixture, rubric, and profile checks,
- `tests/test_live_evals.py` for live harness behavior using fake Codex binaries and important supported harness modes and flags,
- `python3 -m compileall -q hooks scripts tests` for syntax coverage.

The CI suite verifies the live harness and trace grader, but real live Codex sessions stay outside CI because they require a local Codex runtime, profile configuration, and optional subagent/tool availability.

## Skill evals

The fast check suite validates the eval assets and the offline trace grader, but it does not run live agent sessions.

The prompt corpus lives at `evals/skill_prompts.jsonl`. Each row defines the expected hook decision, whether a spawn attempt is required or forbidden, expected and forbidden spawned agent roles for parallel cases, optional pre-spawn boundary overrides, wait/synthesis requirements, host-rule fixtures, command/spawn limits, and rubric ids. Spawn-required cases use the standard pre-spawn boundary terms by default. The grader validates these rows before scoring and exits with code `2` when the corpus is malformed. The set intentionally includes positive, negative, opt-out, child-agent, host-rule, documentation/setup validation, and broad parallel-work cases.

To grade captured JSONL traces, place one trace per prompt id in a directory as `<id>.jsonl`, then run:

```bash
python3 scripts/grade_skill_traces.py --prompts evals/skill_prompts.jsonl --traces path/to/traces
```

The grader checks observed `Result:` metadata from assistant or synthetic hook-context message events, timeout events, spawn attempts, expected spawned agent roles from actual `spawn_agent` arguments, duplicate labeled agent roles when a case opts into that policy, required pre-spawn boundary text, required waits after spawning, synthesis text, forbidden externally visible commands/tools, and command/spawn-count budgets. Synthetic `hook.context` events satisfy the decision check because that check measures hook classification, but they do not satisfy pre-spawn or final-text checks that measure assistant behavior. It supports both function-call traces and live Codex `collab_tool_call` traces; live spawned prompts should include exact `agent_type: so_*` labels when the tool has no dedicated agent-type field. Forbidden-command checks are restricted to command execution events so a safe spawn prompt that says not to run a command is not counted as running it. It prints structured JSON compatible with `evals/trace_eval.schema.json`, including observed `command_count` telemetry for each present trace.

The default `offline` profile enforces `max_command_count` for deterministic fixtures and synthetic regressions:

```bash
python3 scripts/grade_skill_traces.py --prompts evals/skill_prompts.jsonl --traces path/to/traces
```

Use the `live` profile for real Codex traces. It still reports `command_count`, but it does not fail a case only because live Codex used more shell commands than the offline budget:

```bash
python3 scripts/grade_skill_traces.py --profile live --prompts evals/skill_prompts.jsonl --traces path/to/live-traces
```

Representative trace fixtures live under `evals/trace_fixtures/pass` and `evals/trace_fixtures/fail` so the grader itself is tested against realistic saved JSONL events.

To run live Codex traces locally, use the live harness. It is intentionally outside CI and executes the selected prompts through `codex exec --json`, one trace per case:

```bash
python3 scripts/run_live_skill_evals.py \
  --traces evals/live_traces/manual \
  --case simple-repository-question \
  --case parallel-auth-debug
```

Preview commands without running Codex:

```bash
python3 scripts/run_live_skill_evals.py --traces evals/live_traces/manual --dry-run
```

The harness writes `selected_prompts.jsonl`, `<case-id>.jsonl`, optional `<case-id>.stderr.txt`, and `grade.json` into the trace directory. Reusing a trace directory requires `--overwrite`.

Live harness grading uses `--grade-profile live` by default. Pass `--grade-profile offline` when you intentionally want live runs to fail on command budgets. Use `--trials N` to run each selected case independently and emit `<case-id>__trial_N.jsonl` traces.

Each captured trace starts with a synthetic `hook.context` event from the repo-local `hooks/subagent_orchestration_gate.py`, followed by the live `codex exec --json` stream. This keeps hook classification visible and satisfies the `decision` rubric even when Codex does not expose successful hook `additionalContext` as a JSONL event. Assistant-behavior rubrics such as pre-spawn boundaries, waits, final synthesis, and side-effect checks still require live stream evidence. The harness runs this hook in `metadata` mode by default, matching normal activation behavior.

Use `--hook-mode contract` only for live evals that need to measure whether the assistant follows an explicit spawn contract:

```bash
python3 scripts/run_live_skill_evals.py \
  --traces evals/live_traces/manual-contract \
  --hook-mode contract \
  --inject-local-hook-context \
  --case parallel-auth-debug
```

Contract mode is owned by the live harness, not the production hook. The harness appends spawn-contract guidance to its synthetic hook context only for strong `use-subagent-orchestrator` classifications; simple, opt-out, and recursion-guard prompts stay metadata-only. `--inject-local-hook-context` also prepends that harness context to the child prompt, which is useful when the CLI runtime does not expose successful hook `additionalContext` to the model during eval runs.

Contract-mode live runs also append a bounded live-eval execution limit to the child prompt. This keeps broad prompts such as branch reviews focused on producing orchestration trace evidence instead of running full audits, external review services, network calls, package installs, full test suites, or broad repository sweeps. Spawned runs are instructed to use one post-spawn wait and then synthesize from available agent results, noting unavailable agents as blockers instead of repeatedly waiting or falling back to a sequential review.

For prompt rows where `must_not_spawn` is true, contract-mode live runs add a stronger no-spawn case limit: do not perform the underlying branch review, audit, debug, or documentation sweep; finish after a couple of quick read-only checks. This keeps boundary and opt-out cases focused on hook behavior instead of turning them into full repository reviews.

In contract mode, strong orchestration cases are expected to emit a pre-spawn assistant boundary that includes `Subagent orchestration gate`, `Result: use-subagent-orchestrator`, `Reason:`, and the bounded `Subagents:` plan before any spawn call. Spawn prompts should start with the exact `agent_type: so_*` line, leave `fork_context` unset when using custom agent types, and carry any needed context in the prompt body.

For spawn-contract evals, keep the subagent-capable user/profile configuration enabled. `--codex-arg=--ignore-user-config` is useful for metadata-only smoke tests, but it can remove the live spawn surface and turn strong orchestration cases into expected failures.

Use `--no-local-hook-context` when you specifically want to test runtime hook integration without the harness writing that synthetic event to the trace. Prompt rows with `host_rules_fixture` run in an isolated per-case workspace containing an `AGENTS.md` fixture.

Pass extra `codex exec` flags with repeated `--codex-arg`. For cleaner smoke tests that avoid global user workflow config, use:

```bash
python3 scripts/run_live_skill_evals.py \
  --traces evals/live_traces/isolated-smoke \
  --case simple-repository-question \
  --codex-arg=--ignore-user-config
```

## Safety notes

- Keep `max_depth = 1` if you manually merge `snippets/config.subagents.toml`.
- Prefer read-only subagents first.
- Do not let multiple agents edit the same files unless they are in isolated worktrees.
- Treat the hook as classification metadata, not an enforcement boundary; keep contract mode limited to eval or deliberate validation runs.
- Respect user opt-outs.
- Repository-specific `AGENTS.md` files and source-of-truth project rules remain higher authority than this global guidance.
- Do not make this kit compete with Superpowers, Recursive Mode, or other orchestration/routing/bootstrap systems.
