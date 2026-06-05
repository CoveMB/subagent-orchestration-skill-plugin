#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "hooks" / "subagent_orchestration_gate.py"
PLUGIN_ROOT = ROOT / "plugin" / "subagent-orchestrator"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
SKILLS_ROOT = PLUGIN_ROOT / "skills"
ORCHESTRATOR_SKILL = SKILLS_ROOT / "subagent-orchestrator" / "SKILL.md"
USING_ORCHESTRATOR_SKILL = SKILLS_ROOT / "using-subagent-orchestrator" / "SKILL.md"
EXPECTED_SKILL_NAMES = {"subagent-orchestrator", "using-subagent-orchestrator"}
DECISION_MAPPING_REQUIRED_TERMS = [
    "hook result",
    "compatibility-gate action",
    "execution-shape action",
    "`single-thread-default`",
    "`single-thread-likely`",
    "`orchestration-check`",
    "`use-subagent-orchestrator`",
    "`orchestration-opt-out`",
    "`recursion-guard`",
    "`skip`",
    "`check`",
    "proceed normally; do not load orchestration by default",
    "proceed normally after a short local gate check if useful",
    "short local gate check",
    "load `subagent-orchestrator` only if independent tracks are clear",
    "load `subagent-orchestrator`",
    "do not load orchestration or spawn agents",
    "do not recursively orchestrate unless the parent explicitly provided bounded permission",
    "does not spawn agents by itself or inject binding execution instructions",
    "chooses only `single-thread`, `sequential-plan`, or `parallel-subagents`",
]
DECISION_MAPPING_TABLE_REQUIRED_TERMS = [
    "hook result mapping",
    "| hook result | compatibility-gate action | execution-shape action |",
    "| --- | --- | --- |",
    "| `single-thread-default` | `skip` |",
    "| `single-thread-likely` | `check` |",
    "| `orchestration-check` | `check` |",
    "| `use-subagent-orchestrator` | `use-subagent-orchestrator` |",
    "| `orchestration-opt-out` | `skip` |",
    "| `recursion-guard` | `skip` |",
]
SKILL_FORWARD_SCENARIOS = [
    (
        "simple direct question",
        ORCHESTRATOR_SKILL,
        "What does this repository do?",
        [
            "`single-thread`",
            "the user asks a simple direct question",
            "parallelism would add more overhead than value",
        ],
    ),
    (
        "strictly sequential task",
        ORCHESTRATOR_SKILL,
        "Apply each migration step only after the previous step succeeds.",
        [
            "`sequential-plan`",
            "strictly sequential",
            "do not spawn subagents yet",
        ],
    ),
    (
        "debugging with separable tracks",
        ORCHESTRATOR_SKILL,
        "Debug a flaky multi-file auth regression and propose tests.",
        [
            "### debugging",
            "`so_mapper`",
            "`so_reproducer`",
            "`so_tester`",
            "`so_reviewer`",
            "observed failure mode",
            "likely root cause",
        ],
    ),
    (
        "branch review",
        ORCHESTRATOR_SKILL,
        "Review this branch for correctness, security, and missing tests.",
        [
            "### pr/branch review",
            "`so_mapper`",
            "`so_reviewer`",
            "`so_tester`",
            "real issues only",
        ],
    ),
    (
        "opt-out compatibility gate",
        USING_ORCHESTRATOR_SKILL,
        "No subagents. Review this patch linearly.",
        [
            "`skip` for tiny edits",
            "explicit user opt-out",
            "respect explicit user opt-outs",
        ],
    ),
    (
        "complex compatibility gate",
        USING_ORCHESTRATOR_SKILL,
        "Investigate failing API and web tests across modules.",
        [
            "`use-subagent-orchestrator` for complex debugging",
            "load and follow the `subagent-orchestrator` skill",
            "actual spawning is required",
        ],
    ),
]


def run_hook_context(prompt: str) -> str:
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"prompt": prompt}),
        text=True,
        capture_output=True,
        check=True,
    )
    data = json.loads(proc.stdout)
    return data["hookSpecificOutput"]["additionalContext"]


def assert_text_contains_all(text: str, required_terms: list[str], source: Path | str) -> None:
    missing_terms = [term for term in required_terms if term not in text]
    assert not missing_terms, (source, missing_terms)


def parse_skill_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[0] == "---", path
    end_index = lines[1:].index("---") + 1
    frontmatter: dict[str, str] = {}
    for line in lines[1:end_index]:
        key, separator, value = line.partition(":")
        assert separator == ":", (path, line)
        frontmatter[key.strip()] = value.strip()
    return frontmatter


def test_skill_frontmatter_is_valid_and_discoverable() -> None:
    skill_paths = sorted(SKILLS_ROOT.glob("*/SKILL.md"))
    assert {path.parent.name for path in skill_paths} == EXPECTED_SKILL_NAMES

    for path in skill_paths:
        frontmatter = parse_skill_frontmatter(path)
        assert set(frontmatter) == {"name", "description"}, (path, frontmatter)
        assert frontmatter["name"] == path.parent.name, path
        assert len(frontmatter["description"]) >= 80, path
        assert "use" in frontmatter["description"].lower(), path


def test_plugin_manifest_skill_path_matches_skill_folders() -> None:
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    skills_path = PLUGIN_ROOT / manifest["skills"]
    assert skills_path.resolve() == SKILLS_ROOT.resolve()
    assert {path.name for path in skills_path.iterdir() if (path / "SKILL.md").exists()} == EXPECTED_SKILL_NAMES


def test_plugin_interface_respects_optional_helper_boundary() -> None:
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    interface = manifest["interface"]
    searchable_text = " ".join([
        manifest["description"],
        interface["shortDescription"],
        interface["longDescription"],
        *interface["defaultPrompt"],
    ]).lower()

    assert "optional" in searchable_text
    assert "evaluate every prompt" not in searchable_text
    assert "gate first" not in searchable_text
    assert "before work begins" not in searchable_text


def test_plugin_manifest_explains_bounded_write_capability() -> None:
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    interface = manifest["interface"]
    long_description = interface["longDescription"].lower()

    assert interface["capabilities"] == ["Read", "Write"]
    assert "read-only-first" in long_description
    assert "write-capable roles are bounded" in long_description
    assert "explicitly appropriate" in long_description
    assert "metadata plus non-binding hints" in long_description
    assert "not a global bootstrap" in long_description


def test_user_facing_docs_explain_bounded_write_boundary() -> None:
    combined_text = "\n".join([
        (ROOT / "README.md").read_text(encoding="utf-8"),
        (ROOT / "docs" / "skill-usage-examples.md").read_text(encoding="utf-8"),
    ]).lower()

    assert_text_contains_all(
        combined_text,
        [
            "read-only-first",
            "bounded workspace-write roles",
            "`so_implementer`",
            "`so_reproducer`",
            "scratch/log work",
            "explicit task scope or prior synthesis",
            "destructive/external actions",
            "host/user/approval rules",
            "do not write, spawn, or activate a global bootstrap automatically",
        ],
        "README.md and docs/skill-usage-examples.md",
    )


def test_orchestrator_skill_has_execution_runbook() -> None:
    text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8")
    assert_text_contains_all(
        text,
        [
            "## Execution Runbook",
            "### Spawn Template",
            "### Agent Task Templates",
            "### Fallback When Custom Agents Are Unavailable",
        ],
        ORCHESTRATOR_SKILL,
    )


def test_orchestrator_skill_defines_subagent_prompt_compiler() -> None:
    text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    assert_text_contains_all(
        text,
        [
            "## subagent prompt compiler",
            "extract task type",
            "extract scope",
            "changed files from diff/status if available",
            "unknown scope if no reliable boundary is known",
            "identify independent tracks",
            "code-path mapping",
            "risk/security/correctness review",
            "test discovery/verification",
            "docs/api/version verification",
            "design alternatives",
            "select the smallest read-only roster",
            "spawn read-only agents first",
            "emit bounded prompts",
            "spawn",
            "wait",
            "wait and synthesize",
            "workspace-write is justified",
            "scope primer before spawning",
            "minimal local read-only pass",
            "at least two independent tracks",
            "initial roster",
            "review/audit",
            "debugging/root-cause",
            "refactor/migration",
            "docs/api/version-dependent task",
            "comparison/options",
            "implementation after investigation",
            "context:",
            "non-goals:",
            "structured expected output",
            "confidence:",
            "evidence versus inference",
            "no recursive fan-out",
            "no edits unless workspace-write scope is explicit",
        ],
        ORCHESTRATOR_SKILL,
    )


def test_skills_define_decision_taxonomy_and_avoidance_contracts() -> None:
    orchestrator_text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    using_text = USING_ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()

    assert_text_contains_all(
        orchestrator_text,
        [
            "`single-thread`",
            "`sequential-plan`",
            "`parallel-subagents`",
            "spawn subagents when at least two are true",
            "avoid subagents when any are true",
            "the user asks a simple direct question",
            "the edit is tiny and obvious",
            "agents would compete to mutate the same files",
        ],
        ORCHESTRATOR_SKILL,
    )
    assert_text_contains_all(
        using_text,
        [
            "orchestration gate: skip | check | use-subagent-orchestrator",
            "`skip` for tiny edits",
            "`check` for moderate uncertainty",
            "`use-subagent-orchestrator` for complex debugging",
        ],
        USING_ORCHESTRATOR_SKILL,
    )


def test_hook_gate_execution_mapping_is_documented() -> None:
    source_paths = [
        USING_ORCHESTRATOR_SKILL,
        ROOT / "README.md",
        ROOT / "snippets" / "AGENTS.subagent-orchestration.md",
    ]
    for path in source_paths:
        text = path.read_text(encoding="utf-8").lower()
        assert_text_contains_all(text, DECISION_MAPPING_REQUIRED_TERMS, path)
        assert_text_contains_all(text, DECISION_MAPPING_TABLE_REQUIRED_TERMS, path)


def test_skills_define_priority_and_negative_contracts() -> None:
    for path in [ORCHESTRATOR_SKILL, USING_ORCHESTRATOR_SKILL]:
        text = path.read_text(encoding="utf-8").lower()
        assert_text_contains_all(
            text,
            [
                "existing orchestration, routing, bootstrap, skill-selection, and agent-management frameworks take priority",
                "host repository rules win",
                "do not ask the user whether orchestration is preferable",
                "do not recursively",
                "subagent output is a work product",
            ],
            path,
        )


def test_orchestrator_skill_defines_spawn_boundaries_and_synthesis() -> None:
    text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    assert_text_contains_all(
        text,
        [
            "role, mode, scope, expected output, and no recursive fan-out",
            "do not edit files; do not spawn more agents; report uncertainty",
            "for workspace-write tasks",
            "write scope",
            "synthesis must include",
            "conflicts or uncertainty",
            "tests/verification",
            "agent_type: <agent-name>",
            "keep `fork_context` unset",
            "spawn <agent-name> prompt:",
            "why parallel:",
            "independent track 1:",
            "independent track 2:",
            "blockers checked:",
            "complexity alone is not enough",
            "if at least two bounded read-only tracks can return independently useful outputs",
            "spawn the smallest useful read-only roster unless a concrete blocker exists",
        ],
        ORCHESTRATOR_SKILL,
    )


def test_orchestrator_skill_scopes_dirty_state_by_agent_mode() -> None:
    orchestrator_text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    using_text = USING_ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    combined_text = "\n".join([orchestrator_text, using_text])

    assert_text_contains_all(
        orchestrator_text,
        [
            "do not treat dirty repo state as a blocker for bounded read-only mapper, reviewer, tester, docs, or research agents",
            "report whether findings depend on uncommitted changes",
            "treat dirty repo state as a blocker only for workspace-write agents when isolation is unclear",
            "workspace-write dirty-state isolation",
        ],
        ORCHESTRATOR_SKILL,
    )
    assert_text_contains_all(
        combined_text,
        [
            "explicit user opt-out",
            "child-agent recursion",
            "strict sequential",
            "external side effects",
            "privacy/tool",
            "conflicting writes",
            "unbounded broad agent tasks",
        ],
        "orchestrator skill dirty-state boundary",
    )
    assert "the repo state is dirty and isolation is unclear" not in orchestrator_text


def test_skill_forward_scenarios_have_actionable_guidance() -> None:
    for scenario_name, path, prompt, required_terms in SKILL_FORWARD_SCENARIOS:
        assert prompt, scenario_name
        text = path.read_text(encoding="utf-8").lower()
        assert_text_contains_all(text, required_terms, f"{path} scenario={scenario_name}")


def test_debugging_pattern_keeps_reproducer_out_of_read_only_phase() -> None:
    text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    debugging_start = text.index("### debugging")
    next_pattern_start = text.index("### pr/branch review")
    debugging_text = text[debugging_start:next_pattern_start]

    assert_text_contains_all(
        debugging_text,
        [
            "phase 1 - read-only",
            "`so_mapper`: map relevant code paths and likely failure location.",
            "`so_tester`: identify targeted tests, reproduction commands, and missing coverage.",
            "`so_reviewer`: inspect likely fix risks.",
            "phase 2 - only if safe and useful",
            "`so_reproducer`: reproduce the failure and collect logs after mapper/tester narrow scope.",
            "use workspace-write only for temporary scratch artifacts",
            "report artifacts created/removed",
            "do not edit product code unless explicitly assigned",
        ],
        ORCHESTRATOR_SKILL,
    )

    phase_one_start = debugging_text.index("phase 1 - read-only")
    phase_two_start = debugging_text.index("phase 2 - only if safe and useful")
    phase_one_text = debugging_text[phase_one_start:phase_two_start]

    assert "`so_reproducer`" not in phase_one_text


def test_skill_boundary_contract_matches_readme_and_agents_snippet() -> None:
    source_paths = [
        ORCHESTRATOR_SKILL,
        USING_ORCHESTRATOR_SKILL,
        ROOT / "README.md",
        ROOT / "snippets" / "AGENTS.subagent-orchestration.md",
    ]
    for path in source_paths:
        text = path.read_text(encoding="utf-8").lower()
        assert_text_contains_all(
            text,
            [
                "host repository rules",
                "subagent output",
                "source-of-truth",
                "do not",
            ],
            path,
        )


def test_skills_allow_bounded_read_only_delegation_without_overriding_constraints() -> None:
    source_paths = [
        ORCHESTRATOR_SKILL,
        USING_ORCHESTRATOR_SKILL,
        ROOT / "README.md",
        ROOT / "snippets" / "AGENTS.subagent-orchestration.md",
    ]
    required_terms = [
        "do not ask a separate question solely for bounded read-only delegation",
        "still stop or ask",
        "repository rules",
        "user instructions",
        "safety policy",
        "privacy/context-sharing",
        "vendor/tool policy",
        "approval rules",
        "cost/budget limits",
        "destructive actions",
        "external side effects",
        "workspace-write",
        "unclear boundaries",
    ]

    for path in source_paths:
        text = path.read_text(encoding="utf-8").lower()
        assert "standing authorization" not in text, path
        assert_text_contains_all(text, required_terms, path)

    for path in [ORCHESTRATOR_SKILL, USING_ORCHESTRATOR_SKILL]:
        text = path.read_text(encoding="utf-8").lower()
        assert "clear boundaries" in text, path
    orchestrator_text = ORCHESTRATOR_SKILL.read_text(encoding="utf-8").lower()
    assert "ask before code changes unless" not in orchestrator_text
    assert "ask before code changes only if" in orchestrator_text


def test_parallel_subagent_decision_requires_actual_spawn_attempt() -> None:
    required_terms = [
        "spawn_agent",
        "available subagent-spawning tool",
        "do not stop at a plan",
    ]
    for path in [ORCHESTRATOR_SKILL, USING_ORCHESTRATOR_SKILL]:
        text = path.read_text(encoding="utf-8").lower()
        for term in required_terms:
            assert term in text, (path, term)

    context = run_hook_context("Debug a flaky multi-file auth regression and propose tests.")
    assert "use-subagent-orchestrator" in context.lower(), context


def test_skills_define_host_project_boundary() -> None:
    for path in [ORCHESTRATOR_SKILL, USING_ORCHESTRATOR_SKILL]:
        text = path.read_text(encoding="utf-8").lower()
        assert "execution-shape helper" in text, path
        assert "host repository rules win" in text, path


def run_all_tests() -> None:
    for name, value in sorted(globals().items()):
        if name.startswith("test_") and callable(value):
            value()


def main() -> int:
    run_all_tests()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
