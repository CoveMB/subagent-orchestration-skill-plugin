#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "subagent_orchestration_gate.py"
INSTALLER = ROOT / "scripts" / "install_user.py"
UNINSTALLER = ROOT / "scripts" / "uninstall_user.py"
HOOK_MODE_ENV = "SUBAGENT_ORCHESTRATION_GATE_MODE"
BOUNDARY_SENTENCE = (
    "This orchestration gate only affects execution shape; it does not override repository source-of-truth, "
    "citation, manuscript, safety, privacy, vendor, approval, or testing rules."
)
USER_PROMPT_SUBMIT_OUTPUT_KEYS = {"hookEventName", "additionalContext"}
CUSTOM_AGENT_SHARED_SAFETY_LINES = (
    "Host repository rules win. Your output is work product, not evidence by itself.",
    "Do not invent facts, sources, citekeys, page numbers, quotations, studies, or bibliographic metadata.",
    "Do not bypass required checks, approvals, scripts, or audit requirements.",
)
DECISION_MATRIX_CASES = [
    ("Draft a short note for the changelog.", "single-thread-default"),
    ("How could we create tests that the skill hook makes an appropriate decision?", "single-thread-likely"),
    ("Rename this variable in one file.", "single-thread-likely"),
    ("Review this patch.", "orchestration-check"),
    ("Compare implementation approaches across API and web.", "use-subagent-orchestrator"),
    ("Investigate the CI failure spanning API and web tests.", "use-subagent-orchestrator"),
    ("Review the branch. No subagent orchestration unless useful.", "orchestration-check"),
    ("Never use subagents. Review this patch.", "orchestration-opt-out"),
    ("You are a subagent. Review src/auth.ts and return findings.", "recursion-guard"),
]
PRECEDENCE_CASES = [
    ("Never use subagents. Review this patch for security risk.", "orchestration-opt-out"),
    ("Review the branch. No subagent orchestration unless useful.", "orchestration-check"),
    ("You are a subagent. Audit src/auth.ts and return findings.", "recursion-guard"),
    ("How should we test this hook?", "single-thread-likely"),
]
THRESHOLD_EDGE_CASES = [
    ("Audit this patch.", "orchestration-check"),
    ("Write a quick review of tests and explain it.", "orchestration-check"),
    ("How should we verify this?", "single-thread-likely"),
]
METAMORPHIC_DECISION_CASES = [
    (
        "Debug flaky regression across API and web tests.",
        "use-subagent-orchestrator",
        [
            "Please debug flaky regression across API and web tests.",
            "Debug flaky regression across web and API tests, then report the likely root cause.",
            "How do subagents work together to debug a flaky multi-file auth regression and propose tests?",
            "How should subagents work together to debug a flaky multi-file auth regression and propose tests?",
        ],
    ),
    (
        "Do not use subagents. Debug the flaky auth regression linearly.",
        "orchestration-opt-out",
        [
            "Please do not use subagents even though this is a flaky API and web regression.",
            "Debug the flaky auth regression linearly. Don't use subagents.",
        ],
    ),
    (
        "You are so_reviewer. Audit src/auth.ts and return findings.",
        "recursion-guard",
        [
            "You are so_reviewer. Please audit src/auth.ts for security risk.",
            "Task for so_reviewer: audit src/auth.ts and return findings.",
        ],
    ),
]
FALSE_POSITIVE_CASES = [
    ("Explain how subagents work without using them.", "single-thread-likely"),
    ("Summarize the subagent-orchestrator skill behavior.", "single-thread-likely"),
    ("Review the README wording for clarity.", "single-thread-default"),
    ("Review auth and billing docs for clarity only.", "single-thread-default"),
    ("Compare package and workspace names in package.json.", "single-thread-default"),
    ("Debug this one failing cache and storage test.", "single-thread-likely"),
    ("Debug auth and search for root cause.", "orchestration-check"),
    ("Explain what GraphQL and REST are.", "single-thread-likely"),
    ("Compare GraphQL and REST.", "single-thread-default"),
    ("Compare end-to-end and integration tests.", "single-thread-default"),
    ("What are the trade-offs between GraphQL and REST?", "single-thread-likely"),
]
HIGH_VALUE_EDGE_CASES = [
    ("Audit src/auth.ts for vulnerabilities and missing tests.", "use-subagent-orchestrator"),
    (
        "Audit authentication, API routes, and database access for security regressions and missing tests.",
        "use-subagent-orchestrator",
    ),
    ("Review this README paragraph for wording clarity only. Do not inspect the repo.", "single-thread-default"),
    ("Explain how subagents work without using them.", "single-thread-likely"),
    ("Avoid extra agent/tool cost. Review this branch linearly.", "orchestration-opt-out"),
]
DOMAIN_LANGUAGE_MULTI_SURFACE_CASES = [
    (
        "Audit authentication, billing, and background jobs for security regressions and missing tests.",
        "use-subagent-orchestrator",
    ),
    ("Investigate failures spanning worker, queue, and database.", "use-subagent-orchestrator"),
    ("Investigate failures spanning search index and cache.", "use-subagent-orchestrator"),
]
DOMAIN_LANGUAGE_BOUNDARY_CASES = [
    ("Review the authentication module for clarity.", "single-thread-default"),
    ("Refactor one CLI command in src/cli/login.ts.", "orchestration-check"),
]


def hook_subprocess_environment(extra_env: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop(HOOK_MODE_ENV, None)
    environment.update(extra_env or {})
    return environment


def run_hook_with_input(input_text: str, extra_env: dict[str, str] | None = None) -> dict[str, object]:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
        env=hook_subprocess_environment(extra_env),
    )
    return json.loads(proc.stdout)


def run_payload(prompt: object, extra_env: dict[str, str] | None = None) -> dict[str, object]:
    return run_hook_with_input(json.dumps({"prompt": prompt}), extra_env)


def hook_specific_output(prompt: str, extra_env: dict[str, str] | None = None) -> dict[str, object]:
    data = run_payload(prompt, extra_env)
    hook_output = data.get("hookSpecificOutput", {})
    assert isinstance(hook_output, dict), data
    return hook_output


def run(prompt: str, extra_env: dict[str, str] | None = None) -> str | None:
    hook_output = hook_specific_output(prompt, extra_env)
    return hook_output.get("additionalContext")


def assert_context_reports_result_and_reason(prompt: str, expected_result: str) -> str:
    context = run(prompt)
    assert context is not None, prompt
    assert context.splitlines()[0] == "Subagent orchestration gate", (prompt, context)
    assert f"\nResult: {expected_result}\n" in context, (prompt, context)
    assert "\nReason: " in context, (prompt, context)
    return context


def assert_context_uses_professional_status_format(context: str) -> None:
    lines = context.splitlines()
    assert lines[0] == "Subagent orchestration gate", context
    assert lines[1].startswith("Result: "), context
    assert lines[2].startswith("Reason: "), context
    assert len(lines) in {3, 4}, context
    assert lines[2].endswith("."), context
    assert ":" not in lines[2].removeprefix("Reason: "), context
    if len(lines) == 4:
        assert lines[3].startswith("Action: "), context
        assert lines[3].endswith("."), context
    assert "Subagent orchestration gate result:" not in context, context
    assert "Subagent orchestration gate quiet hint" not in context, context
    assert "Preliminary classification:" not in context, context
    assert "Compatibility rules" not in context, context
    assert BOUNDARY_SENTENCE not in context, context


def assert_prompt_decisions(cases: list[tuple[str, str]]) -> None:
    for prompt, expected_result in cases:
        context = assert_context_reports_result_and_reason(prompt, expected_result)
        assert_context_uses_professional_status_format(context)


def assert_context_includes_labels(prompt: str, expected_result: str, labels: list[str]) -> None:
    context = assert_context_reports_result_and_reason(prompt, expected_result)
    lower_context = context.lower()
    for label in labels:
        assert label in lower_context, (prompt, label, context)
    assert_context_uses_professional_status_format(context)


def assert_fail_open_output(output: dict[str, object]) -> None:
    assert "systemMessage" in output, output
    assert "hookSpecificOutput" not in output, output
    assert "could not parse input" in str(output["systemMessage"]), output


def assert_context_includes_action_contract(prompt: str, expected_result: str, terms: list[str]) -> None:
    context = assert_context_reports_result_and_reason(prompt, expected_result)
    lower_context = context.lower()
    assert "\naction: production contract: " in lower_context, (prompt, context)
    assert "non-binding hint" not in lower_context, (prompt, context)
    assert "explicit user authorization before spawning" not in lower_context, (prompt, context)
    assert "ask the user whether to spawn subagents" not in lower_context, (prompt, context)
    for term in terms:
        assert term in lower_context, (prompt, term, context)
    assert_context_uses_professional_status_format(context)


def assert_context_has_no_action_contract(prompt: str, expected_result: str) -> None:
    context = assert_context_reports_result_and_reason(prompt, expected_result)
    assert "\nAction: " not in context, (prompt, context)
    assert_context_uses_professional_status_format(context)


def run_installer(
    arguments: list[str],
    home: Path,
    app_home: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_user_script(INSTALLER, arguments, home, app_home, cwd)


def run_uninstaller(
    arguments: list[str],
    home: Path,
    app_home: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    return run_user_script(UNINSTALLER, arguments, home, app_home, cwd)


def run_user_script(
    script: Path,
    arguments: list[str],
    home: Path,
    app_home: Path,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["CODEX_HOME"] = str(app_home)
    return subprocess.run(
        [sys.executable, "-B", str(script), *arguments],
        text=True,
        capture_output=True,
        env=env,
        cwd=cwd,
        check=False,
    )


def assert_installer_ok(proc: subprocess.CompletedProcess[str]) -> None:
    assert proc.returncode == 0, proc.stderr + proc.stdout


def assert_installer_failed(proc: subprocess.CompletedProcess[str], expected_text: str) -> None:
    assert proc.returncode != 0, proc.stderr + proc.stdout
    assert expected_text in proc.stderr + proc.stdout, proc.stderr + proc.stdout


def skill_path(home: Path, name: str) -> Path:
    return home / ".agents" / "skills" / name


def hook_path(app_home: Path) -> Path:
    return app_home / "hooks" / "subagent_orchestration_gate.py"


def config_path(app_home: Path) -> Path:
    return app_home / "config.toml"


def global_agents_path(app_home: Path) -> Path:
    return app_home / "AGENTS.md"


def plugin_path(app_home: Path) -> Path:
    return app_home / "plugins" / "subagent-orchestrator"


def marketplace_path(home: Path) -> Path:
    return home / ".agents" / "plugins" / "marketplace.json"


def custom_agent_paths(app_home: Path) -> list[Path]:
    return sorted((app_home / "agents").glob("so_*.toml"))


def project_config_path(repo: Path) -> Path:
    return repo / ".codex" / "config.toml"


def project_hook_path(repo: Path) -> Path:
    return repo / ".codex" / "hooks" / "subagent_orchestration_gate.py"


def project_skill_path(repo: Path, name: str) -> Path:
    return repo / ".agents" / "skills" / name


def project_marketplace_path(repo: Path) -> Path:
    return repo / ".agents" / "plugins" / "marketplace.json"


def project_manifest_path(repo: Path) -> Path:
    return repo / ".codex" / "subagent-orchestrator-install.json"


def prepare_vendored_plugin(repo: Path) -> Path:
    vendor_root = repo / "vendor" / "subagent-orchestration-skill-plugin"
    shutil.copytree(ROOT / "plugin", vendor_root / "plugin")
    shutil.copytree(ROOT / "hooks", vendor_root / "hooks")
    return vendor_root


def assert_skills_installed(home: Path) -> None:
    assert (skill_path(home, "subagent-orchestrator") / "SKILL.md").exists()
    assert (skill_path(home, "using-subagent-orchestrator") / "SKILL.md").exists()


def write_different_skill(path: Path) -> None:
    path.mkdir(parents=True)
    (path / "SKILL.md").write_text("---\nname: local-skill\n---\n# Local skill\n", encoding="utf-8")


def test_default_install_stages_skills_without_hook_or_activation() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer([], home, app_home)
        assert_installer_ok(proc)
        assert_skills_installed(home)
        assert not hook_path(app_home).exists()
        assert not config_path(app_home).exists()
        assert not global_agents_path(app_home).exists()
        assert not custom_agent_paths(app_home)
        assert not plugin_path(app_home).exists()
        assert not marketplace_path(home).exists()
        assert "Hook not staged" in proc.stdout
        assert "config.toml was not modified" in proc.stdout


def test_skills_only_skips_hook_and_activation_files() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--skills-only"], home, app_home)
        assert_installer_ok(proc)
        assert_skills_installed(home)
        assert not hook_path(app_home).exists()
        assert not config_path(app_home).exists()
        assert not global_agents_path(app_home).exists()
        assert not custom_agent_paths(app_home)
        assert not plugin_path(app_home).exists()


def test_config_patch_flags_are_rejected_without_touching_config() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        app_home.mkdir(parents=True)
        config = config_path(app_home)
        config.write_text("[features]\nexisting = true\n", encoding="utf-8")

        for flag in ["--patch-config", "--activate-gate"]:
            proc = run_installer([flag], home, app_home)
            assert proc.returncode != 0, proc.stdout
            assert "unrecognized arguments" in proc.stderr or "requires --scope project" in proc.stderr
            assert config.read_text(encoding="utf-8") == "[features]\nexisting = true\n"
            assert not hook_path(app_home).exists()


def test_with_hook_installs_hook_without_config_patch() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--with-hook"], home, app_home)
        assert_installer_ok(proc)
        assert_skills_installed(home)
        assert hook_path(app_home).exists()
        assert not config_path(app_home).exists()


def test_with_hook_refreshes_configured_user_scope_contract_artifacts() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        stale_hook = hook_path(app_home)
        stale_hook.parent.mkdir(parents=True)
        stale_hook.write_text(
            "ACTION_HINTS = {'use-subagent-orchestrator': 'Non-binding hint'}\n",
            encoding="utf-8",
        )
        stale_skill = skill_path(home, "subagent-orchestrator")
        stale_skill.mkdir(parents=True)
        (stale_skill / "SKILL.md").write_text(
            "---\nname: subagent-orchestrator\n---\n# Stale skill\nNon-binding hint\n",
            encoding="utf-8",
        )
        config = config_path(app_home)
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[[hooks.UserPromptSubmit]]\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            f"command = \"python3 {stale_hook}\"\n",
            encoding="utf-8",
        )

        proc = run_installer(["--with-hook"], home, app_home)

        assert_installer_ok(proc)
        configured_hook_text = stale_hook.read_text(encoding="utf-8")
        installed_orchestrator_skill = (
            skill_path(home, "subagent-orchestrator") / "SKILL.md"
        ).read_text(encoding="utf-8")
        installed_gate_skill = (
            skill_path(home, "using-subagent-orchestrator") / "SKILL.md"
        ).read_text(encoding="utf-8")
        assert "Production contract" in configured_hook_text
        assert "standing user authorization" in configured_hook_text
        assert "ask the user whether to spawn subagents" not in configured_hook_text
        assert "Non-binding hint" not in configured_hook_text
        assert "standing user authorization" in installed_orchestrator_skill
        assert "standing user authorization" in installed_gate_skill
        assert "ask the user whether to spawn subagents" not in installed_orchestrator_skill
        assert "ask the user whether to spawn subagents" not in installed_gate_skill
        assert config.read_text(encoding="utf-8") == (
            "[[hooks.UserPromptSubmit]]\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            f"command = \"python3 {stale_hook}\"\n"
        )


def test_user_install_backs_up_different_existing_skill_and_hook() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        existing_skill = skill_path(home, "subagent-orchestrator")
        write_different_skill(existing_skill)
        existing_hook = hook_path(app_home)
        existing_hook.parent.mkdir(parents=True)
        existing_hook.write_text("# local hook\n", encoding="utf-8")

        proc = run_installer(["--with-hook"], home, app_home)

        assert_installer_ok(proc)
        assert (skill_path(home, "subagent-orchestrator.bak") / "SKILL.md").read_text(encoding="utf-8") == "---\nname: local-skill\n---\n# Local skill\n"
        assert (app_home / "hooks" / "subagent_orchestration_gate.py.bak").read_text(encoding="utf-8") == "# local hook\n"
        assert (existing_skill / "SKILL.md").read_text(encoding="utf-8") != "---\nname: local-skill\n---\n# Local skill\n"
        assert existing_hook.read_text(encoding="utf-8") != "# local hook\n"


def test_user_install_leaves_matching_existing_files_without_backup() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        first_proc = run_installer(["--with-hook"], home, app_home)
        second_proc = run_installer(["--with-hook"], home, app_home)

        assert_installer_ok(first_proc)
        assert_installer_ok(second_proc)
        assert not skill_path(home, "subagent-orchestrator.bak").exists()
        assert not (app_home / "hooks" / "subagent_orchestration_gate.py.bak").exists()


def test_custom_agent_flag_is_not_part_of_user_installer() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--with-agents"], home, app_home)
        assert proc.returncode != 0, proc.stdout
        assert "unrecognized arguments" in proc.stderr
        assert not custom_agent_paths(app_home)


def test_global_agents_flag_is_not_part_of_user_installer() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--with-global-agents-md"], home, app_home)
        assert proc.returncode != 0, proc.stdout
        assert "unrecognized arguments" in proc.stderr
        assert not global_agents_path(app_home).exists()


def test_plugin_flag_is_not_part_of_user_installer() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--plugin"], home, app_home)
        assert proc.returncode != 0, proc.stdout
        assert "unrecognized arguments" in proc.stderr
        assert not plugin_path(app_home).exists()
        assert not marketplace_path(home).exists()
        assert not config_path(app_home).exists()


def test_removed_project_flags_are_rejected() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        for flag in ["--available-only", "--copy-skills"]:
            proc = run_installer(["--scope", "project", "--repo-root", str(repo), flag], home, app_home)
            assert_installer_failed(proc, "unrecognized arguments")


def test_dry_run_changes_nothing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        proc = run_installer(["--dry-run"], home, app_home)
        assert_installer_ok(proc)
        assert "would install skill" in proc.stdout
        assert not home.exists()
        assert not app_home.exists()


def test_project_scope_never_writes_home_paths() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--activate-gate",
                "--with-project-agents",
                "--with-repo-marketplace",
                "--append-project-agents-md",
            ],
            home,
            app_home,
        )

        assert_installer_ok(proc)
        assert project_config_path(repo).exists()
        assert project_hook_path(repo).exists()
        assert project_skill_path(repo, "subagent-orchestrator").exists()
        assert project_skill_path(repo, "using-subagent-orchestrator").exists()
        assert (repo / ".codex" / "agents" / "so_mapper.toml").exists()
        assert project_marketplace_path(repo).exists()
        assert (repo / "AGENTS.md").exists()
        agents_text = (repo / "AGENTS.md").read_text(encoding="utf-8")
        assert "execution-shape helper only" in agents_text
        assert "Host project rules win" in agents_text
        assert "Subagent output is not evidence" in agents_text
        assert "Keep subagents read-only by default" in agents_text
        assert "Do not invent sources, citekeys, page numbers, quotations, studies, or metadata" in agents_text
        assert "Do not install global hooks/config by default" in agents_text
        assert not home.exists()
        assert not app_home.exists()


def test_project_scope_dry_run_writes_nothing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--activate-gate",
                "--with-project-agents",
                "--with-repo-marketplace",
                "--append-project-agents-md",
                "--dry-run",
            ],
            home,
            app_home,
        )

        assert_installer_ok(proc)
        assert "would patch project config" in proc.stdout
        assert "would install project hook" in proc.stdout
        assert not list(repo.iterdir())
        assert not home.exists()
        assert not app_home.exists()


def test_project_config_patch_is_idempotent_and_uses_git_root_command() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()
        config = project_config_path(repo)
        config.parent.mkdir(parents=True)
        config.write_text("[features]\nexisting = true\n\n[agents]\nmax_threads_backup = 99\n", encoding="utf-8")

        args = ["--scope", "project", "--repo-root", str(repo), "--activate-gate"]
        first_proc = run_installer(args, home, app_home)
        assert_installer_ok(first_proc)
        first_config = config.read_text(encoding="utf-8")
        second_proc = run_installer(args, home, app_home)
        assert_installer_ok(second_proc)
        second_config = config.read_text(encoding="utf-8")

        assert first_config == second_config
        assert "existing = true" in second_config
        assert "max_threads_backup = 99" in second_config
        assert "hooks = true" in second_config
        deprecated_hooks_key = "codex" + "_hooks"
        assert deprecated_hooks_key not in second_config
        assert "max_threads = 4" in second_config
        assert "max_depth = 1" in second_config
        assert "statusMessage = \"Evaluating subagent orchestration\"" in second_config
        assert "command = 'python3 \"$(git rev-parse --show-toplevel)/.codex/hooks/subagent_orchestration_gate.py\"'" in second_config
        assert second_config.count("subagent_orchestration_gate.py") == 1
        assert tomllib.loads(second_config)["features"]["hooks"] is True
        assert config.with_suffix(".toml.bak").exists()


def test_project_skills_are_symlinked_or_copied_correctly() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        symlink_repo = root / "symlink-repo"
        symlink_repo.mkdir()
        vendor_root = prepare_vendored_plugin(symlink_repo)

        symlink_proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(symlink_repo),
                "--from-vendor",
                str(vendor_root),
                "--link-skills",
            ],
            home,
            app_home,
        )

        assert_installer_ok(symlink_proc)
        linked_skill = project_skill_path(symlink_repo, "subagent-orchestrator")
        assert linked_skill.is_symlink()
        assert linked_skill.resolve() == (vendor_root / "plugin" / "subagent-orchestrator" / "skills" / "subagent-orchestrator").resolve()

        copy_repo = root / "copy-repo"
        copy_repo.mkdir()
        copy_proc = run_installer(
            ["--scope", "project", "--repo-root", str(copy_repo)],
            home,
            app_home,
        )

        assert_installer_ok(copy_proc)
        copied_skill = project_skill_path(copy_repo, "subagent-orchestrator")
        assert copied_skill.exists()
        assert not copied_skill.is_symlink()
        assert (copied_skill / "SKILL.md").exists()


def test_project_agents_are_opt_in() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        default_proc = run_installer(
            ["--scope", "project", "--repo-root", str(repo), "--activate-gate"],
            home,
            app_home,
        )
        assert_installer_ok(default_proc)
        assert not (repo / ".codex" / "agents").exists()

        opt_in_proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--activate-gate",
                "--with-project-agents",
            ],
            home,
            app_home,
        )
        assert_installer_ok(opt_in_proc)
        assert (repo / ".codex" / "agents" / "so_mapper.toml").exists()


def test_project_repo_marketplace_writes_vendored_plugin_path() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()
        vendor_root = prepare_vendored_plugin(repo)

        proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--from-vendor",
                str(vendor_root),
                "--with-repo-marketplace",
            ],
            home,
            app_home,
        )

        assert_installer_ok(proc)
        marketplace = json.loads(project_marketplace_path(repo).read_text(encoding="utf-8"))
        plugin = next(plugin for plugin in marketplace["plugins"] if plugin["name"] == "subagent-orchestrator")
        assert plugin == {
            "name": "subagent-orchestrator",
            "source": {
                "source": "local",
                "path": "./vendor/subagent-orchestration-skill-plugin/plugin/subagent-orchestrator",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }


def test_project_activation_smoke_runs_installed_hook_and_marketplace_plugin() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--activate-gate",
                "--with-repo-marketplace",
            ],
            home,
            app_home,
        )
        assert_installer_ok(proc)

        hook_proc = subprocess.run(
            [sys.executable, str(project_hook_path(repo))],
            input=json.dumps({"prompt": "Debug a flaky regression across API and web tests."}),
            text=True,
            capture_output=True,
            cwd=repo,
            check=True,
        )
        hook_output = json.loads(hook_proc.stdout)
        marketplace = json.loads(project_marketplace_path(repo).read_text(encoding="utf-8"))
        plugin = next(plugin for plugin in marketplace["plugins"] if plugin["name"] == "subagent-orchestrator")
        plugin_root = repo / plugin["source"]["path"]
        plugin_manifest_exists = (plugin_root / ".codex-plugin" / "plugin.json").exists()
        orchestrator_skill_exists = (plugin_root / "skills" / "subagent-orchestrator" / "SKILL.md").exists()
        using_skill_exists = (plugin_root / "skills" / "using-subagent-orchestrator" / "SKILL.md").exists()

    assert "\nResult: use-subagent-orchestrator\n" in hook_output["hookSpecificOutput"]["additionalContext"]
    assert plugin_manifest_exists
    assert orchestrator_skill_exists
    assert using_skill_exists


def test_project_vendored_hook_wrapper_fails_open_when_vendor_hook_is_missing() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()
        vendor_root = prepare_vendored_plugin(repo)

        proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--from-vendor",
                str(vendor_root),
                "--activate-gate",
                "--link-skills",
            ],
            home,
            app_home,
        )
        assert_installer_ok(proc)
        (vendor_root / "hooks" / "subagent_orchestration_gate.py").unlink()

        hook_proc = subprocess.run(
            [sys.executable, str(project_hook_path(repo))],
            input=json.dumps({"prompt": "Debug a flaky auth regression."}),
            text=True,
            capture_output=True,
            cwd=repo,
            check=True,
        )
        hook_output = json.loads(hook_proc.stdout)
        assert "systemMessage" in hook_output, hook_output
        assert "vendored subagent orchestration hook is missing" in hook_output["systemMessage"]
        assert "hookSpecificOutput" not in hook_output


def test_project_uninstall_removes_manifest_owned_files_only() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()

        install_proc = run_installer(
            [
                "--scope",
                "project",
                "--repo-root",
                str(repo),
                "--activate-gate",
                "--with-project-agents",
                "--with-repo-marketplace",
                "--append-project-agents-md",
            ],
            home,
            app_home,
        )
        assert_installer_ok(install_proc)
        unrelated_file = repo / ".codex" / "hooks" / "keep.py"
        unrelated_file.write_text("keep = True\n", encoding="utf-8")

        uninstall_proc = run_installer(
            ["--scope", "project", "--repo-root", str(repo), "--uninstall"],
            home,
            app_home,
        )

        assert_installer_ok(uninstall_proc)
        assert not project_manifest_path(repo).exists()
        assert not project_hook_path(repo).exists()
        assert not project_skill_path(repo, "subagent-orchestrator").exists()
        assert not project_skill_path(repo, "using-subagent-orchestrator").exists()
        assert not (repo / ".codex" / "agents" / "so_mapper.toml").exists()
        if project_config_path(repo).exists():
            assert "subagent_orchestration_gate.py" not in project_config_path(repo).read_text(encoding="utf-8")
        if project_marketplace_path(repo).exists():
            assert "subagent-orchestrator" not in project_marketplace_path(repo).read_text(encoding="utf-8")
        if (repo / "AGENTS.md").exists():
            assert "Optional subagent orchestration" not in (repo / "AGENTS.md").read_text(encoding="utf-8")
        assert unrelated_file.exists()


def test_default_install_preserves_existing_config() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        app_home.mkdir(parents=True)
        config = app_home / "config.toml"
        config.write_text("[agents]\nmax_threads_backup = 99\n", encoding="utf-8")
        proc = run_installer([], home, app_home)
        assert_installer_ok(proc)
        assert config.read_text(encoding="utf-8") == "[agents]\nmax_threads_backup = 99\n"
        assert not config.with_suffix(".toml.bak").exists()


def test_uninstall_removes_owned_config_guidance_and_marketplace() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        install_proc = run_installer(["--with-hook"], home, app_home)
        assert_installer_ok(install_proc)

        config = app_home / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[[hooks.UserPromptSubmit]]\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            "type = \"command\"\n"
            "command = \"python3 ~/.codex/hooks/subagent_orchestration_gate.py\"\n"
            "timeout = 5\n",
            encoding="utf-8",
        )
        agents_file = app_home / "AGENTS.md"
        agents_file.write_text(
            (ROOT / "snippets" / "AGENTS.subagent-orchestration.md").read_text(encoding="utf-8").strip() + "\n",
            encoding="utf-8",
        )
        agents_dir = app_home / "agents"
        agents_dir.mkdir(parents=True)
        shutil.copy2(ROOT / "custom-agents" / "so_mapper.toml", agents_dir / "so_mapper.toml")
        marketplace_file = marketplace_path(home)
        marketplace_file.parent.mkdir(parents=True)
        marketplace_file.write_text(
            json.dumps(
                {
                    "plugins": [
                        {"name": "subagent-orchestrator"},
                        {"name": "other-plugin"},
                    ]
                }
            )
            + "\n",
            encoding="utf-8",
        )

        uninstall_proc = run_uninstaller([], home, app_home)
        assert_installer_ok(uninstall_proc)
        config_text = (app_home / "config.toml").read_text(encoding="utf-8")
        agents_text = (app_home / "AGENTS.md").read_text(encoding="utf-8")
        marketplace = json.loads((home / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
        assert "subagent_orchestration_gate.py" not in config_text
        assert "Subagent orchestration gate" not in agents_text
        assert all(plugin.get("name") != "subagent-orchestrator" for plugin in marketplace["plugins"])
        assert not list((app_home / "agents").glob("so_*.toml"))


def test_user_uninstall_is_available_from_main_installer() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        install_proc = run_installer(["--with-hook"], home, app_home)
        assert_installer_ok(install_proc)

        uninstall_proc = run_installer(["--uninstall"], home, app_home)

        assert_installer_ok(uninstall_proc)
        assert not hook_path(app_home).exists()
        assert not skill_path(home, "subagent-orchestrator").exists()
        assert not skill_path(home, "using-subagent-orchestrator").exists()


def test_global_uninstall_removes_only_owned_hook_entry() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        install_proc = run_installer(["--with-hook"], home, app_home)
        assert_installer_ok(install_proc)
        config = app_home / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            "[[hooks.UserPromptSubmit]]\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            "type = \"command\"\n"
            "command = \"python3 ~/.codex/hooks/subagent_orchestration_gate.py\"\n"
            "timeout = 5\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            "type = \"command\"\n"
            "command = \"python3 other.py\"\n"
            "timeout = 5\n",
            encoding="utf-8",
        )

        uninstall_proc = run_uninstaller([], home, app_home)

        assert_installer_ok(uninstall_proc)
        config_text = config.read_text(encoding="utf-8")
        assert "subagent_orchestration_gate.py" not in config_text
        assert "python3 other.py" in config_text
        assert "[[hooks.UserPromptSubmit]]" in config_text


def test_global_uninstall_leaves_different_skill_and_symlink_targets() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        different_skill = skill_path(home, "subagent-orchestrator")
        write_different_skill(different_skill)
        target = root / "target"
        target.mkdir()
        skill_link = skill_path(home, "using-subagent-orchestrator")
        skill_link.parent.mkdir(parents=True, exist_ok=True)
        skill_link.symlink_to(target, target_is_directory=True)

        uninstall_proc = run_uninstaller([], home, app_home)

        assert_installer_ok(uninstall_proc)
        assert different_skill.exists()
        assert skill_link.exists() or skill_link.is_symlink()


def test_global_uninstall_uses_unique_backup_names() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        install_proc = run_installer(["--with-hook"], home, app_home)
        assert_installer_ok(install_proc)
        config = config_path(app_home)
        config.write_text(
            "[[hooks.UserPromptSubmit]]\n"
            "[[hooks.UserPromptSubmit.hooks]]\n"
            "command = \"python3 ~/.codex/hooks/subagent_orchestration_gate.py\"\n",
            encoding="utf-8",
        )
        existing_backup = config.with_name(config.name + ".bak")
        existing_backup.write_text("first backup\n", encoding="utf-8")

        uninstall_proc = run_uninstaller([], home, app_home)

        assert_installer_ok(uninstall_proc)
        assert existing_backup.read_text(encoding="utf-8") == "first backup\n"
        assert config.with_name(config.name + ".bak.1").exists()


def test_project_install_fails_on_corrupt_manifest() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()
        manifest = project_manifest_path(repo)
        manifest.parent.mkdir(parents=True)
        manifest.write_text("{bad json", encoding="utf-8")

        proc = run_installer(["--scope", "project", "--repo-root", str(repo)], home, app_home)

        assert_installer_failed(proc, "install manifest is not valid JSON")


def test_project_marketplace_requires_valid_json_and_plugin_list() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        home = root / "home"
        app_home = root / "app"
        repo = root / "repo"
        repo.mkdir()
        marketplace = project_marketplace_path(repo)
        marketplace.parent.mkdir(parents=True)

        marketplace.write_text("{bad json", encoding="utf-8")
        bad_json_proc = run_installer(["--scope", "project", "--repo-root", str(repo), "--with-repo-marketplace"], home, app_home)
        assert_installer_failed(bad_json_proc, "marketplace is not valid JSON")

        marketplace.write_text(json.dumps({"plugins": "not-list"}) + "\n", encoding="utf-8")
        bad_schema_proc = run_installer(["--scope", "project", "--repo-root", str(repo), "--with-repo-marketplace"], home, app_home)
        assert_installer_failed(bad_schema_proc, "marketplace plugins must be a list")


def test_classifier_respects_opt_out_variants() -> None:
    cases = [
        "Don't orchestrate. Debug the flaky auth regression.",
        "Don't use orchestration. Audit security risk.",
        "Dont use subagents. Review this patch.",
        "Do not use subagents.",
        "Do not use orchestration. Audit security risk.",
        "Never use subagents. Review this patch.",
        "Never use orchestration. Audit security risk.",
        "Never spawn agents.",
        "Never orchestrate. Debug this failure.",
        "Without orchestration, review this patch.",
        "Without orchestration even if helpful, review this branch for security and architecture.",
        "Without parallel agents, investigate this failure.",
        "Without subagents even if helpful, investigate this failure across API and web tests.",
        "No parallel agents, debug this failure.",
        "No parallel agents even if helpful. Debug this failure.",
        "No subagents even if helpful. Audit authentication, API routes, and database access.",
        "No orchestration even if useful. Review this branch for security.",
        "No orchestration. I want one agent only.",
        "Do not spawn agents. Review this patch.",
        "Do not use subagents. Use orchestration as needed for this audit.",
        "Never spawn agents. Review this patch for security risk.",
        "Do not use subagents even if helpful.",
        "Do not use subagents even if helpful. Audit authentication, API routes, and database access.",
        "No more agents. Audit this patch.",
        "No more agents even if helpful. Audit authentication, API routes, and database access.",
        "Without spawning agents, investigate this failure.",
        "Work linearly through this flaky failure.",
        "Work linearly only.",
        "Use linear execution for this audit.",
        "Single-thread only for this review.",
        "Single-thread only.",
    ]
    for prompt in cases:
        context = run(prompt)
        assert context is not None, prompt
        assert "orchestration-opt-out" in context.lower(), (prompt, context)
        assert_context_uses_professional_status_format(context)


def test_classifier_preserves_conditional_orchestration() -> None:
    assert_prompt_decisions([
        ("Review the branch. No subagent orchestration unless useful.", "orchestration-check"),
        ("Use subagents if helpful.", "orchestration-check"),
        ("Use subagents only if helpful for the implementation review.", "orchestration-check"),
        ("Use agents where appropriate.", "orchestration-check"),
        ("Run parallel agents only if valuable for the audit.", "orchestration-check"),
        ("Orchestration only if needed for this refactor.", "orchestration-check"),
        ("Use subagents if helpful; otherwise work linearly.", "orchestration-check"),
        ("Do not use subagents unless useful.", "orchestration-check"),
        ("Use agents where appropriate for this review.", "orchestration-check"),
        ("No subagents unless useful.", "orchestration-check"),
        ("Delegate only if it reduces risk.", "orchestration-check"),
        ("Orchestrate as needed, but keep it lightweight.", "orchestration-check"),
        ("Spawn agents only if useful.", "orchestration-check"),
        ("Use parallel agents where appropriate.", "orchestration-check"),
        ("Spawn parallel agents only if worthwhile.", "orchestration-check"),
        ("Use parallel agents when worthwhile.", "orchestration-check"),
        ("Use orchestration as needed.", "orchestration-check"),
        ("Only orchestrate if it adds value.", "orchestration-check"),
        ("Spawn read-only agents where warranted.", "orchestration-check"),
    ])


def test_classifier_allows_conditional_permission_with_strong_complexity() -> None:
    assert_prompt_decisions([
        (
            "No subagents unless useful; review auth, API, database, and tests for regressions.",
            "use-subagent-orchestrator",
        ),
        (
            "Do not use subagents unless useful; review auth, API, database, and tests for regressions.",
            "use-subagent-orchestrator",
        ),
        (
            "Use agents if helpful; investigate flaky CI across frontend, backend, and worker services.",
            "use-subagent-orchestrator",
        ),
        (
            "Use orchestration where appropriate; audit security, architecture, correctness, and missing tests.",
            "use-subagent-orchestrator",
        ),
        (
            "Use subagents if helpful; investigate flaky CI failures across API, worker, and database layers.",
            "use-subagent-orchestrator",
        ),
        (
            "Use agents where appropriate; audit authentication, API routes, and database access for security regressions and missing tests.",
            "use-subagent-orchestrator",
        ),
        (
            "No subagent orchestration unless useful; review this branch for security, architecture, correctness, and missing tests across modules.",
            "use-subagent-orchestrator",
        ),
    ])


def test_classifier_detects_broad_investigations() -> None:
    cases = [
        "Find why checkout fails across frontend and backend and propose a fix.",
        "Investigate the CI failure spanning API and web tests.",
    ]
    for prompt in cases:
        context = run(prompt)
        assert context is not None, prompt
        assert "use-subagent-orchestrator" in context.lower(), (prompt, context)
        assert BOUNDARY_SENTENCE not in context
        assert_context_uses_professional_status_format(context)


def test_classifier_detects_broad_validation_sweeps() -> None:
    prompt = (
        "validate no remaining mention of legacy plugin and make sure all documentation "
        "has been properly updated and ensure the qa document has been properly updated "
        "and validate the setup as been properly updated"
    )
    context = assert_context_reports_result_and_reason(prompt, "use-subagent-orchestrator")
    assert "validation sweep" in context.lower(), context
    assert "documentation/qa" in context.lower(), context
    assert "setup/config" in context.lower(), context
    assert "plugin migration cleanup" in context.lower(), context
    assert_context_uses_professional_status_format(context)


def test_classifier_distinguishes_output_sweeps_from_formal_reviews() -> None:
    output_sweep_prompt = (
        "the status feedback is inconsistant mixing status sentence and nestedt punctuation : "
        "how could we make it more professional? "
        "(review all possible result they shall all be profesional)"
    )
    output_sweep_context = assert_context_reports_result_and_reason(output_sweep_prompt, "use-subagent-orchestrator")
    assert "output/status wording" in output_sweep_context.lower(), output_sweep_context
    assert "exhaustive result sweep" in output_sweep_context.lower(), output_sweep_context
    assert "review/audit" not in output_sweep_context.lower(), output_sweep_context

    quick_wording_context = assert_context_reports_result_and_reason(
        "Quick review before I send it: is this status sentence professional?",
        "single-thread-likely",
    )
    assert_context_uses_professional_status_format(output_sweep_context)
    assert_context_uses_professional_status_format(quick_wording_context)


def test_classifier_reports_reason_labels_for_representative_complex_prompt() -> None:
    assert_context_includes_labels(
        "Debug flaky regression across API and web tests.",
        "use-subagent-orchestrator",
        ["debugging/root-cause", "multi-surface scope", "tests/verification"],
    )


def test_classifier_downgrades_single_target_debugging() -> None:
    assert_prompt_decisions([
        ("Fix the failing test in tests/test_auth.py only.", "single-thread-likely"),
        ("Debug this one failing assertion in src/auth.ts and propose tests.", "single-thread-likely"),
        ("Find the root cause of this one stack trace.", "single-thread-likely"),
    ])


def test_classifier_preserves_broad_debugging_orchestration() -> None:
    assert_prompt_decisions([
        ("Investigate flaky CI failures across API, worker, and database layers.", "use-subagent-orchestrator"),
        (
            "Find the root cause of a production regression spanning frontend, backend, and cache.",
            "use-subagent-orchestrator",
        ),
        (
            "Debug a performance regression involving architecture changes and benchmark results.",
            "use-subagent-orchestrator",
        ),
    ])


def test_classifier_detects_parallelizable_uncertainty_runtime_and_release_signals() -> None:
    assert_context_includes_labels(
        "Triage possible root causes for intermittent timeouts across API, worker, and database.",
        "use-subagent-orchestrator",
        ["multi-surface scope", "parallelizable uncertainty", "runtime/performance/concurrency"],
    )
    assert_context_includes_labels(
        "Review this migration for schema compatibility, rollback risk, and missing tests.",
        "use-subagent-orchestrator",
        ["release/data risk", "review/audit", "tests/verification"],
    )
    assert_context_includes_labels(
        "Investigate a possible race condition affecting frontend, backend, and job processing.",
        "use-subagent-orchestrator",
        ["debugging/root-cause", "multi-surface scope", "runtime/performance/concurrency"],
    )


def test_classifier_detects_cross_cutting_integration_work() -> None:
    assert_context_includes_labels(
        "Review cross-cutting integration between client and server contract tests.",
        "use-subagent-orchestrator",
        ["cross-cutting integration", "multi-surface scope", "review/audit", "tests/verification"],
    )


def test_classifier_detects_observability_surface_audits() -> None:
    assert_context_includes_labels(
        "Audit observability, logging, and telemetry gaps across the service.",
        "use-subagent-orchestrator",
        ["multi-surface scope", "review/audit"],
    )


def test_classifier_escalates_high_risk_single_target_review_with_verification() -> None:
    assert_context_includes_labels(
        "Audit src/auth.ts for vulnerabilities and missing tests.",
        "use-subagent-orchestrator",
        ["high-risk single target", "review/audit", "tests/verification"],
    )
    assert_context_includes_labels(
        "Review the payment webhook handler for security regressions and missing tests.",
        "use-subagent-orchestrator",
        ["high-risk single target", "review/audit", "tests/verification"],
    )
    assert_context_includes_labels(
        "Audit the permissions middleware for authorization bugs and coverage gaps.",
        "use-subagent-orchestrator",
        ["high-risk single target", "review/audit", "tests/verification"],
    )
    assert_context_includes_labels(
        "Review the crypto signing module for vulnerabilities and verification gaps.",
        "use-subagent-orchestrator",
        ["high-risk single target", "review/audit", "tests/verification"],
    )


def test_classifier_does_not_escalate_low_risk_single_target_tasks() -> None:
    assert_prompt_decisions([
        ("Rename src/auth.ts.", "single-thread-likely"),
        ("Explain src/auth.ts.", "single-thread-likely"),
        ("Review src/auth.ts for readability.", "single-thread-default"),
        ("Review this one import for correctness and missing tests.", "orchestration-check"),
        ("Add one missing import to the payment webhook handler.", "single-thread-default"),
        ("Review the crypto signing module for performance.", "orchestration-check"),
        ("Audit the auth module for CI failures.", "orchestration-check"),
    ])


def test_classifier_keeps_single_keyword_data_and_surface_tasks_non_orchestrated() -> None:
    assert_prompt_decisions([
        ("Rename this migration file.", "single-thread-likely"),
        ("Fix this one typo in the logging message.", "single-thread-likely"),
        ("Explain what a database schema is.", "single-thread-likely"),
        ("Change one REST route name.", "single-thread-default"),
    ])


def test_classifier_keeps_compound_tiny_edits_non_orchestrated() -> None:
    assert_prompt_decisions([
        ("Rename this migration file and fix one typo in the logging message.", "single-thread-likely"),
        ("Fix one typo in GraphQL and REST docs.", "single-thread-likely"),
        ("Change one GraphQL and REST route name.", "single-thread-likely"),
        ("Change GraphQL and REST route names.", "orchestration-check"),
        ("Rename API and web route names.", "use-subagent-orchestrator"),
    ])


def test_classifier_preserves_multi_surface_audits_with_single_container_wording() -> None:
    assert_prompt_decisions([
        ("Audit observability, logging, and telemetry gaps in the service.", "use-subagent-orchestrator"),
        (
            "Review logging, telemetry, and observability in the service for missing tests.",
            "use-subagent-orchestrator",
        ),
    ])


def test_classifier_decision_matrix_covers_execution_shapes() -> None:
    assert_prompt_decisions(DECISION_MATRIX_CASES)


def test_hook_emits_production_action_contract_for_strong_orchestration() -> None:
    assert_context_includes_action_contract(
        "Review this plugin and suggest meaningful improvements to orchestration triggers.",
        "use-subagent-orchestrator",
        [
            "invoke the subagent-orchestrator skill",
            "choose single-thread, sequential-plan, or parallel-subagents",
            "standing user authorization for bounded read-only delegation",
            "spawn the smallest useful bounded read-only roster",
            "if spawning is otherwise unavailable or blocked",
        ],
    )


def test_hook_emits_checklist_contract_for_conditional_orchestration() -> None:
    assert_context_includes_action_contract(
        "Use subagents if helpful; otherwise work linearly.",
        "orchestration-check",
        [
            "run the orchestration checklist",
            "spawn only if at least two independent read-only tracks can return independently useful outputs",
            "no concrete blocker exists",
            "standing user authorization for bounded read-only delegation",
        ],
    )


def test_hook_keeps_opt_out_and_recursion_guard_non_spawning() -> None:
    assert_context_has_no_action_contract(
        "Do not use subagents. Review this patch for security risk.",
        "orchestration-opt-out",
    )
    assert_context_has_no_action_contract(
        "agent_type: so_tester\nmode: read-only\nscope: identify tests",
        "recursion-guard",
    )


def test_classifier_precedence_keeps_specific_decisions_stable() -> None:
    assert_prompt_decisions(PRECEDENCE_CASES)


def test_classifier_threshold_edges_stay_stable() -> None:
    assert_prompt_decisions(THRESHOLD_EDGE_CASES)


def test_classifier_metamorphic_variants_preserve_decisions() -> None:
    for base_prompt, expected_result, variants in METAMORPHIC_DECISION_CASES:
        assert_context_reports_result_and_reason(base_prompt, expected_result)
        assert_prompt_decisions([(variant, expected_result) for variant in variants])


def test_classifier_avoids_subagent_topic_false_positives() -> None:
    assert_prompt_decisions(FALSE_POSITIVE_CASES)


def test_classifier_covers_high_value_hook_edge_cases() -> None:
    assert_prompt_decisions(HIGH_VALUE_EDGE_CASES)


def test_classifier_detects_domain_language_multi_surface_scope() -> None:
    for prompt, expected_result in DOMAIN_LANGUAGE_MULTI_SURFACE_CASES:
        assert_context_includes_labels(prompt, expected_result, ["multi-surface scope"])
    assert_prompt_decisions(DOMAIN_LANGUAGE_BOUNDARY_CASES)


def test_classifier_recursion_guard_variants() -> None:
    assert_prompt_decisions([
        ("Dispatched as a subagent: review src/auth.ts.", "recursion-guard"),
        ("You are so_mapper. Map the repository and return files.", "recursion-guard"),
        ("Task for so_tester: find relevant tests.", "recursion-guard"),
        ("Parent agent asked you to audit this patch.", "recursion-guard"),
        (
            "\n".join([
                "agent_type: so_mapper",
                "mode: read-only",
                "scope: map auth flow",
                "task: return files and uncertainty",
                "constraints: no recursive fan-out",
            ]),
            "recursion-guard",
        ),
        (
            "\n".join([
                "agent_type: so_reviewer",
                "mode: read-only",
                "scope: review hook classifier flow",
                "task: return material findings only",
                "constraints: do not edit files; do not spawn more agents; report uncertainty",
            ]),
            "recursion-guard",
        ),
        ("agent_type: so_tester\nmode: read-only\nscope: identify tests", "recursion-guard"),
    ])


def test_hook_handles_invalid_or_missing_prompt_payloads() -> None:
    assert_fail_open_output(run_hook_with_input("{not valid json"))

    for input_text in ["[]", "null", '"prompt"']:
        assert_fail_open_output(run_hook_with_input(input_text))

    empty_context = run_hook_with_input("{}")["hookSpecificOutput"]["additionalContext"]
    assert empty_context == "Subagent orchestration gate\nResult: single-thread-default\nReason: No strong orchestration signals detected."

    numeric_context = run_payload(404)["hookSpecificOutput"]["additionalContext"]
    assert "\nResult: single-thread-default\n" in numeric_context, numeric_context


def test_classifier_outputs_professional_status_format_for_all_results() -> None:
    assert_prompt_decisions(DECISION_MATRIX_CASES)


def test_classifier_returns_only_result_for_default_and_simple_prompts() -> None:
    cases = [
        ("What does this repository do?", "single-thread-likely"),
        ("Rename this variable in one file.", "single-thread-likely"),
        ("Draft a short note for the changelog.", "single-thread-default"),
    ]
    for prompt, expected_result in cases:
        context = assert_context_reports_result_and_reason(prompt, expected_result)
        assert "\nAction: " not in context, (prompt, context)
        assert BOUNDARY_SENTENCE not in context, (prompt, context)
        assert "Compatibility rules" not in context, (prompt, context)
        assert "Subagent orchestration gate quiet hint" not in context, (prompt, context)


def test_hook_ignores_live_eval_contract_mode_environment() -> None:
    contract_env = {HOOK_MODE_ENV: "contract"}
    context = run("Debug a flaky multi-file auth regression and propose tests.", contract_env)
    assert context is not None
    assert context.splitlines() == [
        "Subagent orchestration gate",
        "Result: use-subagent-orchestrator",
        "Reason: Strong orchestration signals detected (architecture/refactor, debugging/root-cause, tests/verification).",
        "Action: Production contract: invoke the subagent-orchestrator skill before broad work; choose single-thread, sequential-plan, or parallel-subagents; standing user authorization for bounded read-only delegation applies when parallel-subagents is selected and no concrete blocker exists; emit a compact why-parallel proof, define bounded roles, spawn the smallest useful bounded read-only roster, wait, then synthesize before edits; if spawning is otherwise unavailable or blocked, state the blocker and continue with the closest safe fallback.",
    ]
    assert "Eval mode: live-eval spawn trace contract." not in context

    simple_context = run("What does this repository do?", contract_env)
    assert simple_context is not None
    assert simple_context.splitlines() == [
        "Subagent orchestration gate",
        "Result: single-thread-likely",
        "Reason: Simple-task signals detected (simple question).",
    ]


def test_hook_test_helper_does_not_inherit_hook_mode_environment_by_default() -> None:
    previous_value = os.environ.get(HOOK_MODE_ENV)
    os.environ[HOOK_MODE_ENV] = "contract"
    try:
        context = run("Debug a flaky multi-file auth regression and propose tests.")
        explicit_context = run("Debug a flaky multi-file auth regression and propose tests.", {HOOK_MODE_ENV: "contract"})
    finally:
        if previous_value is None:
            os.environ.pop(HOOK_MODE_ENV, None)
        else:
            os.environ[HOOK_MODE_ENV] = previous_value

    assert context is not None
    assert "Eval mode: live-eval spawn trace contract." not in context
    assert explicit_context is not None
    assert "Eval mode: live-eval spawn trace contract." not in explicit_context


def test_classifier_always_reports_result_and_reason() -> None:
    cases = [
        ("What does this repository do?", "single-thread-likely"),
        ("Rename this variable in one file.", "single-thread-likely"),
        ("Debug a flaky multi-file auth regression and propose tests.", "use-subagent-orchestrator"),
        ("Do not use subagents. Debug the flaky auth regression linearly.", "orchestration-opt-out"),
        ("You are a subagent. Review src/auth.ts and return findings.", "recursion-guard"),
    ]
    for prompt, expected_result in cases:
        assert_context_reports_result_and_reason(prompt, expected_result)


def test_hook_specific_output_uses_only_codex_schema_keys() -> None:
    cases = [
        "What does this repository do?",
        "Debug a flaky multi-file auth regression and propose tests.",
        "Do not use subagents. Debug the flaky auth regression linearly.",
    ]
    for prompt in cases:
        hook_output = hook_specific_output(prompt)
        assert set(hook_output) <= USER_PROMPT_SUBMIT_OUTPUT_KEYS, (prompt, hook_output)


def test_classifier_guards_custom_agent_prompts() -> None:
    cases = [
        "You are so_mapper. Review src/auth.ts and return findings.",
        "Bounded subagent task for so_reviewer: audit this patch.",
    ]
    for prompt in cases:
        context = run(prompt)
        assert context is not None, prompt
        assert "recursion-guard" in context.lower(), (prompt, context)
        assert_context_uses_professional_status_format(context)


def test_toml_snippets_parse() -> None:
    for path in sorted((ROOT / "snippets").glob("*.toml")):
        tomllib.loads(path.read_text(encoding="utf-8"))


def test_hook_config_snippets_stay_quiet() -> None:
    for path in sorted((ROOT / "snippets").glob("config.hooks.*.toml")):
        assert "statusMessage" not in path.read_text(encoding="utf-8"), path


def test_json_files_parse() -> None:
    for path in sorted(ROOT.rglob("*.json")):
        if ".git" not in path.parts:
            json.loads(path.read_text(encoding="utf-8"))


def test_custom_agent_configs_are_valid() -> None:
    required_keys = {"name", "description", "model_reasoning_effort", "sandbox_mode", "nickname_candidates", "developer_instructions"}
    for path in sorted((ROOT / "custom-agents").glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        missing_keys = required_keys - data.keys()
        assert not missing_keys, (path, missing_keys)
        assert data["sandbox_mode"] in {"read-only", "workspace-write"}, path


def test_custom_agent_configs_share_safety_contract() -> None:
    for path in sorted((ROOT / "custom-agents").glob("*.toml")):
        instructions = tomllib.loads(path.read_text(encoding="utf-8"))["developer_instructions"]
        for line in CUSTOM_AGENT_SHARED_SAFETY_LINES:
            assert line in instructions, (path, line)


def test_subagent_config_snippet_matches_project_activation_defaults() -> None:
    snippet = tomllib.loads((ROOT / "snippets" / "config.subagents.toml").read_text(encoding="utf-8"))
    assert snippet["agents"]["max_threads"] == 4
    assert snippet["agents"]["max_depth"] == 1


def test_repo_marketplace_paths_exist() -> None:
    marketplace = json.loads((ROOT / "marketplace" / "repo-marketplace.json").read_text(encoding="utf-8"))
    for plugin in marketplace["plugins"]:
        source = plugin["source"]
        if source["source"] == "local":
            assert (ROOT / source["path"]).exists(), source["path"]


def test_testing_agents_split_read_only_and_reproducer_roles() -> None:
    tester = tomllib.loads((ROOT / "custom-agents" / "so_tester.toml").read_text(encoding="utf-8"))
    reproducer = tomllib.loads((ROOT / "custom-agents" / "so_reproducer.toml").read_text(encoding="utf-8"))
    assert tester["sandbox_mode"] == "read-only"
    assert reproducer["sandbox_mode"] == "workspace-write"


def test_implementer_agent_has_workspace_safety_contract() -> None:
    implementer = tomllib.loads((ROOT / "custom-agents" / "so_implementer.toml").read_text(encoding="utf-8"))
    instructions = implementer["developer_instructions"].lower()
    assert "do not revert unrelated edits" in instructions
    assert "write scope" in instructions


def test_gitignore_excludes_generated_files() -> None:
    patterns = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".DS_Store" in patterns
    assert "__pycache__/" in patterns
    assert "evals/live_traces/" in patterns


def test_no_tracked_ds_store_files() -> None:
    proc = subprocess.run(
        ["git", "ls-files", "*.DS_Store", ".DS_Store"],
        text=True,
        capture_output=True,
        cwd=ROOT,
        check=True,
    )
    assert proc.stdout == ""


def test_check_script_exists_and_runs_expected_commands() -> None:
    check_script = ROOT / "scripts" / "check.sh"
    assert check_script.exists()
    text = check_script.read_text(encoding="utf-8")
    assert "python3 tests/test_hook.py" in text
    assert "python3 tests/test_skills.py" in text
    assert "python3 tests/test_evals.py" in text
    assert "python3 tests/test_live_evals.py" in text
    assert "python3 -m compileall -q hooks scripts tests" in text


def test_github_actions_runs_check_script() -> None:
    workflow = ROOT / ".github" / "workflows" / "tests.yml"
    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "on:" in text
    assert "pull_request:" in text
    assert "push:" in text
    assert "bash scripts/check.sh" in text
    assert "python-version: '3.11'" in text


def test_readme_documents_ci_test_surface() -> None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## CI / maintainer checks" in text
    assert "GitHub Actions" in text
    assert "pushes to `main`" in text
    assert "pull requests" in text
    assert "`bash scripts/check.sh`" in text
    assert "`tests/test_hook.py`" in text
    assert "`tests/test_skills.py`" in text
    assert "`tests/test_evals.py`" in text
    assert "`tests/test_live_evals.py`" in text
    assert "real live Codex sessions stay outside CI" in text


def run_all_tests() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


def main() -> int:
    run_all_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
