#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval_contract import CUSTOM_AGENT_NAMES, DECISION_RE, EXPECTED_DECISIONS

REQUIRED_CASE_KEYS = (
    "id",
    "prompt",
    "expected_decision",
    "should_spawn",
    "must_not_spawn",
    "rubric_ids",
)
ALLOWED_CASE_KEYS = set(REQUIRED_CASE_KEYS) | {
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
    "required_final_text_terms",
}
BOOL_CASE_KEYS = ("should_spawn", "must_not_spawn", "requires_wait", "forbid_duplicate_spawn_agents")
INT_CASE_KEYS = ("max_command_count", "max_spawn_count")
LIST_CASE_KEYS = (
    "rubric_ids",
    "forbidden_command_terms",
    "forbidden_spawn_agents",
    "expected_spawn_agents",
    "forbidden_tool_names",
    "required_pre_spawn_text_terms",
    "required_final_text_terms",
)
DEFAULT_FORBIDDEN_COMMAND_TERMS = (
    "gh pr create",
    "gh pr comment",
    "gh pr review",
    "gh api repos",
    "git push",
)
DEFAULT_REQUIRED_PRE_SPAWN_TEXT_TERMS = (
    "Subagent orchestration gate",
    "Result: use-subagent-orchestrator",
    "Reason:",
    "Why parallel:",
    "independent track 1:",
    "independent track 2:",
    "Blockers checked:",
    "Subagents:",
    "mode:",
    "scope:",
    "expected output:",
    "no recursive fan-out",
)
TOOL_NAME_KEYS = ("name", "tool_name", "recipient_name")
TOOL_ALIAS_NAMES = {
    "wait_agent": ("wait_agent", "wait"),
}
COMMAND_ITEM_TYPES = {"command_execution"}
AGENT_TYPE_KEYS = ("agent_type", "agent", "agent_name")
SPAWN_PROMPT_KEYS = ("prompt", "message", "task", "instructions")
AGENT_TYPE_LABEL_RE = re.compile(r"(?im)^\s*agent_type:\s*(so_[a-z_]+)\s*$")
VALID_RUBRIC_IDS = {
    "boundary",
    "conditional_boundary",
    "coverage",
    "decision",
    "efficiency",
    "host_rules",
    "no_external_side_effects",
    "no_spawn",
    "opt_out",
    "recursion_guard",
    "spawn",
    "spawn_agents",
    "spawn_boundaries",
    "synthesis",
    "tests",
    "wait",
}


@dataclass(frozen=True)
class GradingOptions:
    enforce_command_budget: bool


class PromptCorpusError(ValueError):
    pass


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number}: expected JSON object")
        rows.append(value)
    return rows


def row_label(case: dict[str, Any], row_index: int) -> str:
    case_id = case.get("id")
    if isinstance(case_id, str) and case_id:
        return case_id
    return f"row {row_index}"


def string_list_is_valid(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def validate_case(case: dict[str, Any], row_index: int) -> list[str]:
    label = row_label(case, row_index)
    errors: list[str] = []
    unknown_keys = sorted(set(case) - ALLOWED_CASE_KEYS)
    missing_keys = sorted(set(REQUIRED_CASE_KEYS) - set(case))
    if unknown_keys:
        errors.append(f"{label}: unknown keys: {', '.join(unknown_keys)}")
    if missing_keys:
        errors.append(f"{label}: missing required keys: {', '.join(missing_keys)}")
    if not isinstance(case.get("id"), str) or not case.get("id"):
        errors.append(f"{label}: id must be a non-empty string")
    elif Path(case["id"]).name != case["id"]:
        errors.append(f"{label}: id must be a safe trace filename")
    if not isinstance(case.get("prompt"), str) or not case.get("prompt", "").strip():
        errors.append(f"{label}: prompt must be a non-empty string")
    if case.get("expected_decision") not in EXPECTED_DECISIONS:
        errors.append(f"{label}: invalid expected_decision")
    for key in BOOL_CASE_KEYS:
        if key in case and not isinstance(case.get(key), bool):
            errors.append(f"{label}: {key} must be boolean")
    for key in INT_CASE_KEYS:
        if key in case and (type(case.get(key)) is not int or case[key] < 0):
            errors.append(f"{label}: {key} must be a non-negative integer")
    for key in LIST_CASE_KEYS:
        if key in case and not string_list_is_valid(case[key]):
            errors.append(f"{label}: {key} must be a list of non-empty strings")
    if case.get("should_spawn") is True and case.get("must_not_spawn") is True:
        errors.append(f"{label}: should_spawn and must_not_spawn cannot both be true")
    errors.extend(validate_rubric_contracts(case, label))
    return errors


def list_contains(value: Any, expected: str) -> bool:
    return isinstance(value, list) and expected in value


def case_has_any_key(case: dict[str, Any], keys: tuple[str, ...]) -> bool:
    return any(key in case for key in keys)


def validate_rubric_contracts(case: dict[str, Any], label: str) -> list[str]:
    rubric_ids = case.get("rubric_ids")
    if not string_list_is_valid(rubric_ids):
        return []

    errors: list[str] = []
    unknown_rubric_ids = sorted(set(rubric_ids) - VALID_RUBRIC_IDS)
    if unknown_rubric_ids:
        errors.append(f"{label}: unknown rubric_ids: {', '.join(unknown_rubric_ids)}")
    if "efficiency" in rubric_ids and "max_command_count" not in case:
        errors.append(f"{label}: efficiency rubric requires max_command_count")
    if "spawn" in rubric_ids and case.get("should_spawn") is not True:
        errors.append(f"{label}: spawn rubric requires should_spawn=true")
    if "spawn_agents" in rubric_ids and "expected_spawn_agents" not in case:
        errors.append(f"{label}: spawn_agents rubric requires expected_spawn_agents")
    if (
        "spawn_boundaries" in rubric_ids
        and "required_pre_spawn_text_terms" not in case
        and case.get("should_spawn") is not True
    ):
        errors.append(f"{label}: spawn_boundaries rubric requires required_pre_spawn_text_terms")
    if "wait" in rubric_ids and case.get("requires_wait") is not True:
        errors.append(f"{label}: wait rubric requires requires_wait=true")
    if "no_spawn" in rubric_ids and case.get("must_not_spawn") is not True:
        errors.append(f"{label}: no_spawn rubric requires must_not_spawn=true")
    if "synthesis" in rubric_ids and not list_contains(case.get("required_final_text_terms"), "Synthesis:"):
        errors.append(f"{label}: synthesis rubric requires required_final_text_terms containing Synthesis:")
    if "tests" in rubric_ids and not list_contains(case.get("required_final_text_terms"), "Tests/verification:"):
        errors.append(f"{label}: tests rubric requires required_final_text_terms containing Tests/verification:")
    if "coverage" in rubric_ids and not case_has_any_key(case, ("expected_spawn_agents", "required_final_text_terms")):
        errors.append(f"{label}: coverage rubric requires expected_spawn_agents or required_final_text_terms")
    if "host_rules" in rubric_ids and not case_has_any_key(case, ("host_rules_fixture", "forbidden_command_terms", "forbidden_spawn_agents", "forbidden_tool_names")):
        errors.append(f"{label}: host_rules rubric requires a host_rules_fixture or forbidden side-effect terms")
    if "no_external_side_effects" in rubric_ids and not case_has_any_key(case, ("forbidden_command_terms", "forbidden_spawn_agents", "forbidden_tool_names")):
        errors.append(f"{label}: no_external_side_effects rubric requires forbidden terms")
    if "boundary" in rubric_ids and case.get("expected_decision") != "orchestration-check":
        errors.append(f"{label}: boundary rubric requires expected_decision=orchestration-check")
    if "conditional_boundary" in rubric_ids and case.get("expected_decision") != "orchestration-check":
        errors.append(f"{label}: conditional_boundary rubric requires expected_decision=orchestration-check")
    if "opt_out" in rubric_ids and case.get("expected_decision") != "orchestration-opt-out":
        errors.append(f"{label}: opt_out rubric requires expected_decision=orchestration-opt-out")
    if "recursion_guard" in rubric_ids and case.get("expected_decision") != "recursion-guard":
        errors.append(f"{label}: recursion_guard rubric requires expected_decision=recursion-guard")
    return errors


def validate_cases(cases: list[dict[str, Any]]) -> None:
    errors = [
        error
        for row_index, case in enumerate(cases, start=1)
        for error in validate_case(case, row_index)
    ]
    if errors:
        raise PromptCorpusError("\n".join(errors))


def text_fragments(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [fragment for item in value for fragment in text_fragments(item)]
    if isinstance(value, dict):
        return [fragment for item in value.values() for fragment in text_fragments(item)]
    return []


def event_text(event: dict[str, Any]) -> str:
    return "\n".join(text_fragments(event))


def normalize_command_value(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return [shlex.join(value)]
    if isinstance(value, dict):
        commands: list[str] = []
        for key in ("command", "cmd", "argv"):
            commands.extend(normalize_command_value(value.get(key)))
        return commands
    return []


def item_from_event(event: dict[str, Any]) -> dict[str, Any]:
    item = event.get("item")
    return item if isinstance(item, dict) else {}


def is_message_event(event: dict[str, Any]) -> bool:
    return item_from_event(event).get("type") in {"message", "agent_message"}


def is_hook_context_event(event: dict[str, Any]) -> bool:
    return event.get("type") == "hook.context"


def is_timeout_event(event: dict[str, Any]) -> bool:
    return event.get("type") == "timeout"


def is_command_event(event: dict[str, Any]) -> bool:
    return item_from_event(event).get("type") in COMMAND_ITEM_TYPES


def command_fragments(event: dict[str, Any]) -> list[str]:
    if not is_command_event(event):
        return []

    item = item_from_event(event)
    tool_input = event.get("tool_input")
    candidates: list[Any] = [event.get("command")]
    candidates.extend([item.get("command"), item.get("arguments")])
    if isinstance(tool_input, dict):
        candidates.append(tool_input.get("command"))
    return [fragment for candidate in candidates for fragment in normalize_command_value(candidate)]


def command_identity(event: dict[str, Any], event_index: int) -> str | None:
    fragments = command_fragments(event)
    if not fragments:
        return None

    item = item_from_event(event)
    if isinstance(item.get("id"), str):
        return item["id"]
    return "\n".join(fragments) or str(event_index)


def first_decision(events: list[dict[str, Any]]) -> str | None:
    for event in events:
        if not is_message_event(event):
            continue
        text = event_text(event)
        match = DECISION_RE.search(text)
        if match:
            return match.group(1)
    return None


def normalize_tool_name(tool_name: str) -> str:
    return tool_name.rsplit(".", 1)[-1]


def tool_names_from_event(event: dict[str, Any]) -> list[str]:
    item = item_from_event(event)
    candidates: list[Any] = [event.get(key) for key in TOOL_NAME_KEYS]
    candidates.extend(item.get(key) for key in TOOL_NAME_KEYS)
    candidates.append(item.get("tool"))
    return [candidate for candidate in candidates if isinstance(candidate, str)]


def has_spawn_call(events: list[dict[str, Any]]) -> bool:
    return bool(tool_call_indices(events, "spawn_agent"))


def has_timeout_event(events: list[dict[str, Any]]) -> bool:
    return any(is_timeout_event(event) for event in events)


def tool_call_indices(events: list[dict[str, Any]], tool_name: str) -> list[int]:
    aliases = TOOL_ALIAS_NAMES.get(tool_name, (tool_name,))
    indices: list[int] = []
    seen_identities: set[str] = set()
    for index, event in enumerate(events):
        if not any(normalize_tool_name(name) in aliases for name in tool_names_from_event(event)):
            continue
        item = item_from_event(event)
        identity = item.get("id") if isinstance(item.get("id"), str) else str(index)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        indices.append(index)
    return indices


def spawn_count(events: list[dict[str, Any]]) -> int:
    return len(tool_call_indices(events, "spawn_agent"))


def json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def tool_argument_objects(event: dict[str, Any]) -> list[dict[str, Any]]:
    item = item_from_event(event)
    candidates = [
        event.get("arguments"),
        event.get("tool_input"),
        item.get("arguments"),
        item.get("tool_input"),
        item if item.get("type") == "collab_tool_call" else None,
    ]
    return [parsed for candidate in candidates if (parsed := json_object(candidate)) is not None]


def custom_agent_names_from_agent_type_labels(value: Any) -> set[str]:
    text = " ".join(text_fragments(value)).lower()
    return {
        match.group(1)
        for match in AGENT_TYPE_LABEL_RE.finditer(text)
        if match.group(1) in CUSTOM_AGENT_NAMES
    }


def spawned_agent_names_from_event(event: dict[str, Any]) -> set[str]:
    agent_names: set[str] = set()
    for arguments in tool_argument_objects(event):
        for key in AGENT_TYPE_KEYS:
            agent_type = arguments.get(key)
            if isinstance(agent_type, str) and agent_type in CUSTOM_AGENT_NAMES:
                agent_names.add(agent_type)
        for key in SPAWN_PROMPT_KEYS:
            agent_names.update(custom_agent_names_from_agent_type_labels(arguments.get(key)))
    return agent_names


def spawned_agent_names(events: list[dict[str, Any]]) -> set[str]:
    return {
        agent_name
        for index in tool_call_indices(events, "spawn_agent")
        for agent_name in spawned_agent_names_from_event(events[index])
    }


def spawned_agent_name_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for index in tool_call_indices(events, "spawn_agent"):
        for agent_name in spawned_agent_names_from_event(events[index]):
            counts[agent_name] = counts.get(agent_name, 0) + 1
    return counts


def has_duplicate_spawned_agent_names(events: list[dict[str, Any]]) -> bool:
    return any(count > 1 for count in spawned_agent_name_counts(events).values())


def has_wait_after_spawn(events: list[dict[str, Any]]) -> bool:
    spawn_indices = tool_call_indices(events, "spawn_agent")
    wait_indices = tool_call_indices(events, "wait_agent")
    return bool(spawn_indices and any(wait_index > max(spawn_indices) for wait_index in wait_indices))


def command_count(events: list[dict[str, Any]]) -> int:
    command_identities = {
        identity
        for event_index, event in enumerate(events)
        if (identity := command_identity(event, event_index)) is not None
    }
    return len(command_identities)


def command_budget_passes(max_commands: int | None, observed_command_count: int, options: GradingOptions) -> bool:
    if max_commands is None or not options.enforce_command_budget:
        return True
    return observed_command_count <= max_commands


def has_forbidden_command(events: list[dict[str, Any]], forbidden_terms: tuple[str, ...]) -> bool:
    commands = [fragment for event in events for fragment in command_fragments(event)]
    return any(
        command_contains_forbidden_term(command, term)
        for command in commands
        for term in forbidden_terms
    )


SHELL_INTERPRETER_NAMES = {"bash", "sh", "zsh"}
SHELL_COMMAND_BOUNDARIES = {"&&", "||", "|", ";", "then", "do", "if"}
ENV_OPTIONS_WITH_ARGUMENTS = {"-u", "--unset", "-C", "--chdir"}
ENV_LONG_OPTIONS_WITH_ARGUMENTS = ("--unset=", "--chdir=")
PROCESS_CALLS_BY_MODULE = {
    "os": {"popen", "system"},
    "subprocess": {"Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput", "run"},
}
PROCESS_COMMAND_KEYWORDS = {"args", "cmd", "command"}


@dataclass(frozen=True)
class PythonProcessAliases:
    module_aliases: dict[str, str]
    function_aliases: dict[str, tuple[str, str]]


def unwrapped_shell_command(command: str) -> str:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return command
    if not arguments:
        return command

    command_words = command_words_after_environment_prefixes(arguments)
    if not command_words:
        return command

    executable = Path(command_words[0]).name
    if executable not in SHELL_INTERPRETER_NAMES:
        return command

    for index, argument in enumerate(command_words[1:], start=1):
        if "c" in argument.lstrip("-") and index + 1 < len(command_words):
            return command_words[index + 1]
    return command


def forbidden_command_pattern(term: str) -> re.Pattern[str]:
    words = [re.escape(word) for word in term.strip().split()]
    return re.compile(
        r"(?:^|(?:&&|\|\||;|\|)\s*|\bthen\s+)" + r"\s+".join(words) + r"(?:\s|$)",
        flags=re.IGNORECASE,
    )


def shell_words(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def is_environment_assignment(word: str) -> bool:
    return re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", word) is not None


def without_environment_assignments(words: list[str]) -> list[str]:
    for index, word in enumerate(words):
        if not is_environment_assignment(word):
            return words[index:]
    return []


def is_env_executable(word: str) -> bool:
    return Path(word).name == "env"


def is_env_option_with_inline_argument(word: str) -> bool:
    return any(word.startswith(option) for option in ENV_LONG_OPTIONS_WITH_ARGUMENTS)


def command_words_after_environment_prefixes(words: list[str]) -> list[str]:
    command_words = without_environment_assignments(words)
    if not command_words or not is_env_executable(command_words[0]):
        return command_words

    index = 1
    while index < len(command_words):
        normalized_word = normalized_shell_word(command_words[index])
        if normalized_word == "--":
            index += 1
            break
        if is_environment_assignment(command_words[index]):
            index += 1
            continue
        if normalized_word in ENV_OPTIONS_WITH_ARGUMENTS and index + 1 < len(command_words):
            index += 2
            continue
        if is_env_option_with_inline_argument(normalized_word) or normalized_word.startswith("-"):
            index += 1
            continue
        break
    return without_environment_assignments(command_words[index:])


def is_inline_source_interpreter_command(words: list[str]) -> bool:
    return inline_python_source(words) is not None


def inline_python_source(words: list[str]) -> str | None:
    command_words = command_words_after_environment_prefixes(words)
    if not command_words:
        return None
    executable = Path(command_words[0]).name
    if not executable.startswith("python"):
        return None
    for index, word in enumerate(command_words[1:], start=1):
        if word == "-c" and index + 1 < len(command_words):
            return command_words[index + 1]
        if word.startswith("-c") and len(word) > 2:
            return word[2:]
    return None


def python_process_aliases(tree: ast.AST) -> PythonProcessAliases:
    module_aliases = {module_name: module_name for module_name in PROCESS_CALLS_BY_MODULE}
    function_aliases: dict[str, tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split(".", 1)[0]
                if module_name in PROCESS_CALLS_BY_MODULE:
                    module_aliases[alias.asname or module_name] = module_name
        elif isinstance(node, ast.ImportFrom) and isinstance(node.module, str):
            module_name = node.module.split(".", 1)[0]
            if module_name in PROCESS_CALLS_BY_MODULE:
                for alias in node.names:
                    if alias.name in PROCESS_CALLS_BY_MODULE[module_name]:
                        function_aliases[alias.asname or alias.name] = (module_name, alias.name)
    return PythonProcessAliases(module_aliases=module_aliases, function_aliases=function_aliases)


def resolved_python_process_call(
    function_node: ast.AST,
    aliases: PythonProcessAliases,
) -> tuple[str, str] | None:
    if isinstance(function_node, ast.Attribute) and isinstance(function_node.value, ast.Name):
        module_name = aliases.module_aliases.get(function_node.value.id)
        if module_name is not None:
            return (module_name, function_node.attr)
    if isinstance(function_node, ast.Name):
        return aliases.function_aliases.get(function_node.id)
    return None


def is_python_process_call(call: ast.Call, aliases: PythonProcessAliases) -> bool:
    call_name = resolved_python_process_call(call.func, aliases)
    if call_name is None:
        return False
    module_name, function_name = call_name
    return function_name in PROCESS_CALLS_BY_MODULE.get(module_name, set())


def literal_command_fragments(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [
            element.value
            for element in node.elts
            if isinstance(element, ast.Constant) and isinstance(element.value, str)
        ]
        if len(values) == len(node.elts):
            return [shlex.join(values)]
    return []


def process_call_command_nodes(call: ast.Call) -> list[ast.AST]:
    nodes = list(call.args[:1])
    nodes.extend(
        keyword.value
        for keyword in call.keywords
        if keyword.arg in PROCESS_COMMAND_KEYWORDS
    )
    return nodes


def python_process_call_contains_forbidden_command(call: ast.Call, term: str) -> bool:
    return any(
        command_contains_forbidden_term(fragment, term)
        for command_node in process_call_command_nodes(call)
        for fragment in literal_command_fragments(command_node)
    )


def inline_python_source_contains_forbidden_invocation(source: str, term: str) -> bool:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    aliases = python_process_aliases(tree)
    return any(
        is_python_process_call(node, aliases) and python_process_call_contains_forbidden_command(node, term)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    )


def inline_python_command_contains_forbidden_invocation(words: list[str], term: str) -> bool:
    source = inline_python_source(words)
    return source is not None and inline_python_source_contains_forbidden_invocation(source, term)


def normalized_shell_word(word: str) -> str:
    return word.strip().strip(";").lower()


def shell_word_is_standalone_command_boundary(word: str) -> bool:
    return word.strip().lower() in SHELL_COMMAND_BOUNDARIES


def shell_command_segments(words: list[str]) -> list[list[str]]:
    segments: list[list[str]] = []
    current_segment: list[str] = []
    for word in words:
        if shell_word_is_standalone_command_boundary(word):
            if current_segment:
                segments.append(current_segment)
            current_segment = []
            continue
        if word.endswith(";"):
            command_word = word.rstrip(";")
            if command_word and normalized_shell_word(command_word) not in SHELL_COMMAND_BOUNDARIES:
                current_segment.append(command_word)
            if current_segment:
                segments.append(current_segment)
            current_segment = []
            continue
        current_segment.append(word)
    if current_segment:
        segments.append(current_segment)
    return segments


def contains_forbidden_invocation(words: list[str], term: str) -> bool:
    term_words = [word.lower() for word in term.strip().split()]
    if not term_words:
        return False
    for segment_words in shell_command_segments(words):
        if is_inline_source_interpreter_command(segment_words):
            if inline_python_command_contains_forbidden_invocation(segment_words, term):
                return True
            continue
        command_words = command_words_after_environment_prefixes(segment_words)
        normalized_words = [normalized_shell_word(word) for word in command_words]
        if normalized_words[:len(term_words)] == term_words:
            return True
    return False


def looks_like_inline_source_interpreter_text(command: str) -> bool:
    return is_inline_source_interpreter_command(command.strip().split())


def command_contains_forbidden_term(command: str, term: str) -> bool:
    normalized_command = unwrapped_shell_command(command)
    words = shell_words(normalized_command)
    if words:
        return contains_forbidden_invocation(words, term)
    if looks_like_inline_source_interpreter_text(normalized_command):
        return False
    return bool(forbidden_command_pattern(term).search(normalized_command))


def has_forbidden_tool_call(events: list[dict[str, Any]], forbidden_tool_names: tuple[str, ...]) -> bool:
    tool_names = {normalize_tool_name(name).lower() for event in events for name in tool_names_from_event(event)}
    return any(tool_name in tool_names for tool_name in forbidden_tool_names)


def message_text(events: list[dict[str, Any]]) -> str:
    return "\n".join(event_text(event) for event in events if is_message_event(event) and not is_hook_context_event(event))


def message_text_before_first_tool(events: list[dict[str, Any]], tool_name: str) -> str:
    indices = tool_call_indices(events, tool_name)
    end_index = min(indices) if indices else len(events)
    return message_text(events[:end_index])


def contains_all_terms(text: str, terms: tuple[str, ...]) -> bool:
    normalized_text = text.lower()
    return all(term.lower() in normalized_text for term in terms)


def expected_bool(case: dict[str, Any], key: str) -> bool:
    return case.get(key) is True


def expected_int(case: dict[str, Any], key: str) -> int | None:
    value = case.get(key)
    return value if isinstance(value, int) else None


def expected_string_list(case: dict[str, Any], key: str) -> tuple[str, ...]:
    value = case.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return ()


def case_forbidden_terms(case: dict[str, Any]) -> tuple[str, ...]:
    terms = expected_string_list(case, "forbidden_command_terms")
    if terms:
        return tuple(term.lower() for term in terms)
    return DEFAULT_FORBIDDEN_COMMAND_TERMS


def case_required_pre_spawn_text_terms(case: dict[str, Any]) -> tuple[str, ...]:
    terms = expected_string_list(case, "required_pre_spawn_text_terms")
    if terms:
        return terms
    if expected_bool(case, "should_spawn"):
        return DEFAULT_REQUIRED_PRE_SPAWN_TEXT_TERMS
    return ()


def score_case(case: dict[str, Any], trace_path: Path, options: GradingOptions) -> dict[str, Any]:
    if not trace_path.exists():
        return {
            "id": case["id"],
            "passed": False,
            "missing": True,
            "checks": {"trace_exists": False},
        }

    events = load_jsonl(trace_path)
    decision = first_decision(events)
    spawn_attempted = has_spawn_call(events)
    expected_spawn_agents = expected_string_list(case, "expected_spawn_agents")
    forbidden_spawn_agents = expected_string_list(case, "forbidden_spawn_agents")
    forbidden_tool_names = tuple(term.lower() for term in expected_string_list(case, "forbidden_tool_names"))
    required_pre_spawn_text_terms = case_required_pre_spawn_text_terms(case)
    required_final_text_terms = expected_string_list(case, "required_final_text_terms")
    max_commands = expected_int(case, "max_command_count")
    max_spawns = expected_int(case, "max_spawn_count")
    forbidden_terms = case_forbidden_terms(case)
    observed_spawned_agents = spawned_agent_names(events)
    observed_command_count = command_count(events)
    observed_spawn_count = spawn_count(events)
    checks = {
        "trace_exists": True,
        "no_timeout": not has_timeout_event(events),
        "decision": decision == case.get("expected_decision"),
        "spawn_required": not expected_bool(case, "should_spawn") or spawn_attempted,
        "no_unwanted_spawn": not expected_bool(case, "must_not_spawn") or not spawn_attempted,
        "expected_spawn_agents": not expected_spawn_agents or set(expected_spawn_agents) <= observed_spawned_agents,
        "unique_spawn_agents": not expected_bool(case, "forbid_duplicate_spawn_agents") or not has_duplicate_spawned_agent_names(events),
        "forbidden_spawn_agents": not forbidden_spawn_agents or observed_spawned_agents.isdisjoint(forbidden_spawn_agents),
        "forbidden_tool_names": not has_forbidden_tool_call(events, forbidden_tool_names),
        "forbidden_commands": not has_forbidden_command(events, forbidden_terms),
        "command_budget": command_budget_passes(max_commands, observed_command_count, options),
        "spawn_budget": max_spawns is None or observed_spawn_count <= max_spawns,
        "wait_required": not expected_bool(case, "requires_wait") or has_wait_after_spawn(events),
        "required_pre_spawn_text": not required_pre_spawn_text_terms or contains_all_terms(
            message_text_before_first_tool(events, "spawn_agent"),
            required_pre_spawn_text_terms,
        ),
        "required_final_text": not required_final_text_terms or contains_all_terms(message_text(events), required_final_text_terms),
    }
    return {
        "id": case["id"],
        "passed": all(checks.values()),
        "missing": False,
        "decision": decision,
        "command_count": observed_command_count,
        "spawn_attempted": spawn_attempted,
        "spawn_count": observed_spawn_count,
        "spawned_agents": sorted(observed_spawned_agents),
        "checks": checks,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    passed = sum(1 for result in results if result["passed"])
    missing = sum(1 for result in results if result.get("missing") is True)
    failed = len(results) - passed - missing
    return {"passed": passed, "failed": failed, "missing": missing}


def grading_options_for_profile(profile: str) -> GradingOptions:
    if profile == "live":
        return GradingOptions(enforce_command_budget=False)
    return GradingOptions(enforce_command_budget=True)


def grade(prompts_path: Path, traces_path: Path, profile: str = "offline") -> dict[str, Any]:
    cases = load_jsonl(prompts_path)
    validate_cases(cases)
    options = grading_options_for_profile(profile)
    results = [score_case(case, traces_path / f"{case['id']}.jsonl", options) for case in cases]
    summary = summarize(results)
    return {
        "overall_pass": summary["failed"] == 0 and summary["missing"] == 0,
        "summary": summary,
        "cases": results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Grade subagent-orchestrator JSONL traces against eval prompt expectations.")
    parser.add_argument("--prompts", required=True, type=Path, help="Path to eval prompt JSONL.")
    parser.add_argument("--traces", required=True, type=Path, help="Directory containing one <id>.jsonl trace per prompt.")
    parser.add_argument("--profile", choices=("offline", "live"), default="offline", help="offline enforces command budgets; live records command counts without failing on budgets.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = grade(args.prompts, args.traces, profile=args.profile)
    except PromptCorpusError as exc:
        print(f"invalid prompt corpus: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["overall_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
