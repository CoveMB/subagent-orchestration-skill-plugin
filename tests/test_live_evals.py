#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE_RUNNER = ROOT / "scripts" / "run_live_skill_evals.py"


def write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")


def write_executable_python(path: Path, body_lines: list[str]) -> None:
    path.write_text(
        "\n".join(["#!/usr/bin/env python3", "from __future__ import annotations", *body_lines]) + "\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | 0o111)


def message_event_lines(python_text_expression: str) -> list[str]:
    return [
        "event = {",
        "    'type': 'item.completed',",
        "    'item': {",
        "        'type': 'message',",
        f"        'content': [{{'type': 'output_text', 'text': {python_text_expression}}}],",
        "    },",
        "}",
        "print(json.dumps(event))",
    ]


def write_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "import sys",
        "prompt = sys.argv[-1]",
        "decision = 'single-thread-likely' if 'repository' in prompt else 'single-thread-default'",
        *message_event_lines("f'Subagent orchestration gate\\nResult: {decision}\\nReason: Fake live trace.'"),
    ])


def write_selective_timeout_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "import sys",
        "import time",
        "prompt = sys.argv[-1]",
        "if 'repository' in prompt:",
        "    time.sleep(2)",
        "decision = 'single-thread-likely' if 'repository' in prompt else 'single-thread-default'",
        *message_event_lines("f'Subagent orchestration gate\\nResult: {decision}\\nReason: Fake live trace.'"),
    ])


def write_command_heavy_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "for index in range(6):",
        "    command = f'echo command-{index}'",
        "    event = {'type': 'item.completed', 'item': {'id': f'item_{index}', 'type': 'command_execution', 'command': command}}",
        "    print(json.dumps(event))",
        *message_event_lines("'Subagent orchestration gate\\nResult: single-thread-likely\\nReason: Fake live trace.'"),
    ])


def write_cwd_reporting_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "from pathlib import Path",
        "agents_text = Path('AGENTS.md').read_text(encoding='utf-8') if Path('AGENTS.md').exists() else ''",
        *message_event_lines("'AGENTS:' + agents_text"),
    ])


def write_env_reporting_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "import os",
        "mode = os.environ.get('SUBAGENT_ORCHESTRATION_GATE_MODE', '')",
        *message_event_lines("'HOOK_MODE:' + mode"),
    ])


def write_prompt_reporting_fake_codex(path: Path) -> None:
    write_executable_python(path, [
        "import json",
        "import sys",
        "prompt = sys.argv[-1]",
        *message_event_lines("'PROMPT:' + prompt"),
    ])


def run_live_runner(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(LIVE_RUNNER), *arguments],
        text=True,
        capture_output=True,
        cwd=cwd,
        check=False,
    )


def prompt_rows() -> list[dict[str, object]]:
    return [
        {
            "id": "simple-repository-question",
            "prompt": "What does this repository do?",
            "expected_decision": "single-thread-likely",
            "should_spawn": False,
            "must_not_spawn": True,
            "rubric_ids": ["decision", "no_spawn"],
        },
        {
            "id": "default-changelog-note",
            "prompt": "Draft a short note for the changelog.",
            "expected_decision": "single-thread-default",
            "should_spawn": False,
            "must_not_spawn": True,
            "rubric_ids": ["decision", "no_spawn"],
        },
    ]


def test_live_runner_dry_run_does_not_create_trace_files() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        write_jsonl(prompts, prompt_rows())

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                "fake-codex",
                "--codex-arg=--ignore-user-config",
                "--dry-run",
            ],
            ROOT,
        )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    output = json.loads(proc.stdout)
    assert output["dry_run"] is True
    assert output["selected_cases"] == ["simple-repository-question"]
    assert output["commands"][0][-4:] == ["exec", "--ignore-user-config", "--json", "What does this repository do?"]
    assert not traces.exists()


def test_live_runner_captures_filtered_trace_and_grades_it() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
            ],
            ROOT,
        )

        trace_path = traces / "simple-repository-question.jsonl"
        selected_prompts_path = traces / "selected_prompts.jsonl"
        grade_path = traces / "grade.json"
        trace_exists = trace_path.exists()
        selected_prompts_exists = selected_prompts_path.exists()
        grade_exists = grade_path.exists()
        unselected_trace_exists = (traces / "default-changelog-note.jsonl").exists()

    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result["grade"]["overall_pass"] is True
    assert result["summary"]["captured"] == 1
    assert trace_exists
    assert selected_prompts_exists
    assert grade_exists
    assert not unselected_trace_exists


def test_live_runner_records_timeout_and_continues_following_cases() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_selective_timeout_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--codex-bin",
                str(fake_codex),
                "--timeout",
                "1",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        timeout_trace = (traces / "simple-repository-question.jsonl").read_text(encoding="utf-8")
        following_trace_exists = (traces / "default-changelog-note.jsonl").exists()

    assert proc.returncode == 1, proc.stderr + proc.stdout
    assert result["summary"] == {"captured": 2, "failed_runs": 1}
    assert result["runs"][0]["id"] == "simple-repository-question"
    assert result["runs"][0]["timed_out"] is True
    assert result["runs"][0]["returncode"] == 124
    assert result["runs"][1]["id"] == "default-changelog-note"
    assert result["runs"][1]["returncode"] == 0
    assert following_trace_exists
    assert '"type": "timeout"' in timeout_trace


def test_live_runner_records_local_hook_context_before_live_events() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
            ],
            ROOT,
        )

        trace_rows = [
            json.loads(line)
            for line in (traces / "simple-repository-question.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert trace_rows[0]["type"] == "hook.context"
    assert "Result: single-thread-likely" in json.dumps(trace_rows[0])


def test_live_runner_contract_hook_mode_adds_harness_context_without_child_env() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": "Debug a flaky multi-file auth regression and propose tests.",
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "rubric_ids": ["decision", "spawn"],
                },
            ],
        )
        write_env_reporting_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "parallel-debug",
                "--codex-bin",
                str(fake_codex),
                "--hook-mode",
                "contract",
                "--no-grade",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        trace_text = (traces / "parallel-debug.jsonl").read_text(encoding="utf-8")

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert result["hook_mode"] == "contract"
    assert "Contract mode: live-eval spawn contract." in trace_text
    assert "Why parallel:" in trace_text
    assert "Blockers checked:" in trace_text
    assert "Subagents:" in trace_text
    assert "agent_type: so_mapper" in trace_text
    assert "HOOK_MODE:" in trace_text
    assert "HOOK_MODE:contract" not in trace_text


def test_live_runner_can_inject_contract_hook_context_into_child_prompt() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        original_prompt = "Debug a flaky multi-file auth regression and propose tests."
        write_jsonl(
            prompts,
            [
                {
                    "id": "parallel-debug",
                    "prompt": original_prompt,
                    "expected_decision": "use-subagent-orchestrator",
                    "should_spawn": True,
                    "must_not_spawn": False,
                    "rubric_ids": ["decision", "spawn"],
                },
            ],
        )
        write_prompt_reporting_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "parallel-debug",
                "--codex-bin",
                str(fake_codex),
                "--hook-mode",
                "contract",
                "--inject-local-hook-context",
                "--no-grade",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        trace_text = (traces / "parallel-debug.jsonl").read_text(encoding="utf-8")
        command_prompt = result["runs"][0]["command"][-1]

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert result["inject_local_hook_context"] is True
    assert command_prompt.startswith("Subagent orchestration gate\n")
    assert "Contract mode: live-eval spawn contract." in command_prompt
    assert "Live eval execution limit:" in command_prompt
    assert "Do not run external review services" in command_prompt
    assert "Use exactly one post-spawn wait call" in command_prompt
    assert "Do not perform fallback sequential review after the wait call" in command_prompt
    assert original_prompt in command_prompt
    assert "PROMPT:Subagent orchestration gate" in trace_text


def test_live_runner_does_not_append_execution_limit_in_metadata_mode() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_prompt_reporting_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
                "--no-grade",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        command_prompt = result["runs"][0]["command"][-1]
        trace_text = (traces / "simple-repository-question.jsonl").read_text(encoding="utf-8")

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Live eval execution limit:" not in command_prompt
    assert "Live eval execution limit:" not in trace_text


def test_live_runner_adds_non_spawn_contract_case_limit() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(
            prompts,
            [
                {
                    "id": "conditional-review",
                    "prompt": "Review the branch. No subagent orchestration unless useful.",
                    "expected_decision": "orchestration-check",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision", "no_spawn", "conditional_boundary"],
                },
            ],
        )
        write_prompt_reporting_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "conditional-review",
                "--codex-bin",
                str(fake_codex),
                "--hook-mode",
                "contract",
                "--inject-local-hook-context",
                "--no-grade",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        command_prompt = result["runs"][0]["command"][-1]

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "Non-spawn live eval case:" in command_prompt
    assert "Do not perform the underlying branch review, audit, debug, or documentation sweep." in command_prompt


def test_live_runner_can_capture_without_injected_local_hook_context() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
                "--no-local-hook-context",
            ],
            ROOT,
        )

        trace_rows = [
            json.loads(line)
            for line in (traces / "simple-repository-question.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert trace_rows[0]["type"] == "item.completed"
    assert all(row["type"] != "hook.context" for row in trace_rows)


def test_live_runner_uses_live_grading_profile_for_command_budget() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_command_heavy_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
            ],
            ROOT,
        )

    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result["grade"]["overall_pass"] is True
    assert result["grade"]["cases"][0]["command_count"] == 6
    assert result["grade"]["cases"][0]["checks"]["command_budget"] is True


def test_live_runner_expands_trials_into_independent_traces() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(prompts, prompt_rows())
        write_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                str(fake_codex),
                "--trials",
                "2",
            ],
            ROOT,
        )

        trace_paths = sorted(path.name for path in traces.glob("simple-repository-question__trial_*.jsonl"))
        selected_rows = [
            json.loads(line)
            for line in (traces / "selected_prompts.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    assert proc.returncode == 0, proc.stderr + proc.stdout
    result = json.loads(proc.stdout)
    assert result["summary"]["captured"] == 2
    assert trace_paths == ["simple-repository-question__trial_1.jsonl", "simple-repository-question__trial_2.jsonl"]
    assert [row["id"] for row in selected_rows] == ["simple-repository-question__trial_1", "simple-repository-question__trial_2"]


def test_live_runner_rejects_non_positive_trial_count() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        write_jsonl(prompts, prompt_rows())

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--trials",
                "0",
                "--dry-run",
            ],
            ROOT,
        )

    assert proc.returncode == 2
    assert "--trials must be a positive integer" in proc.stderr


def test_live_runner_materializes_host_rules_fixture_in_isolated_case_workspace() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        fake_codex = root / "fake_codex.py"
        write_jsonl(
            prompts,
            [
                {
                    "id": "host-rules-review",
                    "prompt": "Review the branch while respecting host repository rules.",
                    "expected_decision": "orchestration-check",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "host_rules_fixture": "Host rules prohibit GitHub review-comment responses.",
                    "rubric_ids": ["decision", "host_rules"],
                },
            ],
        )
        write_cwd_reporting_fake_codex(fake_codex)

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "host-rules-review",
                "--codex-bin",
                str(fake_codex),
                "--no-grade",
            ],
            ROOT,
        )

        result = json.loads(proc.stdout)
        run_cwd = Path(result["runs"][0]["cwd"])
        trace_text = (traces / "host-rules-review.jsonl").read_text(encoding="utf-8")
        agents_text = (run_cwd / "AGENTS.md").read_text(encoding="utf-8")

    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert run_cwd.parent.name == "workspaces"
    assert run_cwd != ROOT
    assert agents_text == "Host rules prohibit GitHub review-comment responses.\n"
    assert "Host rules prohibit GitHub review-comment responses." in trace_text


def test_live_runner_rejects_unknown_case_filter() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        write_jsonl(prompts, prompt_rows())

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "missing-case",
            ],
            ROOT,
        )

    assert proc.returncode == 2
    assert "unknown case id: missing-case" in proc.stderr


def test_live_runner_rejects_path_like_case_ids() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        write_jsonl(
            prompts,
            [
                {
                    "id": "nested/case",
                    "prompt": "What does this repository do?",
                    "expected_decision": "single-thread-likely",
                    "should_spawn": False,
                    "must_not_spawn": True,
                    "rubric_ids": ["decision", "no_spawn"],
                },
            ],
        )

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "nested/case",
            ],
            ROOT,
        )

    assert proc.returncode == 2
    assert "id must be a safe trace filename" in proc.stderr


def test_live_runner_refuses_existing_outputs_without_overwrite() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        prompts = root / "prompts.jsonl"
        traces = root / "traces"
        traces.mkdir()
        (traces / "selected_prompts.jsonl").write_text("{}\n", encoding="utf-8")
        write_jsonl(prompts, prompt_rows())

        proc = run_live_runner(
            [
                "--prompts",
                str(prompts),
                "--traces",
                str(traces),
                "--case",
                "simple-repository-question",
                "--codex-bin",
                "fake-codex",
            ],
            ROOT,
        )

    assert proc.returncode == 2
    assert "pass --overwrite" in proc.stderr


def test_live_runner_source_does_not_use_shell_execution() -> None:
    text = LIVE_RUNNER.read_text(encoding="utf-8")
    assert "shell=True" not in text
    assert "os.system" not in text
    assert "stdin=subprocess.DEVNULL" in text


def run_all_tests() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


def main() -> int:
    run_all_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
