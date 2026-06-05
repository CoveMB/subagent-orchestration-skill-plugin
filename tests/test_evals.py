#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVALS_ROOT = ROOT / "evals"
PROMPTS = EVALS_ROOT / "skill_prompts.jsonl"
GRADER = ROOT / "scripts" / "grade_skill_traces.py"
HOOK = ROOT / "hooks" / "subagent_orchestration_gate.py"
EXPECTED_DECISIONS = {
    "single-thread-default",
    "single-thread-likely",
    "orchestration-check",
    "use-subagent-orchestrator",
    "orchestration-opt-out",
    "recursion-guard",
}
REQUIRED_PROMPT_KEYS = {
    "id",
    "prompt",
    "expected_decision",
    "should_spawn",
    "must_not_spawn",
    "rubric_ids",
}
ALLOWED_PROMPT_KEYS = REQUIRED_PROMPT_KEYS | {
    "forbid_duplicate_spawn_agents",
    "forbidden_command_terms",
    "forbidden_spawn_agents",
    "host_rules_fixture",
    "max_command_count",
    "max_spawn_count",
    "expected_spawn_agents",
    "forbidden_tool_names",
    "requires_wait",
    "required_pre_spawn_text_terms",
    "required_spawn_prompt_text_terms",
    "required_final_text_terms",
}


def load_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def run_grader(prompts: Path, traces: Path, extra_args: list[str] | None = None) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(GRADER), "--prompts", str(prompts), "--traces", str(traces), *(extra_args or [])],
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode in {0, 1}, proc.stderr + proc.stdout
    return json.loads(proc.stdout)


def run_grader_process(prompts: Path, traces: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(GRADER), "--prompts", str(prompts), "--traces", str(traces)],
        text=True,
        capture_output=True,
        check=False,
    )


def run_hook(prompt: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    return data["hookSpecificOutput"]["additionalContext"]


def hook_decision(prompt: str) -> str:
    context = run_hook(prompt)
    for line in context.splitlines():
        if line.startswith("Result: "):
            return line.removeprefix("Result: ")
    raise AssertionError(context)


def message_event(text: str) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "message",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def spawn_boundary_event() -> dict[str, object]:
    return message_event(
        "Subagent orchestration gate\n"
        "Result: use-subagent-orchestrator\n"
        "Reason: Strong orchestration signals detected.\n\n"
        "Why parallel:\n"
        "- independent track 1: map targeted eval work and return evidence\n"
        "- independent track 2: identify targeted verification and return commands\n"
        "Blockers checked: explicit user opt-out, child-agent recursion, strict sequential dependencies, conflicting writes, workspace-write dirty-state isolation, external side effects, privacy/tool limits, unbounded broad agent tasks.\nRead-only agents must report whether findings depend on uncommitted changes.\n"
        "Subagents:\n"
        "- so_mapper\n"
        "  mode: read-only\n"
        "  scope: targeted eval work\n"
        "  expected output: evidence\n"
        "  constraints: no recursive fan-out"
    )


def spawn_prompt(agent_type: str) -> str:
    return (
        f"agent_type: {agent_type}\n"
        "mode: read-only\n"
        "scope: targeted eval work\n"
        "expected output: evidence, uncertainty, and report whether findings depend on uncommitted changes\n"
        "constraints: no recursive fan-out"
    )


def spawn_event(agent_type: str = "so_mapper") -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "type": "function_call",
            "name": "spawn_agent",
            "arguments": {
                "agent_type": agent_type,
                "message": spawn_prompt(agent_type),
            },
        },
    }


def bare_spawn_event(agent_type: str = "so_mapper") -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "type": "function_call",
            "name": "spawn_agent",
            "arguments": {"agent_type": agent_type},
        },
    }


def wait_event() -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "type": "function_call",
            "name": "wait_agent",
        },
    }


def collab_spawn_event(agent_type: str = "so_mapper") -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "id": "item_spawn",
            "type": "collab_tool_call",
            "tool": "spawn_agent",
            "prompt": spawn_prompt(agent_type),
        },
    }


def collab_wait_event() -> dict[str, object]:
    return {
        "type": "item.started",
        "item": {
            "id": "item_wait",
            "type": "collab_tool_call",
            "tool": "wait",
        },
    }


def command_event(command: object) -> dict[str, object]:
    return {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": command,
        },
    }


def forbidden_command_base_message(expected_decision: str = "orchestration-check") -> dict[str, object]:
    reason = "Strong orchestration signals detected." if expected_decision == "use-subagent-orchestrator" else "Moderate orchestration signals detected."
    return message_event(f"Subagent orchestration gate\nResult: {expected_decision}\nReason: {reason}")


def forbidden_command_prompt_row(
    *,
    expected_decision: str = "orchestration-check",
    should_spawn: bool = False,
    must_not_spawn: bool = True,
    rubric_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "id": "host-rules-review",
        "prompt": "Review branch without responding to GitHub comments.",
        "expected_decision": expected_decision,
        "should_spawn": should_spawn,
        "must_not_spawn": must_not_spawn,
        "forbidden_command_terms": ["gh pr comment"],
        "rubric_ids": rubric_ids or ["decision", "no_external_side_effects"],
    }


def run_forbidden_command_case(
    events: list[dict[str, object]],
    *,
    expected_decision: str = "orchestration-check",
    should_spawn: bool = False,
    must_not_spawn: bool = True,
    rubric_ids: list[str] | None = None,
) -> dict[str, object]:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                forbidden_command_prompt_row(
                    expected_decision=expected_decision,
                    should_spawn=should_spawn,
                    must_not_spawn=must_not_spawn,
                    rubric_ids=rubric_ids,
                ),
            ],
        )
        write_jsonl(traces / "host-rules-review.jsonl", events)

        return run_grader(prompts, traces)


def assert_forbidden_command_check(
    events: list[dict[str, object]],
    expected: bool,
    *,
    expected_decision: str = "orchestration-check",
    should_spawn: bool = False,
    must_not_spawn: bool = True,
    rubric_ids: list[str] | None = None,
) -> None:
    result = run_forbidden_command_case(
        events,
        expected_decision=expected_decision,
        should_spawn=should_spawn,
        must_not_spawn=must_not_spawn,
        rubric_ids=rubric_ids,
    )

    assert result["overall_pass"] is expected, events
    assert result["cases"][0]["checks"]["forbidden_commands"] is expected, events


def test_eval_prompt_set_is_balanced_and_explicit() -> None:
    cases = load_jsonl(PROMPTS)
    ids = [case["id"] for case in cases]
    decisions = {case["expected_decision"] for case in cases}

    assert len(cases) >= 12
    assert len(ids) == len(set(ids))
    assert EXPECTED_DECISIONS <= decisions
    assert any(case.get("should_spawn") is True for case in cases)
    assert any(case.get("must_not_spawn") is True for case in cases)
    assert any(case.get("host_rules_fixture") for case in cases)

    for case in cases:
        unknown_keys = set(case) - ALLOWED_PROMPT_KEYS
        missing_keys = REQUIRED_PROMPT_KEYS - set(case)
        assert not unknown_keys, case
        assert not missing_keys, case
        assert isinstance(case.get("prompt"), str) and case["prompt"].strip(), case
        assert case["expected_decision"] in EXPECTED_DECISIONS, case
        assert isinstance(case.get("should_spawn"), bool), case
        assert isinstance(case.get("must_not_spawn"), bool), case
        assert not (case["should_spawn"] and case["must_not_spawn"]), case
        assert isinstance(case.get("rubric_ids"), list) and case["rubric_ids"], case
        assert all(isinstance(rubric_id, str) and rubric_id for rubric_id in case["rubric_ids"]), case


def test_moderate_patch_review_prompt_is_bounded_for_live_eval() -> None:
    case = next(case for case in load_jsonl(PROMPTS) if case["id"] == "moderate-patch-review")
    prompt = str(case["prompt"]).lower()

    assert "patch summary" in prompt
    assert "obvious correctness risks" in prompt
    assert "do not inspect the repository" in prompt
    assert "run commands" in prompt


def test_grader_rejects_invalid_prompt_rows() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "broken",
                    "prompt": "Debug this failure.",
                    "expected_decision": "not-a-decision",
                    "should_spawn": True,
                    "must_not_spawn": True,
                    "max_command_count": True,
                    "rubric_ids": ["decision"],
                    "extra": "ignored today",
                },
            ],
        )

        proc = run_grader_process(prompts, traces)

    assert proc.returncode == 2
    assert "invalid prompt corpus" in proc.stderr
    assert "unknown keys" in proc.stderr
    assert "invalid expected_decision" in proc.stderr
    assert "max_command_count must be a non-negative integer" in proc.stderr
    assert "should_spawn and must_not_spawn cannot both be true" in proc.stderr


def test_grader_rejects_path_like_prompt_ids() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "../outside",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision"],
                },
            ],
        )

        proc = run_grader_process(prompts, traces)

    assert proc.returncode == 2
    assert "id must be a safe trace filename" in proc.stderr


def test_grader_rejects_unknown_or_unbacked_rubrics() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "broken",
                    "prompt": "Review this patch.",
                    "expected_decision": "orchestration-check",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["unknown", "efficiency", "spawn"],
                },
            ],
        )

        proc = run_grader_process(prompts, traces)

    assert proc.returncode == 2
    assert "unknown rubric_ids: unknown" in proc.stderr
    assert "efficiency rubric requires max_command_count" in proc.stderr
    assert "spawn rubric requires should_spawn=true" in proc.stderr


def test_parallel_eval_cases_define_trace_contracts() -> None:
    for case in load_jsonl(PROMPTS):
        if case.get("should_spawn") is not True:
            continue
        assert isinstance(case.get("expected_spawn_agents"), list) and case["expected_spawn_agents"], case
        assert case.get("requires_wait") is True, case
        assert case.get("forbid_duplicate_spawn_agents") is True, case
        if "required_pre_spawn_text_terms" in case:
            assert "workspace-write dirty-state isolation" in case["required_pre_spawn_text_terms"], case
            assert "report whether findings depend on uncommitted changes" in case["required_pre_spawn_text_terms"], case
        assert isinstance(case.get("required_final_text_terms"), list), case
        assert "Synthesis:" in case["required_final_text_terms"], case


def test_eval_prompt_set_covers_dirty_repo_read_only_and_write_isolation_boundaries() -> None:
    cases = {case["id"]: case for case in load_jsonl(PROMPTS)}
    dirty_read_only_case = cases["dirty-repo-read-only-branch-review"]
    dirty_write_case = cases["dirty-repo-write-agent-unclear-isolation"]

    assert dirty_read_only_case["expected_decision"] == "use-subagent-orchestrator"
    assert dirty_read_only_case["should_spawn"] is True
    assert dirty_read_only_case["must_not_spawn"] is False
    assert dirty_read_only_case["expected_spawn_agents"] == ["so_mapper", "so_reviewer", "so_tester"]
    assert {"so_implementer", "so_reproducer"} <= set(dirty_read_only_case["forbidden_spawn_agents"])
    assert "report whether findings depend on uncommitted changes" in dirty_read_only_case["required_pre_spawn_text_terms"]

    assert dirty_write_case["expected_decision"] == "use-subagent-orchestrator"
    assert dirty_write_case["should_spawn"] is True
    assert dirty_write_case["must_not_spawn"] is False
    assert dirty_write_case["expected_spawn_agents"] == ["so_mapper", "so_reviewer"]
    assert {"so_implementer", "so_reproducer"} <= set(dirty_write_case["forbidden_spawn_agents"])
    assert "workspace-write dirty-state isolation" in dirty_write_case["required_pre_spawn_text_terms"]


def test_eval_prompt_set_covers_conditional_and_review_boundaries() -> None:
    cases = {case["id"]: case for case in load_jsonl(PROMPTS)}

    conditional_only_ids = [
        "conditional-use-subagents-if-helpful",
        "conditional-agents-where-appropriate",
        "conditional-orchestrate-as-needed",
        "conditional-spawn-only-if-useful",
        "conditional-branch-review",
    ]
    for case_id in conditional_only_ids:
        case = cases[case_id]
        assert case["expected_decision"] == "orchestration-check"
        assert case["should_spawn"] is False
        assert case["must_not_spawn"] is True

    conditional_broad_ids = [
        "conditional-helpful-api-worker-database-ci",
        "conditional-appropriate-security-surface-audit",
    ]
    for case_id in conditional_broad_ids:
        case = cases[case_id]
        assert case["expected_decision"] == "use-subagent-orchestrator"
        assert case["should_spawn"] is True
        assert case["must_not_spawn"] is False
        assert "so_implementer" in case.get("forbidden_spawn_agents", [])

    opt_out_case = cases["hard-opt-out-even-if-helpful-review"]
    assert opt_out_case["expected_decision"] == "orchestration-opt-out"
    assert opt_out_case["should_spawn"] is False
    assert opt_out_case["must_not_spawn"] is True

    pr_diff_case = cases["pr-diff-security-test-review"]
    assert pr_diff_case["expected_decision"] == "use-subagent-orchestrator"
    assert pr_diff_case["expected_spawn_agents"] == ["so_mapper", "so_reviewer", "so_tester"]
    assert "so_implementer" in pr_diff_case["forbidden_spawn_agents"]


def test_fragile_spawn_cases_require_case_specific_spawn_prompt_terms() -> None:
    cases = {case["id"]: case for case in load_jsonl(PROMPTS)}
    expected_terms_by_case_id = {
        "conditional-helpful-api-worker-database-ci": [
            "api",
            "worker",
            "database",
            "flaky ci",
        ],
        "conditional-appropriate-security-surface-audit": [
            "authentication",
            "api routes",
            "database access",
            "security",
        ],
        "pr-diff-security-test-review": [
            "pr diff",
            "authentication",
            "api route",
            "missing tests",
        ],
        "dirty-repo-read-only-branch-review": [
            "dirty branch",
            "uncommitted changes",
            "read-only",
            "missing tests",
        ],
        "dirty-repo-write-agent-unclear-isolation": [
            "dirty-state",
            "write-isolation",
            "authentication",
            "api route",
        ],
    }

    for case_id, expected_terms in expected_terms_by_case_id.items():
        case = cases[case_id]
        required_terms = case.get("required_spawn_prompt_text_terms")
        assert isinstance(required_terms, list) and required_terms, case
        for expected_term in expected_terms:
            assert expected_term in required_terms, case


def test_eval_prompt_set_matches_hook_classifier() -> None:
    for case in load_jsonl(PROMPTS):
        assert hook_decision(str(case["prompt"])) == case["expected_decision"], case


def test_eval_grader_scores_synthetic_traces() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn"],
                },
                {
                    "id": "simple-rename",
                    "prompt": "Rename a local variable in one file.",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision", "no_spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                spawn_boundary_event(),
                spawn_event(),
                wait_event(),
                message_event("Synthesis:\n- Tests/verification: run targeted checks."),
            ],
        )
        write_jsonl(
            traces / "simple-rename.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: single-thread-likely\nReason: Simple-task signals detected."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["summary"] == {"passed": 2, "failed": 0, "missing": 0}


def test_eval_grader_scores_live_collab_tool_trace_shape() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                spawn_boundary_event(),
                collab_spawn_event("so_mapper"),
                collab_wait_event(),
                message_event("Synthesis:\nTests/verification: checked live trace parsing."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["spawn_count"] == 1
    assert result["cases"][0]["spawned_agents"] == ["so_mapper"]


def test_eval_grader_fails_spawn_without_required_prompt_text() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                spawn_boundary_event(),
                bare_spawn_event("so_mapper"),
                wait_event(),
                message_event("Synthesis:\nTests/verification: checked prompt text enforcement."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_spawn_prompt_text"] is False


def test_eval_grader_accepts_custom_required_spawn_prompt_text_terms() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "required_spawn_prompt_text_terms": ["custom-required-marker"],
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait"],
                },
            ],
        )
        custom_spawn = spawn_event("so_mapper")
        custom_spawn["item"]["arguments"]["message"] += "\ncustom-required-marker"
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                spawn_boundary_event(),
                custom_spawn,
                wait_event(),
                message_event("Synthesis:\nTests/verification: checked prompt text override."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["checks"]["required_spawn_prompt_text"] is True


def test_eval_grader_counts_live_collab_start_and_completion_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "max_spawn_count": 1,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait"],
                },
            ],
        )
        started_spawn = collab_spawn_event("so_mapper")
        completed_spawn = json.loads(json.dumps(started_spawn))
        completed_spawn["type"] = "item.completed"
        started_wait = collab_wait_event()
        completed_wait = json.loads(json.dumps(started_wait))
        completed_wait["type"] = "item.completed"
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                spawn_boundary_event(),
                started_spawn,
                completed_spawn,
                started_wait,
                completed_wait,
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["spawn_count"] == 1


def test_eval_grader_applies_default_spawn_boundary_terms_for_spawn_cases() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "rubric_ids": ["decision", "spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_pre_spawn_text"] is False


def test_eval_grader_default_spawn_boundary_requires_parallel_proof_terms() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "rubric_ids": ["decision", "spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event(
                    "Subagent orchestration gate\n"
                    "Result: use-subagent-orchestrator\n"
                    "Reason: Strong orchestration signals detected.\n\n"
                    "Subagents:\n"
                    "- so_mapper\n"
                    "  mode: read-only\n"
                    "  scope: map files\n"
                    "  expected output: evidence\n"
                    "  constraints: no recursive fan-out"
                ),
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_pre_spawn_text"] is False


def test_eval_grader_fails_missing_required_wait_after_spawn() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "requires_wait": True,
                    "rubric_ids": ["decision", "spawn", "wait"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["wait_required"] is False


def test_eval_grader_requires_wait_after_final_spawn() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper", "so_tester"],
                    "requires_wait": True,
                    "rubric_ids": ["decision", "spawn", "wait"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
                wait_event(),
                spawn_event("so_tester"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["wait_required"] is False


def test_eval_grader_fails_missing_expected_spawn_agent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper", "so_tester"],
                    "rubric_ids": ["decision", "spawn_agents"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["expected_spawn_agents"] is False


def test_eval_grader_does_not_count_agent_names_outside_spawn_arguments() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_tester"],
                    "rubric_ids": ["decision", "spawn_agents"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                {
                    "type": "item.started",
                    "item": {
                        "type": "function_call",
                        "name": "spawn_agent",
                        "arguments": {"agent_type": "so_mapper", "notes": "ask so_tester later"},
                    },
                },
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["spawned_agents"] == ["so_mapper"]
    assert result["cases"][0]["checks"]["expected_spawn_agents"] is False


def test_eval_grader_counts_only_explicit_agent_type_label_in_collab_spawn_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_tester"],
                    "rubric_ids": ["decision", "spawn_agents"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                {
                    "type": "item.started",
                    "item": {
                        "id": "item_spawn",
                        "type": "collab_tool_call",
                        "tool": "spawn_agent",
                        "prompt": "agent_type: so_mapper\nCoordinate with so_tester if useful.",
                    },
                },
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["spawned_agents"] == ["so_mapper"]
    assert result["cases"][0]["checks"]["expected_spawn_agents"] is False


def test_eval_grader_does_not_use_hook_context_for_required_pre_spawn_text() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "required_pre_spawn_text_terms": ["Subagents:", "mode:", "scope:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "spawn_boundaries"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                {
                    "type": "hook.context",
                    "item": {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected.\n\nSubagents:\nmode:\nscope:"}],
                    },
                },
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_pre_spawn_text"] is False


def test_eval_grader_does_not_use_hook_context_for_required_final_text() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "synthesis", "tests"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                {
                    "type": "hook.context",
                    "item": {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected.\n\nSynthesis:\nTests/verification:"}],
                    },
                },
                spawn_event("so_mapper"),
                wait_event(),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_final_text"] is False


def test_eval_grader_uses_hook_context_for_decision_metadata() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "moderate-review",
                    "prompt": "Review this patch summary for obvious correctness risks only.",
                    "expected_decision": "orchestration-check",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision", "no_spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "moderate-review.jsonl",
            [
                {
                    "type": "hook.context",
                    "item": {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Subagent orchestration gate\nResult: orchestration-check\nReason: Moderate orchestration signals detected."}],
                    },
                },
                message_event("Findings:\n- No obvious issue from the bounded summary."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["decision"] == "orchestration-check"
    assert result["cases"][0]["checks"]["decision"] is True


def test_eval_grader_fails_timeout_trace_event() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "simple-question",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision", "no_spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "simple-question.jsonl",
            [
                {
                    "type": "hook.context",
                    "item": {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Subagent orchestration gate\nResult: single-thread-likely\nReason: Simple-task signals detected."}],
                    },
                },
                {"type": "timeout", "case_id": "simple-question", "timeout_seconds": 1},
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["no_timeout"] is False


def test_eval_grader_fails_forbidden_spawn_agent() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "host-rules-review",
                    "prompt": "Review branch with read-only subagents.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "forbidden_spawn_agents": ["so_implementer"],
                    "rubric_ids": ["decision", "spawn", "host_rules"],
                },
            ],
        )
        write_jsonl(
            traces / "host-rules-review.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_implementer"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["forbidden_spawn_agents"] is False


def test_eval_grader_allows_duplicate_spawn_roles_when_not_required() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper", "so_tester"],
                    "max_spawn_count": 4,
                    "requires_wait": True,
                    "required_pre_spawn_text_terms": ["Subagent orchestration gate", "Result: use-subagent-orchestrator", "Reason:", "Subagents:", "mode:", "scope:", "expected output:", "no recursive fan-out"],
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait", "synthesis", "tests"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event(
                    "Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected.\n\nSubagents:\n- so_mapper\n  mode: read-only\n  scope: map files\n  expected output: evidence\n  constraints: no recursive fan-out\n- so_tester\n  mode: read-only\n  scope: identify tests\n  expected output: commands\n  constraints: no recursive fan-out"
                ),
                spawn_event("so_mapper"),
                spawn_event("so_mapper"),
                spawn_event("so_tester"),
                wait_event(),
                message_event("Synthesis:\n- mapped duplicate spawn regression.\nTests/verification:\n- run grader."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["checks"]["unique_spawn_agents"] is True


def test_eval_grader_fails_duplicate_spawn_agent_role() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper", "so_tester"],
                    "forbid_duplicate_spawn_agents": True,
                    "max_spawn_count": 4,
                    "requires_wait": True,
                    "required_pre_spawn_text_terms": ["Subagent orchestration gate", "Result: use-subagent-orchestrator", "Reason:", "Subagents:", "mode:", "scope:", "expected output:", "no recursive fan-out"],
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "spawn", "spawn_agents", "wait", "synthesis", "tests"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event(
                    "Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected.\n\nSubagents:\n- so_mapper\n  mode: read-only\n  scope: map files\n  expected output: evidence\n  constraints: no recursive fan-out\n- so_tester\n  mode: read-only\n  scope: identify tests\n  expected output: commands\n  constraints: no recursive fan-out"
                ),
                spawn_event("so_mapper"),
                spawn_event("so_mapper"),
                spawn_event("so_tester"),
                wait_event(),
                message_event("Synthesis:\n- mapped duplicate spawn regression.\nTests/verification:\n- run grader."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["unique_spawn_agents"] is False


def test_eval_grader_fails_missing_required_final_text() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "required_final_text_terms": ["Synthesis:", "Tests/verification:"],
                    "rubric_ids": ["decision", "synthesis"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
                wait_event(),
                message_event("Done."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_final_text"] is False


def test_eval_grader_ignores_decision_like_text_outside_messages() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "simple-question",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision"],
                },
            ],
        )
        write_jsonl(
            traces / "simple-question.jsonl",
            [
                command_event("printf 'Result: single-thread-likely'"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["decision"] is None
    assert result["cases"][0]["checks"]["decision"] is False


def test_eval_grader_requires_parallel_spawn_boundaries_before_spawning() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "expected_spawn_agents": ["so_mapper"],
                    "required_pre_spawn_text_terms": ["Subagents:", "mode:", "scope:", "expected output:", "no recursive fan-out"],
                    "rubric_ids": ["decision", "spawn_boundaries"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
                spawn_event("so_mapper"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["required_pre_spawn_text"] is False


def test_eval_grader_scores_realistic_fixture_traces() -> None:
    result = run_grader(PROMPTS, EVALS_ROOT / "trace_fixtures" / "pass")
    assert result["overall_pass"] is True


def test_eval_grader_fails_realistic_negative_fixture_traces() -> None:
    result = run_grader(PROMPTS, EVALS_ROOT / "trace_fixtures" / "fail")
    assert result["overall_pass"] is False
    failed_cases = {case["id"]: case for case in result["cases"] if case["passed"] is False}
    assert failed_cases["parallel-auth-debug"]["checks"]["wait_required"] is False
    assert failed_cases["parallel-security-architecture"]["checks"]["expected_spawn_agents"] is False
    assert failed_cases["host-rules-branch-review"]["checks"]["forbidden_commands"] is False


def test_trace_eval_schema_describes_grader_output() -> None:
    schema = json.loads((EVALS_ROOT / "trace_eval.schema.json").read_text(encoding="utf-8"))
    case_schema = schema["properties"]["cases"]["items"]
    case_properties = case_schema["properties"]
    check_properties = case_properties["checks"]["properties"]

    assert "command_count" in case_properties
    assert "spawn_count" in case_properties
    assert "spawned_agents" in case_properties
    assert case_properties["checks"]["additionalProperties"] is False
    for check_name in [
        "trace_exists",
        "no_timeout",
        "decision",
        "spawn_required",
        "no_unwanted_spawn",
        "expected_spawn_agents",
        "unique_spawn_agents",
        "forbidden_spawn_agents",
        "forbidden_tool_names",
        "forbidden_commands",
        "command_budget",
        "spawn_budget",
        "wait_required",
        "required_pre_spawn_text",
        "required_spawn_prompt_text",
        "required_final_text",
    ]:
        assert check_properties[check_name]["type"] == "boolean"


def test_eval_grader_fails_missing_required_spawn() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky regression across API and web tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "rubric_ids": ["decision", "spawn"],
                },
            ],
        )
        write_jsonl(
            traces / "parallel-debug.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: use-subagent-orchestrator\nReason: Strong orchestration signals detected."),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["summary"] == {"passed": 0, "failed": 1, "missing": 0}
    assert result["cases"][0]["checks"]["spawn_required"] is False


def test_eval_grader_fails_forbidden_external_command_variants() -> None:
    commands = [
        "gh pr comment 123 --body reviewed",
        ["gh", "pr", "comment", "123", "--body", "reviewed"],
        "/bin/zsh -lc 'gh pr comment 123 --body reviewed'",
        "env GH_TOKEN=redacted gh pr comment 123 --body reviewed",
        "cd repo && GH_TOKEN=redacted gh pr comment 123 --body reviewed",
        "python3 -c \"import subprocess; subprocess.run(['gh', 'pr', 'comment', '123'])\"",
        "python3 -c \"from subprocess import Popen; Popen(['gh', 'pr', 'comment', '123'])\"",
        "python3 -c \"import os; os.system('gh pr comment 123 --body reviewed')\"",
        "python3 -c 'print(1)' ; gh pr comment 123 --body reviewed",
    ]
    for command in commands:
        assert_forbidden_command_check(
            [
                forbidden_command_base_message(),
                command_event(command),
            ],
            False,
        )


def test_eval_grader_does_not_flag_forbidden_command_mentioned_in_spawn_prompt() -> None:
    assert_forbidden_command_check(
        [
            spawn_boundary_event(),
            {
                "type": "item.started",
                "item": {
                    "type": "collab_tool_call",
                    "tool": "spawn_agent",
                    "prompt": (
                        spawn_prompt("so_reviewer")
                        + "\nDo not run gh pr comment or other external side-effect commands."
                    ),
                },
            },
        ],
        True,
        expected_decision="use-subagent-orchestrator",
        should_spawn=True,
        must_not_spawn=False,
        rubric_ids=["decision", "spawn", "no_external_side_effects"],
    )


def test_eval_grader_does_not_flag_forbidden_command_inside_python_literal_variants() -> None:
    live_style_command = (
        r"""/bin/zsh -lc "PYTHONDONTWRITEBYTECODE=1 python3 -c 'from scripts.grade_skill_traces import command_contains_forbidden_term; """
        r"""cases=[\"python3 -c \\'print(1)\\' ; gh pr comment 123 --body reviewed\", """
        r"""\"/bin/zsh -lc \\\"python3 -c \\'print(1)\\' ; gh pr comment 123 --body reviewed\\\"\"]; """
        r'''[print(case, \"=>\", command_contains_forbidden_term(case, \"gh pr comment\")) for case in cases]'"'''
    )
    commands = [
        "python3 -c 'print(\"gh pr comment should be forbidden when executed, not when tested\")'",
        "env PYTHONDONTWRITEBYTECODE=1 python3 -c 'print(\"gh pr comment appears only as fixture text\")'",
        live_style_command,
        "/bin/zsh -lc \"PYTHONDONTWRITEBYTECODE=1 python3 -c \\\"tests=['gh pr comment 1','if gh pr comment 1; then echo ok; fi','bash -lc \\\\\\\"if gh pr comment 1; then echo ok; fi\\\\\\\"','cd x && gh pr comment 1']; print(tests)\\\"\"",
    ]
    for command in commands:
        assert_forbidden_command_check(
            [
                forbidden_command_base_message(),
                command_event(command),
            ],
            True,
        )


def test_eval_grader_fails_command_budget_regression() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "simple-question",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "max_command_count": 0,
                    "rubric_ids": ["decision", "efficiency"],
                },
            ],
        )
        write_jsonl(
            traces / "simple-question.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: single-thread-likely\nReason: Simple-task signals detected."),
                command_event("rg --files"),
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is False
    assert result["cases"][0]["checks"]["command_budget"] is False


def test_eval_grader_counts_command_start_and_completion_once() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "simple-question",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "max_command_count": 1,
                    "rubric_ids": ["decision", "efficiency"],
                },
            ],
        )
        write_jsonl(
            traces / "simple-question.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: single-thread-likely\nReason: Simple-task signals detected."),
                {
                    "type": "item.started",
                    "item": {"id": "item_1", "type": "command_execution", "command": "rg --files"},
                },
                {
                    "type": "item.completed",
                    "item": {"id": "item_1", "type": "command_execution", "command": "rg --files"},
                },
            ],
        )

        result = run_grader(prompts, traces)

    assert result["overall_pass"] is True
    assert result["cases"][0]["checks"]["command_budget"] is True
    assert result["cases"][0]["command_count"] == 1


def test_eval_grader_live_profile_records_but_does_not_enforce_command_budget() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        write_jsonl(
            prompts,
            [
                {
                    "id": "simple-question",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "max_command_count": 0,
                    "rubric_ids": ["decision", "efficiency"],
                },
            ],
        )
        write_jsonl(
            traces / "simple-question.jsonl",
            [
                message_event("Subagent orchestration gate\nResult: single-thread-likely\nReason: Simple-task signals detected."),
                command_event("rg --files"),
            ],
        )

        result = run_grader(prompts, traces, ["--profile", "live"])

    assert result["overall_pass"] is True
    assert result["cases"][0]["checks"]["command_budget"] is True
    assert result["cases"][0]["command_count"] == 1


def test_eval_grader_is_offline_and_does_not_execute_agent_runs() -> None:
    text = GRADER.read_text(encoding="utf-8")
    assert "import subprocess" not in text
    assert "subprocess.run(" not in text
    assert "codex exec" not in text


def run_all_tests() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


def main() -> int:
    run_all_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
