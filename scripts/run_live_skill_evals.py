#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from eval_contract import DECISION_RE
from grade_skill_traces import grade, load_jsonl, validate_cases

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPTS = ROOT / "evals" / "skill_prompts.jsonl"
HOOK = ROOT / "hooks" / "subagent_orchestration_gate.py"
HOOK_MODE_CONTRACT = "contract"
SPAWN_CONTRACT_CONTEXT = "\n".join([
    "Contract mode: live-eval spawn contract.",
    "When using parallel subagents, give a compact why-parallel proof and define bounded roles before the first spawn.",
    "Subagents:",
    "- agent_type: so_mapper",
    "  mode: read-only",
    "  scope: map relevant files, execution paths, dependencies, and uncertainty",
    "  expected output: concise facts with file paths, evidence, risks, and confidence",
    "  constraints: no recursive fan-out",
    "- agent_type: so_tester",
    "  mode: read-only",
    "  scope: identify targeted tests, verification commands, and coverage gaps",
    "  expected output: commands, outcomes or blockers, missing tests, and confidence",
    "  constraints: no recursive fan-out",
    "- agent_type: so_reviewer",
    "  mode: read-only",
    "  scope: review correctness, security, regression, and maintainability risks when needed",
    "  expected output: real findings only, ordered by severity, with evidence and next action",
    "  constraints: no recursive fan-out",
    "Spawn prompt requirements:",
    "Before first spawn, send an assistant message that starts with Subagent orchestration gate.",
    "That message must include Result: use-subagent-orchestrator, Reason:, Why parallel:, independent track 1:, independent track 2:, Blockers checked:, and Subagents: before any spawn tool call.",
    "Keep the why-parallel proof compact; use it to show that at least two bounded tracks can run independently with clear outputs.",
    "If the why-parallel proof or blocker check fails, choose sequential-plan or single-thread instead of spawning.",
    "List each selected role with mode, scope, expected output, and no recursive fan-out.",
    "Use literal field labels mode:, scope:, expected output:, and constraints: in the Subagents: message.",
    "First line of every spawn prompt must be one of:",
    "agent_type: so_mapper",
    "agent_type: so_tester",
    "agent_type: so_reviewer",
    "Do not replace these labels with prose role names.",
    "Do not set fork_context when using agent_type; include required context in the spawn prompt instead.",
    "Spawn each selected role at most once.",
    "Spawn contract: if higher-priority instructions and tool availability permit, call the available spawn tool in this turn.",
    "After spawning, wait for spawned agents before final synthesis.",
    "After one wait call, synthesize with available agent results and mention any unavailable agent as a blocker.",
    "Finish with Synthesis: and Tests/verification: sections.",
    "If blocked, state the blocker in Synthesis: and finish without fallback sequential review.",
])
LIVE_EVAL_EXECUTION_LIMIT = "\n".join([
    "Live eval execution limit:",
    "Treat this as an evaluation of orchestration behavior, not an exhaustive repository task.",
    "Do only the minimum local inspection needed to satisfy the trace contract.",
    "Do not run external review services, network calls, package installs, full test suites, or broad repository sweeps.",
    "For spawned cases, spawn only the required bounded roles, wait after spawning, then finish.",
    "Use exactly one post-spawn wait call; synthesize with available agent results and note unavailable agents as blockers.",
    "Do not perform fallback sequential review after the wait call; finish with the available evidence.",
    "Finish promptly with required final sections once the trace contract is satisfied or a blocker is found.",
])
NON_SPAWN_LIVE_EVAL_EXECUTION_LIMIT = "\n".join([
    "Non-spawn live eval case:",
    "Do not perform the underlying branch review, audit, debug, or documentation sweep.",
    "This case is checking boundary/no-spawn behavior; after at most two quick read-only local checks, finish concisely.",
])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live Codex skill eval prompts and grade captured JSONL traces.")
    parser.add_argument("--prompts", type=Path, default=DEFAULT_PROMPTS, help="Prompt corpus JSONL.")
    parser.add_argument("--traces", type=Path, required=True, help="Directory to write trace JSONL files.")
    parser.add_argument("--case", action="append", default=[], dest="case_ids", help="Prompt case id to run. Repeatable.")
    parser.add_argument("--codex-bin", default="codex", help="Codex executable path or command name.")
    parser.add_argument("--codex-arg", action="append", default=[], dest="codex_args", help="Extra argument passed to `codex exec` before the prompt. Repeatable.")
    parser.add_argument("--cwd", type=Path, default=ROOT, help="Working directory for live Codex runs.")
    parser.add_argument("--timeout", type=int, default=900, help="Per-case timeout in seconds.")
    parser.add_argument("--trials", type=int, default=1, help="Number of independent runs per selected case.")
    parser.add_argument("--dry-run", action="store_true", help="Print selected commands without running Codex.")
    parser.add_argument("--no-grade", action="store_true", help="Capture traces without running the offline grader.")
    parser.add_argument("--no-local-hook-context", action="store_true", help="Do not write the synthetic local hook.context trace event; use this when testing runtime hook integration.")
    parser.add_argument("--inject-local-hook-context", action="store_true", help="Prepend repo-local hook context to the child Codex prompt. Use for contract-mode evals when runtime hook integration is not guaranteed.")
    parser.add_argument("--grade-profile", choices=("live", "offline"), default="live", help="Grading profile. live records command counts without failing on command budgets.")
    parser.add_argument("--hook-mode", choices=("metadata", "contract"), default="metadata", help="Hook output mode for live runs. metadata is production-like; contract adds spawn-contract guidance for end-to-end evals.")
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing existing trace files.")
    return parser.parse_args()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def selected_cases(cases: list[dict[str, Any]], case_ids: list[str]) -> list[dict[str, Any]]:
    if not case_ids:
        return cases

    cases_by_id = {case["id"]: case for case in cases}
    unknown_ids = [case_id for case_id in case_ids if case_id not in cases_by_id]
    if unknown_ids:
        raise ValueError("unknown case id: " + ", ".join(unknown_ids))
    return [cases_by_id[case_id] for case_id in case_ids]


def trial_case_id(case_id: str, trial_number: int) -> str:
    return f"{case_id}__trial_{trial_number}"


def expand_trials(cases: list[dict[str, Any]], trials: int) -> list[dict[str, Any]]:
    if trials < 1:
        raise ValueError("--trials must be a positive integer")
    if trials == 1:
        return cases
    return [
        {**case, "id": trial_case_id(str(case["id"]), trial_number)}
        for case in cases
        for trial_number in range(1, trials + 1)
    ]


def codex_command(codex_bin: str, codex_args: list[str], prompt: str) -> list[str]:
    return [codex_bin, "exec", *codex_args, "--json", prompt]


def safe_case_child_path(parent: Path, case_id: str, label: str, suffix: str = "") -> Path:
    if Path(case_id).name != case_id:
        raise ValueError(f"case id cannot be used as a {label}: {case_id}")
    child_path = (parent / f"{case_id}{suffix}").resolve()
    if child_path.parent != parent.resolve():
        raise ValueError(f"case id cannot be used as a {label}: {case_id}")
    return child_path


def trace_path_for_case(traces_dir: Path, case_id: str) -> Path:
    return safe_case_child_path(traces_dir, case_id, "trace filename", ".jsonl")


def trace_paths_for_cases(traces_dir: Path, cases: list[dict[str, Any]]) -> list[Path]:
    return [trace_path_for_case(traces_dir, str(case["id"])) for case in cases]


def live_metadata_paths(traces_dir: Path) -> list[Path]:
    return [traces_dir / "selected_prompts.jsonl", traces_dir / "grade.json"]


def ensure_trace_targets_are_writable(traces_dir: Path, cases: list[dict[str, Any]], overwrite: bool) -> None:
    workspace_paths = [
        case_workspace_path(traces_dir, str(case["id"]))
        for case in cases
        if host_rules_fixture(case) is not None
    ]
    candidate_paths = trace_paths_for_cases(traces_dir, cases) + live_metadata_paths(traces_dir) + workspace_paths
    existing_paths = [path for path in candidate_paths if path.exists()]
    if existing_paths and not overwrite:
        paths = ", ".join(str(path) for path in existing_paths)
        raise ValueError("trace outputs already exist; pass --overwrite to replace: " + paths)


def host_rules_fixture(case: dict[str, Any]) -> str | None:
    value = case.get("host_rules_fixture")
    if isinstance(value, str) and value.strip():
        return value
    return None


def case_workspace_path(traces_dir: Path, case_id: str) -> Path:
    return safe_case_child_path(traces_dir / "workspaces", case_id, "workspace name")


def prepare_case_workspace(case: dict[str, Any], args: argparse.Namespace) -> Path:
    fixture = host_rules_fixture(case)
    if fixture is None:
        return args.cwd

    workspace_path = case_workspace_path(args.traces, str(case["id"]))
    if args.overwrite and workspace_path.exists():
        if workspace_path.is_dir():
            shutil.rmtree(workspace_path)
        else:
            workspace_path.unlink()
    workspace_path.mkdir(parents=True, exist_ok=True)
    if fixture is not None:
        (workspace_path / "AGENTS.md").write_text(fixture.rstrip() + "\n", encoding="utf-8")
    return workspace_path


def should_load_local_hook_context(args: argparse.Namespace) -> bool:
    return args.inject_local_hook_context or not args.no_local_hook_context


def prompt_with_local_hook_context(prompt: str, hook_context: str) -> str:
    return "\n\n".join([hook_context, "User prompt:", prompt])


def should_append_live_eval_execution_limit(args: argparse.Namespace) -> bool:
    return args.hook_mode == HOOK_MODE_CONTRACT


def live_eval_execution_limit_for_case(case: dict[str, Any]) -> str:
    if case.get("must_not_spawn") is True:
        return "\n\n".join([LIVE_EVAL_EXECUTION_LIMIT, NON_SPAWN_LIVE_EVAL_EXECUTION_LIMIT])
    return LIVE_EVAL_EXECUTION_LIMIT


def prompt_with_live_eval_execution_limit(prompt: str, case: dict[str, Any]) -> str:
    return "\n\n".join([prompt, live_eval_execution_limit_for_case(case)])


def context_decision(context: str) -> str | None:
    match = DECISION_RE.search(context)
    return match.group(1) if match else None


def should_append_spawn_contract(context: str, hook_mode: str) -> bool:
    return hook_mode == HOOK_MODE_CONTRACT and context_decision(context) == "use-subagent-orchestrator"


def context_with_optional_spawn_contract(context: str, hook_mode: str) -> str:
    if should_append_spawn_contract(context, hook_mode):
        return "\n\n".join([context, SPAWN_CONTRACT_CONTEXT])
    return context


def prompt_for_case(case: dict[str, Any], args: argparse.Namespace, hook_context: str | None) -> str:
    prompt = str(case["prompt"])
    if should_append_live_eval_execution_limit(args):
        prompt = prompt_with_live_eval_execution_limit(prompt, case)
    if args.inject_local_hook_context:
        if hook_context is None:
            raise ValueError("local hook context is required when --inject-local-hook-context is set")
        return prompt_with_local_hook_context(prompt, hook_context)
    return prompt


def completed_process_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def timeout_trace_event(case_id: str, timeout_seconds: int) -> dict[str, Any]:
    return {
        "type": "timeout",
        "case_id": case_id,
        "timeout_seconds": timeout_seconds,
        "message": f"live eval case timed out after {timeout_seconds} seconds",
    }


def run_case(case: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    case_id = str(case["id"])
    hook_context = local_hook_context(str(case["prompt"]), args.hook_mode) if should_load_local_hook_context(args) else None
    command = codex_command(args.codex_bin, args.codex_args, prompt_for_case(case, args, hook_context))
    run_cwd = prepare_case_workspace(case, args)
    timed_out = False
    timeout_events: list[dict[str, Any]] = []
    try:
        proc = subprocess.run(
            command,
            text=True,
            capture_output=True,
            cwd=run_cwd,
            stdin=subprocess.DEVNULL,
            timeout=args.timeout,
            check=False,
        )
        stdout = proc.stdout
        stderr = proc.stderr
        process_returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = completed_process_text(exc.stdout)
        stderr = completed_process_text(exc.stderr)
        process_returncode = 124
        timeout_events.append(timeout_trace_event(case_id, args.timeout))
    trace_path = trace_path_for_case(args.traces, case_id)
    trace_path.write_text(
        format_trace_output(case, stdout, not args.no_local_hook_context, args.hook_mode, hook_context, timeout_events),
        encoding="utf-8",
    )
    if stderr:
        trace_path.with_suffix(".stderr.txt").write_text(stderr, encoding="utf-8")
    return {
        "id": case_id,
        "command": command,
        "cwd": str(run_cwd),
        "returncode": process_returncode,
        "trace": str(trace_path),
        "stderr": bool(stderr),
        "timed_out": timed_out,
        "hook_mode": args.hook_mode,
        "inject_local_hook_context": args.inject_local_hook_context,
    }


def run_live_cases(cases: list[dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    args.traces.mkdir(parents=True, exist_ok=True)
    ensure_trace_targets_are_writable(args.traces, cases, args.overwrite)
    return [run_case(case, args) for case in cases]


def dry_run_output(cases: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    return {
        "dry_run": True,
        "hook_mode": args.hook_mode,
        "inject_local_hook_context": args.inject_local_hook_context,
        "selected_cases": [case["id"] for case in cases],
        "commands": [
            codex_command(
                args.codex_bin,
                args.codex_args,
                prompt_for_case(
                    case,
                    args,
                    local_hook_context(str(case["prompt"]), args.hook_mode) if args.inject_local_hook_context else None,
                ),
            )
            for case in cases
        ],
    }


def local_hook_context(prompt: str, hook_mode: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise ValueError(f"local hook failed for live eval prompt: {proc.stderr.strip()}")
    data = json.loads(proc.stdout)
    hook_output = data.get("hookSpecificOutput", {})
    if not isinstance(hook_output, dict) or not isinstance(hook_output.get("additionalContext"), str):
        raise ValueError("local hook did not return hookSpecificOutput.additionalContext")
    return context_with_optional_spawn_contract(hook_output["additionalContext"], hook_mode)


def hook_context_event(case: dict[str, Any], hook_mode: str, hook_context: str | None = None) -> dict[str, Any]:
    return {
        "type": "hook.context",
        "source": "local-subagent-orchestration-gate",
        "case_id": case["id"],
        "hook_mode": hook_mode,
        "item": {
            "type": "message",
            "content": [
                {
                    "type": "output_text",
                    "text": hook_context if hook_context is not None else local_hook_context(str(case["prompt"]), hook_mode),
                }
            ],
        },
    }


def format_trace_output(
    case: dict[str, Any],
    stdout: str,
    include_local_hook_context: bool,
    hook_mode: str,
    hook_context: str | None = None,
    extra_events: list[dict[str, Any]] | None = None,
) -> str:
    lines = [json.dumps(hook_context_event(case, hook_mode, hook_context), sort_keys=True)] if include_local_hook_context else []
    if stdout:
        lines.extend(line for line in stdout.splitlines() if line.strip())
    if extra_events:
        lines.extend(json.dumps(event, sort_keys=True) for event in extra_events)
    return "\n".join(lines) + "\n"


def live_result(cases: list[dict[str, Any]], run_results: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
    selected_prompts_path = args.traces / "selected_prompts.jsonl"
    write_jsonl(selected_prompts_path, cases)
    failed_runs = [result for result in run_results if result["returncode"] != 0]
    result: dict[str, Any] = {
        "dry_run": False,
        "hook_mode": args.hook_mode,
        "inject_local_hook_context": args.inject_local_hook_context,
        "selected_cases": [case["id"] for case in cases],
        "summary": {
            "captured": len(run_results),
            "failed_runs": len(failed_runs),
        },
        "runs": run_results,
        "selected_prompts": str(selected_prompts_path),
    }
    if failed_runs or args.no_grade:
        return result

    grade_result = grade(selected_prompts_path, args.traces, profile=args.grade_profile)
    grade_path = args.traces / "grade.json"
    write_json(grade_path, grade_result)
    result["grade"] = grade_result
    result["grade_path"] = str(grade_path)
    return result


def result_exit_code(result: dict[str, Any]) -> int:
    if result.get("dry_run") is True:
        return 0
    summary = result.get("summary", {})
    if isinstance(summary, dict) and summary.get("failed_runs"):
        return 1
    grade_result = result.get("grade")
    if isinstance(grade_result, dict) and grade_result.get("overall_pass") is False:
        return 1
    return 0


def main() -> int:
    args = parse_args()
    try:
        cases = load_jsonl(args.prompts)
        validate_cases(cases)
        cases_to_run = expand_trials(selected_cases(cases, args.case_ids), args.trials)
        if args.dry_run:
            print(json.dumps(dry_run_output(cases_to_run, args), indent=2, sort_keys=True))
            return 0
        result = live_result(cases_to_run, run_live_cases(cases_to_run, args), args)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return result_exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
